import httpx
import json
import asyncio
import base64
import os
import time
import sys
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

"""
================================================================================
TOOL : ECHO NAVIGATION ENGINE (v6.68 - VAULT REDIRECTION)
VERSION : 6.68
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-03-23

CHANGELOG 6.68 :
- FEAT: Direct Vault storage for screenshots (security and isolation). Removed UPLOADS_DIR dependency for frames.
CHANGELOG 6.67 :
- FEAT: Enriched docstrings with parameters for better model understanding.
CHANGELOG 6.66 :
- FIX: Corrected magnifier (lens) alignment issue caused by object-fit: contain rendering.
CHANGELOG 6.65 :
- FEAT: Added 'get_web_object_url' tool to extract absolute URLs from DOM elements.
- REFACTOR: Using 'generate_echo_file_id' for standardized IDs.
CHANGELOG 6.64 :
- FIX: Switched MIME type to 'text/plain' for Base64 HTML to satisfy Gemini API constraints.
CHANGELOG 6.63 :
- FEAT: Support for Base64 encapsulated HTML via echo_tool_multiparts.
CHANGELOG 6.62 :
- FEAT: Added 'nouveaux_fichiers' support in get_browser_frames_history for Suture persistence.
CHANGELOG 6.61 :
- FIX: Restored UserValves visibility in Open WebUI interface.
- FIX: Aligned class structure for proper valve detection.
CHANGELOG 6.60 :
- FEAT: Added 'web_browse_reset' tool to hard restart browser engine.
- PERF: Full in-memory image pipeline (no disk overhead).
CHANGELOG 6.56 :
- FIX: Harmonized translate3d strings and robust regex for Crop Box stability.
- PERF: Optimized storage access in update loop.
CHANGELOG 6.55 :
- FIX: Perfect centering at startup (center of HUD at center of screen).
- FIX: Restored Double-Click Reset functionality (25% surface + centering).
- FIX: Robust event re-attachment and DOM cleanup.
CHANGELOG 6.54 :
- FIX: Radical fix for slowness (Interval leak) via strict single-engine persistent architecture.
- FIX: Corrected Timer display and filename export (parentheses and concatenation).
- FIX: Optimized rendering loop (only data update after first injection).
CHANGELOG 6.50 :
- PERF: Hardware Accelerated Rendering (GPU) via translate3d for HUD, Lens, and Crop.
- PERF: Optimized Data Flow (Single-Injection Architecture) - parse logic once, update data only.
- FIX: Corrected export filenames and copy fallback logic.
CHANGELOG 6.40 :
- FIX: Crop handles now white with black border.
- FIX: Default crop selection is now the full image.
- FIX: Added fallback for Copy tool in non-secure (HTTP) contexts.
CHANGELOG 6.39 :
- FEAT: Bidirectional crop adjustment (added Top, Bottom, Left, Right handles).
CHANGELOG 6.38 :
- FEAT: Added Advanced Media Toolset: Select/Crop (⛶), Copy (❐), and Download (📥).
- FEAT: Intelligent export (exports selection if active, otherwise full image).
CHANGELOG 6.37 :
- FEAT: Loupe (magnifier) can now overlap HUD borders (unclipped) for better edge visibility.
CHANGELOG 6.36 :
- PERF: Fixed drag/resize lag by disabling transitions during manual interactions.
- PERF: Added hardware acceleration (will-change).
CHANGELOG 6.35 :
- FIX: Improved positioning stability by using async saveState (350ms delay) after transitions.
- FIX: Refined_clampHud margins using distinct horizontal/vertical logic.
CHANGELOG 6.34 :
- FIX: Loupe (magnifier) fix by unifying state on the HUD element.
CHANGELOG 6.33 :
- FEAT: Fully compliant HUD functional specifications (25% start surface, 97% screen limit, persistence).
- FIX: Improved transitions and state restoration (minimized, fullscreen Option A).
CHANGELOG 6.32 :
- FIX: Robust HUD state persistence using chat-specific keys and immediate restoration.
- FIX: Forced 'right: auto' and immediate minimized state application.
CHANGELOG 6.31 :
- FIX: Improved HUD state persistence (size, position, minimized, full-screen) across frames.
CHANGELOG 6.30 :
- FEAT: Implemented 'get_browser_frames_history' (Visual Memory).
- FEAT: Systematic indexing of frames in real-time.
- REM: Removed legacy 'get_visual_snapshot'.
================================================================================
"""

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager, generate_echo_file_id
from echo_constants import ECHO_UPLOADS_DIR

