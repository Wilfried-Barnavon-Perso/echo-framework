"""
================================================================================
MODULE : ECHO BROWSER WORKER API (FASTAPI ASYNC EDITION)
VERSION : 9.29 (Spoofing strict Webdriver/PluginArray)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-10-08

CHANGELOG 9.29 :
- FEAT: Vision Grid Optimisée (Bicolore Magenta/Cyan, crénelures de quarts) pour supprimer le biais d'interpolation IA.
- FIX: Iframe DOM Map Offset bug (Calibration exacte via locator("html").bounding_box() pour gérer les iframes avec scale, bordures et paddings, annulant l'offset visuel).

CHANGELOG 9.27 :
- REFACTOR: JS Constants (no f-strings), Dedicated overlay context.
- FEAT: Real CDP zoom (clip.scale) with absolute grid.
- FEAT: Hit tests (elementFromPoint) before clicks.
- FEAT: Generational DOM index, auto-cleanup.
- FEAT: LRU session eviction (max 20).
- FEAT: Human wheel scrolling & strict wait_for_settle.
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
import logging.config

if os.path.exists('/app/logging.json'):
    with open('/app/logging.json', 'rb') as f:
        logging.config.dictConfig(json.loads(f.read()))
else:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("echo-browser")

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

# --- ETAT GLOBAL ---
IDLE_TIMEOUT_DEFAULT = 3600 # 1 heure de survie par defaut
MAX_SESSIONS = 20
RENDERING_FPS = 9 # Vitesse du moteur headless pour optimiser le CPU
SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()

class GlobalState:
    playwright = None
    browser = None
    overlay_context = None

state = GlobalState()

# --- CONSTANTES & SCRIPTS JS ---
MAX_ZOOM_SCALE = 4.0
ZOOM_TARGET_PX = 1024
ZOOM_MIN_BOX = 40
INTERACTIVE_SELECTORS = ('a, button, input, textarea, select, [role="button"], [role="link"], [onclick], label, '
                         '[role="radio"], [role="checkbox"], [role="switch"], [role="tab"], [role="menuitem"], '
                         '[tabindex], summary')
MIN_FRAME_AREA = 2500

HIGHLIGHT_JS = r"""
(args) => {
    const start = args.start || 0;
    const gen = String(args.gen);
    try {
        if (!document.getElementById('echo-cursor')) {
            const cursor = document.createElement('div');
            cursor.id = 'echo-cursor';
            cursor.style.cssText = 'position: fixed; top: 0; left: 0; transform: translate(-50%, -50%); width: 16px; height: 16px; background-color: #ff0044; border: 2px solid white; border-radius: 50%; z-index: 2147483647; pointer-events: none; transition: transform 0.05s linear;';
            document.body.appendChild(cursor);
            document.addEventListener('mousemove', e => {
                cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
                window.__echo_mouse_x = Math.round(e.clientX);
                window.__echo_mouse_y = Math.round(e.clientY);
            });
        }
        document.querySelectorAll('[data-echo-index]').forEach(e => e.removeAttribute('data-echo-index'));
        
        const interactiveSelectors = args.interactive;
        let items = Array.from(document.querySelectorAll(interactiveSelectors));
        let itemsSet = new Set(items);
        
        document.querySelectorAll('p, h1, h2, h3, h4, h5, h6, li, span, div, i, svg, img').forEach(el => {
            if (!itemsSet.has(el)) {
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
        let count = start;
        items.forEach(el => {
            const style = window.getComputedStyle(el);
            if (style.visibility === 'hidden' || style.display === 'none') return;
            if (style.opacity === '0' && !(el.tagName === 'INPUT' || el.tagName === 'SELECT')) return;
            let rect = el.getBoundingClientRect();
            
            if (rect.bottom < 0 || rect.top > window.innerHeight) return;
            
            if (rect.width > 5 && rect.height > 5) {
                let aria = el.getAttribute('aria-label') || "";
                let text = (el.innerText || aria || el.alt || "").trim().replace(/\s+/g, ' ').substring(0, 200);
                let meta = { id: gen + ':' + count, tag: el.tagName.toLowerCase() };
                
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
                else if (!isPointer && !isMedia && !el.matches(interactiveSelectors)) return;
                
                ['type', 'placeholder', 'value', 'aria-label', 'aria-expanded', 'disabled', 'checked', 'role', 'href'].forEach(attr => {
                    let val = (attr === 'value' || attr === 'checked') ? el[attr] : (el.getAttribute(attr) || el[attr]);
                    if (val && val !== '') {
                        if (attr === 'href') val = String(val).substring(0, 40);
                        meta[attr] = val;
                    }
                });
                
                const cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
                if (cx >= 0 && cy >= 0 && cx < window.innerWidth && cy < window.innerHeight) {
                    const hit = document.elementFromPoint(cx, cy);
                    if (hit && hit !== el && !el.contains(hit) && !hit.contains(el)) meta.occluded = true;
                }
                if (el.hasAttribute('data-echo-found')) meta.found = true;
                
                elements.push(meta);
                el.setAttribute('data-echo-index', gen + ':' + count);
                count++;
            }
        });
        document.querySelectorAll('[data-echo-found]').forEach(e => e.removeAttribute('data-echo-found'));
        return { count: count, elements: elements };
    } catch (e) { return { count: start, elements: [], error: e.toString() }; }
}
"""

GRID_OVERLAY_JS = r"""
async (p) => {
    document.body.style.margin = '0';
    const img = new Image();
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = p.img; });
    const canvas = document.createElement('canvas');
    canvas.width = p.w;
    canvas.height = p.h;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, p.w, p.h);
    const [ox, oy] = p.origin;
    const toX = v => (v - ox) * p.scale;
    const toY = v => (v - oy) * p.scale;
    const xEnd = ox + p.w / p.scale;
    const yEnd = oy + p.h / p.scale;
    const firstX = Math.ceil(ox / p.step) * p.step;
    const firstY = Math.ceil(oy / p.step) * p.step;
    const label = (text, x, y, color) => {
        ctx.font = 'bold 12px monospace';
        ctx.textBaseline = 'top';
        ctx.lineWidth = 4;
        ctx.strokeStyle = 'black';
        ctx.strokeText(text, x, y);
        ctx.fillStyle = color || 'white';
        ctx.fillText(text, x, y);
    };
    ctx.lineWidth = 1;
    const colors = ['rgba(255, 0, 255, 0.55)', 'rgba(0, 255, 255, 0.55)']; // Magenta et Cyan
    const textColors = ['#ff00ff', '#00ffff']; // Magenta et Cyan
    const tickStep = p.step / 4;

    for (let x = firstX; x <= xEnd; x += p.step) {
        let idx = Math.abs(Math.floor(x / p.step));
        ctx.strokeStyle = colors[idx % 2];
        ctx.beginPath(); ctx.moveTo(toX(x), 0); ctx.lineTo(toX(x), p.h); ctx.stroke();
        
        ctx.beginPath();
        for (let y = firstY; y <= yEnd; y += tickStep) {
            if (Math.abs(y % p.step) < 0.1) continue;
            ctx.moveTo(toX(x), toY(y));
            ctx.lineTo(toX(x) + 6, toY(y));
        }
        ctx.stroke();
    }
    for (let y = firstY; y <= yEnd; y += p.step) {
        let idx = Math.abs(Math.floor(y / p.step));
        ctx.strokeStyle = colors[idx % 2];
        ctx.beginPath(); ctx.moveTo(0, toY(y)); ctx.lineTo(p.w, toY(y)); ctx.stroke();
        
        ctx.beginPath();
        for (let x = firstX; x <= xEnd; x += tickStep) {
            if (Math.abs(x % p.step) < 0.1) continue;
            ctx.moveTo(toX(x), toY(y));
            ctx.lineTo(toX(x), toY(y) + 6);
        }
        ctx.stroke();
    }
    const REPEAT = 300;
    for (let x = firstX; x <= xEnd; x += p.step) {
        let idx = Math.abs(Math.floor(x / p.step));
        for (let ly = 2; ly < p.h; ly += REPEAT) label('x' + x, toX(x) + 3, ly, textColors[idx % 2]);
    }
    for (let y = firstY; y <= yEnd; y += p.step) {
        let idx = Math.abs(Math.floor(y / p.step));
        for (let lx = 2 + REPEAT / 2; lx < p.w; lx += REPEAT) label('y' + y, lx, toY(y) + 2, textColors[idx % 2]);
    }
    if (p.center) {
        const cx = toX(p.center[0]), cy = toY(p.center[1]);
        ctx.strokeStyle = 'red';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(cx - 18, cy); ctx.lineTo(cx - 4, cy); ctx.moveTo(cx + 4, cy); ctx.lineTo(cx + 18, cy);
        ctx.moveTo(cx, cy - 18); ctx.lineTo(cx, cy - 4); ctx.moveTo(cx, cy + 4); ctx.lineTo(cx, cy + 18);
        ctx.stroke();
        label('(' + p.center[0] + ',' + p.center[1] + ')', cx + 8, cy + 8, '#ff5c5c');
    }
    if (p.mouse) {
        const [mx, my] = p.mouse;
        if (mx >= ox && mx <= xEnd && my >= oy && my <= yEnd) {
            ctx.strokeStyle = '#00ffff';
            ctx.lineWidth = 2;
            ctx.beginPath(); ctx.arc(toX(mx), toY(my), 10, 0, 2 * Math.PI); ctx.stroke();
            label('souris (' + mx + ',' + my + ')', toX(mx) + 12, toY(my) - 18, '#00ffff');
        }
    }
}
"""

HIGHLIGHT_OVERLAY_JS = r"""
async (p) => {
    document.body.style.margin = '0';
    const img = new Image();
    await new Promise((resolve, reject) => { img.onload = resolve; img.onerror = reject; img.src = p.img; });
    const canvas = document.createElement('canvas');
    canvas.width = p.w;
    canvas.height = p.h;
    document.body.appendChild(canvas);
    const ctx = canvas.getContext('2d');
    ctx.drawImage(img, 0, 0, p.w, p.h);
    ctx.font = '11px sans-serif';
    ctx.textBaseline = 'top';
    const drawn = [];
    for (let el of p.elements) {
        let [x, y, w, h] = el.coords;
        if (y > p.h || y + h < 0 || x > p.w || x + w < 0) continue;
        let adjustedY = y;
        while(drawn.some(pt => Math.abs(pt.x - x) < 25 && Math.abs(pt.y - adjustedY) < 18)) {
            adjustedY += 18;
        }
        drawn.push({x: x, y: adjustedY});
        const text = String(el.id.split(':')[1]); // Keep only n part for vision
        const tWidth = ctx.measureText(text).width;
        ctx.fillStyle = 'rgba(0, 0, 0, 0.75)';
        ctx.fillRect(x, adjustedY, tWidth + 6, 16);
        ctx.fillStyle = 'white';
        ctx.fillText(text, x + 3, adjustedY + 2);
    }
}
"""

SEARCH_DOM_JS = r"""
(args) => {
    const q = String(args.query || '').toLowerCase().trim();
    if (!q) return { found: false, error: 'Requête vide.' };
    const label = el => String(el.innerText || el.getAttribute('aria-label') || el.getAttribute('alt')
        || el.getAttribute('placeholder') || el.value || '').toLowerCase();
    const shown = el => {
        const r = el.getBoundingClientRect();
        if (r.width <= 5 || r.height <= 5) return false;
        const s = getComputedStyle(el);
        return s.visibility !== 'hidden' && s.display !== 'none';
    };
    const pools = [args.interactive, 'p, h1, h2, h3, h4, h5, h6, li', 'td, th, dt, dd, span, div'];
    for (const sel of pools) {
        let best = null, bestLen = Infinity;
        for (const el of document.querySelectorAll(sel)) {
            const t = label(el);
            if (t.length < bestLen && t.includes(q) && shown(el)) { best = el; bestLen = t.length; }
        }
        if (best) {
            best.setAttribute('data-echo-found', '1');
            const r = best.getBoundingClientRect();
            return { found: true, text: label(best).replace(/\s+/g, ' ').substring(0, 100),
                     delta_y: Math.round(r.top + r.height / 2 - window.innerHeight / 2) };
        }
    }
    return { found: false };
}
"""

FOUND_IN_VIEW_JS = """() => { const el = document.querySelector('[data-echo-found]');
    if (!el) return false; const r = el.getBoundingClientRect(); return r.bottom > 0 && r.top < window.innerHeight; }"""

HIT_TEST_JS = """(el, pts) => { const r = el.getBoundingClientRect();
    return pts.map(([fx, fy]) => { const t = document.elementFromPoint(r.left + fx * r.width, r.top + fy * r.height);
        return !!t && (t === el || el.contains(t)); }); }"""

SETTLE_JS = r"""
([quietMs, timeoutMs]) => new Promise(resolve => {
    let timer = setTimeout(done, quietMs);
    const hard = setTimeout(done, timeoutMs);
    const obs = new MutationObserver(() => { clearTimeout(timer); timer = setTimeout(done, quietMs); });
    function done() { obs.disconnect(); clearTimeout(timer); clearTimeout(hard); resolve(true); }
    obs.observe(document, { subtree: true, childList: true, attributes: true, characterData: true });
})
"""

# --- HELPERS ---
def html_to_markdown(html: str) -> str:
    conv = html2text.HTML2Text()
    conv.ignore_links = False
    conv.ignore_images = True
    conv.body_width = 0
    return conv.handle(html)

async def jpeg_capped(shoot, start_q: int = 85, floor_q: int = 40, limit: int = 720000) -> bytes:
    q = start_q
    data = await shoot(q)
    while len(data) > limit and q > floor_q:
        q -= 10
        data = await shoot(q)
    return data

async def render_overlay(image_b64: str, width: int, height: int, script: str, params: dict) -> bytes:
    ghost = await state.overlay_context.new_page()
    try:
        await ghost.set_viewport_size({"width": int(width), "height": int(height)})
        await ghost.evaluate(script, {**params, "img": f"data:image/jpeg;base64,{image_b64}",
                                      "w": int(width), "h": int(height)})
        return await jpeg_capped(lambda q: ghost.screenshot(type="jpeg", quality=q))
    finally:
        await ghost.close()

def fine_grid_step(scale: float) -> int:
    for step in (2, 5, 10, 20, 25, 50, 100):
        if step * scale >= 40: return step
    return 100

def clamp_zoom_box(zb: dict, vp: dict) -> dict:
    x1, x2 = sorted((int(zb["x1"]), int(zb["x2"])))
    y1, y2 = sorted((int(zb["y1"]), int(zb["y2"])))
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(vp["width"], x2), min(vp["height"], y2)
    if x2 - x1 < ZOOM_MIN_BOX:
        x1 = max(0, min((x1 + x2) // 2 - ZOOM_MIN_BOX // 2, vp["width"] - ZOOM_MIN_BOX))
        x2 = x1 + ZOOM_MIN_BOX
    if y2 - y1 < ZOOM_MIN_BOX:
        y1 = max(0, min((y1 + y2) // 2 - ZOOM_MIN_BOX // 2, vp["height"] - ZOOM_MIN_BOX))
        y2 = y1 + ZOOM_MIN_BOX
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}

async def capture_zoom(page, zoom_box: dict) -> tuple:
    vp = page.viewport_size or {"width": 1280, "height": 800}
    zb = clamp_zoom_box(zoom_box, vp)
    w, h = zb["x2"] - zb["x1"], zb["y2"] - zb["y1"]
    scale = max(1.0, min(MAX_ZOOM_SCALE, ZOOM_TARGET_PX / max(w, h)))
    page_x, page_y = await page.evaluate("() => [window.visualViewport.pageLeft, window.visualViewport.pageTop]")
    cdp = await page.context.new_cdp_session(page)
    try:
        shot = await cdp.send("Page.captureScreenshot", {
            "format": "jpeg", "quality": 90,
            "clip": {"x": zb["x1"] + page_x, "y": zb["y1"] + page_y, "width": w, "height": h, "scale": scale},
        })
    finally:
        await cdp.detach()
    return base64.b64decode(shot["data"]), zb, scale

async def collect_dom_map(page, gen: int, vp: dict) -> list:
    elements, next_index = [], 0
    for frame in page.frames:
        offset_x = offset_y = 0
        scale_x = scale_y = 1.0
        
        if frame != page.main_frame:
            try:
                # Calibration exacte de l'iframe : on utilise le <html> pour obtenir la matrice de transformation réelle (scales, borders, OOPiF)
                html_loc = frame.locator("html")
                html_box = await html_loc.bounding_box()
                if not html_box: continue
                
                html_rect = await html_loc.evaluate("el => { let r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }")
                
                # Calcul de l'échelle (gère les CSS transforms: scale)
                scale_x = html_box["width"] / html_rect["w"] if html_rect["w"] else 1.0
                scale_y = html_box["height"] / html_rect["h"] if html_rect["h"] else 1.0
                
                # Calcul de l'origine absolue (0,0) de l'iframe sur la page principale (gère les borders et paddings)
                offset_x = html_box["x"] - html_rect["x"] * scale_x
                offset_y = html_box["y"] - html_rect["y"] * scale_y
                
                if (html_box["x"] >= vp["width"] or html_box["y"] >= vp["height"] or
                    html_box["x"] + html_box["width"] <= 0 or html_box["y"] + html_box["height"] <= 0):
                    continue
            except Exception:
                continue

        try:
            data = await asyncio.wait_for(frame.evaluate(HIGHLIGHT_JS, {
                "start": next_index, "gen": gen, "interactive": INTERACTIVE_SELECTORS}), timeout=2.0)
        except Exception as e:
            logger.warning(f"Frame ignored ({frame.url[:80]}): {e}")
            continue

        for el in data.get("elements", []):
            # Application de la matrice de transformation (Scale + Translation)
            el["coords"][0] = offset_x + el["coords"][0] * scale_x
            el["coords"][1] = offset_y + el["coords"][1] * scale_y
            el["coords"][2] = el["coords"][2] * scale_x
            el["coords"][3] = el["coords"][3] * scale_y
            
            if frame != page.main_frame:
                el["frame_url"] = frame.url
            elements.append(el)
        next_index = data.get("count", next_index)
    return elements

async def first_visible(loc, limit: int = 10):
    for i in range(min(await loc.count(), limit)):
        cand = loc.nth(i)
        try:
            if await cand.is_visible(): return cand
        except Exception: continue
    return loc.first

async def wait_for_settle(page, quiet_ms: int = 400, timeout_ms: int = 3000):
    try: await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception: pass
    try: await page.evaluate(SETTLE_JS, [quiet_ms, timeout_ms])
    except Exception:
        try: await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception: pass

def pick_lru_victim():
    idle = [s for s in SESSIONS.values() if not s.action_lock.locked()]
    return min(idle, key=lambda s: s.last_activity).sid if idle else None

# --- SESSIONS ---
class BrowserSession:
    def __init__(self, sid, user_id, context, idle_timeout, mode="desktop"):
        self.sid = sid
        self.user_id = user_id
        self.context = context
        self.idle_timeout = idle_timeout
        self.mode = mode
        self.pages = []
        self.active_page_index = 0
        self.last_activity = time.time()
        self.cdp_client = None
        self.action_lock = asyncio.Lock()
        self.last_screencast_poll = 0
        self.latest_frame = None
        self.frame_id = 0
        self.index_gen = 0
        self.new_tab_opened = False

    def attach_page_tracking(self):
        def _on_page(new_page):
            if new_page not in self.pages:
                self.pages.append(new_page)
            self.active_page_index = self.pages.index(new_page)
            self.new_tab_opened = True
        self.context.on("page", _on_page)

    async def get_active_page(self):
        self.last_activity = time.time()
        if not self.pages:
            p = await self.context.new_page()
            if p not in self.pages:
                self.pages.append(p)
        if self.active_page_index >= len(self.pages):
            self.active_page_index = len(self.pages) - 1
        target = self.pages[self.active_page_index]
        if target.is_closed():
            self.pages.pop(self.active_page_index)
            return await self.get_active_page()
        return target

    async def bezier_mouse_move(self, page, target_x, target_y):
        import math
        try:
            start_x = getattr(self, 'mouse_x', random.randint(100, 800))
            start_y = getattr(self, 'mouse_y', random.randint(100, 600))
            distance = math.hypot(target_x - start_x, target_y - start_y)
            if distance < 5:
                await page.mouse.move(target_x, target_y)
                self.mouse_x, self.mouse_y = target_x, target_y
                return
            steps = max(5, min(15, int(distance / 60)))
            overshoot_x = target_x + random.uniform(-5, 5) if distance > 300 else target_x
            overshoot_y = target_y + random.uniform(-5, 5) if distance > 300 else target_y
            cp1_x = start_x + (overshoot_x - start_x) * 0.3 + random.uniform(-20, 20)
            cp1_y = start_y + (overshoot_y - start_y) * 0.3 + random.uniform(-20, 20)
            cp2_x = start_x + (overshoot_x - start_x) * 0.7 + random.uniform(-20, 20)
            cp2_y = start_y + (overshoot_y - start_y) * 0.7 + random.uniform(-20, 20)
            def ease_out_quad(t): return t * (2 - t)
            for i in range(1, steps + 1):
                t = i / steps
                et = ease_out_quad(t)
                x = (1-et)**3 * start_x + 3*(1-et)**2 * et * cp1_x + 3*(1-et)*et**2 * cp2_x + et**3 * overshoot_x
                y = (1-et)**3 * start_y + 3*(1-et)**2 * et * cp1_y + 3*(1-et)*et**2 * cp2_y + et**3 * overshoot_y
                x += random.uniform(-1, 1)
                y += random.uniform(-1, 1)
                await page.mouse.move(x, y)
                await asyncio.sleep(random.uniform(0.001, 0.005))
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

    async def pick_hit_point(self, locator, box: dict) -> tuple:
        fractions = [(0.5 + random.uniform(-0.15, 0.15), 0.5 + random.uniform(-0.15, 0.15)),
                     (0.5, 0.5), (0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7)]
        try:
            hits = await locator.evaluate(HIT_TEST_JS, fractions)
        except Exception:
            hits = [False] * len(fractions)
        for (fx, fy), ok in zip(fractions, hits):
            if ok: return box["x"] + fx * box["width"], box["y"] + fy * box["height"], True
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, False

    async def move_mouse_to_locator(self, page, locator) -> bool:
        try:
            await locator.scroll_into_view_if_needed(timeout=5000)
            box = await locator.bounding_box()
            if not box: return False
            tx, ty, hit = await self.pick_hit_point(locator, box)
            await self.bezier_mouse_move(page, tx, ty)
            return hit
        except Exception as e:
            logger.warning(f"[{self.sid}] Unable to compute bounding box: {e}")
            return False

    async def human_wheel_scroll(self, page, delta_y: float):
        vp = page.viewport_size or {"width": 1280, "height": 800}
        mx, my = getattr(self, "mouse_x", -1), getattr(self, "mouse_y", -1)
        if not (0 < mx < vp["width"] and 0 < my < vp["height"]):
            await self.bezier_mouse_move(page, vp["width"] * random.uniform(0.4, 0.6), vp["height"] * random.uniform(0.4, 0.6))
        steps = max(3, min(12, int(abs(delta_y) / 120)))
        remaining = float(delta_y)
        for i in range(steps):
            chunk = remaining if i == steps - 1 else remaining / (steps - i) * random.uniform(0.8, 1.2)
            await page.mouse.wheel(0, chunk)
            remaining -= chunk
            await asyncio.sleep(random.uniform(0.03, 0.09))

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
            "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
            "--disable-background-timer-throttling", "--disable-extensions", "--disable-sync",
            f"--limit-fps={RENDERING_FPS}", "--disable-blink-features=AutomationControlled"
        ]
    )
    state.overlay_context = await state.browser.new_context(device_scale_factor=1)
    cleanup_task = asyncio.create_task(session_cleanup_loop())
    yield
    cleanup_task.cancel()
    await state.overlay_context.close()
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
                    try: asyncio.create_task(s.cdp_client.send('Page.stopScreencast'))
                    except: pass
                    s.cdp_client = None
                    s.last_screencast_poll = 0

@app.post("/start_session")
async def start_session(request: Request):
    data = await request.json()
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    sid = data.get("session_id")
    idle_timeout = data.get("idle_timeout", IDLE_TIMEOUT_DEFAULT)
    mode = data.get("mode", "mobile")
    if not sid: return {"status": "error", "message": "ERREUR_TECHNIQUE : Identifiant de chat manquant."}

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
            victim = pick_lru_victim()
            if victim is None:
                return {"status": "error", "message": "ERREUR_CAPACITE : toutes les sessions exécutent une action."}
            logger.info(f"[{victim}] ♻️ LRU eviction (capacity {MAX_SESSIONS})")
            await SESSIONS.pop(victim).close()
        
        if mode == "mobile":
            ctx_args = {
                "user_agent": "Mozilla/5.0 (iPad; CPU OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1",
                "viewport": {"width": 820, "height": 1180},
                "device_scale_factor": 2, "is_mobile": True, "has_touch": True
            }
        else:
            ctx_args = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 800}, "device_scale_factor": 1
            }

        context = await state.browser.new_context(**ctx_args)
        
        stealth_script = """
            try { delete Navigator.prototype.webdriver; } catch(e) {}
            try {
                const mockPlugins = Object.create(PluginArray.prototype);
                const p1 = Object.create(Plugin.prototype);
                Object.defineProperties(p1, { name: { value: 'Chrome PDF Plugin' }, filename: { value: 'internal-pdf-viewer' }, description: { value: 'Portable Document Format' } });
                const p2 = Object.create(Plugin.prototype);
                Object.defineProperties(p2, { name: { value: 'Chrome PDF Viewer' }, filename: { value: 'mhjimiapiapergbkpnjafkikajddhbdk' }, description: { value: '' } });
                const p3 = Object.create(Plugin.prototype);
                Object.defineProperties(p3, { name: { value: 'Native Client' }, filename: { value: 'internal-nacl-plugin' }, description: { value: '' } });
                Object.defineProperties(mockPlugins, { 0: { value: p1 }, 1: { value: p2 }, 2: { value: p3 }, length: { value: 3 } });
                Object.defineProperty(mockPlugins, 'item', { value: function(index) { return this[index]; } });
                Object.defineProperty(mockPlugins, 'namedItem', { value: function(name) { return [p1, p2, p3].find(p => p.name === name); } });
                Object.defineProperty(mockPlugins, 'refresh', { value: function() {} });
                Object.defineProperty(navigator, 'plugins', { get: () => mockPlugins });
            } catch (e) {}
            Object.defineProperty(navigator, 'languages', { get: () => ['fr-FR', 'fr', 'en-US', 'en'], });
            if (!window.chrome) {
                window.chrome = {
                    app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                    runtime: {}
                };
            }
            const originalQuery = window.navigator.permissions.query;
            window.navigator.permissions.query = new Proxy(originalQuery, {
                apply: (target, thisArg, args) => {
                    if (args && args[0] && args[0].name === 'notifications') return Promise.resolve({ state: Notification.permission });
                    return Reflect.apply(target, thisArg, args);
                }
            });
            try {
                const getParameterProxyHandler = {
                    apply: function (target, thisArg, args) {
                        const param = args[0];
                        if (param === 37445) return 'Google Inc. (NVIDIA)';
                        if (param === 37446) return 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)';
                        return Reflect.apply(target, thisArg, args);
                    }
                };
                ['WebGLRenderingContext', 'WebGL2RenderingContext'].forEach((ctx) => {
                    if (window[ctx] && window[ctx].prototype && window[ctx].prototype.getParameter) {
                        const original = window[ctx].prototype.getParameter;
                        window[ctx].prototype.getParameter = new Proxy(original, getParameterProxyHandler);
                    }
                });
            } catch (e) {}
            if (window.outerWidth === 0 || window.outerHeight === 0) {
                Object.defineProperty(window, 'outerWidth', { get: () => window.innerWidth });
                Object.defineProperty(window, 'outerHeight', { get: () => window.innerHeight });
            }
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            const isIpad = navigator.userAgent.includes('iPad');
            Object.defineProperty(navigator, 'platform', { get: () => isIpad ? 'MacIntel' : 'Win32' });
        """
        await context.add_init_script(stealth_script)
        session = BrowserSession(sid, user_id, context, idle_timeout, mode)
        session.attach_page_tracking()
        SESSIONS[sid] = session
        logger.info(f"[{sid}] 🆕 v6 Session created (Mode: {mode})")
        return {"session_id": sid, "status": "success"}

async def find_element_and_frame(page, selector):
    for frame in page.frames:
        try:
            loc = frame.locator(selector)
            if await loc.count() > 0: return await first_visible(loc)
        except: pass
    return None

@app.post("/screencast/start")
async def screencast_start(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    session = SESSIONS.get(sid)
    if not session: return {"status": "error", "message": "Session introuvable."}
    page = await session.get_active_page()
    if not page: return {"status": "error", "message": "Aucune page active."}
    try:
        if session.cdp_client:
            try: await session.cdp_client.send('Page.stopScreencast')
            except: pass
            session.cdp_client = None
        session.cdp_client = await page.context.new_cdp_session(page)
        vp = page.viewport_size
        max_w = (vp["width"] // 2) if vp else 640
        max_h = (vp["height"] // 2) if vp else 400
        async def handle_frame(event):
            session.latest_frame = event['data']
            session.frame_id += 1
            try: await session.cdp_client.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
            except: pass
        session.cdp_client.on("Page.screencastFrame", handle_frame)
        await session.cdp_client.send('Page.startScreencast', {
            'format': 'jpeg', 'quality': 50, 'maxWidth': max_w, 'maxHeight': max_h, 'everyNthFrame': 1
        })
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[{sid}] Screencast start error: {e}")
        return {"status": "error", "error": str(e)}

@app.post("/screencast/stop")
async def screencast_stop(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    session = SESSIONS.get(sid)
    if not session: return {"status": "error", "message": "Session introuvable."}
    try:
        if session.cdp_client:
            try: await session.cdp_client.send('Page.stopScreencast')
            except: pass
    except Exception as e:
        logger.error(f"[{sid}] Screencast stop error: {e}")
    finally:
        if session.cdp_client:
            try: await session.cdp_client.detach()
            except: pass
            session.cdp_client = None
        session.last_screencast_poll = 0
    return {"status": "success"}

@app.post("/screencast/latest")
async def screencast_latest(request: Request):
    data = await request.json()
    sid = data.get("session_id")
    last_frame_id = data.get("last_frame_id", 0)
    session = SESSIONS.get(sid)
    if not session: return {"status": "error", "message": "Session introuvable."}
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
                    try: await session.cdp_client.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
                    except: pass
                session.cdp_client.on("Page.screencastFrame", handle_frame)
                await session.cdp_client.send('Page.startScreencast', {
                    'format': 'jpeg', 'quality': 50, 'maxWidth': max_w, 'maxHeight': max_h, 'everyNthFrame': 1
                })
            except Exception as e:
                logger.error(f"[{sid}] Auto-Resume error: {e}")
    start_time = time.time()
    while session.frame_id == last_frame_id and time.time() - start_time < 1.0:
        await asyncio.sleep(0.05)
    page = await session.get_active_page()
    current_url = page.url if page else ""
    return {"status": "success", "frame_id": session.frame_id, "frame_b64": session.latest_frame, "url": current_url}

@app.post("/action")
async def browser_action(request: Request):
    data = await request.json()
    sid, action, params = data.get("session_id"), data.get("action"), data.get("params", {})
    session = SESSIONS.get(sid)
    if not session: return {"status": "error", "error_type": "SESSION_NOT_FOUND", "message": "RESTART_REQUIRED"}
    session.last_activity = time.time()

    try:
        async with session.action_lock:
            page = await session.get_active_page()
            result = {"status": "success"}

            if action == "interact_a11y":
                method, value, a_type = params.get("method"), params.get("value"), params.get("action_type")
                name, text_to_type = params.get("name"), params.get("text_to_type", "")
                logger.info(f"[{sid}] 🖱️ Semantic Interact (A11y): {method}={value} name={name} ({a_type})")
                
                if method == "role": loc = page.get_by_role(value, name=name) if name else page.get_by_role(value)
                elif method == "label": loc = page.get_by_label(value)
                elif method == "text": loc = page.get_by_text(value)
                else: return {"status": "error", "message": "Method invalide."}
                
                if await loc.count() == 0:
                    if method == "role" and name:
                        logger.warning(f"[{sid}] Role+Name failed, trying Text fallback for: {name}")
                        loc = page.get_by_text(name)
                        if await loc.count() == 0:
                            return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value}, name={name})."}
                    else: return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value})."}
                
                loc = await first_visible(loc)
                hit = await session.move_mouse_to_locator(page, loc)
                await asyncio.sleep(random.uniform(0.15, 0.4))
                
                if a_type == "click":
                    if hit:
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                    else:
                        try: await loc.click(timeout=4000)
                        except Exception as e:
                            logger.warning(f"[{sid}] Click not actionable, forcing: {e}")
                            await loc.click(force=True, timeout=5000)
                        result["warning"] = "Point d'impact non vérifié (élément possiblement masqué) : contrôler l'effet du clic."
                    result["hit_verified"] = hit
                elif a_type == "type":
                    await loc.click(timeout=10000)
                    if params.get("clear_before", True):
                        await page.keyboard.press("Control+A")
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                        await page.keyboard.press("Backspace")
                    for char in text_to_type:
                        await loc.press_sequentially(char)
                        delay_ms = max(30, min(150, int(random.gauss(80, 40))))
                        await asyncio.sleep(delay_ms / 1000.0)
                        if random.random() < 0.10: await asyncio.sleep(random.uniform(0.2, 0.6))
                elif a_type == "select":
                    option = text_to_type or ""
                    try: await loc.select_option(label=option, timeout=5000)
                    except Exception: await loc.select_option(value=option, timeout=5000)
                    result["value"] = option
                elif a_type == "hover":
                    await loc.hover(timeout=10000)
                elif a_type == "download":
                    async def handle_download():
                        try:
                            file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                            async with page.expect_download(timeout=120000) as download_info:
                                await loc.click()
                            download = await download_info.value
                            dl_dir = os.path.join("/app/downloads", session.user_id, sid, "browser")
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
                            dl_dir = os.path.join("/app/downloads", session.user_id, sid, "browser")
                            os.makedirs(dl_dir, exist_ok=True)
                            filename = target_url.split("/")[-1].split("?")[0]
                            if not filename: filename = "downloaded_file"
                            dest_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                            response = await page.context.request.get(target_url)
                            body = await response.body()
                            with open(dest_path, "wb") as f: f.write(body)
                            logger.info(f"[{sid}] 📥 Stealth Download completed: {dest_path}")
                        except Exception as e:
                            logger.error(f"[{sid}] ⚠️ Stealth Download error: {e}")
                    asyncio.create_task(stealth_download())
                    return {"status": "downloading", "action": a_type, "message": "Téléchargement furtif initié en tâche de fond."}
                
                await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
                result["url"] = page.url

            elif action == "interact_dom":
                a_type = params.get("action_type")
                if a_type == "click_current":
                    logger.info(f"[{sid}] 🖱️ Interact DOM (click_current) on spot")
                    await page.mouse.down()
                    await asyncio.sleep(random.uniform(0.05, 0.12))
                    await page.mouse.up()
                    return {"status": "success", "action": a_type, "url": page.url, "message": "Pression sur place effectuée."}

                idx, x, y, text_to_type = params.get("index"), params.get("x"), params.get("y"), params.get("text_to_type", "")
                if idx is None and (x is None or y is None): 
                    return {"status": "error", "message": "ERREUR_PARAMETRE : Cible manquante (index ou x/y requis)."}
                
                if x is not None and y is not None:
                    css_x, css_y = float(x), float(y)
                    logger.info(f"[{sid}] 🖱️ Interact DOM ({a_type}) Coordinates: Target CSS({css_x}, {css_y})")
                    await session.bezier_mouse_move(page, css_x, css_y)
                    await asyncio.sleep(random.uniform(0.15, 0.4))
                
                    if a_type == "click":
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                        await session.bezier_mouse_move(page, max(0, css_x + random.uniform(2, 5) * random.choice([1, -1])), max(0, css_y + random.uniform(2, 5) * random.choice([1, -1])))
                    elif a_type == "type":
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                        if params.get("clear_before", True):
                            await page.keyboard.press("Control+A")
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await page.keyboard.press("Backspace")
                        for char in text_to_type:
                            await page.keyboard.press(char)
                            delay_ms = max(30, min(150, int(random.gauss(80, 40))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10: await asyncio.sleep(random.uniform(0.2, 0.6))
                    elif a_type == "select":
                        return {"status": "error", "message": "ERREUR_PARAMETRE : select exige un index ou une cible A11y."}
                else:
                    real_selector = f'[data-echo-index="{session.index_gen}:{idx}"]'
                    logger.info(f"[{sid}] 🖱️ Interact DOM ({a_type}) Target: {real_selector}")
                    loc = await find_element_and_frame(page, real_selector)
                    if not loc: return {"status": "error", "message": "ERREUR_DOM : Élément introuvable dans aucune frame."}
                    
                    hit = await session.move_mouse_to_locator(page, loc)
                    await asyncio.sleep(random.uniform(0.15, 0.4))
                
                    if a_type == "click":
                        if hit:
                            await page.mouse.down()
                            await asyncio.sleep(random.uniform(0.05, 0.12))
                            await page.mouse.up()
                        else:
                            try: await loc.click(timeout=4000)
                            except Exception as e:
                                logger.warning(f"[{sid}] Manual click failed, trying force: {e}")
                                await loc.click(force=True, timeout=5000)
                            result["warning"] = "Point d'impact non vérifié (élément possiblement masqué) : contrôler l'effet du clic."
                        result["hit_verified"] = hit
                    elif a_type == "hover":
                        await loc.hover(timeout=10000)
                    elif a_type == "select":
                        option = text_to_type or ""
                        try: await loc.select_option(label=option, timeout=5000)
                        except Exception: await loc.select_option(value=option, timeout=5000)
                        result["value"] = option
                    elif a_type == "download":
                        async def handle_download():
                            try:
                                file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                                async with page.expect_download(timeout=120000) as download_info:
                                    await loc.click()
                                download = await download_info.value
                                dl_dir = os.path.join("/app/downloads", session.user_id, sid, "browser")
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
                                dl_dir = os.path.join("/app/downloads", session.user_id, sid, "browser")
                                os.makedirs(dl_dir, exist_ok=True)
                                filename = target_url.split("/")[-1].split("?")[0]
                                if not filename: filename = "downloaded_file"
                                dest_path = os.path.join(dl_dir, f"{file_id}_{filename}")
                                response = await page.context.request.get(target_url)
                                body = await response.body()
                                with open(dest_path, "wb") as f: f.write(body)
                                logger.info(f"[{sid}] 📥 Stealth Download completed: {dest_path}")
                            except Exception as e:
                                logger.error(f"[{sid}] ⚠️ Stealth Download error: {e}")
                        asyncio.create_task(stealth_download())
                        return {"status": "downloading", "action": a_type, "message": "Téléchargement furtif initié en tâche de fond."}
                    elif a_type == "type":
                        await loc.click(timeout=10000)
                        if params.get("clear_before", True):
                            await page.keyboard.press("Control+A")
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await page.keyboard.press("Backspace")
                        for char in text_to_type:
                            await loc.press_sequentially(char)
                            delay_ms = max(30, min(150, int(random.gauss(80, 40))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10: await asyncio.sleep(random.uniform(0.2, 0.6))
                        
                await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
                result["url"] = page.url

            elif action == "inspect_page":
                target = params.get("target")
                logger.info(f"[{sid}] 🔍 Inspect Page: {target}")
            
                if target == "url":
                    idx = params.get("index")
                    if idx is None: return {"status": "error", "message": "Index manquant pour extraire l'URL."}
                    val = await page.evaluate(f"(sel) => {{ const el = document.querySelector(sel); return el ? (el.href || el.getAttribute('href')) : null; }}", f'[data-echo-index="{session.index_gen}:{idx}"]')
                    result["value"] = val
                    result["url"] = page.url
                
                elif target == "search_dom":
                    res = await page.evaluate(SEARCH_DOM_JS, {"query": params.get("value", ""), "interactive": INTERACTIVE_SELECTORS})
                    if res.get("found") and abs(res.get("delta_y", 0)) > (page.viewport_size or {"height": 800})["height"] * 0.3:
                        await session.human_wheel_scroll(page, res["delta_y"])
                        await wait_for_settle(page, quiet_ms=250, timeout_ms=1500)
                        if not await page.evaluate(FOUND_IN_VIEW_JS):
                            await page.evaluate("() => document.querySelector('[data-echo-found]')?.scrollIntoView({block: 'center'})")
                            await asyncio.sleep(0.3)
                    result["search_result"] = {k: v for k, v in res.items() if k != "delta_y"}
                    result["url"] = page.url

                elif target == "read_text":
                    content = await page.content()
                    text_content = await asyncio.to_thread(html_to_markdown, content)
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
                                if role == "StaticText" and not name and not value: return lines
                                if role in ["generic", "RootWebArea", "WebArea"] and not name and not value: role = ""
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
                    finally:
                        try: await client.detach()
                        except: pass
                    result["url"] = page.url

                elif target in ["vision", "dom_map"]:
                    vision_grid = params.get("vision_grid", False)
                    vision_grid_step = int(params.get("vision_grid_step", 100))
                    zoom_box = params.get("zoom_box")
                    await page.bring_to_front()
                    await wait_for_settle(page, quiet_ms=300, timeout_ms=1500)
                    vp = page.viewport_size or {"width": 1280, "height": 800}
                    clean_bytes = await jpeg_capped(lambda q: page.screenshot(type="jpeg", quality=q, scale="css"))
                    clean_b64 = base64.b64encode(clean_bytes).decode('utf-8')
                
                    all_elements = []
                    if params.get("reindex", True):
                        session.index_gen += 1
                        all_elements = await collect_dom_map(page, session.index_gen, vp)

                    mouse = [int(session.mouse_x), int(session.mouse_y)] if hasattr(session, "mouse_x") else None
                    result.update({
                        "viewport": vp, "mouse_position": mouse, "metadata": all_elements,
                        "count": len(all_elements), "url": page.url,
                        "tab_index": session.active_page_index, "tab_count": len(session.pages)
                    })

                    if zoom_box:
                        zoom_b64, zb, scale = await capture_zoom(page, zoom_box)
                        step = fine_grid_step(scale)
                        center = [(zb["x1"] + zb["x2"]) // 2, (zb["y1"] + zb["y2"]) // 2]
                        annotated = await render_overlay(
                            base64.b64encode(zoom_b64).decode('utf-8'), round((zb["x2"] - zb["x1"]) * scale), round((zb["y2"] - zb["y1"]) * scale),
                            GRID_OVERLAY_JS, {"step": step, "origin": [zb["x1"], zb["y1"]], "scale": scale, "mouse": mouse, "center": center})
                        result["zoom"] = {"box": zb, "scale": round(scale, 2), "center": center, "grid_step": step}
                    elif vision_grid:
                        annotated = await render_overlay(clean_b64, vp["width"], vp["height"], GRID_OVERLAY_JS, {
                            "step": vision_grid_step, "origin": [0, 0], "scale": 1, "mouse": mouse, "center": None})
                        result["vision_grid_info"] = f"Origine (0,0) en haut à gauche. Lignes espacées de {vision_grid_step} pixels."
                    else:
                        annotated = await render_overlay(clean_b64, vp["width"], vp["height"], HIGHLIGHT_OVERLAY_JS, {"elements": all_elements})
                    
                    result["screenshot_b64"] = base64.b64encode(annotated).decode('utf-8')
                    if vision_grid or zoom_box:
                        result["clean_b64"] = clean_b64

            elif action == "browser_control":
                cmd = params.get("command")
                val = params.get("value")
                logger.info(f"[{sid}] ⚙️ Browser Control: {cmd} {val or ''}")
            
                if cmd == "navigate":
                    if not val: return {"status": "error", "message": "URL manquante."}
                    await page.goto(val, wait_until="load", timeout=60000)
                    result["title"], result["url"] = await page.title(), page.url
                elif cmd == "scroll":
                    if val in ("down", "up"):
                        before = await page.evaluate("() => window.scrollY")
                        dy = await page.evaluate("() => window.innerHeight * 0.8") * (1 if val == "down" else -1)
                        await session.human_wheel_scroll(page, dy)
                        await wait_for_settle(page, quiet_ms=250, timeout_ms=1500)
                        if await page.evaluate("() => window.scrollY") == before:
                            await page.evaluate("(d) => window.scrollBy(0, d)", dy)
                    elif val == "top": await page.evaluate("window.scrollTo(0, 0)")
                    elif val == "bottom": await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    result["at_bottom"] = await page.evaluate("() => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2")
                    result["url"] = page.url
                elif cmd == "press_key":
                    await page.keyboard.press(val or "Enter")
                    await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
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
                    if new_p not in session.pages: session.pages.append(new_p)
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

            if session.new_tab_opened:
                page = await session.get_active_page()
                await wait_for_settle(page)
                result["message"] = f"Nouvel onglet ouvert et activé (index {session.active_page_index})."
                session.new_tab_opened = False

            return result

    except Exception as e:
        logger.error(f"[{sid}] 💥 Action Error: {str(e)}")
        return {"status": "error", "message": f"ERREUR_CRITIQUE : {str(e)}"}

@app.get("/health")
async def health():
    return {"status": "ready", "browser": state.browser is not None}

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
