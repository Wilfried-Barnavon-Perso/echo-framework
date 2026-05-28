"""
title: ECHO Context Filter
author: Wilfried BARNAVON
version: 7.12
description: 7.5: Fix génération slug. 7.6: Migration Antigravity 2.1 — suppression GOOGLE_OAUTH_CODE_REGEX (PKCE legacy).
             7.7: Centralisation THINKING_LEVEL_FLASH (echo_constants v4.8) — suppression du "HIGH" hardcodé.
             7.8: Injection registre_plan dans environnement_contexte (Strategic Planner v1.0).
             7.9: Fix multimodal — extraction ordonnée des text-parts du content OWUI
             dans le Draft. Correction perte texte utilisateur avec images inline.
             7.10: Revert dégradation gracieuse CAS 3 (le fallback INDEXATION suffit).
             7.11: Cosmetic — Anonyme → anonyme (minuscule) dans nom_utilisateur.
             7.12: Injection registre_codex dans environnement_contexte (ECHO Codex v1.0).
"""


from pydantic import BaseModel, Field
from typing import Optional, List, Union, Dict, Any
import orjson as json
import pybase64 as base64
import os
import sys
import re
import asyncio
import logging
import time
import uuid as _uuid_module
import hashlib
import shutil
from concurrent.futures import ThreadPoolExecutor

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import resolve_upload_file_path, EchoStateManager, EchoGeminiClient, EchoEvents, EchoAuth
from echo_constants import (
    get_gemini_mime, ECHO_USERS_ROOT, 
    GOOGLE_API_KEY_REGEX, MODEL_FLASH,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    THINKING_LEVEL_FLASH
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    # Priorité basse (1) pour s'exécuter en tout premier (Inlet) avant les autres filtres
    priority: int = 1

    class Valves(BaseModel):
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        MAX_DIRECT_TEXT_SIZE: int = Field(default=262144, description="Taille max (octets) pour l'injection directe sans résumé.")
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum pour le Smart Context.")
        SMART_CONTEXT_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour l'analyse Flash.")
        DEBUG_MODE: bool = Field(default=False)

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(default=False, description="🔒 Partager mon nom avec le modèle.")
        OVERRIDE_LOCATION: str = Field(default="", description="📍 Surcharger ma position géographique (Ex: Paris, France).")

    def __init__(self):
        # ==============================================================================
        # INFRASTRUCTURE ECHO : CONTRÔLE DU RAG NATIF
        # ==============================================================================
        self.file_handler = True
        # ==============================================================================

        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.toggle = True
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIzIi8+PHBhdGggZD0iTTEyIDdWNW0wIDE0di0yTTcgMTJINW0xNCAwaC0ybTEuNS01LjVsLTEuNSAxLjVNOCAxNmwtMS41IDEuNU0xNy41IDE3LjVsLTEuNS0xLjVNOCA4TDYuNSA2LjUiLz48cGF0aCBkPSJNMiAxMmg0bTExIDBoNW0tMyAwbDMtM20tMyAzbDMgMyIvPjwvc3ZnPg=="

    async def _process_file_task(self, user_id: str, file_obj: dict, tokens: List[str], project_id: str, thinking_level: str, chat_id: str, events: Any) -> dict:
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

        # --- CAS 3 : TEXTE LARGE / MULTIMODAL LARGE (RAG Éphémère v7.1) ---
        # Remplace l'injection directe <smart_context> par un pipeline vectoriel.
        # Toutes les étapes sont des tâches de fond invisibles → MODEL_DISTILLATION.
        if self.valves.ENABLE_SMART_CONTEXT and is_supported:
            try:
                print(f"[ECHO-FILTER] --> Mode: RAG_EPHEMERAL (distillation → Qdrant echo_ephemeral)", flush=True)
                await events.status(f"Distillation de {filename}...", False)

                # Construction du prompt selon MIME
                content_part = {}
                if "text/" in mime or "application/json" in mime:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f: raw_text = f.read()
                    content_part = {"text": f"Analyse et résume ce fichier technique nommé '{filename}' :\n\n{raw_text[:200000]}"}
                else:
                    with open(path, "rb") as f: b64_data = base64.b64encode(f.read()).decode("utf-8")
                    content_part = {"inline_data": {"mime_type": mime, "data": b64_data}}

                # Distillat exhaustif → MODEL_DISTILLATION (call_distillation sans target_model)
                u_ctx = {"id": user_id}
                m_ctx = {"chat_id": chat_id}
                distillate = await EchoGeminiClient.call_distillation(
                    "Tu es l'unité de prétraitement contextuel d'ECHO. "
                    "Ta mission est de produire un résumé technique exhaustif et structuré du fichier fourni.",
                    u_ctx, m_ctx, is_json=False,
                    parts=[{"role": "user", "parts": [content_part]}],
                    max_tokens=8192
                )
                if not distillate:
                    raise ValueError("Distillation vide — modèle non disponible")

                # Slug : ASCII uniquement, alphanumériques + underscores, tirets et espaces → underscore
                import unicodedata as _ud
                normalized_name = _ud.normalize("NFKD", filename.rsplit(".", 1)[0])
                # Remplacer espaces et tirets par _ avant le filtrage
                cleaned = normalized_name.replace("-", "_").replace(" ", "_")
                safe_name = "".join(c for c in cleaned if (c.isascii() and c.isalnum()) or c == "_")[:24].strip("_")
                slug = f"{safe_name}_{_uuid_module.uuid4().hex[:4]}"

                # Vectorisation → méthode partagée (MODEL_DISTILLATION en interne)
                await events.status(f"Vectorisation de {filename}...", False)
                nb_points, err = await EchoGeminiClient.index_text_in_ephemeral_rag(
                    distillate, slug, user_id, chat_id, u_ctx, m_ctx
                )
                if nb_points == 0:
                    print(f"[ECHO-FILTER] !! Vectorisation échouée pour {filename} : {err}", flush=True)
                    raise ValueError(f"Vectorisation échouée : {err}")

                # Brief résumé pour le prompt (≤ 300 mots) → MODEL_DISTILLATION
                brief_prompt = f"Résume en maximum 300 mots les points clés de ce texte :\n\n{distillate[:15000]}"
                brief_summary = await EchoGeminiClient.call_distillation(
                    brief_prompt, u_ctx, m_ctx, is_json=False, max_tokens=2048
                )

                # Le slug est aussi dans <environnement_contexte> YAML, mais on le rappelle
                # explicitement ici pour MODEL_LITE qui ne lit pas toujours le YAML fiablement.
                res_text = (
                    f"<smart_context filename=\"{filename}\" mime_type=\"{mime}\" mode=\"rag_ephemeral\"\n"
                    f"                vectors=\"{nb_points}\">\n"
                    f"{brief_summary}\n\n"
                    f"> ⚠️ Ce fichier est vectorisé dans le RAG éphémère ({nb_points} vecteurs). "
                    f"Pour l'interroger, utiliser `query_distilled_data(slug=\"{slug}\", query=\"...\")`.\n"
                    f"</smart_context>"
                )

                state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                state.mark_processed(chat_id, file_id, filename, mime, "rag_ephemeral", res_text)
                print(f"[ECHO-FILTER] ✅ {filename} → RAG éphémère (slug={slug}, {nb_points} vecteurs).", flush=True)
                return {"status": "success", "type": "rag_ephemeral", "slug": slug, "fid": file_id, "name": filename, "mime": mime, "content": res_text}

            except Exception as e:
                print(f"[ECHO-FILTER] !! Exception RAG Éphémère pour {filename}: {e}", flush=True)
                # Fallback → CAS 4 (indexation)

        # --- CAS 4 : FALLBACK BINAIRE (Indexation) ---
        print(f"[ECHO-FILTER] --> Mode: INDEXATION (Fallback)", flush=True)
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.mark_processed(chat_id, file_id, filename, mime, "indexed")
        return {"status": "success", "type": "indexed", "fid": file_id, "name": filename, "mime": mime}

    def _dict_to_yaml(self, d: Any, indent: int = 0) -> str:
        """Sérialiseur YAML minimaliste pour ECHO."""
        lines = []
        space = "  " * indent
        if isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    if not v: lines.append(f"{space}{k}: {{}}")
                    else:
                        lines.append(f"{space}{k}:")
                        lines.append(self._dict_to_yaml(v, indent + 1))
                elif isinstance(v, list):
                    if not v: lines.append(f"{space}{k}: []")
                    else:
                        lines.append(f"{space}{k}:")
                        for item in v:
                            if isinstance(item, dict):
                                lines.append(f"{space}  -")
                                lines.append(self._dict_to_yaml(item, indent + 2))
                            else:
                                lines.append(f"{space}  - {item}")
                else:
                    val = str(v).replace("\n", " ") if v is not None else ""
                    lines.append(f"{space}{k}: {val}")
        return "\n".join(lines)

    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[Dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        try:
            from echo_utils import EchoEvents, get_echo_version
            events = EchoEvents(__event_emitter__)
            
            meta = __metadata__ or body.get("metadata", {})
            chat_id = meta.get("chat_id")
            user_id = __user__.get("id", "system") if __user__ else "system"
            
            all_files = body.get("files", [])
            msgs = body.get("messages", [])
            if not msgs: return body

            if len(msgs) >= 2:
                prev_content = str(msgs[-2].get("content", ""))
                # Interception clé API AI Studio (fallback OAuth2).
                # L'ancien bloc de détection code 4/… (PKCE) est supprimé :
                # le Device Flow (RFC 8628) ne génère pas de code dans le chat.
                last_content = str(msgs[-1].get("content", "")).strip()
                keys = re.findall(GOOGLE_API_KEY_REGEX, last_content)
                if "(ECHO_SESSION_AUTH_PENDING)" in prev_content and keys:
                    body["_api_key"] = last_content
                    msgs[-1]["content"] = "🔐 *Vérification de la clé API Google en cours...*"
                    return body

            tokens = []
            if __user__ and "id" in __user__:
                tokens = self.auth.get_api_keys(__user__["id"])

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
                
            results = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                tasks = [self._process_file_task(user_id, f, tokens, None, THINKING_LEVEL_FLASH, chat_id, events) for f in files_to_process]
                for task in tasks:
                    results.append(await task)
                    await asyncio.sleep(0.5)

            idx = -1
            ordered_user_parts = []  # Parts user en ordre (texte + images entrelacés)
            for i in range(len(msgs)-1, -1, -1):
                if msgs[i].get("role") == "user": 
                    idx = i
                    orig_content = msgs[i].get("content")
                    if isinstance(orig_content, list):
                        # Content multipart OWUI (texte + images inline) : extraction ordonnée
                        for p in orig_content:
                            if isinstance(p, dict):
                                if p.get("type") == "image_url" or "inline_data" in p or "inlineData" in p:
                                    ordered_user_parts.append(p)
                                elif p.get("type") == "text" and p.get("text", "").strip():
                                    ordered_user_parts.append({"text": p["text"]})
                    break

            if idx != -1:
                active_msg_ids = [m.get("id") for m in msgs if m.get("id")]
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
                active_registry = state_manager.get_session_registry(chat_id, active_msg_ids) if chat_id else {}
                
                for r in results:
                    if r.get("status") == "success":
                        entry = {"id": r.get("fid"), "mime": r.get("mime"), "statut": r.get("type")}
                        if r.get("slug"): entry["slug"] = r["slug"]  # Expose le slug dans le YAML → modèle peut interroger le RAG directement
                        active_registry[r.get("name")] = entry

                meta_vars = meta.get("variables", {})
                u_v = __user__.get("valves") if __user__ else self.user_valves
                display_name = __user__.get("name", "anonyme") if getattr(u_v, "ENABLE_USER_NAME", False) else "anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = getattr(u_v, "OVERRIDE_LOCATION", "")
                final_loc = u_loc if u_loc else sys_loc
                
                env_snapshot = {
                    "version_framework_echo": get_echo_version() or "##ECHO_VERSION##",
                    "modèle_actuel": "##MODEL_ID##",
                    "modèle_origine": "##MODEL_ORIGIN##",
                    "nom_utilisateur": display_name,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "localisation": final_loc,
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC"),
                    "registre_fichiers": active_registry,
                    "registre_plan": [{"id": p["plan_id"], "goal": p["goal"][:80], "status": p["status"], "modele": p.get("author_model", "?")} for p in (state_manager.get_plans() if chat_id else [])],
                    "registre_codex": [{"id": d["filename"], "lang": d["language"], "lines": d["lines"], "last_commit": d.get("commit_msg", "")} for d in (state_manager.get_codex_docs() if chat_id else [])],
                }

                body.setdefault("metadata", {})
                body["metadata"]["_echo_env_info"] = {
                    "nom_utilisateur": display_name, "localisation": final_loc,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                }
                
                rich_parts = []
                yaml_str = self._dict_to_yaml(env_snapshot)
                rich_parts.append({"text": f"<environnement_contexte>\n{yaml_str}\n</environnement_contexte>\n\n"})
                if ordered_user_parts: rich_parts.extend(ordered_user_parts)
                
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == "rag_ephemeral": rich_parts.append({"text": res["content"]})
                        elif res["type"] == "transmitted":
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                
                body["metadata"]["_echo_user_parts_draft"] = rich_parts
                body["metadata"]["_echo_user_msg_id"] = msgs[idx].get("id")
                body["metadata"]["_echo_user_msg_updated_at"] = msgs[idx].get("updated_at")
                body["metadata"]["_echo_files_to_seal"] = results

            if all_files:
                body.setdefault("metadata", {})
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
            # Masquage des clés API AI Studio (AIza...) si elles apparaissent dans l'historique
            if re.search(GOOGLE_API_KEY_REGEX, content):
                content = re.sub(GOOGLE_API_KEY_REGEX, "[CLÉ API GOOGLE MASQUÉE PAR SÉCURITÉ]", content)
            m["content"] = content
        return body
