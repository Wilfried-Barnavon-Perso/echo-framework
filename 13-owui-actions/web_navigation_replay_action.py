"""
title: Revue Navigation Web
author: Wilfried BARNAVON
version: 4.12
description: Cockpit vidéo interactif permettant de visionner et d'extraire des captures de la navigation autonome.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxyZWN0IHdpZHRoPSIxOCIgaGVpZ2h0PSIxOCIgeD0iMyIgeT0iMyIgcng9IjIiIC8+PHBhdGggZD0iTTcgM3YxOCIgLz48cGF0aCBkPSJNMyA3LjVoNCIgLz48cGF0aCBkPSJNMyAxMmgxOCIgLz48cGF0aCBkPSJNMyAxNi41aDQiIC8+PHBhdGggZD0iTTE3IDN2MTgiIC8+PHBhdGggZD0iTTE3IDcuNWg0IiAvPjxwYXRoIGQ9Ik0xNyAxNi41aDQiIC8+PC9zdmc+
"""
# Historique des versions :
# 4.10: Ajout d'un toast informatif si l'historique visuel est vide.
# 4.9: Modification de l'icône SVG pour afficher une pellicule de cinéma au lieu d'un quadrillage.
# 4.8: Mise à jour de la priorité d'affichage à 60.
# 4.7: Hotfix - Lecture des frames via le Registre Unifié V2 (echo_resources) au lieu du scan disque.
# 4.6: Renommage sémantique du titre UX (revert self.actions incompatible OWUI simple-action).

import orjson as json
import pybase64 as base64
import logging
import sys
from typing import Optional, List, Dict
from pydantic import BaseModel, Field

# Importations ECHO
sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_ui import EchoUI

