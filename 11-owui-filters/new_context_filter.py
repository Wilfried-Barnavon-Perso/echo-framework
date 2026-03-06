"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 6.11
description: 6.11: Standardized 'version_echo' key & Dynamic Template.
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Union, Dict, Any
import json
import os
import sys
import re
import asyncio
import logging
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoStateManager, resolve_upload_file_path
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    class Valves(BaseModel):
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        MAX_DIRECT_TEXT_SIZE: int = Field(default=262144, description="Taille max (octets) pour l'injection directe sans résumé.")
        DEBUG_MODE: bool = Field(default=False)

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    def _process_file_task(self, file_obj: dict, token: str, project_id: str, thinking_level: str, chat_id: str, state_manager: EchoStateManager, events: Any) -> dict:
        """Tâche isolée de traitement de fichier (Smart Context, Binaire ou Index)."""
        file_id = file_obj.get("id") or file_obj.get("file", {}).get("id")
        filename = file_obj.get("name") or file_obj.get("file", {}).get("meta", {}).get("name", "inconnu")
        mime = file_obj.get("mime_type") or file_obj.get("file", {}).get("meta", {}).get("content_type", "application/octet-stream")
        
        path = resolve_upload_file_path(file_id)
        if not path or not os.path.exists(path):
            return {"status": "error", "fid": file_id, "error": "Fichier introuvable sur le disque."}

        size = os.path.getsize(path)
        
        # --- CAS 1 : IMAGE / AUDIO / VIDEO / PDF (Injection Binaire) ---
        if any(x in mime for x in ["image/", "audio/", "video/", "pdf"]):
            try:
                import base64
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                state_manager.mark_processed(chat_id, file_id, filename, mime, "transmitted")
                return {
                    "status": "success", "type": "Transmitted", "fid": file_id, "sub_type": "binary",
                    "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                }
            except Exception as e:
                return {"status": "error", "fid": file_id, "error": f"Erreur binaire : {str(e)}"}

        # --- CAS 2 : TEXTE PETIT (Injection Directe) ---
        if size < self.valves.MAX_DIRECT_TEXT_SIZE and "text/" in mime:
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f: content = f.read()
                state_manager.mark_processed(chat_id, file_id, filename, mime, "transmitted")
                return {
                    "status": "success", "type": "Transmitted", "fid": file_id, "sub_type": "text",
                    "content": f"📄 **Fichier : {filename}**\n```\n{content}\n```"
                }
            except Exception as e:
                return {"status": "error", "fid": file_id, "error": f"Erreur lecture : {str(e)}"}

        # --- CAS 3 : TEXTE LARGE (Smart Context via Gemini Flash) ---
        if self.valves.ENABLE_SMART_CONTEXT and "text/" in mime:
            if token and project_id:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f: raw_text = f.read()
                    import httpx
                    payload = {
                        "model": "gemini-3-flash-preview", "project": project_id,
                        "request": {
                            "systemInstruction": {"parts": [{"text": "Tu es l'unité de prétraitement contextuel d'ECHO. Ta mission est de produire un résumé technique exhaustif et structuré du fichier fourni."}]},
                            "contents": [{"role": "user", "parts": [{"text": f"Analyse et résume ce fichier technique nommé '{filename}' :\n\n{raw_text}"}]}],
                            "generationConfig": {"temperature": 0.1, "thinkingConfig": {"includeThoughts": False}}
                        }
                    }
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
                    resp = httpx.post(f"{GOOGLE_API_BASE_URL}:generateContent", headers=headers, json=payload, timeout=60)
                    if resp.status_code == 200:
                        summary = resp.json()["candidates"][0]["content"]["parts"][0]["text"]
                        state_manager.mark_processed(chat_id, file_id, filename, mime, "summarized")
                        return {"status": "success", "type": "Summarized", "fid": file_id, "content": f"🧠 **Smart Context : {filename}**\n\n{summary}"}
                except: pass

        # --- CAS 4 : FALLBACK BINAIRE (Indexation) ---
        state_manager.mark_processed(chat_id, file_id, filename, "application/octet-stream", "indexed")
        return {"status": "success", "type": "Indexed", "fid": file_id}

    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        try:
            from echo_utils import EchoEvents
            events = EchoEvents(__event_emitter__)
            body.setdefault("metadata", {})
            msgs = body.get("messages", [])
            chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
            all_files = body.get("files", [])
            state_manager = EchoStateManager(user_id=__user__.get("id", "system")) if __user__ else None

            if not msgs: return body

            # --- 0. INVARIANT HASH ---
            if state_manager:
                idx = -1
                for i in range(len(msgs)-1, -1, -1):
                    if msgs[i].get("role") == "user": idx = i; break
                if idx != -1:
                    m = msgs[idx]
                    inv_hash = state_manager.calculate_invariant_hash(m["role"], m["content"], all_files)
                    body["metadata"]["_echo_invariant_hash"] = inv_hash

            # --- AUTH OAUTH ---
            if msgs and len(msgs) >= 2:
                prev_msg = msgs[-2]
                if "ECHO_SESSION_AUTH_PENDING" in str(prev_msg.get("content", "")):
                    match = re.search(r'(4/[\w-]+)', msgs[-1].get("content", ""))
                    if match:
                        body["_auth_token"] = match.group(1)
                        msgs[-1]["content"] = "🔐 *Authentification ECHO en cours...*"
                        return body

            # --- 1. SYNC & AIGUILLAGE ---
            files_to_process = []
            if state_manager and chat_id:
                ids_in_body = [f.get("id") or f.get("file", {}).get("id") for f in all_files if f.get("id") or f.get("file", {}).get("id")]
                known_files = state_manager.sync_state(chat_id, ids_in_body)
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid and fid not in known_files: files_to_process.append(f)

            token, project_id = None, None
            if self.valves.ENABLE_SMART_CONTEXT and __user__ and "id" in __user__ and files_to_process:
                token, project_id = self.auth.get_credentials(__user__["id"])

            results = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                loop = asyncio.get_running_loop()
                with ThreadPoolExecutor(max_workers=3) as executor:
                    tasks = [loop.run_in_executor(executor, self._process_file_task, f, token, project_id, "HIGH", chat_id, state_manager, events) for f in files_to_process]
                    results = await asyncio.gather(*tasks)
                await events.status("Aiguillage ECHO terminé.", True)

            # --- 2. RECONSTRUCTION META-TRANSPORT ---
            if idx != -1:
                registry = state_manager.get_session_registry(chat_id) if (state_manager and chat_id) else {}
                meta_vars = body["metadata"].get("variables", {})
                etat_echo = {
                    "version_echo": "##ECHO_VERSION##",
                    "moteur_ia": "##GEMINI_ENGINE##",
                    "nom_utilisateur": __user__.get("name", "Anonyme") if __user__ else "Anonyme",
                    "contexte_temporel": {
                        "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                        "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                    },
                    "registre_technique": registry
                }
                rich_parts = [{"text": f"```json:etat_echo\n{json.dumps(etat_echo, ensure_ascii=False)}\n```\n\n"}]
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == "Summarized": rich_parts.append({"text": res["content"]})
                        elif res["type"] == "Transmitted":
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                body["metadata"]["_echo_rich_parts"] = rich_parts

            if all_files:
                body["metadata"]["_echo_files"] = all_files; body["files"] = []

            return body
        except Exception as e:
            logger.error(f"FILTER ERROR: {e}"); return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        msgs = body.get("messages", [])
        for m in msgs:
            content = str(m.get("content", ""))
            if content.startswith("4/") or "Authentification ECHO en cours" in content: m["content"] = "****************"
        return body
