"""
================================================================================
MODULE : ECHO BROWSER WORKER API (FASTAPI ASYNC EDITION)
VERSION : 9.18 (Rate-Limit Healthcheck)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-08-19

CHANGELOG 9.17 :
- FIX: Ajout d'un filtre de logs limitant l'affichage des requêtes /health (1/5min).
CHANGELOG 9.16 :
- MIGRATION: Renommage de l'entité Agent en Worker pour standardisation globale de l'architecture.
CHANGELOG 9.15 :
- FEAT: Levée de la bride `read_text` à 2 millions de caractères (~600k tokens) pour libérer la pleine puissance contextuelle de Gemini 3.x lors des RAG et sondages.
CHANGELOG 9.14 :
- FEAT: Remplacement du stockage RAM WebP par un streaming Live Long-Polling (`/screencast/latest`).
- FEAT: Sérialisation des requêtes concurrentes via `asyncio.Lock` sur `BrowserSession`.
- FEAT: Implémentation du Watchdog d'Auto-Stop pour la libération dynamique du CDP.
CHANGELOG 9.13 :
- OPTIM: Délégation des tâches CPU-bound (Compression WebP Pillow et parsing html2text) vers des threads natifs OS (`asyncio.to_thread`) pour libérer totalement l'Event Loop asynchrone et supporter le multi-chat intensif.
CHANGELOG 9.12 :
- OPTIM: Substitution globale de `networkidle` par `load` pour diviser les temps de navigation par 2-3.
- OPTIM: Déploiement de FastAPI `ORJSONResponse` pour accélérer la sérialisation native des JSON.
- OPTIM: Désactivation des sous-systèmes Chromium inutiles (sync, extensions, background-timer).
CHANGELOG 9.11 :
- OPTIM: Accélération drastique de la souris (Bézier Cubique) via la réduction des étapes (steps) et du sleep.
- FEAT: Ajout d'un délai humain (visée oculaire) de 150-400ms entre la fin du mouvement et l'action (clic/type) pour un réalisme accru.
CHANGELOG 9.10 :
- OPTIM: Bridage du moteur Playwright Chromium à 9 FPS (--limit-fps) pour économiser drastiquement le CPU.
- OPTIM: Ajustement dynamique de la compression WebP pour matcher le FPS de rendu.
CHANGELOG 9.9 :
- FEAT: Implémentation du support screencast natif Playwright pour l'orchestrateur.
- FEAT: Encodage WebP avec compression de frames en direct + Frame HD finale (Pillow).
CHANGELOG 9.8 :
- FIX: Prise en charge du paramètre `name` dans `interact_a11y` pour filtrer les rôles et éviter le clic sur le premier élément du DOM par défaut.
- FIX: Fallback intelligent (text) si l'élément n'est pas trouvé via Role+Name.
- FIX: Nettoyage de l'arbre CDP `a11y_tree` (exclusion des noeuds génériques ou StaticText sans valeur).
CHANGELOG 9.7 :
- FEAT: Support de l'arbre natif d'accessibilité (a11y_tree) converti en texte.
- FEAT: La grille de vision accepte un pas configurable (vision_grid_step).
CHANGELOG 9.6 :
- REFACTOR: API unifiée autour de 4 blocs (`interact_a11y`, `interact_dom`, `inspect_page`, `browser_control`).
CHANGELOG 9.5 :
- FEAT: Stealth Mode - Suppression des Bounding Boxes visuelles pour réduire la pollution de l'image.
- FEAT: Ralentissement de la courbe Bézier et des saisies clavier pour limiter la détection bot.
CHANGELOG 9.4 :
- FEAT: Synergie Multimodale - Remplacement des pastilles par des Bounding Boxes colorées.
- FEAT: DOM Spatial - Injection des coordonnées [x,y,w,h] et extraction du contexte parent sémantique. Troncature augmentée (200).
CHANGELOG 9.3 :
- FEAT: Algorithme de souris Bézier cubique avec Loi de Fitts et Overshoot.
- FEAT: Extraction DOM allégée (HIGHLIGHT_JS) pour la stratégie Vision-First.
- FEAT: Saisie clavier humaine avec `press_sequentially`, délai variable et pauses cognitives.
CHANGELOG 9.2 :
- Ajout de GET /health pour l'orchestration séquentielle Docker Compose.
CHANGELOG 9.1 :
- FIX: Correction d'une erreur de syntaxe à l'import de random (random import -> import random).
CHANGELOG 9.0 :
- PERF: Allègement du payload JSON (suppression des coordonnées x/y dans le DOM Map).
CHANGELOG 8.9 :
- PERF: Migration to orjson and pybase64 with explicit decoding.
CHANGELOG 8.8 :
- PERF: Migration to orjson and pybase64 for high-performance processing.
CHANGELOG 8.7 :
- FEAT: Added 'get_attribute' action to safely retrieve absolute URLs (src, href) from DOM elements.
CHANGELOG 8.6 :
- FEAT: Base64 encoding for 'read_html' action to prevent JSON corruption.
CHANGELOG 8.5 :
- FEAT: Added 'reset' action to fully purge and restart a browser session.
- PERF: Memory-based screenshots (no disk I/O) for faster HUD updates.
CHANGELOG 8.4 :
- FEAT: Added native support for 'index' parameter in click/type/hover.
- FIX: Improved target selection logic (Index priority over Selector).
================================================================================
"""

import asyncio
import pybase64 as base64
import os
import time
import random
import logging
import orjson as json
import html2text
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import ORJSONResponse
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("echo-browser")

