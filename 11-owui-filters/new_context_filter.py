"""
title: ECHO New Context Filter V2
author: Wilfried BARNAVON
author_url: https://github.com/Wilfried-Barnavon-Perso
version: 7.41
description: 7.41: Purge explicite de l'image_url généré par OWUI pour éliminer le doublon Base64.
             7.40: Correction de la stringification fatale des listes multipart (images Base64) dans l'outlet, empêchant l'erreur 400 RAG.
             7.39: Fix angle mort de l'import Workspace (fichiers globaux meta.files non vus).
             7.37: Refonte de l'ingestion par Synthèse Guidée par RAG (O(1)) et ajout de SMART_CONTEXT_CHUNK_LIMIT.
             7.36: Refactorisation statuts d'ingestion. Centralisation via FILE_INGESTION_STATUS.
             7.35: Renommage rag_ephemeral -> vectorized_sum_up et assouplissement de la directive système.
             7.34: Intégration native des PDF dans le Cas 1 (Injection Binaire Directe) via inline_data.
             7.33: Hotfix ImportError get_gemini_mime preventing file processing.
             7.32: Fix missing vault move for PDFs and media causing infinite reprocessing loop.
             7.31: Fallback SQLite transparent for small files without full reprocessing.
             7.30: Zero-RAM file processing. Deferred Registry V2 Sealing (echo_resources).
             7.27: Fix CAS 2 injection — retour de new_path même si Git/SQL échouent pour éviter FileNotFoundError.
             7.26: Factorisation : Création anticipée du Vault/DOMAIN au début de l'inlet pour sécuriser l'ingestion Codex.
             7.25: Sécurisation de l'ingestion Codex Zéro-RAM (shutil.copy2, gestion des commits vides, suppression source post-succès).
             7.5: Fix génération slug. 7.6: Migration Antigravity 2.1 — suppression GOOGLE_OAUTH_CODE_REGEX (PKCE legacy).
             7.7: Centralisation THINKING_LEVEL_FLASH (echo_constants v4.8) — suppression du "HIGH" hardcodé.
             7.8: Injection registre_plan dans environnement_contexte (Strategic Planner v1.0).
             7.9: Fix multimodal — extraction ordonnée des text-parts du content OWUI
             dans le Draft. Correction perte texte utilisateur avec images inline.
             7.10: Revert dégradation gracieuse CAS 3 (le fallback INDEXATION suffit).
             7.11: Cosmetic — Anonyme → anonyme (minuscule) dans nom_utilisateur.
             7.12: Injection registre_codex dans environnement_contexte (ECHO Codex v1.0).
             7.13: Isolation stricte des fichiers par session (resolve_upload_file_path avec chat_id).
             7.14: Refonte Smart Context (Mémoire Vectorisée de Session & Map-Reduce) + Indexation de la donnée brute.
             7.15: Correction hallucination outil RAG et unification directive impérative.
             7.16: Pipeline Mémoire Vectorisée de Session Transmodal. Extraction textuelle API (Phase 1)
             puis Map-Reduce local (Phase 2) pour unifier le traitement du binaire et du texte.
             7.17: Fast-Path pour bypass Map-Reduce sur textes courts (ECHO_MR_SUMMARY_MAX_WORDS).
             7.18: Correction bug multimodal (prompt texte écrasé par echo_utils) + Refonte Prompts.
             7.19: Refonte architecturale identifiants (Éradication du slug). Utilisation de
             file_id natif comme source_id pour la Mémoire Vectorisée de Session.
             7.20: Conversion Office → Markdown via MarkItDown (Microsoft, MIT). Fichiers
             docx/docm/xlsx/xlsm/pptx convertis en .md sur disque avec description d'images
             OOXML via LITE. UserValves ENABLE_OFFICE_CONVERSION et MAX_OFFICE_FILE_SIZE_MB.
             7.29: Architecture d'immutabilité. Fichiers déplacés dans vault 'files' et copiés dans 'codex'.
             Refactorisation CAS 3 Phase 2 en _index_and_summarize() réutilisable.
             7.21: Registre Unifié V2 — Suppression registre_fichiers, registre_plan,
             registre_codex de l'AEC. Remplacement par <evenement_systeme> évènementiel.
             Suppression mark_processed dans le filtre (CAS 3 et CAS 4). Watermark delta
             pour détecter les ressources créées par outils/HUD hors-tour.
             7.22: Fix routage PDF — Les PDF ne passent plus par le CAS 1 (binaire
             direct). Ils sont systématiquement traités par le CAS 3 (extraction +
             résumé Map-Reduce + Mémoire Vectorisée de Session) pour garantir un résumé persistant.
             7.23: Fix _dict_to_yaml — Ajout support des listes racine pour garantir la sérialisation correcte des structures de données dynamiques.
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
import httpx
from concurrent.futures import ThreadPoolExecutor

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import resolve_upload_file_path, EchoStateManager, EchoGeminiClient, EchoEvents, EchoAuth
from echo_constants import (
    get_gemini_mime, ECHO_USERS_ROOT, 
    GOOGLE_API_KEY_REGEX, MODEL_FLASH,
    ECHO_QDRANT_URL,
    THINKING_LEVEL_FLASH,
    CONVERTIBLE_OFFICE_EXTENSIONS, DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB, OOXML_IMAGE_EXTENSIONS,
    FILE_INGESTION_STATUS
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    # Priorité basse (1) pour s'exécuter en tout premier (Inlet) avant les autres filtres
    priority: int = 20

    class Valves(BaseModel):
        DEBUG_MODE: bool = Field(default=False)

    class UserValves(BaseModel):
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        SMART_CONTEXT_CHUNK_LIMIT: int = Field(default=10, ge=2, le=50, description="🧠 Nombre de passages extraits pour la Synthèse Guidée par RAG (2-50).")
        ENABLE_OFFICE_CONVERSION: bool = Field(default=True, description="📄 Convertit automatiquement les fichiers Office (Word, Excel, PowerPoint) en texte pour l'analyse.")
        MAX_OFFICE_FILE_SIZE_MB: int = Field(default=DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB, description="📏 Taille maximale (Mo) des fichiers Office acceptés pour la conversion automatique.")
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

    def _dict_to_yaml(self, d: Any, indent: int = 0) -> str:
        """Sérialiseur YAML minimaliste pour ECHO."""
        lines = []
        space = "  " * indent
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    lines.append(f"{space}-")
                    lines.append(self._dict_to_yaml(item, indent + 1))
                else:
                    lines.append(f"{space}- {item}")
        elif isinstance(d, dict):
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
            from echo_utils import EchoEvents, get_echo_version, EchoStateManager
            events = EchoEvents(__event_emitter__)
            
            meta = __metadata__ or body.get("metadata", {})
            chat_id = meta.get("chat_id")
            user_id = __user__.get("id", "system") if __user__ else "system"
            
            # Factorisation : Création anticipée du DOMAIN (Vault) Utilisateur-Chat
            state_manager = None
            if chat_id:
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
            
            all_files_dict = {}
            for f in (body.get("files") or []):
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid: all_files_dict[fid] = f
                
            user_msg_files = meta.get("user_message", {}).get("files") or []
            for f in user_msg_files:
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid: all_files_dict[fid] = f
                
            # [NOUVEAU] Récupération des fichiers globaux (ex: import Workspace)
            global_workspace_files = meta.get("files") or []
            for f in global_workspace_files:
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid: all_files_dict[fid] = f
                
            all_files = list(all_files_dict.values())
            
            msgs = body.get("messages") or []
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
            files_already_processed = []
            if chat_id:
                from echo_utils import get_echo_session_path
                vault_dir = os.path.normpath(get_echo_session_path(user_id, chat_id, "files"))
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid:
                        path = resolve_upload_file_path(user_id, fid, chat_id=chat_id)
                        if path:
                            if os.path.normpath(path).startswith(vault_dir):
                                files_already_processed.append(f)
                            else:
                                files_to_process.append(f)
                
            # [NEW] Injection des téléchargements Playwright en attente d'ingestion
            if chat_id and state_manager:
                pending_resources = state_manager.get_resources(
                    status=FILE_INGESTION_STATUS.get("PENDING_INGESTION", "pending_ingestion")
                )
                for pr in pending_resources:
                    f_obj = {
                        "id": pr["id"],
                        "name": pr["name"],
                        "mime_type": pr.get("mime", "application/octet-stream"),
                        "file": {
                            "id": pr["id"],
                            "name": pr["name"],
                            "path": pr.get("storage_path", "")
                        }
                    }
                    if f_obj not in files_to_process:
                        files_to_process.append(f_obj)

            results_to_seal = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                
                # Instanciation du Pipeline Externe
                if "/app/backend/echo_libs" not in sys.path:
                    sys.path.append("/app/backend/echo_libs")
                try:
                    from echo_ingestion import EchoIngestionPipeline
                except ImportError as e:
                    # Dans le cas où on teste localement, on ajoute le path du dossier contenant echo_ingestion
                    dir_path = os.path.dirname(os.path.realpath(__file__))
                    lib_path = os.path.join(os.path.dirname(dir_path), "14-owui-libs")
                    sys.path.append(lib_path)
                    from echo_ingestion import EchoIngestionPipeline
                    
                pipeline = EchoIngestionPipeline(valves=self.user_valves)
                sem = asyncio.Semaphore(3)
                
                async def safe_process(f):
                    async with sem:
                        return await pipeline.process_file_task(user_id, f, chat_id, events)
                        
                tasks = [safe_process(f) for f in files_to_process]
                gathered = await asyncio.gather(*tasks, return_exceptions=True)
                
                for i, res in enumerate(gathered):
                    if isinstance(res, Exception):
                        err_msg = str(res)
                        file_name = files_to_process[i].get('name', 'inconnu')
                        print(f"[ECHO-FILTER] !! Pipeline exception for {file_name}: {err_msg}", flush=True)
                        if events: await events.status(f"❌ Crash critique pour {file_name}", False)
                        results_to_seal.append({"status": "error", "name": file_name, "error": f"Crash Pipeline: {err_msg}"})
                    else:
                        results_to_seal.append(res)

            results = list(results_to_seal)
            
            # Réhydratation hybride : Disque (Codex) / Base (Images/PDFs)
            if files_already_processed and chat_id and state_manager:
                for f in files_already_processed:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    res = state_manager.get_resource(fid)
                    if res:
                        if res.get("resource_type") == "codex" and res.get("storage_path") and os.path.exists(res["storage_path"]):
                            with open(res["storage_path"], "r", encoding="utf-8") as file_obj:
                                content = file_obj.read()
                            filename = res.get("name", "fichier")
                            results.append({
                                "status": "success",
                                "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
                                "sub_type": "text",
                                "content": f"📄 **Fichier : {filename}**\n\n```\n{content}\n```"
                            })
                        elif res.get("resource_type") == "media" and res.get("status") == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"] and res.get("storage_path") and os.path.exists(res["storage_path"]):
                            # Réhydratation d'un média binaire petit (CAS 1)
                            with open(res["storage_path"], "rb") as file_obj:
                                b64 = base64.b64encode(file_obj.read()).decode("utf-8")
                            mime = res.get("mime", "application/octet-stream")
                            filename = res.get("name", "fichier")
                            results.append({
                                "status": "success",
                                "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
                                "sub_type": "binary",
                                "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                            })
                        elif res.get("summary"):
                            # CAS 3 (Vectorisé) ou image résumée
                            results.append({
                                "status": "success",
                                "type": res.get("status", FILE_INGESTION_STATUS["PUT_IN_CONTEXT"]),
                                "sub_type": "text" if "text" in str(res.get("mime", "")) or "json" in str(res.get("mime", "")) else "image",
                                "content": res["summary"]
                            })

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
                                if p.get("type") == "image_url":
                                    # [PURGE] Liste Noire : On ignore volontairement 'image_url' (généré par OWUI).
                                    # ECHO gérera l'image via son propre pipeline d'ingestion (inline_data ou text_summary).
                                    pass
                                elif p.get("type") == "text":
                                    if p.get("text", "").strip():
                                        ordered_user_parts.append({"text": p["text"]})
                                else:
                                    # [PASSTHROUGH] Liste Blanche implicite.
                                    # On laisse passer les 'inline_data' d'ECHO, et tout futur format inattendu.
                                    ordered_user_parts.append(p)
                    break

            if idx != -1:
                meta_vars = meta.get("variables", {})
                u_v = __user__.get("valves") if __user__ else self.user_valves
                display_name = __user__.get("name", "anonyme") if getattr(u_v, "ENABLE_USER_NAME", False) else "anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = getattr(u_v, "OVERRIDE_LOCATION", "")
                final_loc = u_loc if u_loc else sys_loc
                
                # === AEC V2 : Snapshot minimaliste (sans registres) ===
                env_snapshot = {
                    "version_framework_echo": get_echo_version() or "##ECHO_VERSION##",
                    "modèle_actuel": "##MODEL_ID##",
                    "modèle_origine": "##MODEL_ORIGIN##",
                    "nom_utilisateur": display_name,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "localisation": final_loc,
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC"),
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

                # === AEC V2 : Évènements système (fichiers uploadés ce tour) ===
                sys_events = []
                error_events = []
                for r in results:
                    if r.get("status") == "success":
                        evt = {"type": r.get("type"), "name": r.get("name"), "mime": r.get("mime")}
                        if r.get("source_id"): evt["source_id"] = r["source_id"]
                        if evt not in sys_events: sys_events.append(evt)
                    elif r.get("status") == "error":
                        err_evt = {"name": r.get("name"), "error": r.get("error", "Erreur inconnue")}
                        if err_evt not in error_events: error_events.append(err_evt)

                # === AEC V2 : Détection delta (ressources créées par outils/HUD hors-tour) ===
                if chat_id:
                    last_check = body.get("metadata", {}).get("_echo_last_event_check_at")
                    if last_check:
                        delta_resources = state_manager.get_resources(created_after=int(last_check))
                        already_processed_ids = [f.get("id") or f.get("file", {}).get("id") for f in files_already_processed]
                        for dr in delta_resources:
                            # Ne pas dupliquer les fichiers du tour courant (sys_events) ou déjà ingérés (files_already_processed)
                            if dr["id"] not in already_processed_ids and not any(e.get("name") == dr["name"] for e in sys_events):
                                sys_events.append({
                                    "type": dr["status"], "name": dr["name"],
                                    "mime": dr.get("mime"), "resource_type": dr["resource_type"],
                                    "source": "outil/HUD"
                                })
                    # Sauvegarder le timestamp actuel pour le prochain delta
                    body["metadata"]["_echo_last_event_check_at"] = int(time.time())

                # Injection des évènements dans l'AEC (uniquement s'il y en a)
                if sys_events or error_events:
                    events_text = "<evenement_systeme>\n"
                    if sys_events:
                        events_text += self._dict_to_yaml(sys_events) + "\n"
                        events_text += "> Utilisez `query_registry` pour consulter l'état complet des ressources.\n"
                    if error_events:
                        events_text += "\n[ERREURS D'INGESTION]\n" + self._dict_to_yaml(error_events) + "\n"
                        events_text += "> Ces fichiers ont échoué et ne sont pas exploitables.\n"
                    events_text += "</evenement_systeme>\n\n"
                    rich_parts.append({"text": events_text})

                if ordered_user_parts: rich_parts.extend(ordered_user_parts)
                
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]: rich_parts.append({"text": res["content"]})
                        elif res["type"] == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"]:
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                
                body["metadata"]["_echo_user_parts_draft"] = rich_parts
                body["metadata"]["_echo_user_msg_id"] = msgs[idx].get("id")
                body["metadata"]["_echo_user_msg_updated_at"] = msgs[idx].get("updated_at")
                body["metadata"]["_echo_files_to_seal"] = results_to_seal

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
            orig = m.get("content", "")
            if isinstance(orig, list):
                new_content = []
                for p in orig:
                    if isinstance(p, dict) and p.get("type") == "text":
                        txt = str(p.get("text", ""))
                        if re.search(GOOGLE_API_KEY_REGEX, txt):
                            txt = re.sub(GOOGLE_API_KEY_REGEX, "[CLÉ API GOOGLE MASQUÉE PAR SÉCURITÉ]", txt)
                        p["text"] = txt
                    new_content.append(p)
                m["content"] = new_content
            else:
                content = str(orig)
                # Masquage des clés API AI Studio (AIza...) si elles apparaissent dans l'historique
                if re.search(GOOGLE_API_KEY_REGEX, content):
                    content = re.sub(GOOGLE_API_KEY_REGEX, "[CLÉ API GOOGLE MASQUÉE PAR SÉCURITÉ]", content)
                m["content"] = content
        return body