# --- FONCTIONS UTILITAIRES PRIVÉES ---

async def _req(valves: Any, endpoint: str, data: dict = None, user_id: str = "anonymous") -> dict:
    url = f"{valves.AGENT_URL}{endpoint}"
    headers = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": str(user_id)}
    try:
        async with httpx.AsyncClient(timeout=valves.HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=data or {}, headers=headers)
            return resp.json()
    except Exception as e:
        return {"status": "error", "message": f"Worker inaccessible : {str(e)}"}

async def _verify_engine_status(valves: Any, chat_id: str, user_id: str, u_valves: Any, events: EchoEvents) -> bool:
    res = await _req(valves, "/action", {"session_id": chat_id, "action": "ping"}, user_id)
    if res.get("message") == "RESTART_REQUIRED" or res.get("error_type") == "SESSION_NOT_FOUND":
        await events.status("🌐 Moteur de navigation initialisation...", done=False)
        start_res = await _req(valves, "/start_session", {
            "session_id": chat_id,
            "idle_timeout": valves.IDLE_TIMEOUT,
            "mode": u_valves.BROWSER_MODE
        }, user_id)
        return start_res.get("status") == "success"
    return True

def _generate_monitor_js(b64: str, sid: str, chat_id: str, timeout: int) -> str:
    """Moteur JS Cockpit v3.5 : Stabilité Totale et Cohérence Graphique."""
    return f"""
    (function() {{
        const HUD_ID = 'echo-browser-monitor';
        const STATE_KEY = 'echo_state_{chat_id}';
        const payload = {{ b64: "{b64}", sid: "{sid}", cid: "{chat_id}", timeout: {timeout} }};

        if (!window.echoHudEngine) {{
            window.echoHudEngine = {{
                hud: null, isCropping: false, zoomActive: false, ratio: 1.44, posX: 0, posY: 0, 
                timeLeft: 0, timerInt: null,

                getBestSize: function(ratio, percent = 0.25) {{
                    const vw = window.innerWidth, vh = window.innerHeight;
                    let w = Math.sqrt(percent * vw * vh / ratio);
                    let h = w * ratio;
                    if (w > vw * 0.97) {{ w = vw * 0.97; h = w * ratio; }}
                    if (h > vh * 0.97) {{ h = vh * 0.97; w = h / ratio; }}
                    return {{ w, h }};
                }},

                clampHud: function() {{
                    if (!this.hud) return;
                    const vw = window.innerWidth, vh = window.innerHeight;
                    const rect = this.hud.getBoundingClientRect();
                    const marginW = 0.015 * vw, marginH = 0.015 * vh;
                    if (this.posX < marginW) this.posX = marginW;
                    if (this.posY < marginH) this.posY = marginH;
                    if (this.posX + rect.width > vw - marginW) this.posX = vw - marginW - rect.width;
                    if (this.posY + rect.height > vh - marginH) this.posY = vh - marginH - rect.height;
                    this.hud.style.transform = "translate3d(" + this.posX + "px, " + this.posY + "px, 0px)";
                }},

                saveState: function(isFS = null) {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const isM = area && area.style.display === 'none';
                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}');
                    localStorage.setItem(STATE_KEY, JSON.stringify({{
                        w: this.hud.offsetWidth, x: this.posX, y: this.posY, m: isM, f: isFS !== null ? isFS : (saved.f || false)
                    }}));
                }},

                applyTransition: function(enabled) {{
                    if (!this.hud) return;
                    this.hud.style.transition = enabled ? 'opacity 0.3s, transform 0.3s ease-out, width 0.3s ease-out, height 0.3s ease-out' : 'opacity 0.3s';
                }},

                exportMedia: async function(mode) {{
                    const img = document.getElementById(HUD_ID + "-img");
                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    const area = document.getElementById(HUD_ID + "-area");
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    const natW = img.naturalWidth, natH = img.naturalHeight;

                    if (this.isCropping && cropBox.style.display !== 'none') {{
                        const rect = cropBox.getBoundingClientRect(), aRect = area.getBoundingClientRect();
                        const scaleX = natW / aRect.width, scaleY = natH / aRect.height;
                        const sx = (rect.left - aRect.left) * scaleX, sy = (rect.top - aRect.top) * scaleY;
                        const sw = rect.width * scaleX, sh = rect.height * scaleY;
                        canvas.width = sw; canvas.height = sh;
                        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
                    }} else {{
                        canvas.width = natW; canvas.height = natH;
                        ctx.drawImage(img, 0, 0);
                    }}

                    if (mode === 'copy') {{
                        if (!navigator.clipboard || !navigator.clipboard.write) {{
                            const win = window.open();
                            win.document.write('<p>Mode non-sécurisé (HTTP). <br>Faites <b>Clic droit -> Copier</b> :</p><img src="' + canvas.toDataURL('image/png') + '" style="max-width:100%; border:1px solid #ccc;" />');
                            return;
                        }}
                        canvas.toBlob(async (blob) => {{
                            try {{
                                await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                                const btn = document.getElementById(HUD_ID + "-btn-copy");
                                const old = btn.innerText; btn.innerText = '✓'; btn.style.color = '#4ade80';
                                setTimeout(() => {{ btn.innerText = old; btn.style.color = '#aaa'; }}, 1000);
                            }} catch (err) {{ alert("Erreur copie : " + err); }}
                        }}, 'image/png');
                    }} else {{
                        const link = document.createElement('a');
                        const label = this.isCropping ? 'Crop' : 'Full';
                        link.download = "ECHO_" + label + "_" + Date.now() + ".png";
                        link.href = canvas.toDataURL('image/png');
                        link.click();
                    }}
                }},

                attachEvents: function() {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const lens = document.getElementById(HUD_ID + "-lens");
                    const img = document.getElementById(HUD_ID + "-img");
                    
                    this.hud.ondblclick = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        this.clampHud();
                        if(area) area.style.display = 'flex';
                        setTimeout(() => this.saveState(false), 350);
                    }};

                    const header = document.getElementById(HUD_ID + "-header");
                    header.onmousedown = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        e.preventDefault(); this.applyTransition(false);
                        let ox = e.clientX, oy = e.clientY;
                        document.onmousemove = (me) => {{
                            this.posX += (me.clientX - ox); this.posY += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            this.clampHud();
                        }};
                        document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                    }};

                    this.hud.querySelectorAll('.hdl').forEach(hdl => {{
                        hdl.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation(); this.applyTransition(false);
                            const isR = hdl.classList.contains('tr') || hdl.classList.contains('br'), isT = hdl.classList.contains('tl') || hdl.classList.contains('tr');
                            const startW = this.hud.offsetWidth, startH = this.hud.offsetHeight, startX = this.posX, startY = this.posY;
                            const ox = e.clientX, oy = e.clientY;
                            document.onmousemove = (me) => {{
                                let nw = isR ? (startW + (me.clientX - ox)) : (startW - (me.clientX - ox));
                                if (nw < 200) nw = 200; if (nw > window.innerWidth * 0.97) nw = window.innerWidth * 0.97;
                                let nh = nw * this.ratio;
                                if (nh > window.innerHeight * 0.97) {{ nh = window.innerHeight * 0.97; nw = nh / this.ratio; }}
                                if (!isR) this.posX = startX + (startW - nw);
                                if (isT) this.posY = startY + (startH - nh);
                                this.hud.style.width = nw + 'px'; this.hud.style.height = nh + 'px';
                                this.clampHud();
                            }};
                            document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                        }};
                    }});

                    area.onmousemove = (e) => {{
                        if (!this.zoomActive) return;
                        const aRect = area.getBoundingClientRect(), hRect = this.hud.getBoundingClientRect();
                        const lx = e.clientX - hRect.left - 75, ly = e.clientY - hRect.top - 75;
                        lens.style.transform = "translate3d(" + lx + "px, " + ly + "px, 0px)";
                        
                        const natW = img.naturalWidth, natH = img.naturalHeight;
                        if (!natW || !natH) return;
                        
                        let renderW = aRect.width, renderH = renderW * (natH / natW);
                        if (renderH > aRect.height) {{
                            renderH = aRect.height;
                            renderW = renderH * (natW / natH);
                        }}
                        
                        const offsetX = (aRect.width - renderW) / 2;
                        const offsetY = (aRect.height - renderH) / 2;
                        
                        const mouseX_on_image = e.clientX - aRect.left - offsetX;
                        const mouseY_on_image = e.clientY - aRect.top - offsetY;
                        
                        const zoomFactor = 2.5;
                        lens.style.backgroundSize = (renderW * zoomFactor) + "px " + (renderH * zoomFactor) + "px";
                        
                        const bgPosX = 75 - (mouseX_on_image * zoomFactor);
                        const bgPosY = 75 - (mouseY_on_image * zoomFactor);
                        lens.style.backgroundPosition = bgPosX + "px " + bgPosY + "px";
                    }};
                    area.onmouseenter = () => {{ if(this.zoomActive) lens.style.display = 'block'; }};
                    area.onmouseleave = () => {{ lens.style.display = 'none'; }};

                    document.getElementById(HUD_ID + "-btn-crop").onclick = (e) => {{
                        e.stopPropagation(); this.isCropping = !this.isCropping;
                        const cropBox = document.getElementById(HUD_ID + "-crop-box");
                        cropBox.style.display = this.isCropping ? 'block' : 'none';
                        e.target.style.color = this.isCropping ? '#fff' : '#aaa';
                        if (this.isCropping) {{
                            cropBox.style.width = area.offsetWidth + 'px'; cropBox.style.height = area.offsetHeight + 'px';
                            cropBox.style.transform = 'translate3d(0px, 0px, 0px)';
                        }}
                    }};

                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    cropBox.onmousedown = (e) => {{
                        if (e.target.classList.contains('cp')) return;
                        e.preventDefault(); e.stopPropagation();
                        let ox = e.clientX, oy = e.clientY;
                        let tx = 0, ty = 0;
                        const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                        if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                        document.onmousemove = (me) => {{
                            tx += (me.clientX - ox); ty += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            cropBox.style.transform = "translate3d(" + tx + "px, " + ty + "px, 0px)";
                        }};
                        document.onmouseup = () => document.onmousemove = null;
                    }};

                    cropBox.querySelectorAll('.cp').forEach(cp => {{
                        cp.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation();
                            const isL = cp.classList.contains('tl') || cp.classList.contains('bl') || cp.classList.contains('lc');
                            const isR = cp.classList.contains('tr') || cp.classList.contains('br') || cp.classList.contains('rc');
                            const isT = cp.classList.contains('tl') || cp.classList.contains('tr') || cp.classList.contains('tc');
                            const isB = cp.classList.contains('bl') || cp.classList.contains('br') || cp.classList.contains('bc');
                            let startW = cropBox.offsetWidth, startH = cropBox.offsetHeight, ox = e.clientX, oy = e.clientY;
                            let tx = 0, ty = 0;
                            const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                            if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                            const startX = tx, startY = ty;
                            document.onmousemove = (me) => {{
                                if (isR) cropBox.style.width = (startW + (me.clientX - ox)) + "px";
                                else if (isL) {{
                                    const nw = startW - (me.clientX - ox);
                                    cropBox.style.width = nw + "px"; 
                                    cropBox.style.transform = "translate3d(" + (startX + (startW - nw)) + "px, " + ty + "px, 0px)";
                                }}
                                if (isB) cropBox.style.height = (startH + (me.clientY - oy)) + "px";
                                else if (isT) {{
                                    const nh = startH - (me.clientY - oy);
                                    cropBox.style.height = nh + "px";
                                    const curX = isL ? (startX + (startW - cropBox.offsetWidth)) : tx;
                                    cropBox.style.transform = "translate3d(" + curX + "px, " + (startY + (startH - nh)) + "px, 0px)";
                                }}
                            }};
                            document.onmouseup = () => document.onmousemove = null;
                        }};
                    }});

                    document.getElementById(HUD_ID + "-btn-copy").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('copy'); }};
                    document.getElementById(HUD_ID + "-btn-save").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('save'); }};
                    document.getElementById(HUD_ID + "-btn-zoom").onclick = (e) => {{
                        e.stopPropagation(); this.zoomActive = !this.zoomActive;
                        e.target.style.color = this.zoomActive ? '#4ade80' : '#aaa';
                        if(!this.zoomActive) lens.style.display = 'none';
                    }};
                    document.getElementById(HUD_ID + "-btn-min").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const a = document.getElementById(HUD_ID + "-area");
                        a.style.display = a.style.display === 'none' ? 'flex' : 'none';
                        this.hud.style.height = a.style.display === 'none' ? 'auto' : (this.hud.offsetWidth * this.ratio) + 'px';
                        this.saveState(false);
                    }};
                    document.getElementById(HUD_ID + "-btn-def").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(false), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-full").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.97);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(true), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-close").onclick = (e) => {{ e.stopPropagation(); this.hud.remove(); }};
                }},

                create: function(data) {{
                    const old = document.getElementById(HUD_ID); if(old) old.remove();
                    this.hud = document.createElement('div');
                    this.hud.id = HUD_ID;
                    this.hud.setAttribute('data-chat-id', data.cid);
                    this.hud.style.cssText = 'position:fixed; top:0; left:0; z-index:10000; background:rgba(30,30,30,0.95); backdrop-filter:blur(12px); border:1px solid #444; border-radius:8px; box-shadow:0 10px 50px rgba(0,0,0,0.7); color:white; font-family:sans-serif; display:flex; flex-direction:column; opacity:0; min-width:200px; transition:opacity 0.3s; will-change:transform, opacity; transform: translate3d(20px, 50px, 0px);';
                    
                    this.hud.innerHTML = `
                        <div id="${{HUD_ID}}-header" style="padding:6px 12px; background:rgba(0,0,0,0.4); display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #444; cursor:move; user-select:none; border-radius: 8px 8px 0 0; min-height: 32px;">
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span style="font-size:11px; font-weight:bold; color:#4ade80;">🌐 ECHO MONITOR</span>
                                <span id="${{HUD_ID}}-timer" style="font-size:10px; color:#888;"></span>
                            </div>
                            <div style="display:flex; gap:10px;">
                                <button id="${{HUD_ID}}-btn-crop" title="Sélection" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">⛶</button>
                                <button id="${{HUD_ID}}-btn-copy" title="Copier" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">❐</button>
                                <button id="${{HUD_ID}}-btn-save" title="Télécharger" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">📥</button>
                                <button id="${{HUD_ID}}-btn-zoom" title="Loupe" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">🔍</button>
                                <button id="${{HUD_ID}}-btn-min" title="Réduire" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">_</button>
                                <button id="${{HUD_ID}}-btn-def" title="Taille Défaut" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">↺</button>
                                <button id="${{HUD_ID}}-btn-full" title="Plein Écran" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">□</button>
                                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:18px; font-weight:bold; line-height:1; padding:2px;">×</button>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-area" style="flex:1; width:100%; height:100%; background:black; display:flex; justify-content:center; overflow:hidden; border-radius: 0 0 8px 8px; position:relative; cursor:crosshair;">
                            <img id="${{HUD_ID}}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" />
                            <div id="${{HUD_ID}}-crop-box" style="position:absolute; border:2px dashed #fff; display:none; box-sizing:border-box; z-index:10002; cursor:move; box-shadow: 0 0 0 1px #000; will-change: transform;">
                                <div class="cp tl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tr" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:-5px; cursor:nesw-resize;"></div>
                                <div class="cp bl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; bottom:-5px; cursor:nesw-resize;"></div>
                                <div class="cp br" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; bottom:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; top:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp bc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; bottom:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp lc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                                <div class="cp rc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-lens" style="position:absolute; width:150px; height:150px; border:2px solid #4ade80; border-radius:50%; pointer-events:none; display:none; box-shadow:0 0 30px rgba(0,0,0,0.8); z-index:10005; background-repeat:no-repeat; will-change: transform;"></div>
                        <div class="hdl tl" style="position:absolute; width:20px; height:20px; left:-10px; top:-10px; cursor:nwse-resize; z-index:100;"></div>
                        <div class="hdl tr" style="position:absolute; width:20px; height:20px; right:-10px; top:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl bl" style="position:absolute; width:20px; height:20px; left:-10px; bottom:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl br" style="position:absolute; width:20px; height:20px; right:-10px; bottom:-10px; cursor:nwse-resize; z-index:100;"></div>
                    `;
                    document.body.appendChild(this.hud);
                    this.attachEvents();
                    setTimeout(() => {{ if(this.hud) this.hud.style.opacity = '1'; }}, 50);

                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                    if (saved && saved.w) {{
                        this.applyTransition(false);
                        this.posX = saved.x; this.posY = saved.y;
                        this.hud.style.width = saved.w + 'px';
                        if (!saved.f && saved.m) {{
                            const a = document.getElementById(HUD_ID + "-area"); if(a) a.style.display = 'none';
                            this.hud.style.height = 'auto';
                        }}
                        this.clampHud();
                    }}
                }},

                update: function(data) {{
                    if (!this.hud || !document.getElementById(HUD_ID)) {{ this.create(data); }}
                    const img = document.getElementById(HUD_ID + "-img");
                    img.onload = () => {{
                        this.ratio = img.naturalHeight / img.naturalWidth;
                        const lens = document.getElementById(HUD_ID + "-lens");
                        if (lens) lens.style.backgroundImage = 'url("' + img.src + '")';
                        const area = document.getElementById(HUD_ID + "-area");
                        const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                        if (saved && saved.w) {{
                            if (saved.f) {{
                                const size = this.getBestSize(this.ratio, 0.97);
                                this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                                this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                            }} else if (area && area.style.display !== 'none') {{
                                this.hud.style.height = (this.hud.offsetWidth * this.ratio) + 'px';
                            }}
                        }} else {{
                            const size = this.getBestSize(this.ratio, 0.25);
                            this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                            this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        }}
                        this.clampHud();
                    }};
                    img.src = "data:image/png;base64," + data.b64;

                    if (this.timerInt) clearInterval(this.timerInt);
                    this.timeLeft = data.timeout;
                    const tSpan = document.getElementById(HUD_ID + "-timer");
                    this.timerInt = setInterval(() => {{
                        window.echoHudEngine.timeLeft--; 
                        if(tSpan) tSpan.innerText = "[" + window.echoHudEngine.timeLeft + "s]";
                        if (window.echoHudEngine.timeLeft <= 5 && this.hud) this.hud.style.opacity = (window.echoHudEngine.timeLeft / 5);
                        if (window.echoHudEngine.timeLeft <= 0) {{ 
                            clearInterval(this.timerInt); 
                            const h = document.getElementById(HUD_ID); if(h) h.remove();
                        }}
                    }}, 1000);
                }}
            }};
        }}
        window.echoHudEngine.update(payload);
    }})();
    """

