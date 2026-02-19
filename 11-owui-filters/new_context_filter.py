"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 4.24
description: 4.24: Production Release. Robust I/O. Thread-Safe Auth. French UX.
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
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

# ==============================================================================
# SECTION 0 : IMPORTATIONS & DÉPENDANCES PARTAGÉES
# ==============================================================================
sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoAuth, EchoStateManager
    from echo_constants import get_gemini_mime
except ImportError:
    class EchoAuth:
        def get_credentials(self, uid): return None, None
    class EchoStateManager:
        def __init__(self, d, u): pass
        def sync_state(self, c, f): return set()
        def mark_processed(self, c, f, m): pass
    def get_gemini_mime(path): return "application/octet-stream", False

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
        debug_context: bool = Field(default=False, description="Debug Logs (Docker Stdout)")
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Activer Smart Context")
        SMART_CONTEXT_THRESHOLD_KB: int = Field(default=2048, description="Seuil (Ko)")
        GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle Analyse")

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(default=False)
        OVERRIDE_LOCATION: str = Field(default="")
        SMART_CONTEXT_THINKING_LEVEL: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="HIGH", description="Niveau de réflexion")

    def __init__(self):
        print(f"[SmartContext:INIT] Initializing Filter (Thread-Safe Auth)", flush=True)
        self.file_handler = True
        self.toggle = True
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.version_path = "/app/backend/data/ECHO_VERSION"
        self.uploads_dir = "/app/backend/data/uploads"

    def _log(self, message: str, level: str = "INFO"):
        if self.valves.debug_context or level == "ERROR" or level == "INIT" or level == "WARNING":
            print(f"[SmartContext:{level}] {message}", flush=True)

    def _get_echo_version(self) -> str:
        try:
            if os.path.exists(self.version_path):
                with open(self.version_path, 'r', encoding='utf-8') as f: return f.read().strip()
        except: pass
        return "Unknown"

    def _resolve_file_path(self, f_obj: dict) -> Optional[str]:
        fid = f_obj.get("id")
        if not fid and "file" in f_obj: fid = f_obj["file"].get("id")
        if not fid: return None

        if HAS_DIRECT_DB_ACCESS:
            try:
                file_record = Files.get_file_by_id(fid)
                if file_record and hasattr(file_record, 'path') and file_record.path and os.path.exists(file_record.path):
                    return file_record.path
            except Exception: pass

        target = f_obj.get("file", f_obj)
        json_path = target.get("path")
        if json_path and os.path.exists(json_path): return json_path

        pattern = os.path.join(self.uploads_dir, f"{fid}_*")
        matches = glob.glob(pattern)
        if matches: return matches[0]

        return None

    def _call_flash(self, token: str, project_id: str, prompt: str, system: str, fpath: str, mime: str, thinking_level: str) -> Optional[str]:
        if not project_id: return None
        clean_pid = project_id.replace("projects/", "")
        
        url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        headers = {
            "Authorization": f"Bearer {token}", 
            "Content-Type": "application/json",
            "User-Agent": "GeminiCLI/0.24.0",
            "x-goog-api-client": "gl-python/3.10"
        }
        
        try:
            # Lecture Robuste
            max_retries = 3
            b64 = ""
            for attempt in range(max_retries):
                try:
                    with open(fpath, 'rb') as f:
                        b64 = base64.b64encode(f.read()).decode('utf-8')
                    break
                except (PermissionError, OSError):
                    if attempt < max_retries - 1: time.sleep(0.5)
                    else: raise

            payload = {
                "model": self.valves.GEMINI_FLASH_MODEL,
                "project": clean_pid,
                "user_prompt_id": hex(random.getrandbits(64))[2:],
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                    "session_id": str(uuid.uuid4()),
                    "generationConfig": {
                        "temperature": 0.2, "maxOutputTokens": 64000, 
                        "thinkingConfig": {"thinkingLevel": thinking_level.upper()},
                        "responseMimeType": "text/plain" 
                    }
                }
            }
            
            for attempt in range(max_retries):
                resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
                if resp.status_code == 200: break
                if resp.status_code == 429:
                    if attempt < max_retries - 1: time.sleep((attempt + 1) * 2)
                    continue
                self._log(f"Flash API Error {resp.status_code} ({mime})", "ERROR")
                return None

            full_text = ""
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: full_text += parts[0]["text"]
                        except: pass
            return full_text
        except Exception as e:
            self._log(f"Flash Exception: {e}", "ERROR")
        return None

    def _process_file_task(self, f_obj, token, project_id, thinking_level, chat_id, state_manager):
        try:
            target = f_obj.get("file", f_obj)
            fid = f_obj.get("id") or target.get("id")
            fname = target.get("filename") or target.get("name") or "unknown"
            
            real_path = self._resolve_file_path(f_obj)
            if not real_path: return {"status": "error", "reason": "not_found", "fname": fname}

            fsize = os.path.getsize(real_path)
            
            # --- DETECTION MIME CENTRALISEE ---
            mime, supported = get_gemini_mime(real_path)
            
            if not supported:
                return {"status": "error", "reason": f"unsupported_type ({mime})", "fname": fname}
            
            # --- CAS 1 : IMAGE (OWUI GESTION NATIVE) ---
            if mime.startswith("image/"):
                return {"status": "skip", "reason": "image_native", "fname": fname}

            # --- CAS 2 : SMART CONTEXT (Flash Analysis) ---
            if (token and project_id and fsize > (self.valves.SMART_CONTEXT_THRESHOLD_KB * 1024)):
                prompt = """
Analyse INTÉGRALE de ce document. Génère une 'Fiche de Lecture Exhaustive' structurée en Markdown.
NE RÉSUME PAS : RESTRUCTURE. Déduplique les informations redondantes et optimise l'organisation des données.
Conserve tous les chiffres, dates, noms propres, arguments et données techniques.
Si c'est du code, analyse la logique.
Si c'est une image/audio, décris tout ce qui est perceptible.
Structure attendue :
# Titre & Métadonnées
## Synthèse Exécutive
## Analyse Détaillée (Section par Section)
## Données Brutes & Citations Clés
"""
                res_text = self._call_flash(token, project_id, prompt, "Analyste Senior Exhaustif.", real_path, mime, thinking_level)
                
                if res_text:
                    if state_manager: state_manager.mark_processed(chat_id, fid, "smart_context")
                    
                    current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    md_block = f"""
<details>
<summary>📄 Analyse Smart Context : {fname} (ID: {fid})</summary>

--- MÉTADONNÉES FICHIER ---
NOM : {fname}
FILE_ID : {fid}
TYPE : Smart Context Analysis (Gemini Flash)
DATE : {current_date}
---

{res_text}
</details>
"""
                    return {"status": "success", "type": "smart", "content": md_block, "fname": fname}

            # --- CAS 3 : RAW INLINE (Base64 Injection) ---
            try:
                # Lecture Robuste
                max_retries = 3
                raw_data = b""
                for attempt in range(max_retries):
                    try:
                        with open(real_path, "rb") as f:
                            raw_data = f.read()
                        break
                    except (PermissionError, OSError):
                        if attempt < max_retries - 1: time.sleep(0.5)
                        else: raise

                b64_data = base64.b64encode(raw_data).decode("utf-8")
                
                injection = {
                    "inline_data": {"mime_type": mime, "data": b64_data},
                    "ux_block": f"""
<details>
<summary>📎 Fichier joint (Raw) : {fname} (ID: {fid})</summary>
(Contenu transmis au modèle en Inline Base64 : {int(fsize/1024)} Ko)
</details>
"""
                }
                
                if state_manager: state_manager.mark_processed(chat_id, fid, "raw_inline")
                return {"status": "success", "type": "raw", "content": injection, "fname": fname}
                
            except Exception as e:
                return {"status": "error", "reason": f"read_error: {e}", "fname": fname}

        except Exception as e:
            return {"status": "error", "reason": f"fatal: {e}", "fname": "unknown"}

    async def inlet(self, body: dict, __event_emitter__=None, __user__: Optional[dict] = None) -> dict:
        try:
            if not self.toggle: return body

            msgs = body.get("messages", [])
            all_files = body.get("files", [])
            
            chat_id = body.get("chat_id") or body.get("metadata", {}).get("chat_id")
            if not chat_id and "__metadata__" in locals(): 
                 chat_id = locals().get("__metadata__", {}).get("chat_id")
            
            user_id = __user__.get("id", "anonymous") if __user__ else "anonymous"
            state_manager = EchoStateManager(user_id=user_id) if chat_id else None

            async def emit_status(desc, done=False):
                if __event_emitter__:
                    await __event_emitter__({
                        "type": "status",
                        "data": {"description": desc, "done": done}
                    })

            user_valves = __user__.get("valves") if __user__ else None
            enable_user_name = False
            override_location = ""
            thinking_level = "HIGH"
            
            if user_valves:
                try:
                    enable_user_name = getattr(user_valves, "ENABLE_USER_NAME", False)
                    override_location = getattr(user_valves, "OVERRIDE_LOCATION", "")
                    thinking_level = getattr(user_valves, "SMART_CONTEXT_THINKING_LEVEL", "HIGH")
                except AttributeError:
                    enable_user_name = user_valves.get("ENABLE_USER_NAME", False)
                    override_location = user_valves.get("OVERRIDE_LOCATION", "")
                    thinking_level = user_valves.get("SMART_CONTEXT_THINKING_LEVEL", "HIGH")

            meta_vars = body.get("metadata", {}).get("variables", {})
            env_block = {
                "environnement_utilisateur": {
                    "version_framework": self._get_echo_version(),
                    "nom_utilisateur": __user__.get("name", "Anonyme") if enable_user_name else "[Masqué]",
                    "modele_actif": "__PIPE_MODEL_ID__",
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC"),
                    "lieu_utilisateur": override_location or meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                }
            }
            
            files_to_process = []
            if all_files:
                if state_manager:
                    current_ids = []
                    for f in all_files:
                        fid = f.get("id") or f.get("file", {}).get("id")
                        if fid: current_ids.append(fid)
                    known_files = state_manager.sync_state(chat_id, current_ids)
                    for f in all_files:
                        fid = f.get("id") or f.get("file", {}).get("id")
                        if fid and fid not in known_files:
                            files_to_process.append(f)
                else:
                    files_to_process = all_files

            token, project_id = None, None
            if self.valves.ENABLE_SMART_CONTEXT and __user__ and "id" in __user__ and files_to_process:
                try: token, project_id = self.auth.get_credentials(__user__["id"])
                except: pass

            injections_smart = []
            injections_raw = []
            
            if files_to_process:
                await emit_status(f"Traitement de {len(files_to_process)} fichiers...", False)
                loop = asyncio.get_running_loop()
                futures = []
                with ThreadPoolExecutor(max_workers=3) as executor:
                    for f in files_to_process:
                        futures.append(loop.run_in_executor(
                            executor, self._process_file_task, 
                            f, token, project_id, thinking_level, chat_id, state_manager
                        ))
                    
                    for completed_task in asyncio.as_completed(futures):
                        res = await completed_task
                        fname = res.get("fname", "unknown")
                        if res["status"] == "success":
                            if res["type"] == "smart":
                                injections_smart.append(res["content"])
                                await emit_status(f"Smart Context: {fname} Analysé.", False)
                            elif res["type"] == "raw":
                                injections_raw.append(res["content"])
                                await emit_status(f"Raw Inline: {fname} Encodé.", False)
                        elif res["status"] == "skip":
                             await emit_status(f"Image: {fname} (Pass-through).", False)
                        elif res["status"] == "error":
                             await emit_status(f"⚠️ Ignoré: {fname} ({res.get('reason')}).", False)

            await emit_status("Transmission au Modèle...", True)

            if "files" in body: body["files"] = []
            if "metadata" in body and "files" in body["metadata"]: body["metadata"]["files"] = []
            if "raw_files_from_filter" in body: del body["raw_files_from_filter"]

            if msgs:
                # Recherche du DERNIER message USER
                idx = -1
                for i in range(len(msgs) - 1, -1, -1):
                    if msgs[i]["role"] == "user":
                        idx = i
                        break
                
                if idx != -1:
                    original_content = msgs[idx].get("content", "")
                    new_content_list = []
                    
                    env_txt = f"```json:context\n{json.dumps(env_block, ensure_ascii=False)}\n```\n\n"
                    new_content_list.append({"type": "text", "text": env_txt})

                    if injections_smart:
                        smart_txt = "\n".join(injections_smart) + "\n\n"
                        new_content_list.append({"type": "text", "text": smart_txt})

                    if injections_raw:
                        for item in injections_raw:
                            new_content_list.append({"type": "inline_data", "inline_data": item["inline_data"]})
                            if "ux_block" in item:
                                new_content_list.append({"type": "text", "text": item["ux_block"] + "\n"})

                    if isinstance(original_content, str):
                        new_content_list.append({"type": "text", "text": original_content})
                    elif isinstance(original_content, list):
                        new_content_list.extend(original_content)
                    
                    msgs[idx]["content"] = new_content_list

            return body

        except Exception as e:
            logger.error(f"FATAL FILTER ERROR: {e}")
            print(f"[SmartContext:FATAL] {e}", flush=True)
            return body