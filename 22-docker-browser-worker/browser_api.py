# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
================================================================================
MODULE : ECHO BROWSER WORKER API (FASTAPI ASYNC EDITION)
VERSION : 6.16.0.1
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-11-21

================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.16.0.1: Migration screencast live vers WebSocket binaire (/ws/screencast, header 16B !BBHHHHIH + JPEG brut, ACK et CDP continu).
# 6.15.0.1: Capture et exposition du statut HTTP (page_http_status, listener main-frame, _goto_resilient, navigate/refresh/tab_new/inspect_page) et normalisation _norm_echo_id dans GET /sessions.
# 6.13.1.1: Découplage live CDP SSE (/screencast/stream), curseur JSON léger, encodage clip MP4 H.264 (curseur incrusté, filtre pair) sur /screencast/stop et correction fuites CDPSession.
# 6.7.5.1: Ajout de _goto_resilient (wait_until="domcontentloaded", repli gracieux sur PlaywrightTimeoutError hors about:blank) pour navigate, tab_new et refresh.
# 6.6.5.1: Ajout de l'endpoint GET /sessions pour lister les sessions Playwright actives par user_id/chat_id.

import asyncio
from collections import deque
import io
import pybase64 as base64
import os
import struct
import subprocess
import tempfile
import time
import random
import uuid
import logging
from typing import Any, Optional
import orjson as json
import html2text
from PIL import Image, ImageDraw
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright
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
IDLE_TIMEOUT_DEFAULT = 3600  # 1 heure de survie par defaut
MAX_SESSIONS = 20
RENDERING_FPS = 9  # Vitesse du moteur headless pour optimiser le CPU
CLIP_MAX_FRAMES = 180
CLIP_MAX_DURATION_SEC = 25.0
CLIP_SAMPLE_MIN_INTERVAL = 0.08
CLIP_FRAME_MIN_DUR_SEC = 0.04
CLIP_FRAME_MAX_DUR_SEC = 1.50
CLIP_FFMPEG_CRF = "28"
CLIP_FFMPEG_TIMEOUT_SEC = 20
LIVE_QUEUE_MAXSIZE = 8
LIVE_FRAME_HEADER = struct.Struct("!BBHHHHIH")
LIVE_MAX_IN_FLIGHT_FRAMES = 2
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

GET_CSS_PATH_JS = r"""
function getCssPath(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return '';
    const path = [];
    let cur = el;
    while (cur && cur.nodeType === Node.ELEMENT_NODE) {
        const tag = cur.tagName.toLowerCase();
        if (tag === 'html') {
            path.unshift('html');
            break;
        }
        let parent = cur.parentElement;
        if (!parent) {
            path.unshift(tag);
            break;
        }
        let index = 1;
        let sib = cur.previousElementSibling;
        while (sib) {
            index++;
            sib = sib.previousElementSibling;
        }
        path.unshift(`${tag}:nth-child(${index})`);
        cur = parent;
    }
    return path.join(' > ');
}
"""

HIGHLIGHT_JS = r"""
(args) => {
    """ + GET_CSS_PATH_JS + r"""
    const start = args.start || 0;
    const gen = String(args.gen);
    try {
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
        
        const foundEl = args.found_sel ? document.querySelector(args.found_sel) : null;
        let elements = [];
        let selectors = {};
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
                if (foundEl && el === foundEl) meta.found = true;
                
                elements.push(meta);
                selectors[count] = getCssPath(el);
                count++;
            }
        });
        return { count: count, elements: elements, selectors: selectors };
    } catch (e) { return { count: start, elements: [], selectors: {}, error: e.toString() }; }
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
    const tickStep = p.step / 8;

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
            ctx.lineTo(toX(x) + 6, toY(y));
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
    """ + GET_CSS_PATH_JS + r"""
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
            const r = best.getBoundingClientRect();
            return { found: true, text: label(best).replace(/\s+/g, ' ').substring(0, 100),
                     delta_y: Math.round(r.top + r.height / 2 - window.innerHeight / 2),
                     css_path: getCssPath(best) };
        }
    }
    return { found: false };
}
"""

FOUND_IN_VIEW_JS = """(sel) => {
    const el = document.querySelector(sel);
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return r.bottom > 0 && r.top < window.innerHeight;
}"""

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
        if step * scale >= 40:
            return step
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


async def collect_dom_map(page, gen: int, vp: dict, found_sel: str = None) -> tuple:
    elements, all_selectors, next_index = [], {}, 0
    for frame in page.frames:
        offset_x = offset_y = 0
        scale_x = scale_y = 1.0
        
        if frame != page.main_frame:
            try:
                # Calibration exacte de l'iframe : on utilise le <html> pour obtenir la matrice de transformation réelle (scales, borders, OOPiF)
                html_loc = frame.locator("html")
                html_box = await html_loc.bounding_box()
                if not html_box:
                    continue
                
                html_rect = await html_loc.evaluate("el => { let r = el.getBoundingClientRect(); return {x: r.left, y: r.top, w: r.width, h: r.height}; }")
                
                # Calcul de l'échelle (gère les CSS transforms: scale)
                scale_x = html_box["width"] / html_rect["w"] if html_rect["w"] else 1.0
                scale_y = html_box["height"] / html_rect["h"] if html_rect["h"] else 1.0
                
                # Calcul de l'origine absolue (0,0) de l'iframe sur la page principale (gère les borders et paddings)
                offset_x = html_box["x"] - html_rect["x"] * scale_x
                offset_y = html_box["y"] - html_rect["y"] * scale_y
                
                if (html_box["x"] >= vp["width"] or html_box["y"] >= vp["height"] or html_box["x"] + html_box["width"] <= 0 or html_box["y"] + html_box["height"] <= 0):
                    continue
            except Exception:
                continue

        try:
            data = await asyncio.wait_for(frame.evaluate(HIGHLIGHT_JS, {
                "start": next_index, "gen": gen, "interactive": INTERACTIVE_SELECTORS, "found_sel": found_sel}), timeout=2.0)
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
        for k, v in data.get("selectors", {}).items():
            all_selectors[int(k)] = v
        next_index = data.get("count", next_index)
    return elements, all_selectors


async def first_visible(loc, limit: int = 10):
    for i in range(min(await loc.count(), limit)):
        cand = loc.nth(i)
        try:
            if await cand.is_visible():
                return cand
        except Exception:
            continue
    return loc.first


async def wait_for_settle(page, quiet_ms: int = 400, timeout_ms: int = 3000):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.evaluate(SETTLE_JS, [quiet_ms, timeout_ms])
    except Exception:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
        except Exception:
            pass


async def _goto_resilient(page, url: str, timeout_ms: int = 25000) -> tuple[Optional[str], Optional[int], str]:
    """Navigue en attendant domcontentloaded + stabilisation légère, avec repli gracieux sur les portails à flux continu."""
    warning_msg: Optional[str] = None
    http_status: Optional[int] = None
    http_status_text: str = ""
    try:
        resp = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if resp is not None:
            http_status = getattr(resp, "status", None)
            http_status_text = getattr(resp, "status_text", "") or ""
    except PlaywrightTimeoutError as te:
        cur_url = getattr(page, "url", "") or ""
        if cur_url and cur_url != "about:blank":
            warning_msg = f"Chargement partiel (délai {timeout_ms}ms dépassé sur ressources secondaires, DOM actif sur {cur_url})."
        else:
            raise te
    await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
    return warning_msg, http_status, http_status_text