import time

class RateLimitHealthCheckFilter(logging.Filter):
    def __init__(self, rate_limit_seconds=300):
        super().__init__()
        self.rate_limit_seconds = rate_limit_seconds
        self.last_logged = 0

    def filter(self, record):
        if hasattr(record, 'args') and isinstance(record.args, tuple) and len(record.args) >= 3:
            if record.args[2] in ('/health', '/health/'):
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        try:
            msg = record.getMessage()
            if "GET /health" in msg:
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        except Exception:
            pass
        return True

logging.getLogger("uvicorn.access").addFilter(RateLimitHealthCheckFilter())

app = FastAPI()

# --- ETAT GLOBAL ---
IDLE_TIMEOUT_DEFAULT = 3600 # 1 heure de survie par defaut
MAX_SESSIONS = 20
RENDERING_FPS = 9 # Vitesse du moteur headless pour optimiser le CPU
SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()

class GlobalState:
    playwright = None
    browser = None

state = GlobalState()

HIGHLIGHT_JS = r"""
(start_index) => {
    try {
        if (!document.getElementById('echo-cursor')) {
            const cursor = document.createElement('div');
            cursor.id = 'echo-cursor';
            cursor.style.cssText = 'position: fixed; top: 0; left: 0; transform: translate(-50%, -50%); width: 16px; height: 16px; background-color: #ff0044; border: 2px solid white; border-radius: 50%; z-index: 2147483647; pointer-events: none; transition: transform 0.05s linear;';
            document.body.appendChild(cursor);
            document.addEventListener('mousemove', e => {
                cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
            });
        }
        document.querySelectorAll('.echo-marker').forEach(e => e.remove());
        const interactiveSelectors = 'a, button, input, textarea, select, [role="button"], [role="link"], [onclick], label, [role="radio"], [role="checkbox"], [role="switch"], [role="tab"], [role="menuitem"], [tabindex], summary';
        let items = Array.from(document.querySelectorAll(interactiveSelectors));
        
        document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, span, div, i, svg, img').forEach(el => {
            if (!items.includes(el)) {
                const style = window.getComputedStyle(el);
                const isPointer = (style.cursor === 'pointer');
                const isMedia = (el.tagName === 'IMG' || el.tagName === 'SVG');
                const hasAlt = (el.getAttribute('alt') || el.getAttribute('aria-label') || '').trim().length > 0;
                
                if (isPointer || (isMedia && hasAlt)) {
                    items.push(el);
                } else if (['P','H1','H2','H3','H4','H5','H6','LI'].includes(el.tagName)) {
                    if (el.innerText && el.innerText.trim().length > 0) {
                        items.push(el);
                    }
                }
            }
        });
        let elements = [];
        let count = start_index || 0;
        items.forEach(el => {
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') return;
            // Conserver les inputs interactifs (checkbox, radio, select) même si opacity=0 (souvent masqués par CSS custom)
            if (style.opacity === '0' && !(el.tagName === 'INPUT' || el.tagName === 'SELECT')) return;
            let rect = el.getBoundingClientRect();
            if (rect.width > 5 && rect.height > 5) {
                let aria = el.getAttribute('aria-label') || "";
                let text = (el.innerText || aria || el.alt || "").trim().replace(/\s+/g, ' ').substring(0, 200);
                let meta = { id: count, tag: el.tagName.toLowerCase() };
                
                // Extraction du conteneur parent sémantique
                let p = el.parentElement;
                while (p) {
                    let tg = p.tagName.toLowerCase();
                    if (['nav', 'form', 'header', 'footer', 'main', 'article', 'aside', 'dialog'].includes(tg)) {
                        let parentStr = tg;
                        if (p.id) parentStr += '#' + p.id;
                        else if (p.className && typeof p.className === 'string') {
                            let cls = p.className.split(' ')[0];
                            if (cls) parentStr += '.' + cls;
                        }
                        meta.parent = parentStr;
                        break;
                    }
                    p = p.parentElement;
                }
                
                meta.coords = [Math.round(rect.left), Math.round(rect.top), Math.round(rect.width), Math.round(rect.height)];
                
                let isPointer = (style.cursor === 'pointer');
                let isMedia = (el.tagName.toUpperCase() === 'IMG' || el.tagName.toUpperCase() === 'SVG');
                
                if (text) meta.text = text;
                else if (!isPointer && !isMedia && !el.matches(interactiveSelectors)) return; // Ignore empty non-interactive elements
                
                ['type', 'placeholder', 'value', 'aria-label', 'aria-expanded', 'disabled', 'checked', 'role', 'href'].forEach(attr => {
                    let val = el.getAttribute(attr) || el[attr];
                    if (val && val !== '') {
                        if (attr === 'href') val = String(val).substring(0, 40);
                        meta[attr] = val;
                    }
                });
                elements.push(meta);
                
                // Ne plus créer de marqueur visuel pour rester furtif
                // L'index est toujours injecté dans le DOM pour le clic précis
                el.setAttribute('data-echo-index', count);
                count++;
            }
        });
        return { "count": count, "elements": elements }; 
    } catch (e) { return { "count": start_index || 0, "elements": [], "error": e.toString() }; }
}
"""

h2t = html2text.HTML2Text()
h2t.ignore_links = False
h2t.ignore_images = True
h2t.body_width = 0 

