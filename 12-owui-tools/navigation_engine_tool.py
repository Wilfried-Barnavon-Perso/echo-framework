import httpx
import json
import asyncio
import base64
import os
import time
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

"""
================================================================================
TOOL : ECHO NAVIGATION ENGINE (v6.13 - UNIFIED INFRA)
VERSION : 6.13 (FIXED CONSTANTS)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-03-01

CHANGELOG 6.13 :
- FIX: Corrected missing ECHO_UPLOADS_DIR import.
================================================================================
"""

# Import Lib Partagée (Volume Docker)
import sys
sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoEvents
    from echo_constants import ECHO_UPLOADS_DIR
except ImportError:
    class EchoEvents:
        def __init__(self, e=None, c=None): pass
        async def status(self, d, done=False): pass
        async def toast(self, c, l="info"): pass
        async def call(self, t, d): return None
    ECHO_UPLOADS_DIR = "/app/backend/data/uploads"

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
    """Moteur JS Cockpit v2.1 : Fluidité absolue et Géométrie de précision."""
    return f"""
    (function() {{
        try {{
            const data = {{ b64: "{b64}", sid: "{sid}", cid: "{chat_id}", timeout: {timeout} }};
            const HUD_ID = 'echo-browser-monitor';
            let hud = document.getElementById(HUD_ID);
            
            if (hud && hud.getAttribute('data-chat-id') !== data.cid) {{
                hud.remove();
                hud = null;
            }}

            if (!hud) {{
                hud = document.createElement('div');
                hud.id = HUD_ID;
                hud.setAttribute('data-chat-id', data.cid);
                hud.style.cssText = 'position:fixed; z-index:10000; background:rgba(30,30,30,0.9); backdrop-filter:blur(12px); border:1px solid #444; border-radius:8px; box-shadow:0 10px 40px rgba(0,0,0,0.6); color:white; font-family:sans-serif; display:flex; flex-direction:column; overflow:visible; opacity:0; min-width:200px;';
                
                const saved = JSON.parse(localStorage.getItem('echo_hud_state') || '{{"w":400, "t":50, "l":null}}');
                hud.style.width = (saved.w || 400) + 'px';
                hud.style.top = (saved.t || 50) + 'px';
                if (saved.l !== null) hud.style.left = saved.l + 'px';
                else hud.style.right = '20px';

                hud.innerHTML = `
                    <div id="${{HUD_ID}}-header" style="padding:6px 10px; background:rgba(0,0,0,0.3); display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #444; cursor:move; user-select:none; border-radius: 8px 8px 0 0;">
                        <div style="display:flex; align-items:center; gap:6px;">
                            <span style="font-size:10px; font-weight:bold; color:#4ade80;">🌐 ECHO MONITOR</span>
                            <span id="${{HUD_ID}}-timer" style="font-size:9px; color:#888;"></span>
                        </div>
                        <div style="display:flex; gap:8px;">
                            <button id="${{HUD_ID}}-btn-min" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:0 4px;">_</button>
                            <button id="${{HUD_ID}}-btn-def" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:0 4px;">↺</button>
                            <button id="${{HUD_ID}}-btn-full" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:0 4px;">□</button>
                            <button id="${{HUD_ID}}-btn-close" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:16px; font-weight:bold; padding:0 4px;">×</button>
                        </div>
                    </div>
                    <div id="${{HUD_ID}}-area" style="flex:1; width:100%; height:100%; background:black; display:flex; justify-content:center; overflow:hidden; border-radius: 0 0 8px 8px;">
                        <img id="${{HUD_ID}}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" />
                    </div>
                    <!-- Poignées de redimensionnement robustes (20px) -->
                    <div class="hdl tl" style="position:absolute; width:20px; height:20px; left:-10px; top:-10px; cursor:nwse-resize; z-index:100;"></div>
                    <div class="hdl tr" style="position:absolute; width:20px; height:20px; right:-10px; top:-10px; cursor:nesw-resize; z-index:100;"></div>
                    <div class="hdl bl" style="position:absolute; width:20px; height:20px; left:-10px; bottom:-10px; cursor:nesw-resize; z-index:100;"></div>
                    <div class="hdl br" style="position:absolute; width:20px; height:20px; right:-10px; bottom:-10px; cursor:nwse-resize; z-index:100;"></div>
                `;
                document.body.appendChild(hud);
                setTimeout(() => hud.style.opacity = '1', 50);

                // --- RESET TOTAL (Double Clic) ---
                hud.ondblclick = (e) => {{
                    hud.style.transition = 'all 0.3s ease-out';
                    const dw = 400; // Largeur par défaut
                    const dh = dw * (window.echoRatio || (1180/820));
                    const nl = (window.innerWidth - dw) / 2;
                    const nt = (window.innerHeight - dh) / 2;
                    
                    hud.style.width = dw + "px";
                    hud.style.height = dh + "px";
                    hud.style.left = nl + "px"; 
                    hud.style.top = nt + "px"; 
                    hud.style.right = "auto";
                    
                    localStorage.setItem('echo_hud_state', JSON.stringify({{w:dw, t:nt, l:nl}}));
                    setTimeout(() => hud.style.transition = 'none', 350);
                }};

                // --- DRAG LOGIC ---
                const header = document.getElementById(`${{HUD_ID}}-header`);
                header.onmousedown = (e) => {{
                    if (e.target.tagName === 'BUTTON') return;
                    e.preventDefault();
                    hud.style.transition = 'none';
                    const rect = hud.getBoundingClientRect();
                    let ox=e.clientX, oy=e.clientY;
                    document.onmousemove = (me) => {{
                        const dx=me.clientX-ox, dy=me.clientY-oy;
                        ox=me.clientX; oy=me.clientY;
                        hud.style.top = (hud.offsetTop + dy) + "px";
                        hud.style.left = (hud.offsetLeft + dx) + "px";
                        hud.style.right = "auto";
                    }};
                    document.onmouseup = () => {{ 
                        document.onmousemove=null; 
                        localStorage.setItem('echo_hud_state', JSON.stringify({{w:hud.offsetWidth, t:hud.offsetTop, l:hud.offsetLeft}}));
                    }};
                }};

                // --- RESIZE LOGIC (4 CORNERS) ---
                hud.querySelectorAll('.hdl').forEach(hdl => {{
                    hdl.onmousedown = (e) => {{
                        e.preventDefault(); e.stopPropagation();
                        hud.style.transition = 'none';
                        const isR = hdl.classList.contains('tr') || hdl.classList.contains('br');
                        const isT = hdl.classList.contains('tl') || hdl.classList.contains('tr');
                        const rect = hud.getBoundingClientRect();
                        const startW = rect.width, startH = rect.height, startT = rect.top, startL = rect.left;
                        const ox = e.clientX, oy = e.clientY;

                        document.onmousemove = (me) => {{
                            const r = window.echoRatio || (1180/820);
                            let nw = isR ? (startW + (me.clientX - ox)) : (startW - (me.clientX - ox));
                            if (nw < 250) nw = 250;
                            const nh = nw * r;
                            if (!isR) hud.style.left = (startL + (startW - nw)) + "px";
                            if (isT) hud.style.top = (startT + (startH - nh)) + "px";
                            hud.style.width = nw + 'px'; hud.style.height = nh + 'px';
                            hud.style.right = "auto";
                        }};
                        document.onmouseup = () => {{ 
                            document.onmousemove = null;
                            localStorage.setItem('echo_hud_state', JSON.stringify({{w:hud.offsetWidth, t:hud.offsetTop, l:hud.offsetLeft}}));
                        }};
                    }};
                }});

                // --- BUTTONS ---
                document.getElementById(`${{HUD_ID}}-btn-min`).onclick = (e) => {{
                    e.stopPropagation(); const a = document.getElementById(`${{HUD_ID}}-area`);
                    a.style.display = a.style.display === 'none' ? 'flex' : 'none';
                    hud.style.height = a.style.display === 'none' ? 'auto' : (hud.offsetWidth * (window.echoRatio||1)) + 'px';
                }};
                document.getElementById(`${{HUD_ID}}-btn-def`).onclick = (e) => {{
                    e.stopPropagation(); hud.style.transition = 'all 0.3s';
                    hud.style.width = '400px'; hud.style.height = (400 * (window.echoRatio||1)) + 'px';
                    document.getElementById(`${{HUD_ID}}-area`).style.display = 'flex';
                    setTimeout(() => hud.style.transition = 'none', 350);
                }};
                document.getElementById(`${{HUD_ID}}-btn-full`).onclick = (e) => {{
                    e.stopPropagation(); hud.style.transition = 'all 0.3s';
                    hud.style.width = '90vw'; hud.style.height = '90vh'; hud.style.top = '5vh'; hud.style.left = '5vw';
                    document.getElementById(`${{HUD_ID}}-area`).style.display = 'flex';
                    setTimeout(() => hud.style.transition = 'none', 350);
                }};
                document.getElementById(`${{HUD_ID}}-btn-close`).onclick = (e) => {{ e.stopPropagation(); hud.remove(); }};
            }}

            const img = document.getElementById(`${{HUD_ID}}-img`);
            img.onload = () => {{
                window.echoRatio = img.naturalHeight / img.naturalWidth;
                const a = document.getElementById(`${{HUD_ID}}-area`);
                if (a.style.display !== 'none') hud.style.height = (hud.offsetWidth * window.echoRatio) + 'px';
            }};
            img.src = "data:image/png;base64," + data.b64;
            
            if (window.echoHudInterval) clearInterval(window.echoHudInterval);
            let timeLeft = data.timeout;
            const tSpan = document.getElementById(`${{HUD_ID}}-timer`);
            window.echoHudInterval = setInterval(() => {{
                timeLeft--;
                if(tSpan) tSpan.innerText = `[${{timeLeft}}s]`;
                if (timeLeft <= 5) hud.style.opacity = (timeLeft / 5);
                if (timeLeft <= 0) {{ clearInterval(window.echoHudInterval); hud.remove(); }}
            }}, 1000);
            return true;
        }} catch (e) {{ return e.toString(); }}
    }})();
    """