def pick_lru_victim():
    idle = [s for s in SESSIONS.values() if not s.action_lock.locked()]
    return min(idle, key=lambda s: s.last_activity).sid if idle else None


def build_stealth_context_args(mode: str, browser_version: str) -> dict:
    major_version = browser_version.split('.')[0] if browser_version else "130"
    sec_ch_ua = f'"Chromium";v="{major_version}", "Google Chrome";v="{major_version}", "Not?A_Brand";v="99"'
    if mode == "mobile":
        return {
            "user_agent": (
                f"Mozilla/5.0 (Linux; Android 14; Pixel 8 Pro) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{major_version}.0.0.0 Mobile Safari/537.36"
            ),
            "viewport": {"width": 412, "height": 915},
            "device_scale_factor": 2,
            "is_mobile": True,
            "has_touch": True,
            "extra_http_headers": {
                "Sec-CH-UA": sec_ch_ua,
                "Sec-CH-UA-Mobile": "?1",
                "Sec-CH-UA-Platform": '"Android"',
            }
        }
    else:
        return {
            "user_agent": (
                f"Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                f"Chrome/{major_version}.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1280, "height": 800},
            "device_scale_factor": 1,
            "is_mobile": False,
            "has_touch": False,
            "extra_http_headers": {
                "Sec-CH-UA": sec_ch_ua,
                "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Linux"',
            }
        }


def build_stealth_init_script(mode: str) -> str:
    target_platform = 'Linux armv8l' if mode == 'mobile' else 'Linux x86_64'
    return f"""
    try {{
        const mockPlugins = Object.create(PluginArray.prototype);
        const p1 = Object.create(Plugin.prototype);
        Object.defineProperties(p1, {{ name: {{ value: 'Chrome PDF Plugin' }}, filename: {{ value: 'internal-pdf-viewer' }}, description: {{ value: 'Portable Document Format' }} }});
        const p2 = Object.create(Plugin.prototype);
        Object.defineProperties(p2, {{ name: {{ value: 'Chrome PDF Viewer' }}, filename: {{ value: 'mhjimiapiapergbkpnjafkikajddhbdk' }}, description: {{ value: '' }} }});
        const p3 = Object.create(Plugin.prototype);
        Object.defineProperties(p3, {{ name: {{ value: 'Native Client' }}, filename: {{ value: 'internal-nacl-plugin' }}, description: {{ value: '' }} }});
        Object.defineProperties(mockPlugins, {{ 0: {{ value: p1 }}, 1: {{ value: p2 }}, 2: {{ value: p3 }}, length: {{ value: 3 }} }});
        Object.defineProperty(mockPlugins, 'item', {{ value: function(index) {{ return this[index]; }} }});
        Object.defineProperty(mockPlugins, 'namedItem', {{ value: function(name) {{ return [p1, p2, p3].find(p => p.name === name); }} }});
        Object.defineProperty(mockPlugins, 'refresh', {{ value: function() {{}} }});
        Object.defineProperty(Navigator.prototype, 'plugins', {{ get: () => mockPlugins, configurable: true }});
    }} catch (e) {{}}

    Object.defineProperty(Navigator.prototype, 'languages', {{ get: () => ['fr-FR', 'fr', 'en-US', 'en'], configurable: true }});
    Object.defineProperty(Navigator.prototype, 'platform', {{ get: () => '{target_platform}', configurable: true }});
    Object.defineProperty(Navigator.prototype, 'deviceMemory', {{ get: () => 8, configurable: true }});
    Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {{ get: () => 8, configurable: true }});

    if (!window.chrome) {{
        window.chrome = {{
            app: {{ isInstalled: false, InstallState: {{ DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }}, RunningState: {{ CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' }} }},
            runtime: {{}}
        }};
    }}

    try {{
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = new Proxy(originalQuery, {{
            apply: (target, thisArg, args) => {{
                if (args && args[0] && args[0].name === 'notifications') return Promise.resolve({{ state: Notification.permission }});
                return Reflect.apply(target, thisArg, args);
            }}
        }});
    }} catch (e) {{}}

    try {{
        const getParameterProxyHandler = {{
            apply: function (target, thisArg, args) {{
                const param = args[0];
                if (param === 37445) return 'Google Inc. (Google)';
                if (param === 37446) return 'ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero)), SwiftShader driver)';
                return Reflect.apply(target, thisArg, args);
            }}
        }};
        ['WebGLRenderingContext', 'WebGL2RenderingContext'].forEach((ctx) => {{
            if (window[ctx] && window[ctx].prototype && window[ctx].prototype.getParameter) {{
                const original = window[ctx].prototype.getParameter;
                window[ctx].prototype.getParameter = new Proxy(original, getParameterProxyHandler);
            }}
        }});
    }} catch (e) {{}}

    try {{
        Object.defineProperty(Window.prototype, 'outerWidth', {{
            get: function() {{ return this.innerWidth; }},
            configurable: true
        }});
        Object.defineProperty(Window.prototype, 'outerHeight', {{
            get: function() {{ return this.innerHeight + 85; }},
            configurable: true
        }});
    }} catch (e) {{}}
    """


def _draw_cursor_on_jpeg(
    raw_bytes: bytes,
    mouse_x: Optional[int],
    mouse_y: Optional[int],
    vp_w: int,
    vp_h: int,
    clicking: bool = False,
) -> bytes:
    """Incruste le curseur (et un halo si clic) sur une frame JPEG brute."""
    if not raw_bytes or mouse_x is None or mouse_y is None:
        return raw_bytes
    img = Image.open(io.BytesIO(raw_bytes))
    scale_x = (img.width / vp_w) if vp_w > 0 else 1.0
    scale_y = (img.height / vp_h) if vp_h > 0 else 1.0
    cx = max(0, min(img.width - 1, int(round(mouse_x * scale_x))))
    cy = max(0, min(img.height - 1, int(round(mouse_y * scale_y))))
    draw = ImageDraw.Draw(img)
    if clicking:
        r_halo = 13
        draw.ellipse([cx - r_halo, cy - r_halo, cx + r_halo, cy + r_halo], outline="#ff0044", width=2)
    r = 8
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="#ff0044", outline="#ffffff", width=2)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=75)
    return buf.getvalue()