class BrowserSession:
    def __init__(self, sid, user_id, context, idle_timeout, mode="desktop"):
        self.sid = sid
        self.user_id = user_id
        self.context = context
        self.idle_timeout = idle_timeout
        self.mode = mode
        self.pages = [] # Liste des onglets ouverts
        self.active_page_index = 0
        self.last_activity = time.time()
        self.cdp_client = None
        self.action_lock = asyncio.Lock()
        self.last_screencast_poll = 0
        self.latest_frame = None
        self.frame_id = 0

    async def get_active_page(self):
        # Refresh activity on every access
        self.last_activity = time.time()
        if not self.pages:
            p = await self.context.new_page()
            # Inherit viewport from context (Fix v8.3)
            self.pages.append(p)
        
        if self.active_page_index >= len(self.pages):
            self.active_page_index = len(self.pages) - 1
            
        target = self.pages[self.active_page_index]
        if target.is_closed():
            self.pages.pop(self.active_page_index)
            return await self.get_active_page()
            
        return target

    async def bezier_mouse_move(self, page, target_x, target_y):
        """Déplacement de souris fluide (Bézier Cubique + Fitts Law + Overshoot)."""
        import math
        try:
            start_x = getattr(self, 'mouse_x', random.randint(100, 800))
            start_y = getattr(self, 'mouse_y', random.randint(100, 600))
            
            distance = math.hypot(target_x - start_x, target_y - start_y)
            if distance < 5:
                await page.mouse.move(target_x, target_y)
                self.mouse_x, self.mouse_y = target_x, target_y
                return

            # Fitts Law: Temps dynamique "Power User" (Accéléré)
            steps = max(5, min(15, int(distance / 60)))
            
            # Overshoot (Micro-correction) pour longues distances (réduit)
            overshoot_x = target_x + random.uniform(-5, 5) if distance > 300 else target_x
            overshoot_y = target_y + random.uniform(-5, 5) if distance > 300 else target_y
            
            # Points de contrôle balistiques
            cp1_x = start_x + (overshoot_x - start_x) * 0.3 + random.uniform(-20, 20)
            cp1_y = start_y + (overshoot_y - start_y) * 0.3 + random.uniform(-20, 20)
            cp2_x = start_x + (overshoot_x - start_x) * 0.7 + random.uniform(-20, 20)
            cp2_y = start_y + (overshoot_y - start_y) * 0.7 + random.uniform(-20, 20)

            def ease_out_quad(t):
                return t * (2 - t)

            for i in range(1, steps + 1):
                t = i / steps
                et = ease_out_quad(t)
                x = (1-et)**3 * start_x + 3*(1-et)**2 * et * cp1_x + 3*(1-et)*et**2 * cp2_x + et**3 * overshoot_x
                y = (1-et)**3 * start_y + 3*(1-et)**2 * et * cp1_y + 3*(1-et)*et**2 * cp2_y + et**3 * overshoot_y
                
                # Micro-tremblements très faibles
                x += random.uniform(-1, 1)
                y += random.uniform(-1, 1)
                
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.001, 0.005))
                
            # Micro-correction finale rapide
            if distance > 300:
                await asyncio.sleep(random.uniform(0.05, 0.15))
                await page.mouse.move(target_x, target_y)
                
            self.mouse_x = target_x
            self.mouse_y = target_y
        except Exception as e:
            logger.warning(f"[{self.sid}] Bezier Fallback: {e}")
            await page.mouse.move(target_x, target_y)
            self.mouse_x = target_x
            self.mouse_y = target_y

    async def move_mouse_to_locator(self, page, locator):
        """Scroll l'élément, récupère sa bounding box exacte et déclenche le mouvement Bézier."""
        try:
            await locator.scroll_into_view_if_needed(timeout=5000)
            box = await locator.bounding_box()
            if box:
                # Calculer un point d'impact aléatoire dans la bounding box
                offset_x = random.uniform(-box["width"] * 0.3, box["width"] * 0.3)
                offset_y = random.uniform(-box["height"] * 0.3, box["height"] * 0.3)
                t_x = box["x"] + box["width"] / 2 + offset_x
                t_y = box["y"] + box["height"] / 2 + offset_y
                await self.bezier_mouse_move(page, t_x, t_y)
            else:
                await self.bezier_mouse_move(page, random.randint(100, 800), random.randint(100, 600))
        except Exception as e:
            logger.warning(f"[{self.sid}] Unable to compute bounding box: {e}")

    async def close(self):
        try:
            await self.context.close()
            logger.info(f"[{self.sid}] 🗑️ BrowserContext closed.")
        except Exception as e:
            logger.error(f"[{self.sid}] ⚠️ Error closing context: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Initializing Global Playwright Async Instance (v6 ENGINE)...")
    state.playwright = await async_playwright().start()
    state.browser = await state.playwright.chromium.launch(
        headless=True,
        args=[
            "--no-sandbox", 
            "--disable-gpu", 
            "--disable-dev-shm-usage",
            "--disable-background-timer-throttling",
            "--disable-extensions",
            "--disable-sync",
            f"--limit-fps={RENDERING_FPS}"
        ]
    )
    cleanup_task = asyncio.create_task(session_cleanup_loop())
    yield
    cleanup_task.cancel()
    await state.browser.close()
    await state.playwright.stop()

app = FastAPI(lifespan=lifespan, default_response_class=ORJSONResponse)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

