# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO New Context Filter
author: Wilfried BARNAVON
author_url: https://github.com/Wilfried-Barnavon-Perso
version: 6.21.7.1
description: Composant système interne : ECHO New Context Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.7.1: Détection de features.voice sur le tour utilisateur courant et injection SSOT de EchoAEC.render_voice_mode_directive().
# 6.18.23.1: Résolution de _echo_user_msg_parent_id pour le scellement Shadow v3 et suppression de l'interception obsolète ECHO_AUTH_PENDING_MARKER/_api_key.
# 6.18.9.1: Lecture directe en mémoire de __user__.get("gender") sans requête SQLite sur identity.db.
# 6.10.0.1: Détection automation_id, neutralisation du salut /help en 1er tour automatisé et injection de directive AEC autonome.
# 6.7.20.1: Détection déterministe des commandes / ECHO en tête de requête, émission de AEC_commande (Priorité 4) et alignement de la directive d'accueil sur /help.


from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import pybase64 as base64
import os
import sys
import re
import asyncio
import logging
import shutil
import time
from datetime import datetime

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_paths import resolve_upload_file_path, wait_for_chat_clone
from echo_gemini_client import EchoGeminiClient
from echo_auth import EchoAuth
from echo_constants import (
    GOOGLE_API_KEY_REGEX,
    DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB,
    FILE_INGESTION_STATUS,
)
from echo_core import EchoCompactionEngine
from echo_session_gc import resource_provenance

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=70, hidden=True, description="Priorité d'exécution (70 = après context_compaction_filter à 60 et avant codex_links_filter à 80).")
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        SMART_CONTEXT_CHUNK_LIMIT: int = Field(default=10, ge=2, le=50, description="🧠 Nombre de passages extraits pour la Synthèse Guidée par RAG (2-50).")
        ENABLE_OFFICE_CONVERSION: bool = Field(default=True, description="📄 Convertit automatiquement les fichiers Office (Word, Excel, PowerPoint) en texte pour l'analyse.")
        MAX_OFFICE_FILE_SIZE_MB: int = Field(default=DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB, description="📏 Taille maximale (Mo) des fichiers Office acceptés pour la conversion automatique.")

    def __init__(self):
        # ==============================================================================
        # INFRASTRUCTURE ECHO : CONTRÔLE DU RAG NATIF
        # ==============================================================================
        self.file_handler = True
        # ==============================================================================

        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[Dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        try:
            from echo_events import EchoEvents
            from echo_paths import get_echo_version
            from echo_state_manager import EchoStateManager
            events = EchoEvents(__event_emitter__)

            meta = __metadata__ or body.get("metadata", {})
            chat_id = meta.get("chat_id") or body.get("chat_id")
            user_id = __user__.get("id", "system") if __user__ else "system"

            # Factorisation : Création anticipée du DOMAIN (Vault) Utilisateur-Chat
            state_manager = None
            if chat_id:
                await wait_for_chat_clone(chat_id)
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)

            all_files_dict = {}
            for f in (body.get("files") or []):
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid:
                    all_files_dict[fid] = f

            user_msg_files = meta.get("user_message", {}).get("files") or []
            for f in user_msg_files:
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid:
                    all_files_dict[fid] = f

            # [NOUVEAU] Récupération des fichiers globaux (ex: import Workspace)
            global_workspace_files = meta.get("files") or []
            for f in global_workspace_files:
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid:
                    all_files_dict[fid] = f

            all_files = list(all_files_dict.values())

            msgs = body.get("messages") or []
            if not msgs:
                return body

            if __user__ and "id" in __user__:
                self.auth.get_api_keys(__user__["id"])

            files_to_process = []
            files_already_processed = []
            results_to_seal = []
            if chat_id:
                from echo_paths import get_echo_session_path
                vault_dir = os.path.normpath(get_echo_session_path(user_id, chat_id, "files"))
                pending_st = FILE_INGESTION_STATUS.get("PENDING_INGESTION", "pending_ingestion")
                global_state_manager = EchoStateManager(user_id=user_id, chat_id=None) if all_files else None
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid:
                        path = resolve_upload_file_path(user_id, fid, chat_id=chat_id)
                        if path:
                            res_existing = state_manager.get_resource(fid) if state_manager else None
                            is_pending = not res_existing or res_existing.get("status") == pending_st
                            if os.path.normpath(path).startswith(vault_dir) and not is_pending:
                                files_already_processed.append(f)
                            else:
                                global_res = global_state_manager.get_resource(fid) if global_state_manager else None
                                if (
                                    global_res
                                    and global_res.get("status") == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]
                                    and global_res.get("summary")
                                ):
                                    os.makedirs(vault_dir, exist_ok=True)
                                    target_vault_path = os.path.join(vault_dir, os.path.basename(path))
                                    if not os.path.exists(target_vault_path) and not os.path.islink(target_vault_path):
                                        try:
                                            os.symlink(os.path.realpath(path), target_vault_path)
                                        except OSError:
                                            shutil.copy2(path, target_vault_path)
                                    cloned_pts = await EchoGeminiClient.clone_file_rag_points_to_chat(user_id, fid, chat_id)
                                    if cloned_pts > 0:
                                        results_to_seal.append({
                                            "status": "success",
                                            "type": FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"],
                                            "source_id": fid,
                                            "fid": fid,
                                            "name": global_res.get("name") or f.get("name") or os.path.basename(path),
                                            "mime": global_res.get("mime") or f.get("content_type") or "application/octet-stream",
                                            "storage_path": target_vault_path,
                                            "content": global_res["summary"],
                                        })
                                        continue
                                files_to_process.append(f)

            # [NEW] Injection des téléchargements Playwright en attente d'ingestion
            if chat_id and state_manager:
                existing_to_process_ids = {
                    (x.get("id") or x.get("file", {}).get("id")) for x in files_to_process
                }
                pending_resources = state_manager.get_resources(
                    status=FILE_INGESTION_STATUS.get("PENDING_INGESTION", "pending_ingestion")
                )
                for pr in pending_resources:
                    if pr["id"] in existing_to_process_ids:
                        continue
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

            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)

                # Instanciation du Pipeline Externe
                if "/app/backend/echo_libs" not in sys.path:
                    sys.path.append("/app/backend/echo_libs")
                try:
                    from echo_ingestion import EchoIngestionPipeline
                except ImportError:
                    # Dans le cas où on teste localement, on ajoute le path du dossier contenant echo_ingestion
                    dir_path = os.path.dirname(os.path.realpath(__file__))
                    lib_path = os.path.join(os.path.dirname(dir_path), "14-owui-libs")
                    sys.path.append(lib_path)
                    from echo_ingestion import EchoIngestionPipeline

                pipeline = EchoIngestionPipeline(valves=self.valves)
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
                        if events:
                            await events.status(f"❌ Crash critique pour {file_name}", False)
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

            # Détermine si des images du message courant ont été prises en charge par le pipeline ECHO via un fid disque.
            # En chat sauvegardé OWUI, middleware.py:2157 & 2254 duplique chaque image de user_msg_files en data:image/...
            # dans msgs[-1]["content"]. Si elles ont un fid résolu, EchoIngestionPipeline les gouverne déjà (PUT_IN_CONTEXT <10Mo
            # ou VECTORIZED_SUM_UP >=10Mo). En revanche, en chat temporaire (isTemporary) ou API directe, fid est absent.
            resolved_fids = {
                (f.get("id") or f.get("file", {}).get("id"))
                for f in (files_to_process + files_already_processed)
            }
            has_ingested_msg_images = any(
                (f.get("id") or f.get("file", {}).get("id")) in resolved_fids
                and (
                    f.get("type") == "image"
                    or str(f.get("content_type") or f.get("mime_type") or f.get("file", {}).get("meta", {}).get("content_type") or "").startswith("image/")
                )
                for f in (user_msg_files + (body.get("files") or []))
            )

            idx = -1
            current_user_multipart_payload = []  # Payload hybride courant (texte + inline_data)
            user_raw_texts = []
            for i in range(len(msgs) - 1, -1, -1):
                if msgs[i].get("role") == "user":
                    idx = i
                    orig_content = msgs[i].get("content")
                    if isinstance(orig_content, list):
                        # Content multipart OWUI (texte + images inline) : extraction ordonnée
                        for p in orig_content:
                            if isinstance(p, dict):
                                if p.get("type") == "image_url":
                                    # Si le tour possède des images avec fid ingérées par EchoIngestionPipeline, on ignore
                                    # le miroir data:image/... injecté par middleware.py:2254 pour éviter tout doublon
                                    # ou contournement de MAX_DIRECT_MMEDIA_INJECT_SIZE (VECTORIZED_SUM_UP).
                                    # Sinon (chat temporaire sans fid ou appel API direct), on préserve l'image inline.
                                    if not has_ingested_msg_images:
                                        url_val = p.get("image_url", {}).get("url", "")
                                        if url_val.startswith("data:") and ";base64," in url_val:
                                            try:
                                                mime_val = url_val.split(";", 1)[0].replace("data:", "").strip()
                                                b64_val = url_val.split(",", 1)[1].strip()
                                                current_user_multipart_payload.append({
                                                    "inline_data": {"mime_type": mime_val, "data": b64_val}
                                                })
                                            except Exception as e:
                                                print(f"[ECHO-FILTER] Erreur décodage image_url inline: {e}", flush=True)
                                elif p.get("type") == "text":
                                    txt_val = p.get("text", "")
                                    if txt_val.strip():
                                        user_raw_texts.append(txt_val)
                                        current_user_multipart_payload.append({"text": f"<REQUETE_UTILISATEUR>\n{txt_val}\n</REQUETE_UTILISATEUR>"})
                                else:
                                    # [PASSTHROUGH] Liste Blanche implicite.
                                    # On laisse passer les 'inline_data' d'ECHO, et tout futur format inattendu.
                                    current_user_multipart_payload.append(p)
                    else:
                        txt_val = str(orig_content or "")
                        if txt_val.strip():
                            user_raw_texts.append(txt_val)
                            current_user_multipart_payload.append({"text": f"<REQUETE_UTILISATEUR>\n{txt_val}\n</REQUETE_UTILISATEUR>"})
                    break

            if idx != -1:
                meta_vars = meta.get("variables", {})

                enable_name = meta.get("_echo_user_name_enabled", False)
                display_name = (__user__ or {}).get("name", "anonyme") if enable_name else "anonyme"
                user_gender = None
                if enable_name and __user__:
                    raw_gender = str(__user__.get("gender") or "").strip().upper()
                    if raw_gender in ("M", "F", "X"):
                        user_gender = raw_gender

                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = meta.get("_echo_override_location", "")
                final_loc = u_loc if u_loc else sys_loc

                tour_conversation = 0
                prev_non_sys_role = None
                for m in msgs:
                    r_role = m.get("role") if isinstance(m, dict) else None
                    if r_role == "system":
                        continue
                    if r_role == "user" and prev_non_sys_role != "user":
                        tour_conversation += 1
                    prev_non_sys_role = r_role

                # === AEC : Méta-données brutes ===
                date_heure = meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu")
                timezone = meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                version = get_echo_version() or "##ECHO_VERSION##"
                model_id = "##MODEL_ID##"
                model_origin = "##MODEL_ORIGIN##"

                env_info = {
                    "nom_utilisateur": display_name,
                    "localisation": final_loc,
                    "date_et_heure": date_heure,
                    "timezone": timezone,
                }
                if user_gender:
                    env_info["genre"] = user_gender

                body.setdefault("metadata", {})
                body["metadata"]["_echo_env_info"] = env_info

                from echo_aec import EchoAEC

                rich_parts = []
                rich_parts.append({"text": EchoAEC.render_model_context(model_id, model_origin, version)})
                rich_parts.append({"text": EchoAEC.render_identity_context(display_name, gender=user_gender)})
                rich_parts.append({"text": EchoAEC.render_time_context(date_heure, timezone, tour_conversation)})
                rich_parts.append({"text": EchoAEC.render_location_context(final_loc)})

                # === AEC : Directive d'Initialisation de Session ou Montée de Version ===
                user_msg_meta = ((meta.get("user_message") or {}).get("meta") or {}) if isinstance(meta, dict) else {}
                automation_id = (meta.get("automation_id") if isinstance(meta, dict) else None) or user_msg_meta.get("automation_id")
                automation_name = user_msg_meta.get("automation_name") or ""
                directive_to_inject = None
                if idx == len(msgs) - 1:
                    if chat_id and state_manager:
                        last_version = state_manager.get_session_setting("last_framework_version")
                        is_new_session = (last_version is None or tour_conversation == 1)
                        is_upgrade = (
                            last_version is not None
                            and version != "##ECHO_VERSION##"
                            and last_version != version
                        )

                        if is_new_session:
                            if not automation_id:
                                directive_to_inject = EchoAEC.render_directive(
                                    "Directive d'Initialisation de Session : Saluer l'Utilisateur et présenter "
                                    f"le Framework (nom vernaculaire Écho, nom technique ECHO, version active {version}, "
                                    "missions fondamentales), consulter silencieusement les Méta-Artéfacts si pertinent, "
                                    "recommander la commande /help, puis répondre à la requête de l'Utilisateur."
                                )
                            if version != "##ECHO_VERSION##":
                                state_manager.save_session_setting("last_framework_version", version)
                        elif is_upgrade:
                            directive_to_inject = EchoAEC.render_directive(
                                f"Directive de Mise à Niveau du Framework : La version du Framework a évolué ({last_version} -> {version}). "
                                "Notifier succinctement la mise à jour de l'ancienne version à la nouvelle avant de traiter la requête de l'Utilisateur."
                            )
                            state_manager.save_session_setting("last_framework_version", version)
                    elif tour_conversation == 1 and not automation_id:
                        directive_to_inject = EchoAEC.render_directive(
                            "Directive d'Initialisation de Session : Saluer l'Utilisateur et présenter "
                            f"le Framework (nom vernaculaire Écho, nom technique ECHO, version active {version}, "
                            "missions fondamentales), consulter silencieusement les Méta-Artéfacts si pertinent, "
                            "recommander la commande /help, puis répondre à la requête de l'Utilisateur."
                        )

                if directive_to_inject:
                    rich_parts.append({"text": directive_to_inject})

                if idx == len(msgs) - 1 and automation_id:
                    auto_label = f" « {automation_name} »" if automation_name else ""
                    rich_parts.append({
                        "text": EchoAEC.render_directive(
                            f"Exécution autonome d'automatisation planifiée{auto_label} (id: {automation_id}) : l'Utilisateur n'est pas présent en interactif. Exécuter intégralement la consigne de façon autonome, ne pas saluer ni invoquer d'outil interactif bloquant (ask_user_input, confirmations modales), et produire un compte-rendu direct et exploitable."
                        )
                    })

                is_voice_mode = bool((body.get("features") or {}).get("voice"))
                if idx == len(msgs) - 1 and is_voice_mode:
                    rich_parts.append({
                        "text": EchoAEC.render_voice_mode_directive()
                    })

                if idx == len(msgs) - 1 and user_raw_texts:
                    from echo_core import extract_leading_slash_commands
                    for cmd_entry in extract_leading_slash_commands("\n".join(user_raw_texts)):
                        rich_parts.append({
                            "text": EchoAEC.render_command_directive(
                                cmd_entry["slash"],
                                cmd_entry["directive"],
                            )
                        })

                # === Configuration ZoneInfo ===
                try:
                    from zoneinfo import ZoneInfo
                except ImportError:
                    from pytz import timezone as ZoneInfo
                user_tz_str = meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                try:
                    user_tz = ZoneInfo(user_tz_str)
                except Exception:
                    try:
                        user_tz = ZoneInfo("UTC")
                    except Exception:
                        from datetime import timezone as dt_timezone
                        user_tz = dt_timezone.utc

                # === AEC : Évènements système (fichiers uploadés ce tour) ===
                sys_events = []
                error_events = []
                for r in results:
                    if r.get("status") == "success":
                        evt = {"type": r.get("type"), "name": r.get("name"), "mime": r.get("mime")}
                        evt["date"] = datetime.fromtimestamp(r.get("created_at", time.time()), tz=user_tz).strftime("%Y-%m-%d %H:%M:%S")
                        if r.get("source_id"):
                            evt["source_id"] = r["source_id"]
                        if evt not in sys_events:
                            sys_events.append(evt)
                    elif r.get("status") == "error":
                        err_evt = {"name": r.get("name"), "error": r.get("error", "Erreur inconnue")}
                        if err_evt not in error_events:
                            error_events.append(err_evt)

                # === AEC : Détection delta (ressources créées par outils/HUD hors-tour) ===
                branch_messages = None
                if chat_id:
                    branch_messages = await EchoCompactionEngine.load_active_branch_messages(chat_id)

                if chat_id and state_manager:
                    last_check = body.get("metadata", {}).get("_echo_last_event_check_at") or int(
                        state_manager.get_session_setting("last_event_check_at") or 0
                    )
                    if last_check:
                        delta_resources = [
                            dr for dr in state_manager.get_resources(created_after=int(last_check))
                            if dr.get("resource_type") not in ("aec_event", "aec_directive")
                        ]
                        if delta_resources:
                            branch_ids = [
                                str(bm.get("id"))
                                for bm in (branch_messages or [])
                                if isinstance(bm, dict) and bm.get("id")
                            ]
                            active_ids = (
                                set(branch_ids)
                                | {m.get("id") for m in msgs if isinstance(m, dict) and m.get("id")}
                                | ({meta.get("user_message_id")} if meta.get("user_message_id") else set())
                            )
                            already_processed_ids = [
                                f.get("id") or f.get("file", {}).get("id")
                                for f in files_already_processed
                            ]
                            for dr in delta_resources:
                                if dr["id"] not in already_processed_ids and not any(
                                    e.get("name") == dr["name"] for e in sys_events
                                ):
                                    sys_events.append({
                                        "type": dr["status"],
                                        "name": dr["name"],
                                        "mime": dr.get("mime"),
                                        "resource_type": dr["resource_type"],
                                        "date": datetime.fromtimestamp(
                                            dr.get("created_at", time.time()), tz=user_tz
                                        ).strftime("%Y-%m-%d %H:%M:%S"),
                                        "source": "outil/HUD",
                                        "message": dr.get("summary"),
                                        "provenance": resource_provenance(dr, active_ids),
                                    })
                    now_check = int(time.time())
                    body["metadata"]["_echo_last_event_check_at"] = now_check
                    state_manager.save_session_setting("last_event_check_at", str(now_check))

                # 1. Extraction et injection du Smart Context (RAG) prioritaire (AEC_smart_context)
                for res in results:
                    if res.get("status") == "success" and res.get("type") == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]:
                        rich_parts.append({"text": res["content"]})

                # 2. Injection de la requête utilisateur (Payload Multipart)
                if current_user_multipart_payload:
                    rich_parts.extend(current_user_multipart_payload)

                # 3. Injection des évènements système et asynchrones (AEC_evenement_systeme)
                events_text = EchoAEC.render_system_events(sys_events, error_events)
                if events_text:
                    rich_parts.append({"text": events_text})

                # 4. Injection des documents et fichiers intégraux en annexe (PUT_IN_CONTEXT)
                for res in results:
                    if res.get("status") == "success" and res.get("type") == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"]:
                        if res["sub_type"] == "text":
                            rich_parts.append({"text": res["content"]})
                        else:
                            rich_parts.append({"text": res["content"]["anchor"]})
                            rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})

                body["metadata"]["_echo_user_parts_draft"] = rich_parts
                resolved_user_msg_id = (
                    msgs[idx].get("id")
                    or meta.get("user_message_id")
                    or (meta.get("user_message") or {}).get("id")
                )
                if not resolved_user_msg_id and branch_messages:
                    for bm in reversed(branch_messages):
                        if isinstance(bm, dict) and bm.get("role") == "user" and bm.get("id"):
                            resolved_user_msg_id = bm.get("id")
                            break

                resolved_user_parent_id = (
                    msgs[idx].get("parentId")
                    or msgs[idx].get("parent_id")
                    or (meta.get("user_message") or {}).get("parentId")
                    or (meta.get("user_message") or {}).get("parent_id")
                    or meta.get("parent_id")
                    or meta.get("parentId")
                )
                if not resolved_user_parent_id and branch_messages:
                    if resolved_user_msg_id:
                        for b_i, bm in enumerate(branch_messages):
                            if isinstance(bm, dict) and str(bm.get("id") or "") == str(resolved_user_msg_id):
                                resolved_user_parent_id = bm.get("parentId") or bm.get("parent_id")
                                if not resolved_user_parent_id and b_i > 0:
                                    resolved_user_parent_id = branch_messages[b_i - 1].get("id")
                                break
                        else:
                            if isinstance(branch_messages[-1], dict):
                                last_bm_id = branch_messages[-1].get("id")
                                if last_bm_id and str(last_bm_id) != str(resolved_user_msg_id):
                                    resolved_user_parent_id = last_bm_id
                if not resolved_user_parent_id and idx > 0:
                    for prev_m in reversed(msgs[:idx]):
                        if isinstance(prev_m, dict) and prev_m.get("role") != "system" and prev_m.get("id"):
                            if str(prev_m.get("id")) != str(resolved_user_msg_id or ""):
                                resolved_user_parent_id = prev_m.get("id")
                                break

                if resolved_user_msg_id and not msgs[idx].get("id"):
                    msgs[idx]["id"] = resolved_user_msg_id
                if resolved_user_parent_id and not msgs[idx].get("parentId"):
                    msgs[idx]["parentId"] = resolved_user_parent_id

                body["metadata"]["_echo_user_msg_id"] = resolved_user_msg_id
                body["metadata"]["_echo_user_msg_parent_id"] = resolved_user_parent_id
                body["metadata"]["_echo_user_msg_updated_at"] = msgs[idx].get("updated_at") or meta.get("user_message", {}).get("updated_at") or int(time.time())
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
            orig = m.get("content")
            if orig is None:
                continue
            if isinstance(orig, list):
                new_content = []
                for p in orig:
                    if isinstance(p, dict) and p.get("type") == "text":
                        txt = str(p.get("text") or "")
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
