from flask import Flask, request, jsonify # pyright: ignore[reportMissingImports]
from DrissionPage import WebPage, ChromiumOptions # pyright: ignore[reportMissingImports]
import threading
import time
import uuid
import os
import shutil
import traceback
import logging

"""
================================================================================
MODULE : ECHO BROWSER AGENT API
VERSION : 2.1 (Enhanced Vision & Smart Click - FR LOGS)
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-01-20

CHANGELOG 2.1 :
- Ajout de l'action 'highlight' pour le Set-of-Mark Prompting (Vision Augmentée).
- Amélioration de l'action 'click' avec stratégie de repli (Smart Click).
- Support multi-sessions concurrentes (Isolation complète).
- Profils Chromium uniques par session (Isolation Cookies/Cache).
- Nettoyage automatique des ressources orphelines.
- Logging de l'identité utilisateur (avec messages FR restaurés).
================================================================================
"""

# Configuration Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# --- CONFIGURATION ---
IDLE_TIMEOUT = 900   # 15 minutes d'inactivité max par session (RAM Saver)
MAX_SESSIONS = 5     # Limite hard pour ne pas faire exploser le conteneur

# Script JS pour la "Vision Augmentée" (Set-of-Mark)
HIGHLIGHT_JS = """
(function() {
    // 1. Nettoyage des anciens marqueurs
    document.querySelectorAll('.echo-marker').forEach(e => e.remove());
    
    // 2. Sélection des éléments interactifs
    let items = document.querySelectorAll('a, button, input, textarea, select, [role="button"], [onclick]');
    let count = 0;
    
    items.forEach(el => {
        let rect = el.getBoundingClientRect();
        // Vérification de visibilité basique
        if (rect.width > 0 && rect.height > 0 && window.getComputedStyle(el).visibility !== 'hidden') {
            
            // Création du marqueur visuel
            let marker = document.createElement('div');
            marker.className = 'echo-marker';
            marker.innerText = count;
            
            // Style "High Contrast" pour la vision artificielle
            marker.style.position = 'absolute';
            marker.style.left = (rect.left + window.scrollX) + 'px';
            marker.style.top = (rect.top + window.scrollY) + 'px';
            marker.style.zIndex = '2147483647'; // Max Z-Index
            marker.style.backgroundColor = '#ff0000'; // Rouge vif
            marker.style.color = '#ffffff';
            marker.style.fontWeight = 'bold';
            marker.style.fontSize = '14px';
            marker.style.padding = '2px 6px';
            marker.style.borderRadius = '4px';
            marker.style.border = '2px solid white';
            marker.style.boxShadow = '0 2px 4px rgba(0,0,0,0.5)';
            marker.style.pointerEvents = 'none'; // Click through
            
            document.body.appendChild(marker);
            
            // Marquage de l'élément DOM pour le clic par index
            el.setAttribute('data-echo-index', count);
            count++;
        }
    });
    return count;
})();
"""

# Stockage des sessions actives : {session_id: BrowserSession}
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()

