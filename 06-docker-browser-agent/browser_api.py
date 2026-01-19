from flask import Flask, request, jsonify # pyright: ignore[reportMissingImports]
from DrissionPage import WebPage, ChromiumOptions # pyright: ignore[reportMissingImports]
import threading
import time
import uuid
import os
import shutil
import logging
import traceback
import html2text # Hard requirement (Docker rebuild needed)

"""
================================================================================
MODULE : ECHO BROWSER AGENT API
VERSION : 3.1 (Robust Vision & Interaction)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-01-20

CHANGELOG 3.1 :
- Hard requirement: html2text.
- Restauration de l'action 'evaluate' (Regression Fix).
- Smart Click (Index -> CSS -> Text).
- Vision Augmentée v2.
================================================================================
"""

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
app = Flask(__name__)

IDLE_TIMEOUT = 900 # 15 min
MAX_SESSIONS = 10

# --- JS VISION AUGMENTÉE V2 (Plus agressif) ---
HIGHLIGHT_JS = """
(function() {
    document.querySelectorAll('.echo-marker').forEach(e => e.remove());
    
    // Selecteurs standards + éléments avec curseur pointer (souvent cliquables)
    let candidates = Array.from(document.querySelectorAll('a, button, input, textarea, select, [role="button"], [onclick]'));
    
    // Ajout des éléments qui ressemblent à des boutons via CSS
    let allInfo = Array.from(document.querySelectorAll('*')).filter(el => {
        return window.getComputedStyle(el).cursor === 'pointer';
    });
    let items = [...new Set([...candidates, ...allInfo])];
    
    let count = 0;
    
    items.forEach(el => {
        let rect = el.getBoundingClientRect();
        // Filtre : doit être visible et avoir une taille minimale (5x5px)
        if (rect.width > 5 && rect.height > 5 && window.getComputedStyle(el).visibility !== 'hidden' && window.getComputedStyle(el).display !== 'none') {
            
            let marker = document.createElement('div');
            marker.className = 'echo-marker';
            marker.innerText = count;
            
            // Style High Contrast
            marker.style.position = 'absolute';
            marker.style.left = (rect.left + window.scrollX) + 'px';
            marker.style.top = (rect.top + window.scrollY) + 'px';
            marker.style.zIndex = '2147483647';
            marker.style.backgroundColor = '#ff0000';
            marker.style.color = '#ffffff';
            marker.style.fontWeight = 'bold';
            marker.style.fontSize = '12px';
            marker.style.padding = '1px 4px';
            marker.style.borderRadius = '2px';
            marker.style.pointerEvents = 'none'; // Click through
            marker.style.boxShadow = '0 2px 4px rgba(0,0,0,0.5)';
            
            document.body.appendChild(marker);
            
            el.setAttribute('data-echo-index', count);
            count++;
        }
    });
    return count;
})();
"""

# Config HTML2Text pour une lecture propre
h2t = html2text.HTML2Text()
h2t.ignore_links = False
h2t.ignore_images = True
h2t.body_width = 0 # No wrap

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
        
        self.co = ChromiumOptions()
        self.co.headless(True)
        self.co.set_argument('--no-sandbox')
        self.co.set_argument('--disable-gpu')
        # Taille standard pour éviter les mises en page mobile
        self.co.set_argument('--window-size=1920,1080') 
        self.co.set_argument('--disable-dev-shm-usage')
        self.co.set_paths(browser_path='/usr/bin/chromium')
        self.co.set_user_data_path(self.profile_dir)

    def get_page(self, mode='s'):
        with self.lock:
            self.last_activity = time.time()
            if self.page is None:
                logger.info(f"[{self.session_id}] 🌱 Init Page (Mode {mode})")
                self.page = WebPage(mode=mode, chromium_options=self.co)
            
            if mode == 'd' and self.page.mode == 's':
                logger.info(f"[{self.session_id}] 🔥 Upgrade to Driver")
                self.page.change_mode('d')
            return self.page

    def close(self):
        with self.lock:
            if self.page:
                try: self.page.quit()
                except: pass
            if os.path.exists(self.profile_dir):
                try: shutil.rmtree(self.profile_dir)
                except: pass