# --- INTERFACE TOOLS ECHO ---

class Tools:
    class Valves(BaseModel):
        AGENT_URL: str = Field(default="http://browser-agent:5002", description="URL du container Browser Agent")
        HTTP_TIMEOUT: int = Field(default=120, description="Timeout global (sec).")
        IDLE_TIMEOUT: int = Field(default=900, description="Délai auto-fermeture (sec).")
        UPLOADS_DIR: str = Field(default=ECHO_UPLOADS_DIR, description="Dossier des uploads OWUI")

    class UserValves(BaseModel):
        BROWSER_MODE: Literal["mobile", "desktop"] = Field(default="mobile", description="Mode de navigation (Mobile = Tablette)")
        SHOW_BROWSER_HUD: bool = Field(default=True, description="Afficher le moniteur de navigation (HUD)")
        HUD_VISIBLE_SEC: int = Field(default=90, description="Durée de visibilité du moniteur (sec)")

    def __init__(self):
        self.valves = self.Valves()

    async def web_browse_navigate(self, url: str, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """
        Accède à une URL et retourne la structure complète du DOM (la carte interactive).
        C'est l'outil à utiliser pour découvrir les éléments d'une page, naviguer sur Internet et extraire des données.
        :param url: L'URL complète de la page web à charger (incluant http/https).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Navigateur indisponible.", status={"status": "error"})
        res_nav = await _req(self.valves, "/action", {"session_id": chat_id, "action": "goto", "params": {"url": url}}, uid)
        if res_nav.get("status") == "error": return wrap_tool_output(text=f"❌ Erreur Navigation: {res_nav.get('message')}", status=res_nav)
        res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
        await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
        text_out = f"Navigué vers {url}\nStructure détectée : {len(res_view.get('metadata', []))} éléments interactifs."
        res_view.pop("screenshot_b64", None)
        return wrap_tool_output(text=text_out, status=res_view)

    async def web_browse_interact(
        self, 
        action: Literal["click", "type", "hover", "press", "scroll", "read", "read_html", "refresh_map", "tab_new", "tab_switch", "tab_close"], 
        selector: Optional[str] = None, 
        text: Optional[str] = None, 
        key: Optional[str] = "Enter",
        direction: Optional[Literal["up", "down", "top", "bottom"]] = "down",
        url: Optional[str] = None,
        index: Optional[int] = 0,
        __user__: dict = {}, 
        __metadata__: dict = {}, 
        __event_call__=None,
        __event_emitter__=None
    ) -> dict:
        """
        Exécute une action interactive spécifique sur la page web actuelle.
        :param action: L'action à effectuer. Utilisez 'refresh_map' pour mettre à jour votre vision du DOM ou 'read_html' pour extraire le code source.
        :param selector: Sélecteur CSS de l'élément cible (alternative à l'index).
        :param index: ID numérique de l'élément cible (issu de la carte précédente). C'est la méthode de ciblage RECOMMANDÉE.
        :param text: Contenu textuel à SAISIR. Utilisé UNIQUEMENT avec l'action 'type'.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        # Action spéciale : Observation du DOM
        if action == "refresh_map":
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
            res_view.pop("screenshot_b64", None)
            return wrap_tool_output(text=f"Carte du DOM mise à jour : {len(res_view.get('metadata', []))} éléments.", status=res_view)

        # Actions d'interaction
        params = {"selector": selector, "text": text, "key": key, "direction": direction, "url": url, "index": index}
        res_action = await _req(self.valves, "/action", {"session_id": chat_id, "action": action, "params": params}, uid)
        
        # Log Docker pour debug modèle
        print(f"[ECHO-NAV] Action: {action} | Index: {index} | Text: {text} | Status: {res_action.get('status')}", flush=True)

        if res_action.get("status") == "success":
            # RAFRAÎCHISSEMENT VISUEL (v6.24) : On reprend une photo pour le HUD
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)

        if res_action.get("status") == "error": 
            res_action.pop("screenshot_b64", None)
            return wrap_tool_output(text=f"❌ Échec action {action}: {res_action.get('message')}", status=res_action)
            
        if action == "read": 
            res_action.pop("screenshot_b64", None)
            return wrap_tool_output(text=res_action.get("content", ""), status=res_action)

        if action == "read_html":
            res_action.pop("screenshot_b64", None)
            b64_html = res_action.pop("content", "")
            multiparts = [{"type": "media", "mime_type": "text/plain", "data": b64_html}]
            text_out = "### Source HTML\nContenu récupéré et encapsulé (MIME: text/plain) pour analyse sécurisée."
            return wrap_tool_output(text=text_out, status=res_action, echo_tool_multiparts=multiparts)
            
        # Pour click/type/scroll : Retour minimaliste (factuel)
        res_action.pop("screenshot_b64", None)
        return wrap_tool_output(text=f"Action {action} terminée avec succès.", status=res_action)

    async def get_web_object_url(
        self, 
        index: int, 
        attribute: Literal["src", "href"] = "src", 
        __user__: dict = {}, 
        __metadata__: dict = {},
        __event_call__=None,
        __event_emitter__=None
    ) -> dict:
        """
        Récupère l'URL absolue d'une image (src) ou d'un lien (href) à partir de son ID (index) identifié dans la liste des éléments interactifs de la page en cours.
        :param index: ID numérique de l'élément identifié dans la liste des éléments interactifs.
        :param attribute: L'attribut à récupérer ('src' pour les images/objets, 'href' pour les liens). Par défaut 'src'.
        """
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        events = EchoEvents(__event_emitter__, __event_call__)

        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "get_attribute", "params": {"index": index, "attribute": attribute}}, uid)
        
        if res.get("status") == "success" and res.get("value"):
            return wrap_tool_output(text=f"✅ L'URL absolue est : {res['value']}", status=res)
        elif res.get("status") == "success":
            return wrap_tool_output(text=f"❌ L'attribut '{attribute}' n'a pas été trouvé pour l'élément à l'index {index}.", status={"status": "error"})
        else:
            return wrap_tool_output(text=f"❌ Erreur lors de la récupération de l'attribut : {res.get('message')}", status=res)

    async def web_browse_reset(self, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """
        Réinitialise complètement l'instance du navigateur pour cette session utilisateur.
        À utiliser en cas d'erreur persistante, de détection de bot ou pour repartir d'une page vierge.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        
        # Commande de reset au worker
        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "reset"}, uid)
        
        if __event_call__:
            # Fermeture du HUD côté client
            close_code = "const h=document.getElementById('echo-browser-monitor'); if(h) h.remove();"
            try: await __event_call__({"type": "execute", "data": {"code": close_code}})
            except: pass

        return wrap_tool_output(text="🚀 Navigateur réinitialisé avec succès. La session repart à zéro.", status=res)

    async def get_browser_frames_history(self, depth: Optional[int] = None, __user__: dict = {}, __metadata__: dict = {}) -> dict:
        """
        Retourne la liste des IDs des captures d'écran passées de la session.
        Permet d'analyser visuellement les étapes précédentes via semantic_probe ou read_raw_file_content.
        :param depth: Nombre optionnel de frames récentes à retourner (ex: 5 pour les 5 dernières). Si omis, retourne tout l'historique.
        """
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        state_manager = EchoStateManager(user_id=uid)
        
        # Récupération depuis la BDD (Standard ECHO)
        try:
            conn = state_manager._get_connection()
            cursor = conn.cursor()
            # On cherche les fichiers PNG indexés pour ce chat (v6.62 : Ajout filename et mime pour Suture)
            query = "SELECT file_id, filename, mime, timestamp FROM processed_files WHERE chat_id = ? AND file_id LIKE 'U_%_C_%_T_%' ORDER BY timestamp DESC"
            params = [chat_id]
            if depth:
                query += " LIMIT ?"
                params.append(depth)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            history = []
            nouveaux_fichiers = []
            for row in rows:
                fid = row[0]
                fname = row[1]
                fmime = row[2] or "image/png"
                ts = row[3]
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                
                history.append({
                    "file_id": fid,
                    "date": dt,
                    "usage": f"Use semantic_probe(file_id='{fid}') to analyze or read_raw_file_content(file_id='{fid}') to view."
                })
                
                # Format Suture (v6.62)
                nouveaux_fichiers.append({
                    "nom": fname,
                    "id": fid,
                    "mime": fmime,
                    "statut": "indexed"
                })
            
            return wrap_tool_output(
                text=json.dumps(history, indent=2), 
                status={"status": "success", "count": len(history)},
                nouveaux_fichiers=nouveaux_fichiers
            )
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur lecture historique: {str(e)}", status={"status": "error"})

async def _deploy_navigation_monitor(valves: Any, res_view: dict, chat_id: str, user_id: str, u_valves: Any, __event_call__) -> str:
    if not res_view.get("screenshot_b64"): return ""
    file_id = generate_echo_file_id(user_id, chat_id)
    filename = f"{file_id}_frame.png"
    
    # Redirection directe vers le Vault utilisateur (v6.68)
    state_manager = EchoStateManager(user_id=user_id)
    vault_path = os.path.join(state_manager.user_dir, "files")
    filepath = os.path.join(vault_path, filename)
    
    try:
        img_data = base64.b64decode(res_view["screenshot_b64"])
        with open(filepath, "wb") as f: f.write(img_data)
        
        # INDEXATION BDD
        state_manager.mark_processed(chat_id, file_id, filename, "image/png", "indexed")
        print(f"[ECHO-NAV] 🗄️ Frame scellée directement dans le Vault : {file_id}", flush=True)
    except Exception as e:
        print(f"[ECHO-NAV] !! Erreur scellement frame: {e}", flush=True)
        
    if __event_call__ and u_valves.SHOW_BROWSER_HUD:
        code = _generate_monitor_js(res_view["screenshot_b64"], chat_id[:8], chat_id, u_valves.HUD_VISIBLE_SEC)
        try: await asyncio.wait_for(__event_call__({"type": "execute", "data": {"code": code}}), timeout=5.0)
        except: pass
    return filename
