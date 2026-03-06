"""
title: Show Web Replay
author: Wilfried BARNAVON
version: 1.9
description: 1.9: Enhanced Ergonomics (Grouped Motor, Symmetrical Spacing & Reversed Play Icon).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxyZWN0IHdpZHRoPSIxOCIgaGVpZ2h0PSIxOCIgeD0iMyIgeT0iMyIgcng9IjIiLz48cGF0aCBkPSJNNyAzdjE4Ii8+PHBhdGggZD0iTTE3IDN2MTgiLz48cGF0aCBkPSJNMyA3aDQiLz48cGF0aCBkPSJNMyAxMmg0Ii8+PHBhdGggZD0iTTMgMTdoNCIvPjxwYXRoIGQ9Ik0xNyA3aDQiLz48cGF0aCBkPSJNMTcgMTJoNCIvPjxwYXRoIGQ9Ik0xNyAxN2g0Ii8+PC9zdmc+
"""

import os
import json
import base64
import time
import logging
import sys
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
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
    ts_json = json.dumps(timestamps)
    return f"""
    (function() {{
        try {{
            const data = {{ timestamps: {ts_json}, cid: "{chat_id}" }};
            const REPLAY_ID = 'echo-browser-replay';
            const RATIO = 1180 / 820;
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
                    <div style="position:absolute; top:20px; right:20px; z-index:100;">
                        <button id="${{REPLAY_ID}}-close" style="background:rgba(255,255,255,0.1); border:none; color:white; font-size:24px; cursor:pointer; width:40px; height:40px; border-radius:50%;">×</button>
                    </div>
                    
                    <div id="${{REPLAY_ID}}-viewport" style="flex:1; width:100%; display:flex; justify-content:center; overflow-y:auto; padding:40px 0; scrollbar-width: thin; scrollbar-color: #4ade80 transparent;">
                        <div id="${{REPLAY_ID}}-canvas" style="position:relative; width:600px; height:${{600 * RATIO}}px; background:#111; box-shadow:0 0 100px rgba(0,0,0,1); border:1px solid #333; border-radius:4px; cursor:crosshair;">
                            <img id="${{REPLAY_ID}}-img" style="width:100%; height:100%; display:block; border-radius:4px; opacity:0; transition:opacity 0.2s;" />
                            <div id="${{REPLAY_ID}}-loupe" style="position:absolute; width:200px; height:200px; border:2px solid #4ade80; border-radius:50%; pointer-events:none; display:none; background-repeat:no-repeat; box-shadow:0 0 30px rgba(0,0,0,0.8); z-index:100;"></div>
                        </div>
                    </div>

                    <div style="width:100%; background:rgba(0,0,0,0.5); border-top:1px solid #333; padding:20px; display:flex; flex-direction:column; align-items:center; gap:15px; backdrop-filter:blur(10px);">
                        <div id="${{REPLAY_ID}}-meta" style="font-size:11px; color:#4ade80; font-family:monospace; letter-spacing:1px; background:rgba(0,0,0,0.3); padding:4px 12px; border-radius:10px;">INITIALISATION...</div>
                        
                        <div style="display:flex; gap:15px; align-items:center;">
                            <!-- Bloc Manuel Gauche -->
                            <button id="${{REPLAY_ID}}-first" style="background:none; border:none; color:white; cursor:pointer; font-size:18px;">|◀</button>
                            <button id="${{REPLAY_ID}}-prev" style="background:none; border:none; color:white; cursor:pointer; font-size:18px; margin-right:40px;">◀</button>
                            
                            <!-- Bloc Moteur Central Groupé -->
                            <div style="display:flex; gap:10px; align-items:center;">
                                <button id="${{REPLAY_ID}}-play-rev" style="background:#f97316; border:none; color:white; padding:10px 25px; border-radius:20px 5px 5px 20px; font-weight:bold; cursor:pointer; min-width:110px;">◀ PLAY</button>
                                <button id="${{REPLAY_ID}}-play" style="background:#4ade80; border:none; color:black; padding:10px 25px; border-radius:5px 20px 20px 5px; font-weight:bold; cursor:pointer; min-width:110px;">PLAY ▶</button>
                            </div>

                            <!-- Bloc Manuel Droite -->
                            <button id="${{REPLAY_ID}}-next" style="background:none; border:none; color:white; cursor:pointer; font-size:18px; margin-left:40px;">▶</button>
                            <button id="${{REPLAY_ID}}-last" style="background:none; border:none; color:white; cursor:pointer; font-size:18px;">▶|</button>
                        </div>

                        <div style="display:flex; gap:15px; align-items:center; width:400px; opacity:0.8;">
                            <input type="range" id="${{REPLAY_ID}}-speed" min="1" max="30" value="5" style="flex:1; height:4px; cursor:pointer; accent-color:#4ade80;">
                            <span id="${{REPLAY_ID}}-speed-val" style="font-size:10px; color:#888; width:30px;">5s</span>
                        </div>
                    </div>
                `;
                document.body.appendChild(replay);

                // --- Atomic State ---
                let currentIndex = 0;
                let isPlaying = false;
                let isPlayingRev = false;
                let playInterval = null;
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
                    document.getElementById(`${{REPLAY_ID}}-meta`).innerText = `REQUÊTE FLUX... (${{idx + 1}}/${{timestamps.length}})`;
                    if(window.echoReplayResolve) {{
                        window.echoReplayResolve({{ action: "goto", index: idx, name: timestamps[idx].name }});
                    }}
                }};

                const updateUI = (b64, ts, current, total) => {{
                    const img = document.getElementById(`${{REPLAY_ID}}-img`);
                    const loupe = document.getElementById(`${{REPLAY_ID}}-loupe`);
                    const date = new Date(ts * 1000).toLocaleString('fr-FR');
                    
                    img.onload = () => {{ img.style.opacity = '1'; }};
                    img.src = "data:image/png;base64," + b64;
                    loupe.style.backgroundImage = "url(" + img.src + ")";
                    document.getElementById(`${{REPLAY_ID}}-meta`).innerText = `PREUVE DU ${{date}} (${{current}}/${{total}})`;
                }};
                window.echoReplayUpdate = updateUI;

                // --- Loupe ---
                const canvas = document.getElementById(`${{REPLAY_ID}}-canvas`);
                const loupe = document.getElementById(`${{REPLAY_ID}}-loupe`);
                canvas.onmousemove = (e) => {{
                    const rect = canvas.getBoundingClientRect();
                    const x = e.clientX - rect.left;
                    const y = e.clientY - rect.top;
                    loupe.style.display = 'block';
                    loupe.style.left = (x - 100) + 'px';
                    loupe.style.top = (y - 100) + 'px';
                    const zoom = 2.5;
                    loupe.style.backgroundSize = (rect.width * zoom) + 'px ' + (rect.height * zoom) + 'px';
                    loupe.style.backgroundPosition = `-${{(x * zoom) - 100}}px -${{(y * zoom) - 100}}px`;
                }};
                canvas.onmouseleave = () => {{ loupe.style.display = 'none'; }};

                // --- Handlers ---
                document.getElementById(`${{REPLAY_ID}}-first`).onclick = () => {{ stopPlayback(); requestFrame(0); }};
                document.getElementById(`${{REPLAY_ID}}-last`).onclick = () => {{ stopPlayback(); requestFrame(timestamps.length - 1); }};
                document.getElementById(`${{REPLAY_ID}}-prev`).onclick = () => {{ stopPlayback(); requestFrame(Math.max(0, currentIndex - 1)); }};
                document.getElementById(`${{REPLAY_ID}}-next`).onclick = () => {{ stopPlayback(); requestFrame(Math.min(timestamps.length - 1, currentIndex + 1)); }};
                
                document.getElementById(`${{REPLAY_ID}}-play`).onclick = () => {{
                    if (isPlaying) {{
                        stopPlayback();
                    }} else {{
                        stopPlayback();
                        isPlaying = true;
                        const btn = document.getElementById(`${{REPLAY_ID}}-play`);
                        btn.innerText = "⏸ STOP";
                        btn.style.background = "#ff4444";
                        playInterval = setInterval(() => {{
                            requestFrame(currentIndex + 1);
                        }}, document.getElementById(`${{REPLAY_ID}}-speed`).value * 1000);
                    }}
                }};

                document.getElementById(`${{REPLAY_ID}}-play-rev`).onclick = () => {{
                    if (isPlayingRev) {{
                        stopPlayback();
                    }} else {{
                        stopPlayback();
                        isPlayingRev = true;
                        const btn = document.getElementById(`${{REPLAY_ID}}-play-rev`);
                        btn.innerText = "⏸ STOP";
                        btn.style.background = "#ff4444";
                        playInterval = setInterval(() => {{
                            requestFrame(currentIndex - 1);
                        }}, document.getElementById(`${{REPLAY_ID}}-speed`).value * 1000);
                    }}
                }};

                document.getElementById(`${{REPLAY_ID}}-speed`).oninput = (e) => {{
                    document.getElementById(`${{REPLAY_ID}}-speed-val`).innerText = e.target.value + "s";
                }};

                document.getElementById(`${{REPLAY_ID}}-close`).onclick = () => {{
                    stopPlayback();
                    replay.remove();
                    if(window.echoReplayResolve) window.echoReplayResolve({{ action: "close" }});
                }};
            }}
            return true;
        }} catch (e) {{ return e.toString(); }}
    }})();
    """

