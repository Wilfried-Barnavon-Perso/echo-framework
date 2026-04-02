import httpx
import orjson as json
import asyncio
import pybase64 as base64
import os
import time
import sys
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

"""
================================================================================
TOOL : ECHO NAVIGATION ENGINE (v6.70)
VERSION : 6.70
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-04-01

CHANGELOG 6.70 :
- REFACTOR: Délégation totale du HUD interactif à la classe EchoUI (echo_utils).
CHANGELOG 6.69 :
- PERF: Migration to orjson and pybase64 for high-performance processing.
- FIX: Explicit decoding for JSON strings injected into JavaScript Cockpit.
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
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager, generate_echo_file_id, EchoUI
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
                text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'), 
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
        events_obj = EchoEvents(caller=__event_call__)
        await EchoUI.monitor_ECHO(
            events=events_obj,
            b64=res_view["screenshot_b64"],
            mime="image/png",
            hud_id="echo-browser-monitor",
            title="🌐 ECHO MONITOR",
            state_key=f"echo_state_{chat_id[:8]}",
            timeout=u_valves.HUD_VISIBLE_SEC
        )
    return filename
