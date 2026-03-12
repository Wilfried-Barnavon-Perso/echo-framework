"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 6.21
description: 6.21: Fixed missing content injection caused by case sensitivity issue on status string comparisons.
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
from echo_constants import ECHO_USER_AGENT, GOOGLE_API_BASE_URL, get_gemini_mime

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
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.toggle = True
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIzIi8+PHBhdGggZD0iTTEyIDdWNW0wIDE0di0yTTcgMTJINW0xNCAwaC0ybTEuNS01LjVsLTEuNSAxLjVNOCAxNmwtMS41IDEuNU0xNy41IDE3LjVsLTEuNS0xLjVNOCA4TDYuNSA2LjUiLz48cGF0aCBkPSJNMiAxMmg0bTExIDBoNW0tMyAwbDMtM20tMyAzbDMgMyIvPjwvc3ZnPg=="

    async def _process_file_task(self, file_obj: dict, token: str, project_id: str, thinking_level: str, chat_id: str, state_manager: EchoStateManager, events: Any) -> dict:
        """Tâche de traitement de fichier (Smart Context, Binaire ou Index)."""
        file_id = file_obj.get("id") or file_obj.get("file", {}).get("id")
        filename = file_obj.get("name") or file_obj.get("file", {}).get("meta", {}).get("name", "inconnu")
        mime = file_obj.get("mime_type") or file_obj.get("file", {}).get("meta", {}).get("content_type", "application/octet-stream")
        
        path = resolve_upload_file_path(file_id)
        if not path or not os.path.exists(path):
            print(f"[ECHO-FILTER] ❌ Fichier {filename} introuvable sur le disque.", flush=True)
            return {"status": "error", "fid": file_id, "error": "Fichier introuvable sur le disque."}

        size = os.path.getsize(path)
        mime, is_supported = get_gemini_mime(path)
        
        print(f"[ECHO-FILTER] 📄 Analyse de {filename} ({mime}) - Taille: {size} octets", flush=True)

        # --- CAS 1 : IMAGE / AUDIO / VIDEO / PDF (Injection Binaire Directe si petit) ---
        # Note: On injecte directement si c'est une image ou si c'est très petit.
        if is_supported and any(x in mime for x in ["image/", "audio/", "video/", "pdf"]) and size < self.valves.MAX_DIRECT_TEXT_SIZE:
            try:
                import base64
                print(f"[ECHO-FILTER] --> Mode: BINAIRE (Base64)", flush=True)
                await events.status(f"Encapsulation de {filename}...", False)
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                state_manager.mark_processed(chat_id, file_id, filename, mime, "transmitted")
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
                state_manager.mark_processed(chat_id, file_id, filename, mime, "transmitted")
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
                        # Détection de l'enveloppe 'response' (Standard Cloud Code)
                        target = data.get("response", {}) if "response" in data else data
                        candidates = target.get("candidates", [])
                        
                        if candidates and candidates[0].get("content"):
                            summary = candidates[0]["content"]["parts"][0].get("text", "")
                            state_manager.mark_processed(chat_id, file_id, filename, mime, "summarized")
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
        state_manager.mark_processed(chat_id, file_id, filename, mime, "indexed")
        return {"status": "success", "type": "indexed", "fid": file_id, "name": filename, "mime": mime}

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

            # --- AUTH OAUTH INTERCEPTION (v6.18) ---
            if len(msgs) >= 2:
                prev_content = str(msgs[-2].get("content", ""))
                if "(ECHO_SESSION_AUTH_PENDING)" in prev_content:
                    last_content = str(msgs[-1].get("content", ""))
                    match = re.search(r"(4/[\w-]+)", last_content)
                    if match:
                        body["_auth_token"] = match.group(1)
                        msgs[-1]["content"] = "🔐 *Authentification ECHO en cours...*"
                        print(f"[ECHO-FILTER] 🔐 Code OAuth intercepté et transmis.", flush=True)
                        return body

            # --- 0. INVARIANT HASH & NATIVE PART PRESERVATION ---
            idx = -1
            native_parts = []
            if state_manager:
                for i in range(len(msgs)-1, -1, -1):
                    if msgs[i].get("role") == "user": 
                        idx = i
                        # SAUVEGARDE MULTIMODALE : Conserver les images Base64 d'OWUI
                        orig_content = msgs[i].get("content")
                        if isinstance(orig_content, list):
                            for p in orig_content:
                                if isinstance(p, dict) and (p.get("type") == "image_url" or "inline_data" in p or "inlineData" in p):
                                    native_parts.append(p)
                        break

            # --- AUTH OAUTH & REFRESH ---
            token, project_id = None, None
            if __user__ and "id" in __user__:
                # Rafraîchissement proactif pour le Filtre
                token = await self.auth.refresh_google_token(__user__["id"])
                _, project_id = self.auth.get_credentials(__user__["id"])

            # --- 1. SYNC & AIGUILLAGE ---
            files_to_process = []
            if state_manager and chat_id:
                ids_in_body = [f.get("id") or f.get("file", {}).get("id") for f in all_files if f.get("id") or f.get("file", {}).get("id")]
                known_files = state_manager.sync_state(chat_id, ids_in_body)
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid and fid not in known_files: files_to_process.append(f)

            results = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                tasks = [self._process_file_task(f, token, project_id, "HIGH", chat_id, state_manager, events) for f in files_to_process]
                results = []
                for task in tasks:
                    results.append(await task)
                    await asyncio.sleep(1.5)
                await events.status("Aiguillage ECHO terminé.", True)

            # --- 2. RECONSTRUCTION META-TRANSPORT ---
            if idx != -1:
                registry = state_manager.get_session_registry(chat_id) if (state_manager and chat_id) else {}
                meta_vars = body["metadata"].get("variables", {})
                
                u_v = __user__.get("valves") if __user__ else self.user_valves
                display_name = __user__.get("name", "Anonyme") if getattr(u_v, "ENABLE_USER_NAME", False) else "Anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = getattr(u_v, "OVERRIDE_LOCATION", "")
                final_loc = u_loc if u_loc else sys_loc

                etat_echo = {
                    "version_echo": "##ECHO_VERSION##",
                    "moteur_ia": "##GEMINI_ENGINE##",
                    "nom_utilisateur": display_name,
                    "contexte_temporel": {
                        "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                        "localisation": final_loc,
                        "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                    },
                    "registre_fichiers": registry,
                    "nouveaux_fichiers": [
                        {
                            "nom": r.get("name"),
                            "id": r.get("fid"),
                            "mime": r.get("mime"),
                            "statut": r.get("type")
                        } for r in results if r.get("status") == "success"
                    ]
                }
                
                # Fusion : Parts natives (Images OWUI) + Etat ECHO
                rich_parts = []
                if native_parts: rich_parts.extend(native_parts)
                
                rich_parts.append({"text": f"```json:etat_echo\n{json.dumps(etat_echo, ensure_ascii=False)}\n```\n\n"})
                
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == "summarized": rich_parts.append({"text": res["content"]})
                        elif res["type"] == "transmitted":
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                
                body["metadata"]["_echo_rich_parts"] = rich_parts

                m = msgs[idx]
                inv_hash = state_manager.calculate_invariant_hash(m["role"], rich_parts + [{"text": m["content"]}] if isinstance(m["content"], str) else rich_parts + m["content"])
                state_manager.save_rich_payload(inv_hash, rich_parts)
                body["metadata"]["_echo_invariant_hash"] = inv_hash

            if all_files:
                body["metadata"]["_echo_files"] = all_files; body["files"] = []

            return body
        except Exception as e:
            print(f"[ECHO-FILTER] ❌ CRITICAL ERROR: {e}", flush=True)
            logger.error(f"FILTER ERROR: {e}"); return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        msgs = body.get("messages", [])
        for m in msgs:
            content = str(m.get("content", ""))
            if content.startswith("4/") or "Authentification ECHO en cours" in content: m["content"] = "****************"
        return body