# --- CONFIGURATION LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _generate_replay_shell(timestamps: List[Dict], chat_id: str) -> str:
    """Génère le script JS Cockpit (Interface et Listeners) - Version INTEGRALE v5.136."""
    ts_json = json.dumps(timestamps).decode('utf-8')
    return f"""
    (function() {{
        try {{
            const data = {{ timestamps: {ts_json}, cid: "{chat_id}" }};
            const REPLAY_ID = 'echo-browser-replay';
            {EchoUI.get_mobile_guard_js('echo-browser-replay')}
            let replay = document.getElementById(REPLAY_ID);

            if (replay && replay.getAttribute('data-chat-id') !== data.cid) {{
                replay.remove();
                replay = null;
            }}

            if (!replay) {{
                replay = document.createElement('div');
                replay.id = REPLAY_ID;
                replay.setAttribute('data-chat-id', data.cid);
                replay.style.cssText = 'position:fixed; inset:0; z-index:10001; background:rgba(0,0,0,0.92); backdrop-filter:blur(20px); display:flex; flex-direction:column; align-items:center; color:white; font-family:sans-serif; overflow:hidden;';

                replay.innerHTML = `
                    <div style="position:absolute; top:20px; right:20px; z-index:100; display:flex; gap:15px;">
                        <button id="${{REPLAY_ID}}-btn-crop" title="Sélection" style="background:rgba(255,255,255,0.1); border:none; color:white; font-size:18px; cursor:pointer; width:40px; height:40px; border-radius:50%;">⛶</button>
                        <button id="${{REPLAY_ID}}-btn-copy" title="Copier" style="background:rgba(255,255,255,0.1); border:none; color:white; font-size:18px; cursor:pointer; width:40px; height:40px; border-radius:50%;">❐</button>
                        <button id="${{REPLAY_ID}}-btn-save" title="Télécharger" style="background:rgba(255,255,255,0.1); border:none; color:white; font-size:18px; cursor:pointer; width:40px; height:40px; border-radius:50%;">📥</button>
                        <button id="${{REPLAY_ID}}-close" style="background:rgba(255,255,255,0.1); border:none; color:white; font-size:24px; cursor:pointer; width:40px; height:40px; border-radius:50%;">×</button>
                    </div>

                    <div id="${{REPLAY_ID}}-viewport" style="flex:1; width:100%; display:flex; justify-content:center; overflow:auto; padding:40px 0; scrollbar-width: thin; scrollbar-color: #4ade80 transparent;">
                        <div id="${{REPLAY_ID}}-canvas" style="position:relative; width:600px; height:800px; background:#111; box-shadow:0 0 100px rgba(0,0,0,1); border:1px solid #333; border-radius:4px; cursor:crosshair; transition: width 0.3s, height 0.3s;">
                            <img id="${{REPLAY_ID}}-img" style="position:absolute; top:0; left:0; width:100%; height:100%; object-fit:contain !important; display:block; border-radius:4px; opacity:0; transition:opacity 0.2s; pointer-events:none; z-index:1;" />
                            <div id="${{REPLAY_ID}}-crop-box" style="position:absolute; top:0; left:0; border:2px dashed #fff; display:none; box-sizing:border-box; z-index:101; cursor:move; box-shadow: 0 0 0 1px #000; will-change: transform;">
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
                    </div>

                    <div style="width:100%; background:rgba(0,0,0,0.5); border-top:1px solid #333; padding:20px; display:flex; flex-direction:column; align-items:center; gap:15px; backdrop-filter:blur(10px);">
                        <div id="${{REPLAY_ID}}-meta" style="font-size:11px; color:#4ade80; font-family:monospace; letter-spacing:1px; background:rgba(0,0,0,0.3); padding:4px 12px; border-radius:10px;">INITIALISATION...</div>

                        <div style="display:flex; gap:15px; align-items:center;">
                            <button id="${{REPLAY_ID}}-first" style="background:none; border:none; color:white; cursor:pointer; font-size:18px;">|◀</button>    
                            <button id="${{REPLAY_ID}}-prev" style="background:none; border:none; color:white; cursor:pointer; font-size:18px; margin-right:40px;">◀</button>

                            <div style="display:flex; gap:10px; align-items:center;">
                                <button id="${{REPLAY_ID}}-play-rev" style="background:#f97316; border:none; color:white; padding:10px 25px; border-radius:20px 5px 5px 20px; font-weight:bold; cursor:pointer; min-width:110px;">◀ PLAY</button>
                                <button id="${{REPLAY_ID}}-play" style="background:#4ade80; border:none; color:black; padding:10px 25px; border-radius:5px 20px 20px 5px; font-weight:bold; cursor:pointer; min-width:110px;">PLAY ▶</button>
                            </div>

                            <button id="${{REPLAY_ID}}-next" style="background:none; border:none; color:white; cursor:pointer; font-size:18px; margin-left:40px;">▶</button>
                            <button id="${{REPLAY_ID}}-last" style="background:none; border:none; color:white; cursor:pointer; font-size:18px;">▶|</button>     
                        </div>

                        <div style="display:flex; gap:15px; align-items:center; width:400px; opacity:0.8;">
                            <input type="range" id="${{REPLAY_ID}}-speed" min="1" max="30" value="5" style="flex:1; height:4px; cursor:pointer; accent-color:#4ade80;">
                            <span id="${{REPLAY_ID}}-speed-val" style="font-size:10px; color:#888; width:30px;">5s</span>
                            <button id="${{REPLAY_ID}}-btn-zoom" title="Reset Zoom" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:18px; margin-left:20px;">↺</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(replay);

                // --- Atomic State ---
                let currentIndex = 0;
                let isPlaying = false;
                let isPlayingRev = false;
                let playInterval = null;
                let currentZoom = 1.0;
                let isCropping = false;
                const timestamps = data.timestamps;

                const stopPlayback = () => {{
                    if (playInterval) clearInterval(playInterval);
                    isPlaying = false;
                    isPlayingRev = false;
                    const btnPlay = document.getElementById(`${{REPLAY_ID}}-play`);
                    const btnRev = document.getElementById(`${{REPLAY_ID}}-play-rev`);
                    btnPlay.innerText = "PLAY ▶";
                    btnPlay.style.background = "#4ade80";
                    btnRev.innerText = "◀ PLAY";
                    btnRev.style.background = "#f97316";
                }};

                const requestFrame = (idx) => {{
                    if (idx < 0 || idx >= timestamps.length) {{
                        stopPlayback();
                        return;
                    }}
                    currentIndex = idx;
                    document.getElementById(`${{REPLAY_ID}}-meta`).innerText = "REQUÊTE FLUX... (" + (idx + 1) + "/" + timestamps.length + ")";
                    if(window.echoReplayResolve) window.echoReplayResolve({{ action: "goto", index: idx }});
                }};

                const updateUI = (b64, ts, current, total) => {{
                    const img = document.getElementById(`${{REPLAY_ID}}-img`);
                    const canvas = document.getElementById(`${{REPLAY_ID}}-canvas`);
                    const cropBox = document.getElementById(`${{REPLAY_ID}}-crop-box`);
                    const date = new Date(ts).toLocaleString('fr-FR');

                    img.onload = () => {{
                        img.style.opacity = '1';
                        const r = img.naturalHeight / img.naturalWidth;
                        const targetH = Math.min(window.innerHeight * 0.75, img.naturalHeight);
                        const targetW = targetH / r;
                        const scaleW = targetW > (window.innerWidth * 0.95) ? (window.innerWidth * 0.95) / targetW : 1;
                        canvas.style.height = (targetH * currentZoom * scaleW) + "px";
                        canvas.style.width = (targetW * currentZoom * scaleW) + "px";
                        if (isCropping) {{
                            cropBox.style.width = canvas.offsetWidth + "px";
                            cropBox.style.height = canvas.offsetHeight + "px";
                            cropBox.style.transform = "translate3d(0,0,0)";
                        }}
                    }};
                    img.src = "data:image/png;base64," + b64;
                    document.getElementById(`${{REPLAY_ID}}-meta`).innerText = "PREUVE DU " + date + " (" + current + "/" + total + ")";
                }};
                window.echoReplayUpdate = updateUI;

                const exportMedia = async (mode) => {{
                    const imgEl = document.getElementById(`${{REPLAY_ID}}-img`);
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    const natW = imgEl.naturalWidth, natH = imgEl.naturalHeight;
                    if (!natW || !natH) return;

                    let isCropped = false;
                    const cropBox = document.getElementById(`${{REPLAY_ID}}-crop-box`);
                    if (isCropping && cropBox && cropBox.style.display !== 'none') {{
                        const rect = cropBox.getBoundingClientRect();
                        const cRect = document.getElementById(`${{REPLAY_ID}}-canvas`).getBoundingClientRect();
                        const scaleX = natW / cRect.width, scaleY = natH / cRect.height;
                        let sx = (rect.left - cRect.left) * scaleX, sy = (rect.top - cRect.top) * scaleY;
                        let sw = rect.width * scaleX, sh = rect.height * scaleY;
                        sx = Math.max(0, sx); sy = Math.max(0, sy);
                        sw = Math.min(sw, natW - sx); sh = Math.min(sh, natH - sy);

                        if (sw > 5 && sh > 5) {{
                            canvas.width = sw; canvas.height = sh;
                            ctx.drawImage(imgEl, sx, sy, sw, sh, 0, 0, sw, sh);
                            isCropped = true;
                        }}
                    }}

                    if (!isCropped) {{
                        canvas.width = natW; canvas.height = natH;
                        ctx.drawImage(imgEl, 0, 0);
                    }}

                    const dataUrl = canvas.toDataURL('image/png');
                    const showFallback = (url) => {{
                        const over = document.createElement('div');
                        over.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.85); z-index:20000; display:flex; flex-direction:column; align-items:center; justify-content:center; color:white; backdrop-filter:blur(5px); padding:20px;';
                        over.innerHTML = `
                            <p style="background:#4ade80; color:black; padding:8px 16px; border-radius:20px; font-weight:bold; margin-bottom:20px;">Mode Restreint : Faites Clic droit -> Copier l'image</p>
                            <img src="${{url}}" style="max-width:90%; max-height:70%; border:2px solid #555; border-radius:8px; box-shadow:0 0 50px rgba(0,0,0,0.5);" />
                            <p style="margin-top:20px; color:#aaa; cursor:pointer; font-size:14px;">[ Cliquez n'importe où pour fermer ]</p>
                        `;
                        over.onclick = () => over.remove();
                        document.body.appendChild(over);
                    }};

                    if (mode === 'copy') {{
                        if (!navigator.clipboard || !window.ClipboardItem) {{
                            showFallback(dataUrl);
                            return;
                        }}
                        canvas.toBlob(async (blob) => {{
                            try {{
                                await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                                const btn = document.getElementById(`${{REPLAY_ID}}-btn-copy`);
                                if(btn) {{
                                    const oldText = btn.innerText; btn.innerText = '✓'; btn.style.color = '#4ade80';
                                    setTimeout(() => {{ btn.innerText = oldText; btn.style.color = 'white'; }}, 1000);
                                }}
                            }} catch (err) {{
                                showFallback(dataUrl);
                            }}
                        }}, 'image/png');
                    }} else {{
                        const link = document.createElement('a');
                        link.download = `ECHO_REPLAY_${{isCropped ? 'Crop' : 'Full' }}_${{Date.now()}}.png`;
                        link.href = dataUrl;
                        link.click();
                    }}
                }};

                const canvas = document.getElementById(`${{REPLAY_ID}}-canvas`);
                const cropBox = document.getElementById(`${{REPLAY_ID}}-crop-box`);
                const viewport = document.getElementById(`${{REPLAY_ID}}-viewport`);

                viewport.addEventListener('wheel', (e) => {{
                    if (e.ctrlKey) {{
                        e.preventDefault();
                        const delta = e.deltaY > 0 ? 0.9 : 1.1;
                        currentZoom = Math.min(Math.max(0.1, currentZoom * delta), 15);

                        const imgEl = document.getElementById(`${{REPLAY_ID}}-img`);
                        if (imgEl && imgEl.naturalHeight) {{
                            const r = imgEl.naturalHeight / imgEl.naturalWidth;
                            const targetH = Math.min(window.innerHeight * 0.75, imgEl.naturalHeight);
                            const targetW = targetH / r;
                            const scaleW = targetW > (window.innerWidth * 0.95) ? (window.innerWidth * 0.95) / targetW : 1;
                            canvas.style.height = (targetH * currentZoom * scaleW) + "px";
                            canvas.style.width = (targetW * currentZoom * scaleW) + "px";

                            if (isCropping) {{
                                cropBox.style.width = canvas.offsetWidth + "px";
                                cropBox.style.height = canvas.offsetHeight + "px";
                                cropBox.style.transform = 'translate3d(0, 0, 0)';
                            }}
                        }}
                    }}
                }}, {{ passive: false }});

                document.getElementById(`${{REPLAY_ID}}-btn-zoom`).onclick = () => {{
                    currentZoom = 1.0;
                    const imgEl = document.getElementById(`${{REPLAY_ID}}-img`);
                    if (imgEl && imgEl.naturalHeight) {{
                        const r = imgEl.naturalHeight / imgEl.naturalWidth;
                        const targetH = Math.min(window.innerHeight * 0.75, imgEl.naturalHeight);
                        const targetW = targetH / r;
                        const scaleW = targetW > (window.innerWidth * 0.95) ? (window.innerWidth * 0.95) / targetW : 1;
                        canvas.style.height = (targetH * scaleW) + "px";
                        canvas.style.width = (targetW * scaleW) + "px";
                        if (isCropping) {{
                            cropBox.style.width = canvas.offsetWidth + "px";
                            cropBox.style.height = canvas.offsetHeight + "px";
                            cropBox.style.transform = 'translate3d(0, 0, 0)';
                        }}
                    }}
                }};

                document.getElementById(`${{REPLAY_ID}}-btn-crop`).onclick = () => {{
                    isCropping = !isCropping;
                    cropBox.style.display = isCropping ? 'block' : 'none';
                    document.getElementById(`${{REPLAY_ID}}-btn-crop`).style.background = isCropping ? '#4ade80' : 'rgba(255,255,255,0.1)';
                    if (isCropping) {{
                        cropBox.style.width = canvas.offsetWidth + "px"; cropBox.style.height = canvas.offsetHeight + "px";
                        cropBox.style.transform = "translate3d(0,0,0)";
                    }}
                }};

                cropBox.onmousedown = (e) => {{
                    if (e.target.classList.contains('cp')) return;
                    e.preventDefault(); e.stopPropagation();
                    let ox = e.clientX, oy = e.clientY;
                    let tx = 0, ty = 0;
                    const match = cropBox.style.transform.match(/translate3d\\((.+?)px, (.+?)px/);
                    if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                    document.onmousemove = (me) => {{
                        tx += (me.clientX - ox); ty += (me.clientY - oy);
                        ox = me.clientX; oy = me.clientY;
                        cropBox.style.transform = "translate3d(" + tx + "px, " + ty + "px, 0)";
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
                        const match = cropBox.style.transform.match(/translate3d\\((.+?)px, (.+?)px/);
                        if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                        const startX = tx, startY = ty;
                        document.onmousemove = (me) => {{
                            if (isR) cropBox.style.width = (startW + (me.clientX - ox)) + "px";
                            else if (isL) {{
                                const nw = startW - (me.clientX - ox);
                                cropBox.style.width = nw + "px";
                                cropBox.style.transform = "translate3d(" + (startX + (startW - nw)) + "px, " + ty + "px, 0)";
                            }}
                            if (isB) cropBox.style.height = (startH + (me.clientY - oy)) + "px";
                            else if (isT) {{
                                const nh = startH - (me.clientY - oy);
                                cropBox.style.height = nh + "px";
                                const curX = isL ? (startX + (startW - cropBox.offsetWidth)) : tx;
                                cropBox.style.transform = "translate3d(" + curX + "px, " + (startY + (startH - nh)) + "px, 0)";
                            }}
                        }};
                        document.onmouseup = () => document.onmousemove = null;
                    }};
                }});

                document.getElementById(`${{REPLAY_ID}}-btn-copy`).onclick = () => exportMedia('copy');
                document.getElementById(`${{REPLAY_ID}}-btn-save`).onclick = () => exportMedia('save');
                document.getElementById(`${{REPLAY_ID}}-first`).onclick = () => {{ stopPlayback(); requestFrame(0); }};
                document.getElementById(`${{REPLAY_ID}}-last`).onclick = () => {{ stopPlayback(); requestFrame(timestamps.length - 1); }};
                document.getElementById(`${{REPLAY_ID}}-prev`).onclick = () => {{ stopPlayback(); requestFrame(Math.max(0, currentIndex - 1)); }};
                document.getElementById(`${{REPLAY_ID}}-next`).onclick = () => {{ stopPlayback(); requestFrame(Math.min(timestamps.length - 1, currentIndex + 1)); }};

                document.getElementById(`${{REPLAY_ID}}-play`).onclick = () => {{
                    if (isPlaying) stopPlayback();
                    else {{
                        stopPlayback(); isPlaying = true;
                        document.getElementById(`${{REPLAY_ID}}-play`).innerText = "⏸ STOP";
                        document.getElementById(`${{REPLAY_ID}}-play`).style.background = "#ff4444";
                        playInterval = setInterval(() => requestFrame(currentIndex + 1), document.getElementById(`${{REPLAY_ID}}-speed`).value * 1000);
                    }}
                }};

                document.getElementById(`${{REPLAY_ID}}-play-rev`).onclick = () => {{
                    if (isPlayingRev) stopPlayback();
                    else {{
                        stopPlayback(); isPlayingRev = true;
                        document.getElementById(`${{REPLAY_ID}}-play-rev`).innerText = "⏸ STOP";
                        document.getElementById(`${{REPLAY_ID}}-play-rev`).style.background = "#ff4444";
                        playInterval = setInterval(() => requestFrame(currentIndex - 1), document.getElementById(`${{REPLAY_ID}}-speed`).value * 1000);
                    }}
                }};

                document.getElementById(`${{REPLAY_ID}}-speed`).oninput = (e) => {{ document.getElementById(`${{REPLAY_ID}}-speed-val`).innerText = e.target.value + "s"; }};
                document.getElementById(`${{REPLAY_ID}}-close`).onclick = () => {{ stopPlayback(); replay.remove(); if(window.echoReplayResolve) window.echoReplayResolve({{ action: "close" }}); }};
            }}
            return true;
        }} catch (e) {{ return e.toString(); }}
    }})();
    """

