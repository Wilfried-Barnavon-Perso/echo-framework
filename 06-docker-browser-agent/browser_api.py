from flask import Flask, request, jsonify # pyright: ignore[reportMissingImports]
from DrissionPage import WebPage, ChromiumOptions # pyright: ignore[reportMissingImports]
import threading
import time
import uuid
import os
import shutil
import traceback
"""
================================================================================
MODULE : ECHO BROWSER AGENT API
VERSION : v1.0
AUTEUR : Wilfried BARNAVON
================================================================================
"""

app = Flask(__name__)

# --- CONFIGURATION DRISSIONPAGE ---
IDLE_TIMEOUT = 900   # Secondes avant de tuer le processus Chromium inutile

class BrowserManager:
    def __init__(self):
        self.page = None
        self.lock = threading.Lock()
        self.timer = None
        self.last_activity = 0
        
        # Configuration des options Chromium pour Docker (HARDENED)
        self.co = ChromiumOptions()
        self.co.headless(True)
        # Arguments critiques pour la stabilité dans Docker
        self.co.set_argument('--no-sandbox') 
        self.co.set_argument('--disable-gpu')
        self.co.set_argument('--disable-dev-shm-usage') # Vital pour /dev/shm limité
        self.co.set_argument('--disable-setuid-sandbox')
        self.co.set_argument('--no-zygote')
        self.co.set_argument('--single-process') # Fallback ultime si crash mémoire
        self.co.set_argument('--mute-audio')
        self.co.set_argument('--ignore-certificate-errors')
        
        # Chemin standard sur Debian/Ubuntu slim (installé via apt-get)
        self.co.set_paths(browser_path='/usr/bin/chromium')

    def get_page(self, mode='s'):
        """
        Récupère l'instance WebPage.
        Mode 's' (Session/Requests) = Ultra-léger.
        Mode 'd' (Driver/Chromium) = Lourd.
        """
        with self.lock:
            self.last_activity = time.time()
            self._schedule_shutdown()

            if self.page is None:
                print(f"[Drission] 🌱 Initialisation WebPage (Mode: {mode})")
                try:
                    # Démarrage direct avec options durcies
                    self.page = WebPage(mode=mode, chromium_options=self.co)
                except Exception as e:
                    print(f"[Drission] ❌ CRASH INIT: {e}")
                    # On renvoie l'erreur pour que l'API puisse la catcher
                    raise RuntimeError(f"Impossible de lancer Chromium. Erreur: {e}")
            
            # Gestion du changement de mode (si instance existe déjà)
            if mode == 'd' and self.page.mode == 's':
                print("[Drission] 🔥 Passage en Mode Driver (Chromium Start)")
                try:
                    self.page.change_mode('d')
                except Exception as e:
                    print(f"[Drission] ❌ CRASH SWITCH MODE: {e}")
                    # Tentative de récupération : on tue et on relance
                    try:
                        self.page.quit()
                    except: pass
                    self.page = None
                    self.page = WebPage(mode='d', chromium_options=self.co)
            
            return self.page

    def _shutdown_chromium(self):
        """Arrête le processus Chromium si inactif."""
        with self.lock:
            if self.page and self.page.mode == 'd':
                if time.time() - self.last_activity < IDLE_TIMEOUT:
                    self._schedule_shutdown() # Report si activité récente
                    return
                
                print("[Drission] ❄️ Extinction Chromium (RAM Saver)...")
                try:
                    # On repasse en mode session pour fermer le browser proprement
                    self.page.change_mode('s') 
                except Exception as e:
                    print(f"Error closing browser: {e}")

    def _schedule_shutdown(self):
        if self.timer: self.timer.cancel()
        self.timer = threading.Timer(IDLE_TIMEOUT, self._shutdown_chromium)
        self.timer.start()

MANAGER = BrowserManager()

@app.route('/start_session', methods=['POST'])
def start_session():
    try:
        # On tente d'initialiser une page (mode léger par défaut) pour vérifier que le moteur répond
        MANAGER.get_page(mode='s')
        return jsonify({"session_id": "global_drission_session", "status": "ready", "engine": "DrissionPage"})
    except Exception as e:
        return jsonify({"error": f"Echec démarrage moteur: {str(e)}", "trace": traceback.format_exc()}), 500

@app.route('/stop_session', methods=['POST'])
def stop_session():
    return jsonify({"status": "acknowledged"})

@app.route('/action', methods=['POST'])
def browser_action():
    data = request.json
    action = data.get("action")
    params = data.get("params", {})
    
    result = {"status": "success"}

    try:
        # Détermine le mode nécessaire
        # goto/read peuvent se faire en mode 's' (léger) SAUF si JS requis explicitement
        required_mode = 'd' if action in ['click', 'type', 'screenshot', 'evaluate'] else 's'
        
        # Obtention de la page (peut lever une exception si Chromium est cassé)
        page = MANAGER.get_page(mode=required_mode)
        
        if action == "goto":
            url = params.get("url")
            page.get(url)
            # Petit hack: si le mode session ne renvoie rien (JS required), on switch en d
            if required_mode == 's' and (not page.html or len(page.html) < 500):
                 print("[Drission] ⚠️ Page vide/JS détecté, upgrade en Mode Driver...")
                 page = MANAGER.get_page(mode='d')
                 page.get(url)
                 
            result["title"] = page.title
            result["url"] = page.url

        elif action == "click":
            page.ele(params.get("selector")).click()
            
        elif action == "type":
            page.ele(params.get("selector")).input(params.get("text"))
            
        elif action == "read":
            content = page.ele('body').text
            content = " ".join(content.split())
            result["content"] = content[:15000]
            result["url"] = page.url
            result["mode_used"] = page.mode

        elif action == "screenshot":
            filename = f"screenshot_{int(time.time())}.png"
            path = f"/app/data/{filename}"
            page.get_screenshot(path=path, full_page=True)
            result["path"] = path

        elif action == "evaluate":
            res = page.run_js(params.get("script"))
            result["result"] = res

        else:
            return jsonify({"error": "Action inconnue"}), 400

    except Exception as e:
        print(f"ERROR: {e}")
        return jsonify({"error": f"Drission Error: {str(e)}"}), 500

    return jsonify(result)

if __name__ == '__main__':
    # Mode debug=False pour stabilité production
    app.run(host='0.0.0.0', port=5002, debug=False)