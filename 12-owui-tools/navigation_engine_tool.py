"""
title: ECHO Navigation Engine
author: Wilfried BARNAVON & ECHO Team
version: 9.4
description: 8.5: RAG éphémère. 8.8: Suppression manual_control. 8.9: Fix Qdrant. 9.0: Fix SigLIP-2. 9.1: bge-m3 — chunking paragraphes sémantiques, max_tokens=8192, dim=1024. 9.2: Fix MODEL_FLASH→MODEL_DISTILLATION sur brief_summary, factorisation vectorisation via index_text_in_ephemeral_rag. 9.3: Suppression ANALYSE_MODEL UserValve. Distillation page via call_cascade() centralisé. 9.4: Propagation user_id au clamp_model (fallback SQLite echo_settings).
"""

import httpx
import orjson as json
import asyncio
import pybase64 as base64
import os
import time
import sys
import uuid
import urllib.parse
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager, generate_echo_file_id, EchoGeminiClient, clamp_model
from echo_ui import EchoUI
from echo_constants import (
    ECHO_UPLOADS_TRANSIT_DIR, MODEL_ROUTING,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

# --- FONCTIONS UTILITAIRES PRIVÉES ---

async def _req(valves: Any, endpoint: str, data: dict = None, user_id: str = "anonymous") -> dict:
    """Effectue une requête POST asynchrone vers le Browser Agent."""
    url = f"{valves.AGENT_URL}{endpoint}"
    headers = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": str(user_id)}
    try:
        async with httpx.AsyncClient(timeout=valves.HTTP_TIMEOUT) as client:
            resp = await client.post(url, json=data or {}, headers=headers)
            return resp.json()
    except Exception as e:
        return {"status": "error", "message": f"Worker inaccessible : {str(e)}"}

async def _verify_engine_status(valves: Any, chat_id: str, user_id: str, u_valves: Any, events: EchoEvents) -> bool:
    """Vérifie si la session du navigateur est active et la redémarre si nécessaire."""
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

async def _deploy_navigation_monitor(valves, res_view, chat_id, uid, u_valves, events: EchoEvents):
    """Déploie l'interface de contrôle du navigateur (HUD) et archive la frame dans le Vault."""
    if not u_valves.SHOW_BROWSER_HUD or not events:
        return "🌐 Navigation en cours...", res_view

    b64 = res_view.get("screenshot_b64", "")
    metadata = res_view.get("metadata", [])
    
    # --- ARCHIVAGE VAULT (Restauration v5.136) ---
    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.png"
            state_manager = EchoStateManager(user_id=uid)
            vault_path = os.path.join(state_manager.user_dir, "files")
            
            if not os.path.exists(vault_path): os.makedirs(vault_path, exist_ok=True)
            
            img_data = base64.b64decode(b64)
            with open(os.path.join(vault_path, filename), "wb") as f: 
                f.write(img_data)
            
            # Indexation SQLite pour le Playback et l'Historique
            state_manager.mark_processed(chat_id, file_id, filename, "image/png", "indexed")
        except Exception as e:
            print(f"[Navigation] Erreur archivage Vault: {e}")

    if b64:
        await EchoUI.safe_deploy(
            events=events,
            monitor_func=EchoUI.monitor_ECHO,
            b64=b64,
            metadata=metadata,
            hud_id=f"nav-{chat_id[:8]}",
            state_key=f"nav_state_{chat_id}",
            current_url=res_view.get("url", "")
        )

    return "🌐 Moniteur de navigation actif.", res_view

class Tools:
    class Valves(BaseModel):
        AGENT_URL: str = Field(default="http://browser-agent:5002", description="URL du container Browser Agent")
        HTTP_TIMEOUT: int = Field(default=120, description="Timeout global (sec).")
        IDLE_TIMEOUT: int = Field(default=900, description="Délai auto-fermeture (sec).")
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Seuil de basculement de clé.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        UPLOADS_DIR: str = Field(default=ECHO_UPLOADS_TRANSIT_DIR, description="Dossier des uploads OWUI")

    class UserValves(BaseModel):
        BROWSER_MODE: Literal["mobile", "desktop"] = Field(default="mobile", description="Mode de navigation (Mobile = Tablette)")
        SHOW_BROWSER_HUD: bool = Field(default=True, description="Afficher le moniteur de navigation (HUD)")
        HUD_VISIBLE_SEC: int = Field(default=90, description="Durée de visibilité du moniteur (sec)")

    def __init__(self):
        self.valves = self.Valves()

    async def web_browse_navigate(self, url: str, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """Accède à une URL et retourne la structure du DOM."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Navigateur indisponible.", status={"status": "error"})
            
        res_nav = await _req(self.valves, "/action", {"session_id": chat_id, "action": "goto", "params": {"url": url}}, uid)
        if res_nav.get("status") == "error": return wrap_tool_output(text=f"❌ Erreur Navigation: {res_nav.get('message')}", status=res_nav)
        
        res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
        report, res_view = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, events)
        
        text_out = f"{report}\nNavigué vers {url}\nStructure : {len(res_view.get('metadata',[]))} éléments interactifs."
        res_view.pop("screenshot_b64", None)
        return wrap_tool_output(text=text_out, status=res_view)

    async def web_browse_interact(
        self, action: Literal["click", "type", "hover", "press", "scroll", "read", "distill_page", "refresh_map", "tab_new", "tab_switch", "tab_close"], 
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
        """Exécute une action interactive sur la page web (click, type, scroll, analyse_html, etc.)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        if action == "distill_page":
            res_action = await _req(self.valves, "/action", {"session_id": chat_id, "action": "read_html"}, uid)
            b64_html = res_action.get("content", "")
            
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            current_url = res_view.get("url", "unknown")
            domain = urllib.parse.urlparse(current_url).netloc.replace(".", "_") if current_url != "unknown" else "page"
            slug = f"{domain}_{uuid.uuid4().hex[:4]}"
            
            await events.status(f"🧠 Distillation de la page → vectorisation locale...")
            try:
                html_text = base64.b64decode(b64_html).decode('utf-8', errors='ignore')
                instruction = text if text else "Analyse ce code HTML et génère un résumé structuré, sémantique et très détaillé de tout le contenu de la page. Extrais les concepts clés, le texte principal, et les données pertinentes."
                prompt = f"SOURCE HTML :\n{html_text[:100000]}\n\nINSTRUCTION :\n{instruction}"
                
                # Distillation via call_cascade centralisé (plus de call_distillation avec target_model)
                analyse_model = clamp_model("MODEL_FLASH", __metadata__, user_id=uid)
                data, _, _ = await EchoGeminiClient.call_cascade(
                    target_model_key=analyse_model,
                    payload={
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"maxOutputTokens": 8192},
                    },
                    user_id=uid,
                    metadata=__metadata__,
                    events=events,
                    timeout=120,
                    include_thoughts=False,
                )
                if not data:
                    return wrap_tool_output(text="❌ Cascade épuisée : aucun modèle disponible pour la distillation.", status={"status": "error"})
                # Extraction texte
                candidates = data.get("candidates", [])
                distillate = "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []) if "text" in p) if candidates else ""
                
                # --- VECTORISATION DANS LE RAG ÉPHÉMÈRE (méthode partagée) ---
                nb_points, err = await EchoGeminiClient.index_text_in_ephemeral_rag(
                    distillate, slug, uid, chat_id, __user__, __metadata__
                )
                if nb_points == 0:
                    return wrap_tool_output(
                        text=f"❌ Distillation réussie mais vectorisation échouée. {err}",
                        status={"status": "error"}
                    )

                # Le status ✅ n'est émis que si l'indexation a réellement réussi
                await events.status(f"✅ Page indexée dans le RAG éphémère ({nb_points} vecteurs).", done=True)

                # brief_summary : tâche de fond invisible → MODEL_DISTILLATION (sans target_model)
                summary_prompt = f"Fais un résumé sémantique large des points clés de ce texte (maximum 1500 mots) :\n\n{distillate[:30000]}"
                brief_summary = await EchoGeminiClient.call_distillation(summary_prompt, __user__, __metadata__, is_json=False, max_tokens=8000, target_model="MODEL_DISTILLATION")
                
                out_msg = (
                    f"✅ Page web traitée et indexée sous le slug `{slug}` ({nb_points} vecteurs).\n\n"
                    f"### Résumé Sémantique\n{brief_summary}\n\n"
                    f"> **Action requise :** Utilisez l'outil `search_session_context(slug=\"{slug}\", query=\"...\")` pour interroger la page en profondeur."
                )
                
                # IMPORTANT: On ne retourne pas res_action dans status, car il contient 'content' (le HTML b64 complet)
                clean_status = {k: v for k, v in res_action.items() if k != "content"}
                return wrap_tool_output(text=out_msg, status=clean_status)

            except Exception as e:
                return wrap_tool_output(text=f"❌ Erreur analyse: {str(e)}", status={})

        if action == "refresh_map":
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            report, res_view = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, events)
            res_view.pop("screenshot_b64", None)
            return wrap_tool_output(text=f"{report}\nCarte du DOM mise à jour.", status=res_view)

        params = {"selector": selector, "text": text, "key": key, "direction": direction, "url": url, "index": index}
        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": action, "params": params}, uid)
        
        if res.get("status") == "success":
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            report, _ = await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, events)
            return wrap_tool_output(text=f"{report}\nAction {action} terminée avec succès.", status=res)
            
        return wrap_tool_output(text=f"❌ Échec action {action}: {res.get('message')}", status=res)

    async def web_browse_reset(self, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """Réinitialise la session du navigateur (Fermeture et Nettoyage)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "reset"}, uid)
        return wrap_tool_output(text="🚀 Navigateur réinitialisé.", status=res)

    async def get_web_object_url(
        self, index: int, attribute: Literal["src", "href"] = "src", 
        __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None
    ) -> dict:
        """
        Extrait une URL absolue (src ou href) d'un élément spécifique de la page via son index numérique.
        Utile pour récupérer des sources d'images ou des liens de téléchargement directs.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())

        if not await _verify_engine_status(self.valves, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        res = await _req(self.valves, "/action", {"session_id": chat_id, "action": "get_attribute", "params": {"index": index, "attribute": attribute}}, uid)   

        if res.get("status") == "success" and res.get("value"):
            res_view = await _req(self.valves, "/action", {"session_id": chat_id, "action": "highlight"}, uid)
            await _deploy_navigation_monitor(self.valves, res_view, chat_id, uid, u_valves, events)
            return wrap_tool_output(text=f"✅ URL absolue : {res['value']}", status=res)
        return wrap_tool_output(text=f"❌ Erreur: {res.get('message', 'Attribut non trouvé.')}", status=res)

    async def get_browser_frames_history(
        self, depth: Optional[int] = 10, __user__: dict = {}, __metadata__: dict = {}
    ) -> dict:
        """
        Consulte l'historique des captures d'écran de la session actuelle dans le Vault.
        Permet de revoir visuellement les étapes précédentes sans recharger la page.
        """
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        state_manager = EchoStateManager(user_id=uid)

        try:
            conn = state_manager._get_connection()
            cursor = conn.cursor()
            query = "SELECT file_id, filename, mime, timestamp FROM processed_files WHERE chat_id = ? AND file_id LIKE 'U_%_C_%_T_%' ORDER BY timestamp DESC LIMIT ?"   
            cursor.execute(query, (chat_id, depth))
            rows = cursor.fetchall()
            conn.close()

            history, nouveaux = [],[]
            for row in rows:
                fid, fname, fmime, ts = row[0], row[1], row[2] or "image/png", row[3]
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
                history.append({"file_id": fid, "date": dt, "frame": fname})
                nouveaux.append({"nom": fname, "id": fid, "mime": fmime, "statut": "indexed"})

            return wrap_tool_output(text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'), status={"status": "success"}, nouveaux_fichiers=nouveaux)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"})
