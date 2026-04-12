"""
================================================================================
MODULE : ECHO BROWSER AGENT API (FASTAPI ASYNC EDITION)
VERSION : 9.0 (TURBO JSON)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-04-10

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
import secrets
import shutil
import time
import random
import uuid
import logging
import orjson as json
import html2text
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- ETAT GLOBAL ---
IDLE_TIMEOUT_DEFAULT = 3600 # 1 heure de survie par defaut
MAX_SESSIONS = 20
SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()

class GlobalState:
    playwright = None
    browser = None

state = GlobalState()

HIGHLIGHT_JS = """
(function() {
    try {
        document.querySelectorAll('.echo-marker').forEach(e => e.remove());
        const selectors = 'a, button, input, textarea, select, [role="button"], [role="link"], [onclick]';
        let items = Array.from(document.querySelectorAll(selectors));
        document.querySelectorAll('div, span, li, i, svg, img').forEach(el => {
            if (!items.includes(el) && window.getComputedStyle(el).cursor === 'pointer') {
                items.push(el);
            }
        });
        let elements = [];
        let count = 0;
        items.forEach(el => {
            let rect = el.getBoundingClientRect();
            if (rect.width > 0 && rect.height > 0) {
                let text = (el.innerText || el.ariaLabel || el.placeholder || "").trim().substring(0, 50);
                elements.push({
                    id: count, tag: el.tagName.toLowerCase(), text: text
                });
                let marker = document.createElement('div');
                marker.className = 'echo-marker';
                marker.innerText = count;
                marker.style.cssText = `
                    position: absolute; left: ${rect.left + window.scrollX}px; top: ${rect.top + window.scrollY}px;
                    z-index: 2147483647; background-color: #ff0000; color: #ffffff; font-weight: bold;
                    font-size: 10px; padding: 1px 2px; border: 1px solid white; border-radius: 2px;
                    pointer-events: none;
                `;
                document.body.appendChild(marker);
                el.setAttribute('data-echo-index', count);
                count++;
            }
        });
        return { "count": count, "elements": elements }; 
    } catch (e) { return { "count": 0, "elements": [], "error": e.toString() }; }
})();
"""

h2t = html2text.HTML2Text()
h2t.ignore_links = False
h2t.ignore_images = True
h2t.body_width = 0 

class BrowserSession:
    def __init__(self, sid, user_id, context, idle_timeout):
        self.sid = sid
        self.user_id = user_id
        self.context = context
        self.idle_timeout = idle_timeout
        self.pages = [] # Liste des onglets ouverts
        self.active_page_index = 0
        self.last_activity = time.time()

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

    async def mouse_shake(self, page):
        """Macro de presence."""
        try:
            for _ in range(3):
                x, y = random.randint(100, 700), random.randint(100, 1000)
                await page.mouse.move(x, y, steps=5)
                await asyncio.sleep(0.05)
        except: pass

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
            "--disable-async-dns" # Souverainete DNS: utilise le resolver systeme
        ]
    )
    cleanup_task = asyncio.create_task(session_cleanup_loop())
    yield
    cleanup_task.cancel()
    await state.browser.close()
    await state.playwright.stop()

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

async def session_cleanup_loop():
    while True:
        await asyncio.sleep(60)
        now = time.time()
        async with SESSIONS_LOCK:
            to_remove = [sid for sid, s in SESSIONS.items() if now - s.last_activity > s.idle_timeout]
            for sid in to_remove:
                logger.info(f"[{sid}] ⏰ Cleaning idle session")
                session = SESSIONS.pop(sid)
                await session.close()

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
            SESSIONS[sid].last_activity = time.time()
            return {"session_id": sid, "status": "success", "message": "Session deja active."}
        
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
        session = BrowserSession(sid, user_id, context, idle_timeout)
        SESSIONS[sid] = session
        logger.info(f"[{sid}] 🆕 v6 Session created (Mode: {mode} | Tablet Profile Active)")
        return {"session_id": sid, "status": "success"}

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
        page = await session.get_active_page()
        result = {"status": "success"}

        if action == "goto":
            url = params.get("url")
            if not url: return {"status": "error", "message": "ERREUR_PARAMETRE : URL manquante."}
            logger.info(f"[{sid}] 🌐 Goto: {url}")
            await page.goto(url, wait_until="networkidle", timeout=60000)
            result["title"], result["url"] = await page.title(), page.url

        elif action == "click":
            idx, sel = params.get("index"), params.get("selector")
            if idx is None and not sel: return {"status": "error", "message": "ERREUR_PARAMETRE : Cible manquante (index ou selector requis)."}
            
            real_selector = f'[data-echo-index="{idx}"]' if idx is not None else sel
            logger.info(f"[{sid}] 🖱️ Click Target: {real_selector}")
            
            try:
                await session.mouse_shake(page)
                await page.locator(real_selector).first.dispatch_event("click")
            except:
                await page.click(real_selector, timeout=10000)
            
            await page.wait_for_load_state("networkidle", timeout=15000)
            result["url"] = page.url

        elif action == "type":
            idx, sel, text = params.get("index"), params.get("selector"), params.get("text")
            if (idx is None and not sel) or text is None: 
                return {"status": "error", "message": "ERREUR_PARAMETRE : Cible ou texte manquant."}
            
            real_selector = f'[data-echo-index="{idx}"]' if idx is not None else sel
            logger.info(f"[{sid}] ⌨️ Type Target: {real_selector} | Content: {text}")
            await page.fill(real_selector, text, timeout=30000)
            result["url"] = page.url

        elif action == "press":
            key = params.get("key", "Enter")
            logger.info(f"[{sid}] ⌨️ Press: {key}")
            await page.keyboard.press(key)
            await page.wait_for_load_state("networkidle", timeout=30000)
            result["url"] = page.url

        elif action == "hover":
            idx, sel = params.get("index"), params.get("selector")
            if idx is None and not sel: return {"status": "error", "message": "ERREUR_PARAMETRE : Cible manquante."}
            
            real_selector = f'[data-echo-index="{idx}"]' if idx is not None else sel
            logger.info(f"[{sid}] 🖱️ Hover Target: {real_selector}")
            await page.hover(real_selector, timeout=10000)
            result["url"] = page.url

        elif action == "scroll":
            direction = params.get("direction", "down")
            logger.info(f"[{sid}] 📜 Scroll: {direction}")
            if direction == "down": await page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            elif direction == "up": await page.evaluate("window.scrollBy(0, -window.innerHeight * 0.8)")
            elif direction == "top": await page.evaluate("window.scrollTo(0, 0)")
            elif direction == "bottom": await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.5)
            result["url"] = page.url

        elif action == "read":
            logger.info(f"[{sid}] 📖 Read Markdown")
            content = await page.content()
            result["content"] = h2t.handle(content)[:30000]
            result["url"] = page.url

        elif action == "read_html":
            logger.info(f"[{sid}] 📖 Read HTML Source")
            # Encapsulation Base64 pour protection du JSON (v8.6)
            html_content = await page.content()
            result["content"] = base64.b64encode(html_content.encode('utf-8')).decode('utf-8')
            result["url"] = page.url

        elif action == "get_attribute":
            idx, attr = params.get("index"), params.get("attribute")
            if idx is None or not attr:
                return {"status": "error", "message": "ERREUR_PARAMETRE : Index ou attribut manquant."}
            real_selector = f'[data-echo-index="{idx}"]'
            logger.info(f"[{sid}] 🔍 Get Attribute '{attr}' for Target: {real_selector}")
            val = await page.evaluate(f"(sel) => {{ const el = document.querySelector(sel); return el ? (el.{attr} || el.getAttribute('{attr}')) : null; }}", real_selector)
            result["value"] = val
            result["url"] = page.url

        elif action == "tab_new":
            url = params.get("url", "about:blank")
            logger.info(f"[{sid}] 📑 New Tab: {url}")
            new_p = await session.context.new_page()
            await new_p.goto(url, wait_until="networkidle")
            session.pages.append(new_p)
            session.active_page_index = len(session.pages) - 1
            result["message"] = f"Nouvel onglet ouvert (Index: {session.active_page_index})"

        elif action == "tab_switch":
            idx = int(params.get("index", 0))
            if 0 <= idx < len(session.pages):
                session.active_page_index = idx
                result["message"] = f"Basculé sur l'onglet {idx}"
            else:
                return {"status": "error", "message": f"Index d'onglet invalide : {idx}"}

        elif action == "tab_close":
            if len(session.pages) > 1:
                p = session.pages.pop(session.active_page_index)
                await p.close()
                session.active_page_index = max(0, session.active_page_index - 1)
                result["message"] = "Onglet fermé."
            else:
                return {"status": "error", "message": "Impossible de fermer le dernier onglet restant."}

        elif action == "reset":
            logger.info(f"[{sid}] 🔄 Hard Resetting Session")
            async with SESSIONS_LOCK:
                if sid in SESSIONS:
                    session = SESSIONS.pop(sid)
                    await session.close()
            result["message"] = "Session réinitialisée. Nouveau navigateur au prochain appel."

        elif action == "highlight":
            logger.info(f"[{sid}] 📸 Visual Highlight Flow (Tab {session.active_page_index})")
            await page.bring_to_front()
            await session.mouse_shake(page)
            vision_data = await page.evaluate(HIGHLIGHT_JS)
            result.update({"metadata": vision_data.get("elements", []), "count": vision_data.get("count", 0), "url": page.url, "tab_index": session.active_page_index, "tab_count": len(session.pages)})
            
            # Stabilisation Paint
            await asyncio.sleep(0.5)
            
            # Optimisation v8.5 : Screenshot direct en memoire
            img_bytes = await page.screenshot(type="png")
            result["screenshot_b64"] = base64.b64encode(img_bytes).decode()

        else:
            return {"status": "error", "message": f"Action '{action}' non supportée."}

        return result

    except Exception as e:
        logger.error(f"[{sid}] 💥 Action Error: {str(e)}")
        return {"status": "error", "message": f"ERREUR_CRITIQUE : {str(e)}"}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