async def session_cleanup_loop():
    while True:
        await asyncio.sleep(1)
        now = time.time()
        async with SESSIONS_LOCK:
            to_remove = [sid for sid, s in SESSIONS.items() if now - s.last_activity > s.idle_timeout]
            for sid in to_remove:
                logger.info(f"[{sid}] ⏰ Cleaning idle session")
                session = SESSIONS.pop(sid)
                await session.close()
                
            for sid, s in SESSIONS.items():
                if s.cdp_client and (now - s.last_screencast_poll > 2.5) and s.last_screencast_poll > 0:
                    logger.info(f"[{sid}] 🛑 Watchdog: Stopping idle screencast")
                    try:
                        asyncio.create_task(s.cdp_client.send('Page.stopScreencast'))
                    except: pass
                    s.cdp_client = None
                    s.last_screencast_poll = 0

@app.post("/start_session")
async def start_session(request: Request):
    data = await request.json()
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    sid = data.get("session_id") # C'est le chat_id permanent
    idle_timeout = data.get("idle_timeout", IDLE_TIMEOUT_DEFAULT)
    mode = data.get("mode", "mobile")

    if not sid:
        return {"status": "error", "message": "ERREUR_TECHNIQUE : Identifiant de chat manquant."}

    async with SESSIONS_LOCK:
        if sid in SESSIONS:
            if getattr(SESSIONS[sid], 'mode', None) == mode:
                SESSIONS[sid].last_activity = time.time()
                return {"session_id": sid, "status": "success", "message": "Session deja active."}
            else:
                logger.info(f"[{sid}] 🔄 Uservalve changed to {mode}. Resetting session.")
                old_session = SESSIONS.pop(sid)
                await old_session.close()
        
        if len(SESSIONS) >= MAX_SESSIONS:
            return {"status": "error", "message": "ERREUR_CAPACITE : Le worker est sature."}
        
        # Configuration Contextuelle (Tablette / Desktop)
        if mode == "mobile":
            ctx_args = {
                "user_agent": "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "viewport": {"width": 820, "height": 1180},
                "device_scale_factor": 2,
                "is_mobile": True,
                "has_touch": True
            }
        else:
            ctx_args = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 800},
                "device_scale_factor": 1
            }

        context = await state.browser.new_context(**ctx_args)
        
        # Injection Stealth (Anti-Bot)
        stealth_script = """
            // Masquer la propriété webdriver
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            
            // Masquer Playwright des plugins
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3],
            });
            Object.defineProperty(navigator, 'languages', {
                get: () => ['fr-FR', 'fr', 'en-US', 'en'],
            });
        """
        await context.add_init_script(stealth_script)
        
        session = BrowserSession(sid, user_id, context, idle_timeout, mode)
        SESSIONS[sid] = session
        logger.info(f"[{sid}] 🆕 v6 Session created (Mode: {mode})")
        return {"session_id": sid, "status": "success"}

async def find_element_and_frame(page, selector):
    for frame in page.frames:
        try:
            loc = frame.locator(selector)
            if await loc.count() > 0:
                return loc.first
        except:
            pass
    return None

