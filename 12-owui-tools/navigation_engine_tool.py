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
TOOL : ECHO NAVIGATION ENGINE (v7.5)
VERSION : 7.5
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-04-17

CHANGELOG 7.5 :
- FEAT: Instruction agentique sur la gestion des erreurs de délégation (analyse_html).
CHANGELOG 7.4 :
- REFACTOR: Mise à jour des imports suite à l'éclatement de echo_utils.py.
CHANGELOG 7.3 :
- FEAT: Ajout de l'outil `web_browse_manual_control` permettant à l'utilisateur de forcer l'ouverture du HUD en mode Humain sans action préalable de l'IA.
CHANGELOG 7.0 :
- FEAT: Co-pilot Edition. Implémentation du Handover hybride (IA/Humain).
- SECURITY: Intégration du Dead Man's Switch (180s) pour garantir la stabilité de session.
================================================================================
"""

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager, generate_echo_file_id, EchoGeminiClient, EchoAuth
from echo_ui import EchoUI
from echo_constants import ECHO_UPLOADS_DIR, MODEL_FLASH, MODEL_LITE, MODEL_PRO

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
        ANALYSE_MODEL: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = Field(default="MODEL_FLASH", description="Modèle utilisé pour l'analyse sémantique du HTML")

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
        
        # HANDOVER LOOP (v6.0)
        report, res_view = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
        
        text_out = f"{report}\nNavigué vers {url}\nStructure actuelle : {len(res_view.get('metadata',[]))} éléments interactifs."
        res_view.pop("screenshot_b64", None)
        return wrap_tool_output(text=text_out, status=res_view)

    async def web_browse_interact(
        self, 
        action: Literal["click", "type", "hover", "press", "scroll", "read", "read_html", "analyse_html", "refresh_map", "tab_new", "tab_switch", "tab_close"], 
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
        :param action: L'action à effectuer (read_html pour le code brut, analyse_html pour une extraction sémantique via LLM).
        :param index: ID numérique de l'élément cible (RECOMMANDÉ).
        :param text: Texte à saisir ou instruction pour analyse_html.

        NOTE ANALYSE_HTML : En cas d'échec de la délégation cognitive (ex: erreur quota 429), 
        le Modèle peut décider de renouveler sa demande avec un niveau cognitif différent 
        (configurable via les Valves utilisateur) ou procéder par lecture brute.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        if action == "refresh_map":
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            report, res_view = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
            res_view.pop("screenshot_b64", None)
            return wrap_tool_output(text=f"{report}\nCarte du DOM mise à jour : {len(res_view.get('metadata',[]))} éléments.", status=res_view)

        params = {"selector": selector, "text": text, "key": key, "direction": direction, "url": url, "index": index}
        # Routage interne : analyse_html utilise la fonction read_html du worker
        worker_action = "read_html" if action == "analyse_html" else action
        res_action = await _req(self.valves, "/action", {"session_id": chat_id, "action": worker_action, "params": params}, uid)
        
        report = ""
        if res_action.get("status") == "success":
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            report, res_view = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)

        if res_action.get("status") == "error": 
            res_action.pop("screenshot_b64", None)
            return wrap_tool_output(text=f"❌ Échec action {action}: {res_action.get('message')}", status=res_action)
            
        if action == "read": 
            res_action.pop("screenshot_b64", None)
            return wrap_tool_output(text=res_action.get("content", ""), status=res_action)

        if action == "read_html":
            res_action.pop("screenshot_b64", None)
            b64_html = res_action.pop("content", "")
            multiparts =[{"type": "media", "mime_type": "text/plain", "data": b64_html}]
            return wrap_tool_output(text="### Source HTML récupérée.", status=res_action, echo_tool_multiparts=multiparts)

        if action == "analyse_html":
            res_action.pop("screenshot_b64", None)
            b64_html = res_action.pop("content", "")
            
            # --- ANALYSE SÉMANTIQUE DÉLÉGUÉE (v7.3) ---
            await events.status(f"🧠 Analyse sémantique HTML ({u_valves.ANALYSE_MODEL})...")
            try:
                html_text = base64.b64decode(b64_html).decode('utf-8', errors='ignore')
                auth = EchoAuth(user_id=uid)
                api_keys = auth.get_api_keys(uid)
                
                if not api_keys:
                    return wrap_tool_output(text="❌ Erreur: Aucune clé API Google Studio trouvée pour l'analyse.", status=res_action)

                # Résolution du modèle
                model_map = {"MODEL_LITE": MODEL_LITE, "MODEL_FLASH": MODEL_FLASH, "MODEL_PRO": MODEL_PRO}
                target_model = model_map.get(u_valves.ANALYSE_MODEL, MODEL_FLASH)
                
                # Construction du prompt
                instruction = text if text else "Analyse ce code HTML et extrais de manière structurée les informations principales, le texte lisible et les liens importants."
                prompt = f"SOURCE HTML :\n{html_text[:100000]}\n\nINSTRUCTION :\n{instruction}"
                
                payload = {
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.3, "maxOutputTokens": 4096}
                }
                
                data = await EchoGeminiClient.call(keys=api_keys, target_model=target_model, payload=payload, events=events)
                
                # Extraction de la réponse
                analysis_res = "Analyse indisponible."
                target = data.get("response", {}) if "response" in data else data
                candidates = target.get("candidates", [])
                if candidates and candidates[0].get("content"):
                    analysis_res = "".join([p.get("text", "") for p in candidates[0]["content"].get("parts", [])])
                
                await events.status("✅ Analyse HTML terminée.", done=True)
                return wrap_tool_output(text=f"### Analyse Sémantique de la Page ({u_valves.ANALYSE_MODEL})\n\n{analysis_res}", status=res_action)
                
            except Exception as e:
                await events.status(f"❌ Erreur analyse: {str(e)}", done=True)
                return wrap_tool_output(text=f"❌ Erreur lors de l'analyse sémantique : {str(e)}", status=res_action)
            
        res_action.pop("screenshot_b64", None)
        return wrap_tool_output(text=f"{report}\nAction {action} terminée avec succès.", status=res_action)

    async def get_web_object_url(self, index: int, attribute: Literal["src", "href"] = "src", __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        events = EchoEvents(__event_emitter__, __event_call__)

        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "get_attribute", "params": {"index": index, "attribute": attribute}}, uid)
        
        if res.get("status") == "success" and res.get("value"):
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            report, _ = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, __event_call__)
            return wrap_tool_output(text=f"{report}\n✅ URL absolue : {res['value']}", status=res)
        return wrap_tool_output(text=f"❌ Erreur: {res.get('message', 'Attribut non trouvé.')}", status=res)

    async def web_browse_reset(self, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        
        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "reset"}, uid)
        if __event_call__:
            try: await __event_call__({"type": "execute", "data": {"code": "const h=document.getElementById('echo-webplayer'); if(h) h.remove();"}})
            except: pass
        return wrap_tool_output(text="🚀 Navigateur réinitialisé avec succès.", status=res)

    async def get_browser_frames_history(self, depth: Optional[int] = None, __user__: dict = {}, __metadata__: dict = {}) -> dict:
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        state_manager = EchoStateManager(user_id=uid)
        
        try:
            conn = state_manager._get_connection()
            cursor = conn.cursor()
            query = "SELECT file_id, filename, mime, timestamp FROM processed_files WHERE chat_id = ? AND file_id LIKE 'U_%_C_%_T_%' ORDER BY timestamp DESC"
            params = [chat_id]
            if depth: query += " LIMIT ?"; params.append(depth)
            
            cursor.execute(query, params)
            rows = cursor.fetchall()
            conn.close()
            
            history, nouveaux = [],[]
            for row in rows:
                fid, fname, fmime, ts = row[0], row[1], row[2] or "image/png", row[3]
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                history.append({"file_id": fid, "date": dt, "usage": f"semantic_probe('{fid}') or read_raw('{fid}')"})
                nouveaux.append({"nom": fname, "id": fid, "mime": fmime, "statut": "indexed"})
            
            return wrap_tool_output(text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'), status={"status": "success"}, nouveaux_fichiers=nouveaux)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"})

async def _deploy_navigation_monitor(valves: Any, res_view: dict, chat_id: str, user_id: str, u_valves: Any, __event_call__) -> tuple:
    """Déploie le WebPlayer (Mode Auto)."""
    if not __event_call__ or not u_valves.SHOW_BROWSER_HUD: return "", res_view
    
    events_obj = EchoEvents(caller=__event_call__)
    
    file_id = generate_echo_file_id(user_id, chat_id)
    filename = f"{file_id}_frame.png"
    state_manager = EchoStateManager(user_id=user_id)
    vault_path = os.path.join(state_manager.user_dir, "files")
    try:
        img_data = base64.b64decode(res_view["screenshot_b64"])
        with open(os.path.join(vault_path, filename), "wb") as f: f.write(img_data)
        state_manager.mark_processed(chat_id, file_id, filename, "image/png", "indexed")
    except: pass

    await EchoUI.monitor_ECHO(
        events=events_obj,
        b64=res_view["screenshot_b64"],
        metadata=res_view.get("metadata",[]),
        current_url=res_view.get("url", ""),
        hud_id="echo-webplayer",
        state_key="echo_webplayer_state"
    )
    
    res_view = await _req(valves, "/action", {"session_id": chat_id, "action": "highlight"}, user_id)
    return "", res_view