def _encode_clip_mp4_sync(
    frames_snapshot: list[tuple[float, bytes, Optional[int], Optional[int], bool]],
    vp_w: int,
    vp_h: int,
) -> tuple[bytes, int]:
    """Encode une sequence de frames JPEG annotees en MP4 H.264 via ffmpeg concat."""
    if len(frames_snapshot) < 2:
        return (b"", 0)
    durations: list[float] = []
    for i in range(len(frames_snapshot) - 1):
        dt = frames_snapshot[i + 1][0] - frames_snapshot[i][0]
        durations.append(max(CLIP_FRAME_MIN_DUR_SEC, min(CLIP_FRAME_MAX_DUR_SEC, dt)))
    durations.append(0.25)
    total_duration_ms = int(round(sum(durations) * 1000.0))

    try:
        with tempfile.TemporaryDirectory(prefix="echo_clip_") as tmpdir:
            lines: list[str] = []
            last_fname = ""
            for idx, (ts, raw_b, mx, my, clicking) in enumerate(frames_snapshot):
                fname = f"f_{idx:04d}.jpg"
                last_fname = fname
                fpath = os.path.join(tmpdir, fname)
                annotated = _draw_cursor_on_jpeg(raw_b, mx, my, vp_w, vp_h, clicking=clicking)
                with open(fpath, "wb") as jf:
                    jf.write(annotated)
                lines.append(f"file '{fname}'\n")
                lines.append(f"duration {durations[idx]:.4f}\n")
            lines.append(f"file '{last_fname}'\n")

            list_path = os.path.join(tmpdir, "frames.txt")
            with open(list_path, "w", encoding="utf-8") as lf:
                lf.writelines(lines)

            out_path = os.path.join(tmpdir, "clip.mp4")
            res = subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error",
                    "-f", "concat", "-safe", "0", "-i", list_path,
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", CLIP_FFMPEG_CRF,
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                    out_path,
                ],
                capture_output=True,
                timeout=CLIP_FFMPEG_TIMEOUT_SEC,
                check=False,
            )
            if res.returncode == 0 and os.path.exists(out_path):
                with open(out_path, "rb") as fh:
                    return (fh.read(), total_duration_ms)
            stderr_msg = res.stderr.decode("utf-8", errors="replace") if res.stderr else ""
            logger.warning(f"ffmpeg clip encoding failed (rc={res.returncode}): {stderr_msg}")
            return (b"", 0)
    except Exception as e:
        logger.warning(f"_encode_clip_mp4_sync error: {e}")
        return (b"", 0)


