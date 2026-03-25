"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 6.37
description: 6.37: Filter session registry strictly by active message_ids for perfect Branching.
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
import shutil
from concurrent.futures import ThreadPoolExecutor

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, resolve_upload_file_path, EchoStateManager
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, get_gemini_mime, ECHO_USERS_ROOT

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    class Valves(BaseModel):
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        MAX_DIRECT_TEXT_SIZE: int = Field(default=262144, description="Taille max (octets) pour l'injection directe sans résumé.")
        DEBUG_MODE: bool = Field(default=False)

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(default=False, description="🔒 Partager mon nom avec le modèle.")
        OVERRIDE_LOCATION: str = Field(default="", description="📍 Surcharger ma position géographique (Ex: Paris, France).")

    def __init__(self):
        # ==============================================================================
        # INFRASTRUCTURE ECHO : CONTRÔLE DU RAG NATIF
        # ==============================================================================
        # file_handler = True informe Open WebUI que ce filtre gère les fichiers
        # de manière exclusive. Cela désactive le Retrieval (RAG) natif d'OWUI.
        self.file_handler = True
        # ==============================================================================

        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.toggle = True
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIzIi8+PHBhdGggZD0iTTEyIDdWNW0wIDE0di0yTTcgMTJINW0xNCAwaC0ybTEuNS01LjVsLTEuNSAxLjVNOCAxNmwtMS41IDEuNU0xNy41IDE3LjVsLTEuNS0xLjVNOCA4TDYuNSA2LjUiLz48cGF0aCBkPSJNMiAxMmg0bTExIDBoNW0tMyAwbDMtM20tMyAzbDMgMyIvPjwvc3ZnPg=="

    async def _process_file_task(self, user_id: str, file_obj: dict, token: str, project_id: str, thinking_level: str, chat_id: str, events: Any) -> dict:
        """Tâche de traitement de fichier (Smart Context, Binaire ou Index)."""
        file_id = file_obj.get("id") or file_obj.get("file", {}).get("id")
        filename = file_obj.get("name") or file_obj.get("file", {}).get("meta", {}).get("name", "inconnu")
        mime = file_obj.get("mime_type") or file_obj.get("file", {}).get("meta", {}).get("content_type", "application/octet-stream")
        
        path = resolve_upload_file_path(user_id, file_id)
        if not path or not os.path.exists(path):
            print(f"[ECHO-FILTER] ❌ Fichier {filename} introuvable sur le disque.", flush=True)
            return {"status": "error", "fid": file_id, "error": "Fichier introuvable sur le disque."}

        size = os.path.getsize(path)
        mime, is_supported = get_gemini_mime(path)
        
        print(f"[ECHO-FILTER] 📄 Analyse de {filename} ({mime}) - Taille: {size} octets", flush=True)

        # --- CAS 1 : IMAGE / AUDIO / VIDEO / PDF (Injection Binaire Directe si petit) ---
        if is_supported and any(x in mime for x in ["image/", "audio/", "video/", "pdf"]) and size < self.valves.MAX_DIRECT_TEXT_SIZE:
            try:
                import base64
                print(f"[ECHO-FILTER] --> Mode: BINAIRE (Base64)", flush=True)
                await events.status(f"Encapsulation de {filename}...", False)
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "status": "success", "type": "transmitted", "fid": file_id, "name": filename, "mime": mime, "sub_type": "binary",
                    "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                }
            except Exception as e:
                print(f"[ECHO-FILTER] !! Erreur binaire: {e}", flush=True)
                return {"status": "error", "fid": file_id, "name": filename, "mime": mime, "error": f"Erreur binaire : {str(e)}"}

        # --- CAS 2 : TEXTE PETIT (Injection Directe) ---
        if is_supported and size < self.valves.MAX_DIRECT_TEXT_SIZE and "text/" in mime:
            try:
                print(f"[ECHO-FILTER] --> Mode: INJECTION_DIRECTE (Texte)", flush=True)
                with open(path, "r", encoding="utf-8", errors="ignore") as f: content = f.read()
                return {
                    "status": "success", "type": "transmitted", "fid": file_id, "name": filename, "mime": mime, "sub_type": "text",
                    "content": f"📄 **Fichier : {filename}**\n```\n{content}\n```"
                }
            except Exception as e:
                print(f"[ECHO-FILTER] !! Erreur lecture: {e}", flush=True)
                return {"status": "error", "fid": file_id, "name": filename, "mime": mime, "error": f"Erreur lecture : {str(e)}"}

        # --- CAS 3 : TEXTE LARGE / MULTIMODAL LARGE (Smart Context via Gemini Flash) ---
        if self.valves.ENABLE_SMART_CONTEXT and is_supported:
            if token and project_id:
                try:
                    print(f"[ECHO-FILTER] --> Mode: SMART_CONTEXT (Gemini Flash)", flush=True)
                    await events.status(f"Analyse intelligente de {filename}...", False)
                    
                    import httpx
                    # On prépare le payload multimodal si besoin
                    content_part = {}
                    if "text/" in mime or "application/json" in mime:
                        with open(path, "r", encoding="utf-8", errors="ignore") as f: raw_text = f.read()
                        content_part = {"text": f"Analyse et résume ce fichier technique nommé '{filename}' :\n\n{raw_text}"}
                    else:
                        import base64
                        with open(path, "rb") as f: b64_data = base64.b64encode(f.read()).decode("utf-8")
                        content_part = {"inline_data": {"mime_type": mime, "data": b64_data}}

                    payload = {
                        "model": "gemini-3-flash-preview", "project": project_id,
                        "request": {
                            "systemInstruction": {"parts": [{"text": "Tu es l'unité de prétraitement contextuel d'ECHO. Ta mission est de produire un résumé technique exhaustif et structuré du fichier fourni. Identifie les points clés, la structure et le but du document."}]},
                            "contents": [{"role": "user", "parts": [content_part]}],
                            "generationConfig": {"temperature": 0.1, "thinkingConfig": {"includeThoughts": False}}
                        }
                    }
                    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
                    async with httpx.AsyncClient() as client:
                        resp = await client.post(f"{GOOGLE_API_BASE_URL}:generateContent", headers=headers, json=payload, timeout=90)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        target = data.get("response", {}) if "response" in data else data
                        candidates = target.get("candidates", [])
                        
                        if candidates and candidates[0].get("content"):
                            summary = candidates[0]["content"]["parts"][0].get("text", "")
                            print(f"[ECHO-FILTER] ✅ Résumé Flash généré pour {filename}.", flush=True)
                            return {"status": "success", "type": "summarized", "fid": file_id, "name": filename, "mime": mime, "content": f"🧠 **Smart Context : {filename}**\n\n{summary}"}
                        else:
                            print(f"[ECHO-FILTER] !! Format inattendu ou blocage Google pour {filename}: {data}", flush=True)
                    else:
                        print(f"[ECHO-FILTER] !! Erreur API Flash ({resp.status_code}): {resp.text}", flush=True)
                except Exception as e:
                    print(f"[ECHO-FILTER] !! Exception Smart Context pour {filename}: {e}", flush=True)

        # --- CAS 4 : FALLBACK BINAIRE (Indexation) ---
        print(f"[ECHO-FILTER] --> Mode: INDEXATION (Fallback)", flush=True)
        return {"status": "success", "type": "indexed", "fid": file_id, "name": filename, "mime": mime}

    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[Dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        try:
            from echo_utils import EchoEvents
            events = EchoEvents(__event_emitter__)
            body.setdefault("metadata", {})
            msgs = body.get("messages", [])
            chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
            all_files = body.get("files", [])
            user_id = __user__.get("id", "system") if __user__ else "system"
            
            if not msgs: return body

            # --- AUTH OAUTH INTERCEPTION ---
            if len(msgs) >= 2:
                prev_content = str(msgs[-2].get("content", ""))
                if "(ECHO_SESSION_AUTH_PENDING)" in prev_content:
                    last_content = str(msgs[-1].get("content", ""))
                    match = re.search(r"(4/[\w-]+)", last_content)
                    if match:
                        body["_auth_token"] = match.group(1)
                        msgs[-1]["content"] = "🔐 *Authentification ECHO en cours...*"
                        return body

            # --- 3. TRAITEMENT DES FICHIERS (DRAFT) ---
            token, project_id = None, None
            if __user__ and "id" in __user__:
                token = await self.auth.refresh_google_token(__user__["id"])
                _, project_id = self.auth.get_credentials(__user__["id"])

            files_to_process = []
            if chat_id:
                safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
                vault_dir = os.path.normpath(os.path.join(ECHO_USERS_ROOT, safe_uid, "files"))
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid:
                        path = resolve_upload_file_path(user_id, fid)
                        if path and not os.path.normpath(path).startswith(vault_dir):
                            files_to_process.append(f)
                
            print(f"[ECHO-FILTER DEBUG] Fichiers totaux: {len(all_files)} | À traiter: {len(files_to_process)}", flush=True)

            results = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                tasks = [self._process_file_task(user_id, f, token, project_id, "HIGH", chat_id, events) for f in files_to_process]
                for task in tasks:
                    results.append(await task)
                    await asyncio.sleep(0.5)

            # --- 4. ARCHITECTURE DU DRAFT (Bit-Perfect Ready) ---
            idx = -1
            native_parts = []
            for i in range(len(msgs)-1, -1, -1):
                if msgs[i].get("role") == "user": 
                    idx = i
                    orig_content = msgs[i].get("content")
                    if isinstance(orig_content, list):
                        for p in orig_content:
                            if isinstance(p, dict) and (p.get("type") == "image_url" or "inline_data" in p or "inlineData" in p):
                                native_parts.append(p)
                    break

            if idx != -1:
                # 1. Extraction des IDs de la branche historique active
                active_msg_ids = [m.get("id") for m in msgs if m.get("id")]
                
                # 2. Récupération du registre depuis la BDD (Filtré par messages actifs)
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
                active_registry = state_manager.get_session_registry(chat_id, active_msg_ids) if chat_id else {}
                
                # 3. Ajout des nouveaux fichiers du tour courant
                for r in results:
                    if r.get("status") == "success":
                        active_registry[r.get("name")] = {
                            "id": r.get("fid"),
                            "mime": r.get("mime"),
                            "statut": r.get("type")
                        }

                meta_vars = body["metadata"].get("variables", {})
                u_v = __user__.get("valves") if __user__ else self.user_valves
                display_name = __user__.get("name", "Anonyme") if getattr(u_v, "ENABLE_USER_NAME", False) else "Anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = getattr(u_v, "OVERRIDE_LOCATION", "")
                final_loc = u_loc if u_loc else sys_loc
                
                # 5. Génération du bloc JSON etat_echo complet
                etat_echo = {
                    "version_framework_echo": "##ECHO_VERSION##",
                    "moteur_technique_ia": "##GEMINI_ENGINE##",
                    "nom_utilisateur": display_name,
                    "contexte_temporel": {
                        "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                        "localisation": final_loc,
                        "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                    },
                    "registre_fichiers": active_registry
                }

                body["metadata"]["_echo_env_info"] = {
                    "nom_utilisateur": display_name,
                    "localisation": final_loc,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                }
                
                rich_parts = []
                # 6. Injection de l'état en premier
                rich_parts.append({"text": f"```json:etat_echo\n{json.dumps(etat_echo, ensure_ascii=False)}\n```\n\n"})
                
                if native_parts: rich_parts.extend(native_parts)
                
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == "summarized": rich_parts.append({"text": res["content"]})
                        elif res["type"] == "transmitted":
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                
                # INJECTION DU DRAFT (Délégué au Pipe)
                body["metadata"]["_echo_user_parts_draft"] = rich_parts
                body["metadata"]["_echo_user_msg_id"] = msgs[idx].get("id")
                body["metadata"]["_echo_user_msg_updated_at"] = msgs[idx].get("updated_at")
                body["metadata"]["_echo_files_to_seal"] = results

            if all_files:
                body["metadata"]["_echo_files"] = all_files
                body["files"] = []
                body["citations"] = False

            return body
        except Exception as e:
            print(f"[ECHO-FILTER] ❌ CRITICAL ERROR: {e}", flush=True)
            return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        msgs = body.get("messages", [])
        for m in msgs:
            content = str(m.get("content", ""))
            if content.startswith("4/") or "Authentification ECHO en cours" in content: m["content"] = "****************"
        return body