async def _deploy_navigation_monitor(valves: Any, res_view: dict, chat_id: str, user_id: str, u_valves: Any, __event_call__) -> str:
    if not res_view.get("screenshot_b64"): return ""
    ts = int(time.time())
    filename = f"U_{user_id}_C_{chat_id}_T_{ts}.png"
    filepath = os.path.join(valves.UPLOADS_DIR, filename)
    try:
        img_data = base64.b64decode(res_view["screenshot_b64"])
        with open(filepath, "wb") as f: f.write(img_data)
    except: pass
    if __event_call__ and u_valves.SHOW_BROWSER_HUD:
        code = _generate_monitor_js(res_view["screenshot_b64"], chat_id[:8], chat_id, u_valves.HUD_VISIBLE_SEC)
        try: await asyncio.wait_for(__event_call__({"type": "execute", "data": {"code": code}}), timeout=5.0)
        except: pass
    return filename

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
        HUD_VISIBLE_SEC: int = Field(default=30, description="Durée de visibilité du moniteur (sec)")

    def __init__(self):
        self.valves = self.Valves()

    async def web_browse_navigate(self, url: str, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> str:
        """[POINT D'ENTRÉE] Accède à une URL et retourne la structure du DOM."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return "ERREUR_SYSTÈME : Navigateur indisponible."
        res_nav = await _req(self.valves, "/action", {"session_id": chat_id, "action": "goto", "params": {"url": url}}, uid)
        if res_nav.get("status") == "error": return f"ACTION_FAILED: {res_nav.get('message')}"
        res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
        filename = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
        return json.dumps({"url": res_view.get("url"), "screenshot_filename": filename, "interactive_elements": res_view.get("metadata", [])}, ensure_ascii=False)

    async def web_browse_interact(
        self, 
        action: Literal["click", "type", "hover", "press", "scroll", "read", "read_html", "tab_new", "tab_switch", "tab_close"], 
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
    ) -> str:
        """[MANIPULATION] Exécute une action sur la page (clic via ID rouge #0, saisie, défilement)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return "ERREUR_SYSTÈME : Session perdue."
        params = {"selector": selector, "text": text, "key": key, "direction": direction, "url": url, "index": index}
        res_action = await _req(self.valves, "/action", {"session_id": chat_id, "action": action, "params": params}, uid)
        if res_action.get("status") == "error": return f"ACTION_FAILED: {res_action.get('message')}"
        if action in ["read", "read_html"]:
            content = res_action.get("content", "")
            return f"CONTENU_BRUT ({res_action.get('url')}) :\n\n{content}"
        res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
        filename = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
        return json.dumps({"action_performed": action, "url": res_view.get("url"), "screenshot_filename": filename, "interactive_elements": res_view.get("metadata", [])}, ensure_ascii=False)