# --- SESSIONS ---
class BrowserSession:
    def __init__(self, sid, user_id, context, idle_timeout, mode="desktop", chat_id=None):
        self.sid = sid
        self.chat_id = chat_id or sid
        self.user_id = user_id
        self.context = context
        self.idle_timeout = idle_timeout
        self.mode = mode
        self.pages = []
        self.active_page_index = 0
        self.last_activity = time.time()
        self.cdp_client = None
        self.cdp_page = None
        self.action_lock = asyncio.Lock()
        self.frame_id = 0
        self.index_gen = 0
        self.new_tab_opened = False
        self.dom_selectors = {}
        self.dom_elements = {}
        self.last_found_selector = None
        self.raw_frame_bytes = None
        self.mouse_x = None
        self.mouse_y = None
        self.mouse_clicking = False
        self.viewport = None
        self.ws_subscribers: dict[str, dict[str, Any]] = {}
        self.recording_clip = False
        self.clip_frames: deque = deque(maxlen=CLIP_MAX_FRAMES)
        self._clip_start_ts = 0.0
        self._last_clip_sample_ts = 0.0
        self.latest_url = ""
        self.page_http_status: dict[int, dict[str, Any]] = {}

    def record_page_http_status(self, page, status: Optional[int], status_text: str = "", url: str = "") -> None:
        if page is None:
            return
        pid = id(page)
        cur_url = url or getattr(page, "url", "") or ""
        prev = self.page_http_status.get(pid)
        if status is None and prev and prev.get("url") == cur_url:
            return
        self.page_http_status[pid] = {
            "http_status": status,
            "http_status_text": status_text or "",
            "url": cur_url,
        }

    def get_page_http_status(self, page) -> dict[str, Any]:
        if page is None:
            return {}
        info = self.page_http_status.get(id(page))
        if not info or info.get("http_status") is None:
            return {}
        return {
            "http_status": info["http_status"],
            "http_status_text": info.get("http_status_text", ""),
        }

    def attach_page_response_listener(self, page) -> None:
        if page is None or getattr(page, "_echo_resp_tracked", False):
            return
        setattr(page, "_echo_resp_tracked", True)

        def _on_response(resp):
            try:
                if resp.request.is_navigation_request() and resp.frame == page.main_frame:
                    self.record_page_http_status(
                        page,
                        resp.status,
                        getattr(resp, "status_text", "") or "",
                        resp.url,
                    )
            except Exception:
                pass

        def _on_close(_closed_page=None):
            self.page_http_status.pop(id(page), None)

        page.on("response", _on_response)
        page.on("close", _on_close)

    def attach_page_tracking(self):
        def _on_page(new_page):
            self.attach_page_response_listener(new_page)
            if new_page not in self.pages:
                self.pages.append(new_page)
            self.active_page_index = self.pages.index(new_page)
            self.new_tab_opened = True
        self.context.on("page", _on_page)

    async def get_active_page(self):
        self.last_activity = time.time()
        if not self.pages:
            p = await self.context.new_page()
            self.attach_page_response_listener(p)
            if p not in self.pages:
                self.pages.append(p)
        if self.active_page_index >= len(self.pages):
            self.active_page_index = len(self.pages) - 1
        target = self.pages[self.active_page_index]
        if target.is_closed():
            self.pages.pop(self.active_page_index)
            return await self.get_active_page()
        self.attach_page_response_listener(target)
        return target

    def _get_viewport_dims(self) -> tuple[int, int]:
        if self.viewport:
            return (int(self.viewport.get("width", 1280)), int(self.viewport.get("height", 800)))
        return (1280, 800)

    def pack_binary_frame(self, raw_jpeg: bytes, frame_type: int = 1) -> bytes:
        vp_w, vp_h = self._get_viewport_dims()
        cx = max(0, min(65534, int(self.mouse_x))) if self.mouse_x is not None else 0xFFFF
        cy = max(0, min(65534, int(self.mouse_y))) if self.mouse_y is not None else 0xFFFF
        flags = 0x01 if self.mouse_clicking else 0x00
        header = LIVE_FRAME_HEADER.pack(
            frame_type & 0xFF,
            flags,
            max(1, min(65535, vp_w)),
            max(1, min(65535, vp_h)),
            cx,
            cy,
            self.frame_id & 0xFFFFFFFF,
            0,
        )
        return header + raw_jpeg

    @staticmethod
    def _push_ws_queue(q: asyncio.Queue, item: tuple[str, Any]) -> bool:
        try:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(item)
            return True
        except (asyncio.QueueFull, Exception):
            return False

    def _publish_ws_frame(self, raw_jpeg: bytes, frame_type: int = 1) -> None:
        if not self.ws_subscribers:
            return
        packet = self.pack_binary_frame(raw_jpeg, frame_type)
        for sub in list(self.ws_subscribers.values()):
            acked_id = sub.get("acked_id", 0)
            if sub.get("sent_id", 0) > acked_id and (self.frame_id - acked_id) > LIVE_MAX_IN_FLIGHT_FRAMES:
                continue
            if self._push_ws_queue(sub["queue"], ("bytes", packet)):
                sub["sent_id"] = self.frame_id

    def _publish_ws_json(self, event: dict[str, Any]) -> None:
        if not self.ws_subscribers:
            return
        payload_txt = json.dumps(event).decode("utf-8")
        for sub in list(self.ws_subscribers.values()):
            self._push_ws_queue(sub["queue"], ("text", payload_txt))

    def _record_clip_sample(self, raw_bytes: Optional[bytes], force: bool = False) -> None:
        now = time.monotonic()
        if not self.recording_clip or not raw_bytes:
            return
        if (now - self._clip_start_ts) > CLIP_MAX_DURATION_SEC:
            return
        if not force and (now - self._last_clip_sample_ts) < CLIP_SAMPLE_MIN_INTERVAL:
            return
        self.clip_frames.append((now, raw_bytes, self.mouse_x, self.mouse_y, self.mouse_clicking))
        self._last_clip_sample_ts = now

    def _notify_cursor(
        self,
        mouse_x: Optional[float],
        mouse_y: Optional[float],
        clicking: Optional[bool] = None,
    ) -> None:
        if mouse_x is not None and mouse_y is not None:
            self.mouse_x = int(round(mouse_x))
            self.mouse_y = int(round(mouse_y))
        if clicking is not None:
            self.mouse_clicking = bool(clicking)
        vp_w, vp_h = self._get_viewport_dims()
        if self.mouse_x is not None and self.mouse_y is not None:
            self._publish_ws_json({
                "type": "cursor",
                "session_id": self.sid,
                "x": self.mouse_x,
                "y": self.mouse_y,
                "vp_w": vp_w,
                "vp_h": vp_h,
                "clicking": self.mouse_clicking,
                "ts": time.time(),
            })
        self._record_clip_sample(self.raw_frame_bytes, force=bool(clicking))

    async def _detach_cdp(self) -> None:
        cdp = self.cdp_client
        self.cdp_client = None
        self.cdp_page = None
        if cdp:
            try:
                await cdp.send('Page.stopScreencast')
            except Exception:
                pass
            try:
                await cdp.detach()
            except Exception:
                pass

    async def ensure_screencast(self, page):
        if self.cdp_client and self.cdp_page is page:
            return
        if self.cdp_client:
            await self._detach_cdp()
        self.cdp_client = await page.context.new_cdp_session(page)
        self.cdp_page = page
        vp = page.viewport_size
        if vp:
            self.viewport = vp
        max_w = (vp["width"] // 2) if vp else 640
        max_h = (vp["height"] // 2) if vp else 400

        async def handle_frame(event):
            try:
                raw_b = base64.b64decode(event['data'])
                self.raw_frame_bytes = raw_b
                self.frame_id += 1
                current_url = page.url
                if current_url != self.latest_url:
                    self.latest_url = current_url
                    self._publish_ws_json({
                        "type": "state",
                        "session_id": self.sid,
                        "url": self.latest_url,
                        "status": "live",
                        "active": True,
                    })
                self._record_clip_sample(raw_b, force=True)
                self._publish_ws_frame(raw_b, frame_type=1)
            except Exception as e:
                logger.warning(f"[{self.sid}] handle_frame error: {e}")
            finally:
                if self.cdp_client:
                    try:
                        await self.cdp_client.send('Page.screencastFrameAck', {'sessionId': event['sessionId']})
                    except Exception:
                        pass

        self.cdp_client.on("Page.screencastFrame", handle_frame)
        await self.cdp_client.send('Page.startScreencast', {
            'format': 'jpeg', 'quality': 50, 'maxWidth': max_w, 'maxHeight': max_h, 'everyNthFrame': 1
        })

    async def resolve_indexed_locator(self, page, idx: int):
        try:
            idx_int = int(idx)
        except (ValueError, TypeError):
            idx_int = idx
        sel = self.dom_selectors.get(idx_int) or self.dom_selectors.get(str(idx))
        if sel:
            loc = await find_element_and_frame(page, sel)
            if loc:
                return loc
            try:
                cand = page.locator(sel)
                if await cand.count() > 0:
                    return await first_visible(cand)
            except Exception:
                pass
        # Fallback sur boîte englobante ou document.elementFromPoint
        el_meta = self.dom_elements.get(idx_int)
        if el_meta and "coords" in el_meta:
            coords = el_meta["coords"]
            cx = coords[0] + coords[2] / 2
            cy = coords[1] + coords[3] / 2
            try:
                fallback_sel = await page.evaluate(f"""([x, y]) => {{
                    {GET_CSS_PATH_JS}
                    const el = document.elementFromPoint(x, y);
                    return el ? getCssPath(el) : null;
                }}""", [cx, cy])
                if fallback_sel:
                    loc = await find_element_and_frame(page, fallback_sel)
                    if loc:
                        return loc
                    cand = page.locator(fallback_sel)
                    if await cand.count() > 0:
                        return await first_visible(cand)
            except Exception:
                pass
        return None

    async def bezier_mouse_move(self, page, target_x, target_y):
        import math
        try:
            start_x = self.mouse_x if self.mouse_x is not None else random.randint(100, 800)
            start_y = self.mouse_y if self.mouse_y is not None else random.randint(100, 600)
            distance = math.hypot(target_x - start_x, target_y - start_y)
            if distance < 5:
                await page.mouse.move(target_x, target_y)
                self._notify_cursor(target_x, target_y)
                return
            steps = max(5, min(15, int(distance / 60)))
            overshoot_x = target_x + random.uniform(-5, 5) if distance > 300 else target_x
            overshoot_y = target_y + random.uniform(-5, 5) if distance > 300 else target_y
            cp1_x = start_x + (overshoot_x - start_x) * 0.3 + random.uniform(-20, 20)
            cp1_y = start_y + (overshoot_y - start_y) * 0.3 + random.uniform(-20, 20)
            cp2_x = start_x + (overshoot_x - start_x) * 0.7 + random.uniform(-20, 20)
            cp2_y = start_y + (overshoot_y - start_y) * 0.7 + random.uniform(-20, 20)

            def ease_out_quad(t):
                return t * (2 - t)

            for i in range(1, steps + 1):
                t = i / steps
                et = ease_out_quad(t)
                x = (1 - et)**3 * start_x + 3 * (1 - et)**2 * et * cp1_x + 3 * (1 - et) * et**2 * cp2_x + et**3 * overshoot_x
                y = (1 - et)**3 * start_y + 3 * (1 - et)**2 * et * cp1_y + 3 * (1 - et) * et**2 * cp2_y + et**3 * overshoot_y
                x += random.uniform(-1, 1)
                y += random.uniform(-1, 1)
                await page.mouse.move(x, y)
                self._notify_cursor(x, y)
                await asyncio.sleep(random.uniform(0.001, 0.005))
            if distance > 300:
                await asyncio.sleep(random.uniform(0.05, 0.15))
                await page.mouse.move(target_x, target_y)
                self._notify_cursor(target_x, target_y)
            else:
                self._notify_cursor(target_x, target_y)
        except Exception as e:
            logger.warning(f"[{self.sid}] Bezier Fallback: {e}")
            await page.mouse.move(target_x, target_y)
            self._notify_cursor(target_x, target_y)

    async def pick_hit_point(self, locator, box: dict) -> tuple:
        fractions = [(0.5 + random.uniform(-0.15, 0.15), 0.5 + random.uniform(-0.15, 0.15)),
                     (0.5, 0.5), (0.3, 0.5), (0.7, 0.5), (0.5, 0.3), (0.5, 0.7)]
        try:
            hits = await locator.evaluate(HIT_TEST_JS, fractions)
        except Exception:
            hits = [False] * len(fractions)
        for (fx, fy), ok in zip(fractions, hits):
            if ok:
                return box["x"] + fx * box["width"], box["y"] + fy * box["height"], True
        return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, False

    async def move_mouse_to_locator(self, page, locator) -> bool:
        try:
            await locator.scroll_into_view_if_needed(timeout=5000)
            await wait_for_settle(page, quiet_ms=150, timeout_ms=1500)
            box = await locator.bounding_box()
            if not box:
                return False
            tx, ty, hit = await self.pick_hit_point(locator, box)
            await self.bezier_mouse_move(page, tx, ty)
            
            box_final = await locator.bounding_box()
            if box_final and (abs(box_final["x"] - box["x"]) > 1 or abs(box_final["y"] - box["y"]) > 1):
                tx, ty, hit = await self.pick_hit_point(locator, box_final)
                await page.mouse.move(tx, ty)
                self._notify_cursor(tx, ty)
                
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
            await self._detach_cdp()
            self._publish_ws_json({"type": "eof", "session_id": self.sid})
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
            "--enable-webgl", "--ignore-gpu-blocklist", "--use-gl=angle", "--use-angle=swiftshader",
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


app = FastAPI(lifespan=lifespan)
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
                if s.cdp_client and not s.ws_subscribers and not s.recording_clip:
                    logger.info(f"[{sid}] 🛑 Watchdog: Stopping idle screencast")
                    await s._detach_cdp()


def _norm_echo_id(raw: Optional[str]) -> str:
    return "".join(x for x in str(raw or "") if x.isalnum() or x in "-_")


@app.get("/sessions")
async def list_active_sessions(user_id: Optional[str] = None, chat_id: Optional[str] = None) -> dict[str, Any]:
    """Retourne les identifiants des sessions Playwright actives (filtrés optionnellement par user_id/chat_id)."""
    active_sids: list[str] = []
    req_uid_norm = _norm_echo_id(user_id) if user_id else ""
    req_cid_norm = _norm_echo_id(chat_id) if chat_id else ""
    async with SESSIONS_LOCK:
        for sid, s in SESSIONS.items():
            s_uid = getattr(s, "user_id", None)
            if user_id and s_uid not in (user_id, "anonymous", None) and _norm_echo_id(s_uid) != req_uid_norm:
                continue
            s_cid = getattr(s, "chat_id", None)
            if (
                chat_id
                and s_cid != chat_id
                and sid != chat_id
                and _norm_echo_id(s_cid) != req_cid_norm
                and _norm_echo_id(sid) != req_cid_norm
            ):
                continue
            active_sids.append(sid)
    return {"status": "success", "active_sessions": active_sids}


@app.post("/start_session")
async def start_session(request: Request) -> dict[str, Any]:
    data = await request.json()
    user_id = request.headers.get('X-OpenWebUI-User-Id', 'anonymous')
    sid = data.get("session_id")
    chat_id = data.get("chat_id") or sid
    idle_timeout = data.get("idle_timeout", IDLE_TIMEOUT_DEFAULT)
    mode = data.get("mode", "mobile")
    if not sid:
        return {"status": "error", "message": "ERREUR_TECHNIQUE : Identifiant de chat manquant."}

    async with SESSIONS_LOCK:
        if sid in SESSIONS:
            if getattr(SESSIONS[sid], 'mode', None) == mode:
                SESSIONS[sid].last_activity = time.time()
                if chat_id:
                    SESSIONS[sid].chat_id = chat_id
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
        
        browser_ver = state.browser.version if state.browser else "130.0.0.0"
        ctx_args = build_stealth_context_args(mode, browser_ver)
        context = await state.browser.new_context(**ctx_args)
        stealth_script = build_stealth_init_script(mode)
        await context.add_init_script(stealth_script)
        
        session = BrowserSession(sid, user_id, context, idle_timeout, mode, chat_id=chat_id)
        session.viewport = ctx_args.get("viewport")
        session.attach_page_tracking()
        SESSIONS[sid] = session
        logger.info(f"[{sid}] 🆕 v6 Session created (Mode: {mode}, Chat: {chat_id})")
        return {"session_id": sid, "status": "success"}


async def find_element_and_frame(page, selector):
    for frame in page.frames:
        try:
            loc = frame.locator(selector)
            if await loc.count() > 0:
                return await first_visible(loc)
        except Exception:
            pass
    return None


@app.post("/screencast/start")
async def screencast_start(request: Request) -> dict[str, Any]:
    data = await request.json()
    sid = data.get("session_id")
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "message": "Session introuvable."}
    page = await session.get_active_page()
    if not page:
        return {"status": "error", "message": "Aucune page active."}
    try:
        session.clip_frames.clear()
        session._clip_start_ts = time.monotonic()
        session._last_clip_sample_ts = 0.0
        session.recording_clip = True
        await session.ensure_screencast(page)
        if session.raw_frame_bytes:
            session._record_clip_sample(session.raw_frame_bytes, force=True)
        return {"status": "success"}
    except Exception as e:
        logger.error(f"[{sid}] Screencast start error: {e}")
        return {"status": "error", "error": str(e)}


@app.post("/screencast/stop")
async def screencast_stop(request: Request) -> dict[str, Any]:
    data = await request.json()
    sid = data.get("session_id")
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "message": "Session introuvable."}
    session.recording_clip = False
    snapshot = list(session.clip_frames)
    session.clip_frames.clear()
    mp4_bytes = b""
    duration_ms = 0
    try:
        if len(snapshot) >= 2:
            vp_w, vp_h = session._get_viewport_dims()
            mp4_bytes, duration_ms = await asyncio.to_thread(_encode_clip_mp4_sync, snapshot, vp_w, vp_h)
    except Exception as e:
        logger.error(f"[{sid}] Screencast stop error: {e}")
    finally:
        if not session.ws_subscribers:
            await session._detach_cdp()
    clip_b64 = base64.b64encode(mp4_bytes).decode("utf-8") if mp4_bytes else ""
    return {
        "status": "success",
        "clip_b64": clip_b64,
        "clip_mime": "video/mp4",
        "clip_duration_ms": duration_ms if mp4_bytes else 0,
        "clip_size_bytes": len(mp4_bytes),
        "clip_frames_count": len(snapshot),
    }