def cleanup_loop():
    while True:
        time.sleep(60)
        now = time.time()
        with SESSIONS_LOCK:
            to_remove = [sid for sid, s in SESSIONS.items() if (now - s.last_activity) > IDLE_TIMEOUT]
            for sid in to_remove:
                logger.info(f"⏰ Timeout Session {sid}")
                stop_session_internal(sid)

def stop_session_internal(session_id):
    if session_id in SESSIONS:
        SESSIONS.pop(session_id).close()
        return True
    return False

threading.Thread(target=cleanup_loop, daemon=True).start()

@app.route('/start_session', methods=['POST'])
def start_session():
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    with SESSIONS_LOCK:
        if len(SESSIONS) >= MAX_SESSIONS: return jsonify({"error": "Busy (Max sessions)"}), 503
        session = BrowserSession(user_id)
        SESSIONS[session.session_id] = session
    return jsonify({"session_id": session.session_id})

@app.route('/stop_session', methods=['POST'])
def stop_session():
    return jsonify({"status": "closed"}) if stop_session_internal(request.json.get("session_id")) else ("", 404)

@app.route('/action', methods=['POST'])
def browser_action():
    data = request.json
    sid = data.get("session_id")
    action = data.get("action")
    params = data.get("params", {})
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    
    session = SESSIONS.get(sid)
    if not session: return jsonify({"error": "Session invalid"}), 404
    
    # SÉCURITÉ : Vérification du propriétaire
    if session.user_id != 'anonymous' and user_id != 'anonymous' and session.user_id != user_id:
        logger.warning(f"⛔ Access Denied: {user_id} tried to use session of {session.user_id}")
        return jsonify({"error": "Access Denied"}), 403
    
    try:
        # Actions nécessitant le mode Driver (Chrome réel)
        needs_driver = action in ['click', 'type', 'screenshot', 'highlight', 'key', 'scroll', 'evaluate']
        page = session.get_page(mode='d' if needs_driver else 's')
        
        result = {"status": "success"}

        if action == "goto":
            page.get(params.get("url"))
            result["title"] = page.title
            
        elif action == "click":
            selector = params.get("selector")
            ele = None
            
            # Stratégie 1: Index Vision
            if selector.replace("#", "").isdigit():
                try: ele = page.ele(f'@data-echo-index={selector.replace("#", "")}')
                except: pass
            
            # Stratégie 2: Sélecteur CSS/XPath Direct
            if not ele:
                try: ele = page.ele(selector)
                except: pass
            
            # Stratégie 3: Texte approximatif
            if not ele:
                try: ele = page.ele(f'text:{selector}')
                except: pass
                
            if ele:
                # AMÉLIORATION : Scroll avant clic pour éviter les erreurs "not clickable"
                try: ele.scroll.to_see(center=True)
                except: pass
                time.sleep(0.2)
                ele.click()
                result["message"] = "Clic effectué"
            else:
                return jsonify({"error": f"Element '{selector}' introuvable"}), 404

        elif action == "type":
            ele = page.ele(params.get("selector"))
            if ele:
                ele.scroll.to_see()
                ele.input(params.get("text"))
            else:
                # Fallback: essaye de taper dans l'élément actif
                page.actions.type(params.get("text"))
        
        elif action == "key":
            key = params.get("key", "ENTER")
            page.actions.key(key)
            result["message"] = f"Touche {key} pressée"

        elif action == "read":
            # AMÉLIORATION : Conversion Markdown
            html = page.html
            try:
                content = h2t.handle(html)
            except:
                content = page.ele('body').text
                
            result["content"] = content[:20000] # Augmenté à 20k
            result["url"] = page.url

        elif action == "highlight":
            count = page.run_js(HIGHLIGHT_JS)
            result["count"] = count

        elif action == "screenshot":
            path = f"/app/data/screenshot_{sid}.png"
            page.get_screenshot(path=path, full_page=True)
            result["path"] = path

        elif action == "evaluate":
            res = page.run_js(params.get("script"))
            result["result"] = res

        else:
            return jsonify({"error": "Action inconnue"}), 400

    except Exception as e:
        logger.error(f"Error: {e}")
        return jsonify({"error": str(e)}), 500

    return jsonify(result)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5002, threaded=True)