@app.post("/screencast/start")
async def screencast_start(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "message": "Session introuvable."}
    
    page = await session.get_active_page()
    if not page:
        return {"status": "error", "message": "Aucune page active."}
        
    try:
        if session.cdp_client:
            try:
                await session.cdp_client.send('Page.stopScreencast')
            except: pass
            session.cdp_client = None
            
        session.cdp_client = await page.context.new_cdp_session(page)
        
        vp = page.viewport_size
        max_w = (vp["width"] // 2) if vp else 640
        max_h = (vp["height"] // 2) if vp else 400
        
        async def handle_frame(event):
            session.latest_frame = event['data']
            session.frame_id += 1
            try:
                await session.cdp_client.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
            except:
                pass
                
        session.cdp_client.on("Page.screencastFrame", handle_frame)
        await session.cdp_client.send('Page.startScreencast', {
            'format': 'jpeg',
            'quality': 50,
            'maxWidth': max_w,
            'maxHeight': max_h,
            'everyNthFrame': 1
        })
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[{sid}] Screencast start error: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/screencast/stop")
async def screencast_stop(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    hd_b64 = data.get("hd_b64")
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "message": "Session introuvable."}
        
    try:
        if session.cdp_client:
            try:
                await session.cdp_client.send('Page.stopScreencast')
            except: pass
    except Exception as e:
        logger.error(f"[{sid}] Screencast stop error: {e}")
    finally:
        if session.cdp_client:
            try:
                await session.cdp_client.detach()
            except:
                pass
            session.cdp_client = None
        session.last_screencast_poll = 0
        
    return {
        "status": "success", 
        "screenshot_b64": hd_b64, 
        "webp_b64": None
    }

@app.post("/screencast/latest")
async def screencast_latest(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    last_frame_id = data.get("last_frame_id", 0)
    
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "message": "Session introuvable."}
        
    session.last_screencast_poll = time.time()
    
    if not session.cdp_client:
        page = await session.get_active_page()
        if page:
            try:
                session.cdp_client = await page.context.new_cdp_session(page)
                vp = page.viewport_size
                max_w = (vp["width"] // 2) if vp else 640
                max_h = (vp["height"] // 2) if vp else 400
                
                async def handle_frame(event):
                    session.latest_frame = event['data']
                    session.frame_id += 1
                    try:
                        await session.cdp_client.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
                    except: pass
                        
                session.cdp_client.on("Page.screencastFrame", handle_frame)
                await session.cdp_client.send('Page.startScreencast', {
                    'format': 'jpeg',
                    'quality': 50,
                    'maxWidth': max_w,
                    'maxHeight': max_h,
                    'everyNthFrame': 1
                })
            except Exception as e:
                logger.error(f"[{sid}] Auto-Resume error: {e}")
                
    start_time = time.time()
    while session.frame_id == last_frame_id and time.time() - start_time < 1.0:
        await asyncio.sleep(0.05)
        
    return {
        "status": "success",
        "frame_id": session.frame_id,
        "frame_b64": session.latest_frame
    }

@app.post("/action")
async def browser_action(request: Request):
    data = await request.json()
    sid, action, params = data.get("session_id"), data.get("action"), data.get("params", {})
    
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "error_type": "SESSION_NOT_FOUND", "message": "RESTART_REQUIRED"}
    
    # Heartbeat immediat
    session.last_activity = time.time()

    try:
        async with session.action_lock:
            page = await session.get_active_page()
            result = {"status": "success"}

            if action == "interact_a11y":
                method, value, a_type = params.get("method"), params.get("value"), params.get("action_type")
                name = params.get("name")
                text_to_type = params.get("text_to_type", "")
            
                logger.info(f"[{sid}] 🖱️ Semantic Interact (A11y): {method}={value} name={name} ({a_type})")
            
                if method == "role":
                    if name: loc = page.get_by_role(value, name=name)
                    else: loc = page.get_by_role(value)
                elif method == "label": loc = page.get_by_label(value)
                elif method == "text": loc = page.get_by_text(value)
                else: return {"status": "error", "message": "Method invalide."}
                
                if await loc.count() == 0:
                    if method == "role" and name:
                        logger.warning(f"[{sid}] Role+Name failed, trying Text fallback for: {name}")
                        loc = page.get_by_text(name)
                        if await loc.count() == 0:
                            return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value}, name={name})."}
                    else:
                        return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value})."}
            
                loc = loc.first
                await session.move_mouse_to_locator(page, loc)
            
                # Délai humain avant interaction (visée oculaire)
                await asyncio.sleep(random.uniform(0.15, 0.4))
            
                if a_type == "click":
                    try:
                        await loc.click(timeout=10000)
                    except Exception as e:
                        logger.warning(f"[{sid}] Native semantic click failed, trying force: {e}")
                        await loc.click(force=True, timeout=5000)
                    try:
                        box = await loc.bounding_box()
                        if box:
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await session.bezier_mouse_move(page, max(0, box['x'] + box['width']/2 + random.uniform(30, 100) * random.choice([1, -1])), max(0, box['y'] + box['height']/2 + random.uniform(30, 100) * random.choice([1, -1])))
                    except Exception:
                        pass
                elif a_type == "type":
                    await loc.click(timeout=10000)
                    for char in text_to_type:
                        await loc.press_sequentially(char)
                        delay_ms = max(50, min(300, int(random.gauss(150, 60))))
                        await asyncio.sleep(delay_ms / 1000.0)
                        if random.random() < 0.10: await asyncio.sleep(random.uniform(0.5, 1.2))
                elif a_type == "hover":
                    await loc.hover(timeout=10000)
                elif a_type == "download":
                    async def handle_download():
                        try:
                            file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                            async with page.expect_download(timeout=120000) as download_info:
                                await loc.click()
                            download = await download_info.value
                        
                            dl_dir = os.path.join("/app/data/downloads", session.user_id, sid)
                            os.makedirs(dl_dir, exist_ok=True)
                        
                            filename = download.suggested_filename
                            final_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                        
                            await download.save_as(final_path)
                            logger.info(f"[{sid}] 📥 Download completed: {final_path}")
                        except Exception as e:
                            logger.error(f"[{sid}] ⚠️ Download error: {e}")

                        asyncio.create_task(handle_download())
                        return {"status": "downloading", "action": a_type, "message": "Téléchargement initié en tâche de fond."}
                    
                elif a_type == "save_target":
                    async def stealth_download():
                        try:
                            tag_name = await loc.evaluate("el => el.tagName.toLowerCase()")
                            url_attr = "href" if tag_name == "a" else "src"
                            target_url = await loc.get_attribute(url_attr)
                            if not target_url:
                                logger.error(f"[{sid}] Save_target failed: target has no href or src")
                                return
                            from urllib.parse import urljoin
                            target_url = urljoin(page.url, target_url)
                            file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                            dl_dir = os.path.join("/app/data/downloads", session.user_id, sid)
                            os.makedirs(dl_dir, exist_ok=True)
                            filename = target_url.split("/")[-1].split("?")[0]
                            if not filename: filename = "downloaded_file"
                            dest_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                            response = await page.context.request.get(target_url)
                            body = await response.body()
                            with open(dest_path, "wb") as f:
                                f.write(body)
                            logger.info(f"[{sid}] 📥 Stealth Download completed: {dest_path}")
                        except Exception as e:
                            logger.error(f"[{sid}] ⚠️ Stealth Download error: {e}")

                    asyncio.create_task(stealth_download())
                    return {"status": "downloading", "action": a_type, "message": "Téléchargement furtif initié en tâche de fond."}
                
                await page.wait_for_load_state("load", timeout=15000)
                result["url"] = page.url

            elif action == "interact_dom":
                a_type = params.get("action_type")
                idx = params.get("index")
                x, y = params.get("x"), params.get("y")
                text_to_type = params.get("text_to_type", "")
            
                if idx is None and (x is None or y is None): 
                    return {"status": "error", "message": "ERREUR_PARAMETRE : Cible manquante (index ou x/y requis)."}
                
                if x is not None and y is not None:
                    dsf = await page.evaluate("window.devicePixelRatio")
                    css_x, css_y = float(x) / dsf, float(y) / dsf
                    logger.info(f"[{sid}] 🖱️ Interact DOM ({a_type}) Coordinates: Img({x}, {y}) -> CSS({css_x}, {css_y})")
                    await session.bezier_mouse_move(page, css_x, css_y)
                
                    # Délai humain avant interaction (visée oculaire)
                    await asyncio.sleep(random.uniform(0.15, 0.4))
                
                    if a_type == "click":
                        await page.mouse.click(css_x, css_y)
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                        await session.bezier_mouse_move(page, max(0, css_x + random.uniform(30, 100) * random.choice([1, -1])), max(0, css_y + random.uniform(30, 100) * random.choice([1, -1])))
                    elif a_type == "type":
                        await page.mouse.click(css_x, css_y)
                        for char in text_to_type:
                            await page.keyboard.press(char)
                            delay_ms = max(50, min(300, int(random.gauss(150, 60))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10: await asyncio.sleep(random.uniform(0.5, 1.2))
                else:
                    real_selector = f'[data-echo-index="{idx}"]'
                    logger.info(f"[{sid}] 🖱️ Interact DOM ({a_type}) Target: {real_selector}")
                    loc = await find_element_and_frame(page, real_selector)
                
                    if not loc:
                        return {"status": "error", "message": "ERREUR_DOM : Élément introuvable dans aucune frame."}
                    
                    await session.move_mouse_to_locator(page, loc)
                
                    # Délai humain avant interaction (visée oculaire)
                    await asyncio.sleep(random.uniform(0.15, 0.4))
                
                    if a_type == "click":
                        try:
                            await loc.click(timeout=10000)
                        except Exception as e:
                            logger.warning(f"[{sid}] Native DOM click failed, trying force: {e}")
                            await loc.click(force=True, timeout=5000)
                        try:
                            box = await loc.bounding_box()
                            if box:
                                await asyncio.sleep(random.uniform(0.05, 0.15))
                                await session.bezier_mouse_move(page, max(0, box['x'] + box['width']/2 + random.uniform(30, 100) * random.choice([1, -1])), max(0, box['y'] + box['height']/2 + random.uniform(30, 100) * random.choice([1, -1])))
                        except Exception:
                            pass
                    elif a_type == "hover":
                        await loc.hover(timeout=10000)
                    elif a_type == "download":
                        async def handle_download():
                            try:
                                file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                                async with page.expect_download(timeout=120000) as download_info:
                                    await loc.click()
                                download = await download_info.value
                            
                                dl_dir = os.path.join("/app/data/downloads", session.user_id, sid)
                                os.makedirs(dl_dir, exist_ok=True)
                            
                                filename = download.suggested_filename
                                final_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                            
                                await download.save_as(final_path)
                                logger.info(f"[{sid}] 📥 Download completed: {final_path}")
                            except Exception as e:
                                logger.error(f"[{sid}] ⚠️ Download error: {e}")

                        asyncio.create_task(handle_download())
                        return {"status": "downloading", "action": a_type, "message": "Téléchargement initié en tâche de fond."}
                    elif a_type == "save_target":
                        async def stealth_download():
                            try:
                                tag_name = await loc.evaluate("el => el.tagName.toLowerCase()")
                                url_attr = "href" if tag_name == "a" else "src"
                                target_url = await loc.get_attribute(url_attr)
                                if not target_url:
                                    logger.error(f"[{sid}] Save_target failed: target has no href or src")
                                    return
                                from urllib.parse import urljoin
                                target_url = urljoin(page.url, target_url)
                                file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                                dl_dir = os.path.join("/app/data/downloads", session.user_id, sid)
                                os.makedirs(dl_dir, exist_ok=True)
                                filename = target_url.split("/")[-1].split("?")[0]
                                if not filename: filename = "downloaded_file"
                                dest_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                                response = await page.context.request.get(target_url)
                                body = await response.body()
                                with open(dest_path, "wb") as f:
                                    f.write(body)
                                logger.info(f"[{sid}] 📥 Stealth Download completed: {dest_path}")
                            except Exception as e:
                                logger.error(f"[{sid}] ⚠️ Stealth Download error: {e}")

                        asyncio.create_task(stealth_download())
                        return {"status": "downloading", "action": a_type, "message": "Téléchargement furtif initié en tâche de fond."}
                    elif a_type == "type":
                        await loc.click(timeout=10000)
                        for char in text_to_type:
                            await loc.press_sequentially(char)
                            delay_ms = max(50, min(300, int(random.gauss(150, 60))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10: await asyncio.sleep(random.uniform(0.5, 1.2))
                        
                await page.wait_for_load_state("load", timeout=15000)
                result["url"] = page.url

            elif action == "inspect_page":
                target = params.get("target")
                logger.info(f"[{sid}] 🔍 Inspect Page: {target}")
            
                if target == "url":
                    idx = params.get("index")
                    if idx is None: return {"status": "error", "message": "Index manquant pour extraire l'URL."}
                    val = await page.evaluate(f"(sel) => {{ const el = document.querySelector(sel); return el ? (el.href || el.getAttribute('href')) : null; }}", f'[data-echo-index="{idx}"]')
                    result["value"] = val
                    result["url"] = page.url
                
                elif target == "search_dom":
                    query = str(params.get("value", "")).lower().replace("'", "\\'")
                    script = f"""
                    () => {{
                        let elements = document.querySelectorAll('[data-echo-index]');
                        for (let el of elements) {{
                            let text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('alt') || '').toLowerCase();
                            if (text.includes('{query}')) {{
                                el.scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                return {{
                                    "found": true,
                                    "index": parseInt(el.getAttribute('data-echo-index')),
                                    "text": text.substring(0, 100)
                                }};
                            }}
                        }}
                        return {{"found": false}};
                    }}
                    """
                    res = await page.evaluate(script)
                    if res.get("found"): await asyncio.sleep(1)
                    result["search_result"] = res
                    result["url"] = page.url

                elif target == "read_text":
                    content = await page.content()
                    text_content = await asyncio.to_thread(h2t.handle, content)
                    result["content"] = text_content[:2000000]
                    result["url"] = page.url

                elif target == "read_html":
                    html_content = await page.content()
                    result["content"] = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
                    result["url"] = page.url

                elif target == "a11y_tree":
                    try:
                        client = await page.context.new_cdp_session(page)
                        tree_data = await client.send("Accessibility.getFullAXTree")
                        nodes = tree_data.get("nodes", [])
                        node_map = {n["nodeId"]: n for n in nodes}
                    
                        def format_cdp_node(node_id, depth=0):
                            n = node_map.get(node_id)
                            if not n: return []
                            lines = []
                            ignored = n.get("ignored", False)
                        
                            if not ignored:
                                role = n.get("role", {}).get("value", "")
                                name = n.get("name", {}).get("value", "")
                                value = n.get("value", {}).get("value", "")
                            
                                # Filter out noisy and useless internal Chrome CDP nodes
                                if role == "StaticText" and not name and not value:
                                    return lines
                                if role in ["generic", "RootWebArea", "WebArea"] and not name and not value:
                                    # We don't return lines immediately as they might have children, we just skip appending them
                                    role = "" # This will prevent it from being appended if name and value are also empty
                                
                                if role or name or value:
                                    line = "  " * depth + f"[{role}] {name}"
                                    if value: line += f" (val: {value})"
                                    lines.append(line)
                                    depth += 1
                                
                            for cid in n.get("childIds", []):
                                lines.extend(format_cdp_node(cid, depth))
                            return lines
                    
                        root_id = nodes[0]["nodeId"] if nodes else None
                        result_lines = format_cdp_node(root_id) if root_id else []
                        result["content"] = "\n".join(result_lines) if result_lines else "Arbre A11y vide ou indisponible."
                    except Exception as e:
                        logger.error(f"[{sid}] CDP A11y Error: {e}")
                        result["content"] = f"Erreur d'extraction A11y : {str(e)}"
                    result["url"] = page.url

                elif target in ["vision", "dom_map"]:
                    vision_grid = params.get("vision_grid", False)
                    vision_grid_step = params.get("vision_grid_step", 100)
                    await page.bring_to_front()
                    await asyncio.sleep(0.5)
                
                    clean_bytes = await page.screenshot(type="png")
                    clean_b64 = base64.b64encode(clean_bytes).decode('utf-8')
                
                    all_elements = []
                    global_index = 0
                
                    for frame in page.frames:
                        offset_x, offset_y = 0, 0
                        try:
                            frame_el = await frame.frame_element()
                            if frame_el:
                                box = await frame_el.bounding_box()
                                if box:
                                    offset_x, offset_y = box['x'], box['y']
                        except:
                            pass
                        
                        try:
                            vision_data = await asyncio.wait_for(frame.evaluate(HIGHLIGHT_JS, global_index), timeout=2.0)
                            elements = vision_data.get("elements", [])
                            for el in elements:
                                el['coords'][0] += offset_x
                                el['coords'][1] += offset_y
                                el['frame_url'] = frame.url
                                all_elements.append(el)
                            global_index = vision_data.get("count", global_index)
                        except Exception as e:
                            logger.warning(f"[{sid}] Failed to extract frame: {e}")

                    result.update({
                        "metadata": all_elements, 
                        "count": global_index, 
                        "url": page.url, 
                        "tab_index": getattr(session, 'active_page_index', 0), 
                        "tab_count": len(getattr(session, 'pages', [page]))
                    })
                
                    ghost_page = await session.context.new_page()
                    if vision_grid:
                        draw_script = f"""
                        () => {{
                            document.body.style.margin = '0';
                            document.body.style.overflow = 'hidden';
                            document.body.style.backgroundColor = '#222';
                            const img = new Image();
                            img.style.width = window.innerWidth + 'px';
                            img.style.height = window.innerHeight + 'px';
                            img.style.display = 'block';
                            img.style.position = 'absolute';
                            img.style.top = '0';
                            img.style.left = '0';
                            img.src = 'data:image/png;base64,{clean_b64}';
                            img.onload = () => {{
                                const canvas = document.createElement('canvas');
                                canvas.width = window.innerWidth;
                                canvas.height = window.innerHeight;
                                canvas.style.position = 'absolute';
                                canvas.style.top = '0';
                                canvas.style.left = '0';
                                document.body.appendChild(img);
                                document.body.appendChild(canvas);
                                const ctx = canvas.getContext('2d');
                                ctx.font = '12px sans-serif';
                                ctx.textBaseline = 'top';
                                ctx.strokeStyle = 'rgba(255, 0, 68, 0.5)';
                                ctx.fillStyle = 'rgba(255, 0, 68, 0.8)';
                                for (let x = 0; x < canvas.width; x += {vision_grid_step}) {{
                                    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, canvas.height); ctx.stroke();
                                    ctx.fillText(x, x + 2, 2);
                                }}
                                for (let y = 0; y < canvas.height; y += {vision_grid_step}) {{
                                    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(canvas.width, y); ctx.stroke();
                                    ctx.fillText(y, 2, y + 2);
                                }}
                                window.__echo_draw_done = true;
                            }};
                        }}
                        """
                    else:
                        elements_json = json.dumps(all_elements).decode('utf-8')
                        draw_script = f"""
                        () => {{
                            document.body.style.margin = '0';
                            document.body.style.overflow = 'hidden';
                            document.body.style.backgroundColor = '#222';
                            const img = new Image();
                            img.style.width = window.innerWidth + 'px';
                            img.style.height = window.innerHeight + 'px';
                            img.style.display = 'block';
                            img.style.position = 'absolute';
                            img.style.top = '0';
                            img.style.left = '0';
                            img.src = 'data:image/png;base64,{clean_b64}';
                            img.onload = () => {{
                                const canvas = document.createElement('canvas');
                                canvas.width = window.innerWidth;
                                canvas.height = window.innerHeight;
                                canvas.style.position = 'absolute';
                                canvas.style.top = '0';
                                canvas.style.left = '0';
                                document.body.appendChild(img);
                                document.body.appendChild(canvas);
                                const ctx = canvas.getContext('2d');
                                ctx.font = '11px sans-serif';
                                ctx.textBaseline = 'top';
                                const elements = {elements_json};
                                const drawn = [];
                                for (let el of elements) {{
                                    let [x, y, w, h] = el.coords;
                                    if (y > window.innerHeight || y + h < 0 || x > window.innerWidth || x + w < 0) continue;
                                    let adjustedY = y;
                                    while(drawn.some(p => Math.abs(p.x - x) < 25 && Math.abs(p.y - adjustedY) < 18)) {{
                                        adjustedY += 18;
                                    }}
                                    drawn.push({{x: x, y: adjustedY}});
                                    const text = String(el.id);
                                    const tWidth = ctx.measureText(text).width;
                                    ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
                                    ctx.fillRect(x, adjustedY, tWidth + 6, 16);
                                    ctx.fillStyle = 'white';
                                    ctx.fillText(text, x + 3, adjustedY + 2);
                                }}
                                window.__echo_draw_done = true;
                            }};
                        }}
                        """
                    
                    await ghost_page.evaluate(draw_script)
                    for _ in range(10):
                        done = await ghost_page.evaluate("() => window.__echo_draw_done === true")
                        if done: break
                        await asyncio.sleep(0.1)
                    
                    annotated_bytes = await ghost_page.screenshot(type="png")
                    await ghost_page.close()
                    result["screenshot_b64"] = base64.b64encode(annotated_bytes).decode('utf-8')
                    result["url"] = page.url
                    if vision_grid:
                        result["vision_grid_info"] = f"Origine (0,0) en haut à gauche. Lignes espacées de {vision_grid_step} pixels."

            elif action == "browser_control":
                cmd = params.get("command")
                val = params.get("value")
                logger.info(f"[{sid}] ⚙️ Browser Control: {cmd} {val or ''}")
            
                if cmd == "navigate":
                    if not val: return {"status": "error", "message": "URL manquante."}
                    await page.goto(val, wait_until="load", timeout=60000)
                    result["title"], result["url"] = await page.title(), page.url
                elif cmd == "scroll":
                    if val == "down": await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
                    elif val == "up": await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
                    elif val == "top": await page.evaluate("window.scrollTo(0, 0)")
                    elif val == "bottom": await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(0.5)
                    result["url"] = page.url
                elif cmd == "press_key":
                    await page.keyboard.press(val or "Enter")
                    await page.wait_for_load_state("load", timeout=30000)
                    result["url"] = page.url
                elif cmd == "pause":
                    await asyncio.sleep(float(val or 2))
                    result["url"] = page.url
                elif cmd == "refresh":
                    await page.reload(wait_until="load", timeout=30000)
                    result["url"] = page.url
                elif cmd == "reset":
                    async with SESSIONS_LOCK:
                        if sid in SESSIONS:
                            old_s = SESSIONS.pop(sid)
                            await old_s.close()
                    result["message"] = "Session réinitialisée."
                elif cmd == "tab_new":
                    new_p = await session.context.new_page()
                    await new_p.goto(val or "about:blank", wait_until="load")
                    session.pages.append(new_p)
                    session.active_page_index = len(session.pages) - 1
                    result["message"] = f"Nouvel onglet ouvert (Index: {session.active_page_index})"
                elif cmd == "tab_switch":
                    idx = int(val or 0)
                    if 0 <= idx < len(session.pages):
                        session.active_page_index = idx
                        result["message"] = f"Basculé sur l'onglet {idx}"
                    else: return {"status": "error", "message": "Index invalide."}
                elif cmd == "tab_close":
                    if len(session.pages) > 1:
                        p = session.pages.pop(session.active_page_index)
                        await p.close()
                        session.active_page_index = max(0, session.active_page_index - 1)
                        result["message"] = "Onglet fermé."
                    else: return {"status": "error", "message": "Impossible de fermer le dernier onglet."}

            else:
                return {"status": "error", "message": f"Action '{action}' non supportée."}

            return result

    except Exception as e:
        logger.error(f"[{sid}] 💥 Action Error: {str(e)}")
        return {"status": "error", "message": f"ERREUR_CRITIQUE : {str(e)}"}

@app.get("/health")
async def health():
    """Healthcheck pour Docker Compose (orchestration séquentielle)."""
    return {"status": "ready", "browser": state.browser is not None}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