class Action:
    class Valves(BaseModel):
        priority: int = Field(default=2, description="Priorité d'affichage (2 = Deuxième).")
        UPLOADS_DIR: str = Field(default="/app/backend/data/uploads", description="Dossier des captures ECHO")

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __event_call__: return None

        uid = __user__.get("id", "anonymous")
        cid = body.get("chat_id") or __metadata__.get("chat_id")
        if not cid: return None

        prefix = f"U_{uid}_C_{cid}_T_"
        files = []
        try:
            # Tri CHRONOLOGIQUE (du plus ancien au plus récent)
            all_files = sorted(os.listdir(self.valves.UPLOADS_DIR), reverse=False)
            for f_name in all_files:
                if f_name.startswith(prefix) and f_name.endswith(".png"):
                    ts_str = f_name.replace(prefix, "").replace(".png", "")
                    try: files.append({"ts": int(ts_str), "name": f_name})
                    except: pass
        except Exception as e:
            await events.toast(f"Erreur scan : {e}", "error")
            return None

        if not files:
            await events.status("📭 Aucune archive visuelle.", done=True)
            return None

        # 1. Installation de la Console
        shell_code = _generate_replay_shell(files, cid)
        await __event_call__({"type": "execute", "data": {"code": shell_code}})

        # 2. Boucle de Streaming Atomique
        current_idx = 0
        while True:
            # a. PUSH: Envoi de la frame actuelle (Atomic Update)
            f_name = files[current_idx]["name"]
            path = os.path.join(self.valves.UPLOADS_DIR, f_name)
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
