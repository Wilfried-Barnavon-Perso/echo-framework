"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 4.32.0
description: 4.32.0: Persistent Technical Registry (Strict Architecture).
"""

from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal
import os
import json
import logging
import requests
import base64
import sys
import uuid
import random
import glob
import time
import re
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ==============================================================================
# SECTION 0 : IMPORTATIONS ECHO STRICTES
# ==============================================================================
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoStateManager, EchoEvents, resolve_upload_file_path
from echo_constants import get_gemini_mime, ECHO_UPLOADS_DIR, ECHO_VERSION_FILE

# TENTATIVE D'ACCES DIRECT AU BACKEND OWUI
try:
    from open_webui.models.files import Files
    HAS_DIRECT_DB_ACCESS = True
except ImportError:
    HAS_DIRECT_DB_ACCESS = False

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=0, description="Priorité.")
        debug_context: bool = Field(default=False, description="Debug.")
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Activer Smart Context")
        SMART_CONTEXT_THRESHOLD_KB: int = Field(default=2048, description="Seuil (Ko)")
        GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle Analyse")

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(default=False)
        OVERRIDE_LOCATION: str = Field(default="")
        SMART_CONTEXT_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH", description="Niveau")

    def __init__(self):
        self.file_handler = True
        self.toggle = True
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.version_path = ECHO_VERSION_FILE
        self.uploads_dir = ECHO_UPLOADS_DIR

    def _get_echo_version(self) -> str:
        try:
            if os.path.exists(self.version_path):
                with open(self.version_path, 'r', encoding='utf-8') as f: return f.read().strip()
        except: pass
        return "Unknown"

    def _call_flash(self, token: str, project_id: str, prompt: str, system: str, fpath: str, mime: str, thinking_level: str) -> Optional[str]:
        if not project_id: return None
        url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": "GeminiCLI/0.24.0", "x-goog-api-client": "gl-python/3.10"}
        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            payload = {"model": self.valves.GEMINI_FLASH_MODEL, "project": project_id, "user_prompt_id": hex(random.getrandbits(64))[2:], "request": {"contents": [{"role": "user", "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}]}], "session_id": str(uuid.uuid4()), "generationConfig": {"temperature": 0.2, "maxOutputTokens": 64000, "thinkingConfig": {"thinkingLevel": thinking_level.upper()}, "responseMimeType": "text/plain"}}}
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
            if resp.status_code != 200: return None
            full_text = ""
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            data = json.loads(decoded[5:].strip())
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: full_text += parts[0]["text"]
                        except: pass
            return full_text
        except: return None

    def _process_file_task(self, f_obj, token, project_id, thinking_level, chat_id, state_manager):
        try:
            target = f_obj.get("file", f_obj)
            fid = f_obj.get("id") or target.get("id")
            fname = target.get("filename") or target.get("name") or "unknown"
            
            real_path = resolve_upload_file_path(fid, self.uploads_dir)
            if not real_path: return {"status": "error", "reason": "not_found", "fname": fname}
            
            fsize = os.path.getsize(real_path)
            mime, supported = get_gemini_mime(real_path)
            if not supported: return {"status": "error", "reason": f"unsupported_type ({mime})", "fname": fname}
            if mime.startswith("image/"): return {"status": "skip", "reason": "image_native", "fname": fname}
            
            if (token and project_id and fsize > (self.valves.SMART_CONTEXT_THRESHOLD_KB * 1024)):
                prompt = "Analyse INTÉGRALE de ce document. Génère une 'Fiche de Lecture Exhaustive' structurée en Markdown."
                res_text = self._call_flash(token, project_id, prompt, "Analyste Senior.", real_path, mime, thinking_level)
                if res_text:
                    if state_manager: state_manager.mark_processed(chat_id, fid, fname, "smart_context")
                    md_block = (
                        f"<details>\n"
                        f"  <summary>📄 Smart Context : {fname}</summary>\n"
                        f"  - **FILE_ID (Technical Reference)**: {fid}\n"
                        f"  - **Note**: Utilisez cet ID précis (UUID) pour tout appel d'outil se référant à ce fichier.\n\n"
                        f"{res_text}\n"
                        f"</details>"
                    )
                    return {"status": "success", "type": "smart", "content": md_block, "fname": fname}
            
            with open(real_path, "rb") as f: b64_data = base64.b64encode(f.read()).decode("utf-8")
            if state_manager: state_manager.mark_processed(chat_id, fid, fname, "raw_inline")
            ux_block = (
                f"<details>\n"
                f"  <summary>📎 Fichier (Raw) : {fname}</summary>\n"
                f"  - **FILE_ID (Technical Reference)**: {fid}\n"
                f"  - **Note**: Utilisez cet ID précis (UUID) pour toute lecture profonde.\n"
                f"  (Taille: {int(fsize/1024)} Ko)\n"
                f"</details>\n"
            )
            return {"status": "success", "type": "raw", "content": {"inline_data": {"mime_type": mime, "data": b64_data}, "ux_block": ux_block}, "fname": fname}
        except Exception as e: return {"status": "error", "reason": str(e), "fname": "unknown"}

    async def inlet(self, body: dict, __event_emitter__: Any = None, __event_call__: Any = None, __user__: Optional[dict] = None) -> dict:
        try:
            events = EchoEvents(__event_emitter__, __event_call__)
            if not self.toggle: return body
            msgs = body.get("messages", [])
            all_files = body.get("files", [])
            chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id")
            user_id = __user__.get("id", "anonymous") if __user__ else "anonymous"
            state_manager = EchoStateManager(user_id=user_id) if chat_id else None

            # --- DÉTECTION ET EXTRACTION DU TOKEN OAUTH (STEALTH V2) ---
            if msgs and len(msgs) >= 2:
                prev_msg = msgs[-2]
                prev_content = str(prev_msg.get("content", ""))
                if "ECHO_SESSION_AUTH_PENDING" in prev_content:
                    current_user_msg = msgs[-1]
                    raw_content = current_user_msg.get("content", "")
                    text_to_search = raw_content if isinstance(raw_content, str) else "".join([x.get("text", "") for x in raw_content if isinstance(x, dict) and x.get("type") == "text"])
                    match = re.search(r"(4/[a-zA-Z0-9._-]+)", text_to_search.strip())
                    if match:
                        body["_auth_token"] = match.group(1)
                        current_user_msg["content"] = "🔐 *Authentification ECHO en cours...*"
                        return body

            user_valves = __user__.get("valves") if __user__ else None
            enable_user_name, override_location, thinking_level = False, "", "HIGH"
            if user_valves:
                enable_user_name = getattr(user_valves, "ENABLE_USER_NAME", False)
                override_location = getattr(user_valves, "OVERRIDE_LOCATION", "")
                thinking_level = getattr(user_valves, "SMART_CONTEXT_THINKING_LEVEL", "HIGH")

            meta_vars = body.get("metadata", {}).get("variables", {})
            env_block = {"environnement_utilisateur": {"version_framework": self._get_echo_version(), "nom_utilisateur": __user__.get("name", "Anonyme") if enable_user_name else "[Masqué]", "modele_actif": "__PIPE_MODEL_ID__", "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"), "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC"), "lieu_utilisateur": override_location or meta_vars.get("{{USER_LOCATION}}", "Inconnu")}}
            
            # --- RÉCUPÉRATION DU REGISTRE DE SESSION (PERSISTANT) ---
            registry = state_manager.get_session_registry(chat_id) if state_manager else {}
            
            files_to_process = []
            if all_files and state_manager:
                known_files = state_manager.sync_state(chat_id, [f.get("id") or f.get("file", {}).get("id") for f in all_files if f.get("id") or f.get("file", {}).get("id")])
                for f in all_files:
                    if (fid := (f.get("id") or f.get("file", {}).get("id"))) and fid not in known_files: files_to_process.append(f)
            elif all_files: files_to_process = all_files

            token, project_id = None, None
            if self.valves.ENABLE_SMART_CONTEXT and __user__ and "id" in __user__ and files_to_process:
                token, project_id = self.auth.get_credentials(__user__["id"])

            injections_smart, injections_raw = [], []
            if files_to_process:
                await events.status(f"Traitement de {len(files_to_process)} fichiers...", False)
                loop = asyncio.get_running_loop()
                with ThreadPoolExecutor(max_workers=3) as executor:
                    tasks = [loop.run_in_executor(executor, self._process_file_task, f, token, project_id, thinking_level, chat_id, state_manager) for f in files_to_process]
                    for res in await asyncio.gather(*tasks):
                        if res["status"] == "success":
                            if res["type"] == "smart": injections_smart.append(res["content"])
                            else: injections_raw.append(res["content"])
                await events.status("Fichiers traités.", False)
                # Rafraîchir le registre après traitement
                registry = state_manager.get_session_registry(chat_id) if state_manager else {}

            if msgs:
                idx = next((i for i in range(len(msgs)-1, -1, -1) if msgs[i]["role"] == "user"), -1)
                if idx != -1:
                    # Bloc 1 : Environnement
                    new_content = [{"type": "text", "text": f"```json:context\n{json.dumps(env_block, ensure_ascii=False)}\n```\n\n"}]
                    # Bloc 2 : Registre Technique (Persistant)
                    if registry:
                        file_block = {"registre_technique": registry}
                        new_content.append({"type": "text", "text": f"```json:fichiers\n{json.dumps(file_block, ensure_ascii=False)}\n```\n\n"})
                    # Bloc 3 : Smart Context (Événementiel)
                    if injections_smart: new_content.append({"type": "text", "text": "\n".join(injections_smart) + "\n\n"})
                    if injections_raw:
                        for item in injections_raw:
                            new_content.append({"type": "inline_data", "inline_data": item["inline_data"]})
                            new_content.append({"type": "text", "text": item["ux_block"]})
                    orig = msgs[idx].get("content", "")
                    if isinstance(orig, str): new_content.append({"type": "text", "text": orig})
                    else: new_content.extend(orig)
                    msgs[idx]["content"] = new_content
            return body
        except Exception as e:
            logger.error(f"FILTER ERROR: {e}")
            return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        msgs = body.get("messages", [])
        for m in msgs:
            content = str(m.get("content", ""))
            if content.startswith("4/") or "Authentification ECHO en cours" in content:
                m["content"] = "****************"
        return body