class BrowserSession:
    def __init__(self, user_id):
        self.session_id = str(uuid.uuid4())
        self.user_id = user_id
        self.page = None
        self.lock = threading.Lock()
        self.last_activity = time.time()
        self.profile_dir = f"/app/data/profiles/{self.session_id}"
        
        # Options Chromium Isolées
        self.co = ChromiumOptions()
        self.co.headless(True)
        self.co.set_argument('--no-sandbox')
        self.co.set_argument('--disable-gpu')
        self.co.set_argument('--disable-dev-shm-usage')
        self.co.set_argument('--disable-setuid-sandbox')
        self.co.set_argument('--single-process')
        self.co.set_argument('--mute-audio')
        self.co.set_argument('--ignore-certificate-errors')
        self.co.set_paths(browser_path='/usr/bin/chromium')
        
        # ISOLATION CRITIQUE : Dossier de profil unique
        self.co.set_user_data_path(self.profile_dir)

    def get_page(self, mode='s'):
        """
        Récupère ou initialise l'instance WebPage pour cette session.
        Gère le switch de mode (s <-> d) de manière thread-safe pour cette session.
        """
        with self.lock:
            self.last_activity = time.time()
            
            if self.page is None:
                logger.info(f"[{self.session_id}] 🌱 Initialisation WebPage (Mode: {mode}) pour {self.user_id}")
                try:
                    self.page = WebPage(mode=mode, chromium_options=self.co)
                except Exception as e:
                    logger.error(f"[{self.session_id}] ❌ CRASH INIT: {e}")
                    raise RuntimeError(f"Browser Init Failed: {e}")
            
            # Gestion du changement de mode
            if mode == 'd' and self.page.mode == 's':
                logger.info(f"[{self.session_id}] 🔥 Passage en Mode Driver (Chromium Start)")
                try:
                    self.page.change_mode('d')
                except Exception as e:
                    logger.error(f"[{self.session_id}] ❌ CRASH SWITCH MODE: {e}")
                    # Recovery: Kill & Restart
                    try: self.page.quit()
                    except: pass
                    self.page = WebPage(mode='d', chromium_options=self.co)
            
            return self.page

    def close(self):
        """Arrêt propre du navigateur et suppression des fichiers temporaires."""
        with self.lock:
            if self.page:
                try:
                    self.page.quit()
                except: pass
                self.page = None
            
            # Nettoyage du profil disque
            if os.path.exists(self.profile_dir):
                try:
                    shutil.rmtree(self.profile_dir)
                    logger.info(f"[{self.session_id}] 🧹 Profil nettoyé (Suppression cache)")
                except Exception as e:
                    logger.error(f"[{self.session_id}] ⚠️ Erreur nettoyage profil: {e}")

# --- GESTIONNAIRE DE TÂCHES DE FOND ---

def cleanup_loop():
    """Thread de maintenance qui tue les sessions inactives."""
    while True:
        time.sleep(60)
        now = time.time()
        to_remove = []
        
        with SESSIONS_LOCK:
            for sid, session in SESSIONS.items():
                if (now - session.last_activity) > IDLE_TIMEOUT:
                    to_remove.append(sid)
        
        for sid in to_remove:
            logger.info(f"⏰ Timeout Session {sid} (Inactivité > {IDLE_TIMEOUT}s)")
            stop_session_internal(sid)

def stop_session_internal(session_id):
    """Logique d'arrêt interne thread-safe."""
    session = None
    with SESSIONS_LOCK:
        if session_id in SESSIONS:
            session = SESSIONS.pop(session_id)
    
    if session:
        session.close()
        return True
    return False

# Démarrage du thread de nettoyage
t = threading.Thread(target=cleanup_loop, daemon=True)
t.start()

# --- ROUTES API ---

@app.route('/start_session', methods=['POST'])
def start_session():
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    
    with SESSIONS_LOCK:
        # 1. Vérification des quotas
        if len(SESSIONS) >= MAX_SESSIONS:
            # Tentative de nettoyage d'urgence (sessions orphelines ?)
            return jsonify({"error": "Serveur occupé (Max sessions atteintes). Réessayez plus tard."}), 503
        
        # 2. Création Session
        session = BrowserSession(user_id)
        SESSIONS[session.session_id] = session
    
    # 3. Préchauffage (Optionnel, mode 's' léger)
    try:
        session.get_page(mode='s')
        logger.info(f"✅ Session Démarrée: {session.session_id} | User: {user_id}")
        return jsonify({
            "session_id": session.session_id, 
            "status": "ready", 
            "engine": "DrissionPage",
            "isolation": "active"
        })
    except Exception as e:
        stop_session_internal(session.session_id)
        return jsonify({"error": str(e)}), 500

@app.route('/stop_session', methods=['POST'])
def stop_session():
    data = request.json
    sid = data.get("session_id")
    if stop_session_internal(sid):
        return jsonify({"status": "closed"})
    return jsonify({"status": "not_found"}), 404