class Action:
    class Valves(BaseModel):
        priority: int = Field(default=60, description="Priorité d'affichage (60 = Sixième).")

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __event_call__: return None

        uid = __user__.get("id", "anonymous")
        cid = body.get("chat_id") or __metadata__.get("chat_id")
        if not cid: return None

        # Redirection vers le Registre V2
        from echo_state_manager import EchoStateManager
        
        state_manager = EchoStateManager(user_id=uid, chat_id=cid)
        resources = state_manager.get_resources(resource_type='media')
        
        prefix = f"U_{uid}_C_{cid}_T_"
        files = []
        try:
            for r in resources:
                if r.get('id', '').startswith(prefix):
                    try:
                        ts_str = r['id'].split("_T_")[-1].split("_")[0].split(".")[0]
                        if r.get('storage_path'):
                            files.append({"ts": int(ts_str), "storage_path": r['storage_path']})
                    except: continue

            # Tri CHRONOLOGIQUE STRICT
            files.sort(key=lambda x: x["ts"])

        except Exception as e:
            await events.toast(f"Erreur scan Registre : {e}", "error")
            return None

        if not files:
            await events.status("📭 Aucune archive visuelle dans le Registre.", done=True)
            await events.toast("ℹ️ Aucune archive de navigation web trouvée pour ce chat.", "info")
            return None

        # 1. Installation de la Console
        shell_code = _generate_replay_shell(files, cid)
        await __event_call__({"type": "execute", "data": {"code": shell_code}})

        # 2. Boucle de Streaming Atomique
        current_idx = 0
        while True:
            # a. PUSH: Envoi de la frame actuelle (Atomic Update)
            path = files[current_idx]["storage_path"]
            try:
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode()

                update_code = f"if(window.echoReplayUpdate) window.echoReplayUpdate('{b64}', {files[current_idx]['ts']}, {current_idx+1}, {len(files)});"       
                await __event_call__({"type": "execute", "data": {"code": update_code}})
            except: break

            # b. WAIT: Attente de la prochaine commande (Atomic Listen)
            wait_code = "return new Promise(r => window.echoReplayResolve = r);"
            response = await __event_call__({"type": "execute", "data": {"code": wait_code}})

            if not response or not isinstance(response, dict): break
            action = response.get("action")
            if action == "close": break
            if action == "goto":
                current_idx = response.get("index", 0)

        return {"status": "success"}