@app.websocket("/ws/screencast/{session_id}")
async def ws_screencast(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = SESSIONS.get(session_id)
    if not session:
        try:
            await websocket.send_text(json.dumps({"type": "eof", "session_id": session_id}).decode("utf-8"))
            await websocket.close(code=1000)
        except Exception:
            pass
        return

    sub_id = uuid.uuid4().hex
    sub: dict[str, Any] = {
        "queue": asyncio.Queue(maxsize=LIVE_QUEUE_MAXSIZE),
        "sent_id": session.frame_id,
        "acked_id": session.frame_id,
    }
    session.ws_subscribers[sub_id] = sub
    send_lock = asyncio.Lock()

    try:
        page = await session.get_active_page()
        if page:
            await session.ensure_screencast(page)

        init_state = {
            "type": "state",
            "session_id": session.sid,
            "url": session.latest_url or (page.url if page else ""),
            "status": "live",
            "active": True,
        }
        async with send_lock:
            await websocket.send_text(json.dumps(init_state).decode("utf-8"))
            if session.raw_frame_bytes:
                await websocket.send_bytes(session.pack_binary_frame(session.raw_frame_bytes, frame_type=1))

        async def _sender() -> None:
            q: asyncio.Queue = sub["queue"]
            while True:
                try:
                    kind, payload = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    async with send_lock:
                        await websocket.send_text(json.dumps({"type": "ping"}).decode("utf-8"))
                    continue
                async with send_lock:
                    if kind == "bytes":
                        await websocket.send_bytes(payload)
                    else:
                        await websocket.send_text(payload)
                        try:
                            if json.loads(payload).get("type") == "eof":
                                return
                        except Exception:
                            pass

        async def _receiver() -> None:
            while True:
                raw_msg = await websocket.receive_text()
                try:
                    msg = json.loads(raw_msg)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                msg_type = msg.get("type")
                if msg_type == "ack":
                    try:
                        ack_id = int(msg.get("frame_id", 0))
                    except (TypeError, ValueError):
                        ack_id = 0
                    sub["acked_id"] = max(sub["acked_id"], ack_id)
                elif msg_type == "ping":
                    async with send_lock:
                        await websocket.send_text(json.dumps({"type": "pong"}).decode("utf-8"))

        sender_task = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())
        try:
            done, pending = await asyncio.wait(
                {sender_task, receiver_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in pending:
                try:
                    await task
                except (asyncio.CancelledError, WebSocketDisconnect, Exception):
                    pass
            for task in done:
                try:
                    await task
                except (asyncio.CancelledError, WebSocketDisconnect):
                    pass
        finally:
            for task in (sender_task, receiver_task):
                if not task.done():
                    task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning(f"[{session_id}] WebSocket screencast error: {e}")
    finally:
        session.ws_subscribers.pop(sub_id, None)
        if not session.ws_subscribers and not session.recording_clip:
            await session._detach_cdp()


@app.post("/action")
async def browser_action(request: Request) -> dict[str, Any]:
    data = await request.json()
    sid, action, params = data.get("session_id"), data.get("action"), data.get("params", {})
    chat_id = data.get("chat_id")
    session = SESSIONS.get(sid)
    if not session:
        return {"status": "error", "error_type": "SESSION_NOT_FOUND", "message": "RESTART_REQUIRED"}
    if chat_id:
        session.chat_id = chat_id
    session.last_activity = time.time()

    try:
        async with session.action_lock:
            page = await session.get_active_page()
            result = {"status": "success"}

            if action == "interact_a11y":
                method, value, a_type = params.get("method"), params.get("value"), params.get("action_type")
                name, text_to_type = params.get("name"), params.get("text_to_type", "")
                logger.info(f"[{sid}] 🖱️ Semantic Interact (A11y): {method}={value} name={name} ({a_type})")
                
                if method == "role":
                    loc = page.get_by_role(value, name=name) if name else page.get_by_role(value)
                elif method == "label":
                    loc = page.get_by_label(value)
                elif method == "text":
                    loc = page.get_by_text(value)
                else:
                    return {"status": "error", "message": "Method invalide."}
                
                if await loc.count() == 0:
                    if method == "role" and name:
                        logger.warning(f"[{sid}] Role+Name failed, trying Text fallback for: {name}")
                        loc = page.get_by_text(name)
                        if await loc.count() == 0:
                            return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value}, name={name})."}
                    else:
                        return {"status": "error", "message": f"ERREUR_DOM : Élément introuvable ({method}={value})."}
                
                loc = await first_visible(loc)
                hit = await session.move_mouse_to_locator(page, loc)
                await asyncio.sleep(random.uniform(0.15, 0.4))
                
                if a_type == "click":
                    if hit:
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=True)
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=False)
                    else:
                        try:
                            await loc.click(timeout=4000)
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
                        if random.random() < 0.10:
                            await asyncio.sleep(random.uniform(0.2, 0.6))
                elif a_type == "select":
                    option = text_to_type or ""
                    try:
                        await loc.select_option(label=option, timeout=5000)
                    except Exception:
                        await loc.select_option(value=option, timeout=5000)
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
                            dl_dir = os.path.join("/app/downloads", session.user_id, session.chat_id, "browser")
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
                            dl_dir = os.path.join("/app/downloads", session.user_id, session.chat_id, "browser")
                            os.makedirs(dl_dir, exist_ok=True)
                            filename = target_url.split("/")[-1].split("?")[0]
                            if not filename:
                                filename = "downloaded_file"
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
                
                await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
                result["url"] = page.url

            elif action == "interact_dom":
                a_type = params.get("action_type")
                if a_type == "click_current":
                    logger.info(f"[{sid}] 🖱️ Interact DOM (click_current) on spot")
                    session._notify_cursor(session.mouse_x, session.mouse_y, clicking=True)
                    await page.mouse.down()
                    await asyncio.sleep(random.uniform(0.05, 0.12))
                    await page.mouse.up()
                    session._notify_cursor(session.mouse_x, session.mouse_y, clicking=False)
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
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=True)
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=False)
                        await asyncio.sleep(random.uniform(0.05, 0.15))
                        await session.bezier_mouse_move(
                            page,
                            max(0, css_x + random.uniform(2, 5) * random.choice([1, -1])),
                            max(0, css_y + random.uniform(2, 5) * random.choice([1, -1]))
                        )
                    elif a_type == "type":
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=True)
                        await page.mouse.down()
                        await asyncio.sleep(random.uniform(0.05, 0.12))
                        await page.mouse.up()
                        session._notify_cursor(session.mouse_x, session.mouse_y, clicking=False)
                        if params.get("clear_before", True):
                            await page.keyboard.press("Control+A")
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await page.keyboard.press("Backspace")
                        for char in text_to_type:
                            await page.keyboard.press(char)
                            delay_ms = max(30, min(150, int(random.gauss(80, 40))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10:
                                await asyncio.sleep(random.uniform(0.2, 0.6))
                    elif a_type == "select":
                        return {"status": "error", "message": "ERREUR_PARAMETRE : select exige un index ou une cible A11y."}
                else:
                    loc = await session.resolve_indexed_locator(page, idx)
                    if not loc:
                        return {"status": "error", "message": f"ERREUR_DOM : Élément index {idx} introuvable dans aucune frame."}
                    sel = session.dom_selectors.get(idx) or session.dom_selectors.get(int(idx) if str(idx).isdigit() else idx) or f"index_{idx}"
                    logger.info(f"[{sid}] 🖱️ Interact DOM ({a_type}) Target: index {idx} -> {sel}")
                    
                    hit = await session.move_mouse_to_locator(page, loc)
                    await asyncio.sleep(random.uniform(0.15, 0.4))
                
                    if a_type == "click":
                        if hit:
                            session._notify_cursor(session.mouse_x, session.mouse_y, clicking=True)
                            await page.mouse.down()
                            await asyncio.sleep(random.uniform(0.05, 0.12))
                            await page.mouse.up()
                            session._notify_cursor(session.mouse_x, session.mouse_y, clicking=False)
                        else:
                            try:
                                await loc.click(timeout=4000)
                            except Exception as e:
                                logger.warning(f"[{sid}] Manual click failed, trying force: {e}")
                                await loc.click(force=True, timeout=5000)
                            result["warning"] = "Point d'impact non vérifié (élément possiblement masqué) : contrôler l'effet du clic."
                        result["hit_verified"] = hit
                    elif a_type == "hover":
                        await loc.hover(timeout=10000)
                    elif a_type == "select":
                        option = text_to_type or ""
                        try:
                            await loc.select_option(label=option, timeout=5000)
                        except Exception:
                            await loc.select_option(value=option, timeout=5000)
                        result["value"] = option
                    elif a_type == "download":
                        async def handle_download():
                            try:
                                file_id = params.get("download_file_id", f"DL_{int(time.time())}")
                                async with page.expect_download(timeout=120000) as download_info:
                                    await loc.click()
                                download = await download_info.value
                                dl_dir = os.path.join("/app/downloads", session.user_id, session.chat_id, "browser")
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
                                dl_dir = os.path.join("/app/downloads", session.user_id, session.chat_id, "browser")
                                os.makedirs(dl_dir, exist_ok=True)
                                filename = target_url.split("/")[-1].split("?")[0]
                                if not filename:
                                    filename = "downloaded_file"
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
                        if params.get("clear_before", True):
                            await page.keyboard.press("Control+A")
                            await asyncio.sleep(random.uniform(0.05, 0.15))
                            await page.keyboard.press("Backspace")
                        for char in text_to_type:
                            await loc.press_sequentially(char)
                            delay_ms = max(30, min(150, int(random.gauss(80, 40))))
                            await asyncio.sleep(delay_ms / 1000.0)
                            if random.random() < 0.10:
                                await asyncio.sleep(random.uniform(0.2, 0.6))
                        
                await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
                result["url"] = page.url

            elif action == "inspect_page":
                target = params.get("target")
                logger.info(f"[{sid}] 🔍 Inspect Page: {target}")
            
                if target == "url":
                    idx = params.get("index")
                    if idx is None:
                        return {"status": "error", "message": "Index manquant pour extraire l'URL."}
                    try:
                        idx_int = int(idx)
                    except (ValueError, TypeError):
                        idx_int = idx
                    sel = session.dom_selectors.get(idx_int) or session.dom_selectors.get(str(idx))
                    if not sel:
                        return {"status": "error", "message": f"Sélecteur introuvable pour l'index {idx}."}
                    val = await page.evaluate(
                        "(sel) => { const el = document.querySelector(sel); return el ? (el.href || el.getAttribute('href')) : null; }",
                        sel
                    )
                    result["value"] = val
                    result["url"] = page.url
                
                elif target == "search_dom":
                    res = await page.evaluate(
                        SEARCH_DOM_JS,
                        {"query": params.get("value", ""), "interactive": INTERACTIVE_SELECTORS}
                    )
                    session.last_found_selector = res.get("css_path")
                    if res.get("found") and abs(res.get("delta_y", 0)) > (page.viewport_size or {"height": 800})["height"] * 0.3:
                        await session.human_wheel_scroll(page, res["delta_y"])
                        await wait_for_settle(page, quiet_ms=250, timeout_ms=1500)
                        found_sel = session.last_found_selector
                        if found_sel and not await page.evaluate(FOUND_IN_VIEW_JS, found_sel):
                            await page.evaluate(
                                "(sel) => document.querySelector(sel)?.scrollIntoView({block: 'center'})",
                                found_sel
                            )
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
                            if not n:
                                return []
                            lines = []
                            ignored = n.get("ignored", False)
                            if not ignored:
                                role = n.get("role", {}).get("value", "")
                                name = n.get("name", {}).get("value", "")
                                value = n.get("value", {}).get("value", "")
                                if role == "StaticText" and not name and not value:
                                    return lines
                                if role in ["generic", "RootWebArea", "WebArea"] and not name and not value:
                                    role = ""
                                if role or name or value:
                                    line = "  " * depth + f"[{role}] {name}"
                                    if value:
                                        line += f" (val: {value})"
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
                        try:
                            await client.detach()
                        except Exception:
                            pass
                    result["url"] = page.url

                elif target in ["vision", "dom_map"]:
                    vision_grid = params.get("vision_grid", False)
                    vision_grid_step = int(params.get("vision_grid_step", 100))
                    zoom_box = params.get("zoom_box")
                    await page.bring_to_front()
                    await wait_for_settle(page, quiet_ms=300, timeout_ms=1500)
                    vp = page.viewport_size or {"width": 1280, "height": 800}
                    session.viewport = vp
                    clean_bytes = await jpeg_capped(lambda q: page.screenshot(type="jpeg", quality=q, scale="css"))
                    clean_b64 = base64.b64encode(clean_bytes).decode('utf-8')
                
                    all_elements = []
                    if params.get("reindex", True):
                        session.index_gen += 1
                        all_elements, selectors = await collect_dom_map(
                            page, session.index_gen, vp, found_sel=session.last_found_selector
                        )
                        session.dom_selectors = selectors
                        session.dom_elements = {
                            int(el["id"].split(":")[1]): el
                            for el in all_elements
                            if "id" in el and ":" in el["id"]
                        }

                    mouse = [int(session.mouse_x), int(session.mouse_y)] if session.mouse_x is not None and session.mouse_y is not None else None
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

                http_info = session.get_page_http_status(page)
                if http_info:
                    result.update(http_info)

            elif action == "browser_control":
                cmd = params.get("command")
                val = params.get("value")
                logger.info(f"[{sid}] ⚙️ Browser Control: {cmd} {val or ''}")
            
                if cmd == "navigate":
                    if not val:
                        return {"status": "error", "message": "URL manquante."}
                    session.attach_page_response_listener(page)
                    nav_warn, http_status, http_status_text = await _goto_resilient(page, val, timeout_ms=25000)
                    session.record_page_http_status(page, http_status, http_status_text, page.url)
                    if http_status is None:
                        info = session.get_page_http_status(page)
                        http_status = info.get("http_status")
                        http_status_text = info.get("http_status_text", "")
                    result["title"], result["url"] = await page.title(), page.url
                    if http_status is not None:
                        result["http_status"] = http_status
                        result["http_status_text"] = http_status_text
                    if nav_warn:
                        result["warning"] = nav_warn
                    elif http_status is not None and http_status >= 400:
                        result["warning"] = f"Réponse HTTP {http_status} {http_status_text}".strip()
                elif cmd == "scroll":
                    if val in ("down", "up"):
                        before = await page.evaluate("() => window.scrollY")
                        dy = await page.evaluate("() => window.innerHeight * 0.8") * (1 if val == "down" else -1)
                        await session.human_wheel_scroll(page, dy)
                        await wait_for_settle(page, quiet_ms=250, timeout_ms=1500)
                        if await page.evaluate("() => window.scrollY") == before:
                            await page.evaluate("(d) => window.scrollBy(0, d)", dy)
                    elif val == "top":
                        await page.evaluate("window.scrollTo(0, 0)")
                    elif val == "bottom":
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
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
                    session.attach_page_response_listener(page)
                    resp = None
                    try:
                        resp = await page.reload(wait_until="domcontentloaded", timeout=20000)
                    except PlaywrightTimeoutError:
                        if not page.url or page.url == "about:blank":
                            raise
                    await wait_for_settle(page, quiet_ms=400, timeout_ms=3000)
                    http_status = getattr(resp, "status", None) if resp is not None else None
                    http_status_text = (getattr(resp, "status_text", "") or "") if resp is not None else ""
                    session.record_page_http_status(page, http_status, http_status_text, page.url)
                    if http_status is None:
                        info = session.get_page_http_status(page)
                        http_status = info.get("http_status")
                        http_status_text = info.get("http_status_text", "")
                    result["url"] = page.url
                    if http_status is not None:
                        result["http_status"] = http_status
                        result["http_status_text"] = http_status_text
                        if http_status >= 400 and "warning" not in result:
                            result["warning"] = f"Réponse HTTP {http_status} {http_status_text}".strip()
                elif cmd == "reset":
                    async with SESSIONS_LOCK:
                        if sid in SESSIONS:
                            old_s = SESSIONS.pop(sid)
                            await old_s.close()
                    result["message"] = "Session réinitialisée."
                elif cmd == "tab_new":
                    new_p = await session.context.new_page()
                    session.attach_page_response_listener(new_p)
                    nav_warn, http_status, http_status_text = await _goto_resilient(new_p, val or "about:blank", timeout_ms=25000)
                    session.record_page_http_status(new_p, http_status, http_status_text, new_p.url)
                    if http_status is None:
                        info = session.get_page_http_status(new_p)
                        http_status = info.get("http_status")
                        http_status_text = info.get("http_status_text", "")
                    if new_p not in session.pages:
                        session.pages.append(new_p)
                    session.active_page_index = len(session.pages) - 1
                    result["url"] = new_p.url
                    result["message"] = f"Nouvel onglet ouvert (Index: {session.active_page_index})"
                    if http_status is not None:
                        result["http_status"] = http_status
                        result["http_status_text"] = http_status_text
                    if nav_warn:
                        result["warning"] = nav_warn
                    elif http_status is not None and http_status >= 400:
                        result["warning"] = f"Réponse HTTP {http_status} {http_status_text}".strip()
                elif cmd == "tab_switch":
                    idx = int(val or 0)
                    if 0 <= idx < len(session.pages):
                        session.active_page_index = idx
                        result["message"] = f"Basculé sur l'onglet {idx}"
                    else:
                        return {"status": "error", "message": "Index invalide."}
                elif cmd == "tab_close":
                    if len(session.pages) > 1:
                        p = session.pages.pop(session.active_page_index)
                        await p.close()
                        session.active_page_index = max(0, session.active_page_index - 1)
                        result["message"] = "Onglet fermé."
                    else:
                        return {"status": "error", "message": "Impossible de fermer le dernier onglet."}

            if session.new_tab_opened:
                page = await session.get_active_page()
                await wait_for_settle(page)
                result["message"] = f"Nouvel onglet ouvert et activé (index {session.active_page_index})."
                session.new_tab_opened = False

            return result

    except Exception as e:
        logger.error(f"[{sid}] 💥 Action Error: {str(e)}")
        return {"status": "error", "message": f"ERREUR_CRITIQUE : {str(e)}"}


@app.post("/render_ephemeral_view")
async def render_ephemeral_view(request: Request) -> dict[str, Any]:
    """Rendu HTML éphémère en contexte incognito isolé (100 % RAM, Zéro-Trace, Zéro-Disque)."""
    if not state.browser:
        return {"status": "error", "ok": False, "error": "Navigateur Playwright non initialisé."}
    data = await request.json()
    html_content = data.get("html") or ""
    selector = data.get("selector") or "#visual-target, #visual-target-container, #celestial-map, .echo-container"
    timeout_ms = int(data.get("timeout_ms") or 15000)
    settle_ms = int(data.get("settle_ms") or 250)
    viewport_w = int(data.get("viewport_w") or 1200)
    viewport_h = int(data.get("viewport_h") or 800)

    if not html_content.strip():
        return {"status": "error", "ok": False, "error": "Payload HTML vide."}

    context = await state.browser.new_context(
        viewport={"width": viewport_w, "height": viewport_h},
        device_scale_factor=1,
    )
    js_errors = []
    try:
        page = await context.new_page()
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))
        page.on(
            "console",
            lambda msg: js_errors.append(msg.text) if msg.type == "error" else None,
        )
        await page.set_content(html_content, wait_until="load", timeout=timeout_ms)
        max_wait_ms = max(settle_ms, 2000)
        elapsed_ms = 0
        while elapsed_ms < max_wait_ms:
            await asyncio.sleep(0.1)
            elapsed_ms += 100
            if elapsed_ms < settle_ms:
                continue
            is_ready = await page.evaluate(
                """() => {
                    if (document.body && document.body.getAttribute('data-echo-visual-error')) return true;
                    const el = document.querySelector('#visual-target, #visual-target-container, #celestial-map');
                    if (!el) return true;
                    const txt = (el.innerText || el.textContent || '').trim();
                    if (txt.includes('⚠️')) return true;
                    if (txt.startsWith('Chargement')) return false;
                    if (el.tagName === 'SVG' && el.children.length > 0) return true;
                    if (el.querySelector('svg, canvas, .tl-timeline, a-scene, .leaflet-pane')) return true;
                    return el.children.length > 0;
                }"""
            )
            if is_ready:
                break

        dom_error = await page.evaluate(
            """() => {
                const attrErr = (document.body && document.body.getAttribute('data-echo-visual-error')) || '';
                const el = document.querySelector('#visual-target, #visual-target-container, #celestial-map');
                const txt = el ? (el.innerText || el.textContent || '').trim() : '';
                const warnTxt = txt.includes('⚠️') ? txt : '';
                if (attrErr && warnTxt && !attrErr.includes(warnTxt)) return attrErr + ' | ' + warnTxt;
                return attrErr || warnTxt;
            }"""
        )
        target = page.locator(selector).first
        if await target.count() > 0 and await target.is_visible():
            png_bytes = await target.screenshot(type="png", timeout=5000)
        else:
            png_bytes = await page.screenshot(type="png", timeout=5000)

        return {
            "status": "success",
            "ok": True,
            "b64": base64.b64encode(png_bytes).decode("utf-8"),
            "mime": "image/png",
            "js_errors": js_errors,
            "dom_error": dom_error,
        }
    except Exception as e:
        return {
            "status": "error",
            "ok": False,
            "error": str(e),
            "js_errors": js_errors,
        }
    finally:
        try:
            await context.close()
        except Exception:
            pass


@app.get("/health")
async def health() -> dict[str, Any]:
    return {"status": "ready", "browser": state.browser is not None}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5002)
