"""
title: Show Web Replay
author: Wilfried BARNAVON
version: 3.3
description: 3.3: Fixed date formatting to prevent year 58000+ bug (timestamp in ms).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxyZWN0IHdpZHRoPSIxOCIgaGVpZ2h0PSIxOCIgeD0iMyIgeT0iMyIgcng9IjIiLz48cGF0aCBkPSJNNyAzdjE4Ii8+PHBhdGggZD0iTTEyIDN2MTgiLz48cGF0aCBkPSJNMTcgM3YxOCIvPjxwYXRoIGQ9Ik0zIDdoMTgiLz48cGF0aCBkPSJNMyAxMmgyMSIvPjxwYXRoIGQ9Ik0zIDE3aDE4Ii8+PC9zdmc+
"""

import os
import orjson as json
import pybase64 as base64
import time
import logging
import sys
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_constants import ECHO_USERS_ROOT
try:
    from echo_utils import EchoEvents
except ImportError:
    class EchoEvents:
        def __init__(self, e=None, c=None): pass
        async def status(self, d, done=False): pass
        async def toast(self, c, l="info"): pass
        async def call(self, t, d): return None

# --- CONFIGURATION LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _generate_replay_shell(timestamps: List[Dict], chat_id: str) -> str:
    """Génère le script JS Cockpit (Interface et Listeners)."""
    ts_json = json.dumps(timestamps).decode('utf-8')
    return f"""
    (function() {{
        try {{
            const data = {{ timestamps: {ts_json}, cid: "{chat_id}" }};
            const REPLAY_ID = 'echo-browser-replay';
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
                    
                    <div id="${{REPLAY_ID}}-viewport" style="flex:1; width:100%; display:flex; justify-content:center; overflow-y:auto; padding:40px 0; scrollbar-width: thin; scrollbar-color: #4ade80 transparent;">
                        <div id="${{REPLAY_ID}}-canvas" style="position:relative; width:600px; height:800px; background:#111; box-shadow:0 0 100px rgba(0,0,0,1); border:1px solid #333; border-radius:4px; cursor:crosshair; transition: width 0.3s, height 0.3s;">
                            <img id="${{REPLAY_ID}}-img" style="position:absolute; top:0; left:0; width:100%; height:100%; display:block; border-radius:4px; opacity:0; transition:opacity 0.2s; pointer-events:none; z-index:1;" />
                            <div id="${{REPLAY_ID}}-loupe" style="position:absolute; width:200px; height:200px; border:2px solid #4ade80; border-radius:50%; pointer-events:none; display:none; background-repeat:no-repeat; box-shadow:0 0 30px rgba(0,0,0,0.8); z-index:100; will-change: transform;"></div>
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
                            <button id="${{REPLAY_ID}}-btn-zoom" title="Loupe" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:18px; margin-left:20px;">🔍</button>
                        </div>
                    </div>
                `;
                document.body.appendChild(replay);

                // --- Atomic State ---
                let currentIndex = 0;
                let isPlaying = false;
                let isPlayingRev = false;
                let playInterval = null;
                let zoomActive = false;
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
                    const loupe = document.getElementById(`${REPLAY_ID}-loupe`);
                    const date = new Date(ts).toLocaleString('fr-FR');

                    img.onload = () => {
                        img.style.opacity = '1'; 
                        const r = img.naturalHeight / img.naturalWidth;
                        const targetH = Math.min(window.innerHeight * 0.75, img.naturalHeight);
                        canvas.style.height = targetH + "px";
                        canvas.style.width = (targetH / r) + "px";
                        if (isCropping) {{
                            cropBox.style.width = canvas.offsetWidth + "px";
                            cropBox.style.height = canvas.offsetHeight + "px";
                            cropBox.style.transform = "translate3d(0,0,0)";
                        }}
                    }};
                    img.src = "data:image/png;base64," + b64;
                    loupe.style.backgroundImage = "url(" + img.src + ")";
                    document.getElementById(`${{REPLAY_ID}}-meta`).innerText = "PREUVE DU " + date + " (" + current + "/" + total + ")";
                }};
                window.echoReplayUpdate = updateUI;

                const exportMedia = async (mode) => {{
                    const imgEl = document.getElementById(`${{REPLAY_ID}}-img`);
                    const cropBox = document.getElementById(`${{REPLAY_ID}}-crop-box`);
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    const natW = imgEl.naturalWidth, natH = imgEl.naturalHeight;
                    
                    if (isCropping && cropBox.style.display !== 'none') {{
                        const rect = cropBox.getBoundingClientRect();
                        const cRect = document.getElementById(`${{REPLAY_ID}}-canvas`).getBoundingClientRect();
                        const scaleX = natW / cRect.width, scaleY = natH / cRect.height;
                        const sx = (rect.left - cRect.left) * scaleX, sy = (rect.top - aRect.top) * scaleY;
                        const sw = rect.width * scaleX, sh = rect.height * scaleY;
                        canvas.width = sw; canvas.height = sh;
                        ctx.drawImage(imgEl, sx, sy, sw, sh, 0, 0, sw, sh);
                    }} else {{
                        canvas.width = natW; canvas.height = natH;
                        ctx.drawImage(imgEl, 0, 0);
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
                                const btn = document.getElementById(`${{REPLAY_ID}}-btn-copy`);
                                btn.style.color = '#4ade80';
                                setTimeout(() => btn.style.color = 'white', 1000);
                            }} catch (err) {{ alert("Erreur copie : " + err); }}
                        }}, 'image/png');
                    }} else {{
                        const link = document.createElement('a');
                        const label = isCropping ? 'Crop' : 'Full';
                        link.download = "ECHO_REPLAY_" + label + "_" + Date.now() + ".png";
                        link.href = canvas.toDataURL('image/png');
                        link.click();
                    }}
                }};

                const canvas = document.getElementById(`${{REPLAY_ID}}-canvas`);
                const loupe = document.getElementById(`${{REPLAY_ID}}-loupe`);
                const cropBox = document.getElementById(`${{REPLAY_ID}}-crop-box`);

                canvas.onmousemove = (e) => {{
                    if (!zoomActive) return;
                    const rect = canvas.getBoundingClientRect();
                    const lx = e.clientX - rect.left - 100, ly = e.clientY - rect.top - 100;
                    loupe.style.transform = "translate3d(" + lx + "px, " + ly + "px, 0)";
                    loupe.style.display = 'block';
                    const zoom = 2.5;
                    loupe.style.backgroundSize = (rect.width * zoom) + 'px ' + (rect.height * zoom) + 'px';
                    const px = ((e.clientX - rect.left) / rect.width) * 100, py = ((e.clientY - rect.top) / rect.height) * 100;
                    loupe.style.backgroundPosition = px + "% " + py + "%";
                }};
                canvas.onmouseleave = () => {{ loupe.style.display = 'none'; }};

                document.getElementById(`${{REPLAY_ID}}-btn-crop`).onclick = () => {{
                    isCropping = !isCropping;
                    cropBox.style.display = isCropping ? 'block' : 'none';
                    document.getElementById(`${{REPLAY_ID}}-btn-crop`).style.background = isCropping ? '#4ade80' : 'rgba(255,255,255,0.1)';
                    if (isCropping) {{
                        cropBox.style.width = canvas.offsetWidth + 'px'; cropBox.style.height = canvas.offsetHeight + 'px';
                        cropBox.style.transform = 'translate3d(0,0,0)';
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

                document.getElementById(`${{REPLAY_ID}}-btn-zoom`).onclick = () => {{
                    zoomActive = !zoomActive;
                    document.getElementById(`${{REPLAY_ID}}-btn-zoom`).style.color = zoomActive ? '#4ade80' : '#aaa';
                    if(!zoomActive) loupe.style.display = 'none';
                }};

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
        priority: int = Field(default=2, description="Priorité d'affichage (2 = Deuxième).")

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __event_call__: return None

        uid = __user__.get("id", "anonymous")
        cid = body.get("chat_id") or __metadata__.get("chat_id")
        if not cid: return None

        # Redirection vers le Vault (v3.0)
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        vault_path = os.path.join(ECHO_USERS_ROOT, safe_uid, "files")
        
        prefix = f"U_{uid}_C_{cid}_T_"
        files = []
        try:
            if not os.path.exists(vault_path):
                await events.status("📭 Aucun Vault détecté pour cet utilisateur.", done=True)
                return None

            # Récupération et parsing universel depuis le Vault
            all_entries = os.listdir(vault_path)
            for f_name in all_entries:
                if f_name.startswith(prefix) and f_name.endswith(".png"):
                    try:
                        ts_str = f_name.split("_T_")[-1].split("_")[0].split(".")[0]
                        files.append({"ts": int(ts_str), "name": f_name})
                    except: continue
            
            # Tri CHRONOLOGIQUE STRICT
            files.sort(key=lambda x: x["ts"])
            
        except Exception as e:
            await events.toast(f"Erreur scan Vault : {e}", "error")
            return None

        if not files:
            await events.status("📭 Aucune archive visuelle dans le Vault.", done=True)
            return None

        # 1. Installation de la Console
        shell_code = _generate_replay_shell(files, cid)
        await __event_call__({"type": "execute", "data": {"code": shell_code}})

        # 2. Boucle de Streaming Atomique
        current_idx = 0
        while True:
            # a. PUSH: Envoi de la frame actuelle (Atomic Update)
            f_name = files[current_idx]["name"]
            path = os.path.join(vault_path, f_name)
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