@app.route('/action', methods=['POST'])
def browser_action():
    data = request.json
    sid = data.get("session_id")
    action = data.get("action")
    params = data.get("params", {})
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    
    session = None
    with SESSIONS_LOCK:
        session = SESSIONS.get(sid)
    
    if not session:
        return jsonify({"error": "Session invalide ou expirée"}), 404
    
    # Vérification basique de propriété (Optionnelle, mais bonne pratique)
    if session.user_id != 'anonymous' and user_id != 'anonymous' and session.user_id != user_id:
        logger.warning(f"⛔ Access Denied: User {user_id} tried to use session of {session.user_id}")
        return jsonify({"error": "Access Denied"}), 403

    result = {"status": "success"}

    try:
        # Détermine le mode nécessaire
        # 'highlight' nécessite 'd' pour exécuter le JS sur le DOM
        required_mode = 'd' if action in ['click', 'type', 'screenshot', 'evaluate', 'highlight'] else 's'
        
        # Récupération de la page (Thread-Safe via la session)
        page = session.get_page(mode=required_mode)
        
        if action == "goto":
            url = params.get("url")
            page.get(url)
            # Petit hack: si le mode session ne renvoie rien (JS required), on switch en d
            if required_mode == 's' and (not page.html or len(page.html) < 500):
                 logger.info(f"[{sid}] ⚠️ Page vide/JS détecté, upgrade en Mode Driver...")
                 page = session.get_page(mode='d')
                 page.get(url)
            
            result["title"] = page.title
            result["url"] = page.url

        elif action == "click":
            selector = params.get("selector")
            ele = None
            try:
                # 1. Essai direct (CSS / XPath / Drission syntax)
                ele = page.ele(selector)
            except: pass

            # 2. Smart Fallback : Si pas trouvé, on essaie des stratégies floues
            if not ele:
                # a. Est-ce un numéro généré par 'highlight' ? ex: "12" ou "#12"
                clean_sel = selector.replace("#", "")
                if clean_sel.isdigit():
                    logger.info(f"[{sid}] 💡 Smart Click: Recherche par index Vision {clean_sel}")
                    try: ele = page.ele(f'@data-echo-index={clean_sel}')
                    except: pass
                
                # b. Est-ce du texte simple ? ex: "Connexion"
                if not ele and " " not in selector and not selector.startswith((".", "//", "[")):
                    logger.info(f"[{sid}] 💡 Smart Click: Recherche par texte '{selector}'")
                    try: ele = page.ele(f'text:{selector}')
                    except: pass

            if ele:
                ele.click()
                result["message"] = "Clic effectué"
            else:
                return jsonify({"error": f"Element introuvable: {selector}"}), 404
            
        elif action == "type":
            page.ele(params.get("selector")).input(params.get("text"))
            
        elif action == "read":
            content = page.ele('body').text
            content = " ".join(content.split())
            result["content"] = content[:15000]
            result["url"] = page.url
            result["mode_used"] = page.mode

        elif action == "highlight":
            # NOUVEAU : Injection de marqueurs visuels (Vision Augmentée)
            count = page.run_js(HIGHLIGHT_JS)
            result["message"] = f"Vision Augmentée activée : {count} éléments marqués."
            result["count"] = count

        elif action == "screenshot":
            # Support optionnel de l'overlay highlight avant capture
            if params.get("overlay", False):
                page.run_js(HIGHLIGHT_JS)
                
            filename = f"screenshot_{sid}_{int(time.time())}.png"
            path = f"/app/data/{filename}"
            page.get_screenshot(path=path, full_page=True)
            result["path"] = path

        elif action == "evaluate":
            res = page.run_js(params.get("script"))
            result["result"] = res

        else:
            return jsonify({"error": "Action inconnue"}), 400

    except Exception as e:
        logger.error(f"[{sid}] Erreur Action: {e}")
        return jsonify({"error": f"Browser Error: {str(e)}"}), 500

    return jsonify(result)

if __name__ == '__main__':
    # threaded=True permet de gérer les requêtes concurrentes sur le Flask
    # (Chaque requête manipule sa propre session via SESSIONS dict)
    app.run(host='0.0.0.0', port=5002, debug=False, threaded=True)