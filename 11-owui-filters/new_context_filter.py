"""
title: ECHO New Context Filter
author: Wilfried BARNAVON
author_url: https://github.com/Wilfried-Barnavon-Perso
version: 7.49
description: Composant système interne : ECHO New Context Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 7.48: Typage hiérarchique XML de l'AEC et suppression du formateur YAML.
# 7.47: Délégation des UserValves vers user_native_context_filter et verrouillage de la désactivation.
# 7.46: Nettoyage des mentions "V2" du registre et de l'AEC.
# 7.45: Migration des configurations globales vers les Valves (SMART_CONTEXT, OFFICE_CONVERSION).
# 7.44: Ajout du tour de conversation dans le snapshot AEC (<environnement_contexte>).
# 7.43: Nettoyage du code mort (suppression de la Valve DEBUG_MODE inutilisée).
# 7.42: Factorisation de l'AEC et de l'horodatage zoné, retrait de _dict_to_yaml.


from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import pybase64 as base64
import os
import sys
import re
import asyncio
import logging
import time
from datetime import datetime

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import resolve_upload_file_path, EchoAuth
from echo_constants import (
    GOOGLE_API_KEY_REGEX,
    DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=20, hidden=True, description="Priorité d'exécution (0 = premier).")
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
                
                enable_name = meta.get("_echo_user_name_enabled", False)
                display_name = __user__.get("name", "anonyme") if enable_name else "anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = meta.get("_echo_override_location", "")
                final_loc = u_loc if u_loc else sys_loc
                
                tour_conversation = sum(1 for m in msgs if m.get("role") == "user")
                
                # === AEC : Snapshot hiérarchisé XML ===
                env_snapshot = {
                    "systeme": {
                        "version_framework_echo": get_echo_version() or "##ECHO_VERSION##"
                    },
                    "modeles": {
                        "actuel": "##MODEL_ID##",
                        "origine": "##MODEL_ORIGIN##"
                    },
                    "utilisateur": {
                        "nom": display_name,
                        "localisation": final_loc,
                        "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                    },
                    "session": {
                        "tour_conversation": tour_conversation,
                        "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu")
                    }
                }

                body.setdefault("metadata", {})
                body["metadata"]["_echo_env_info"] = {
                    "nom_utilisateur": display_name, "localisation": final_loc,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                }
                
                from echo_utils import _dict_to_xml_aec, build_aec_system_events
                
                rich_parts = []
                xml_str = _dict_to_xml_aec(env_snapshot, indent=1)
                rich_parts.append({"text": f"<environnement_contexte>\n{xml_str}\n</environnement_contexte>\n\n"})

                # === Configuration ZoneInfo ===
                try:
                    from zoneinfo import ZoneInfo
                except ImportError:
                    import pytz as ZoneInfo
                user_tz_str = meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                try:
                    user_tz = ZoneInfo(user_tz_str)
                except Exception:
                    user_tz = ZoneInfo("UTC")

                # === AEC : Évènements système (fichiers uploadés ce tour) ===
                sys_events = []
                error_events = []
                for r in results:
                    if r.get("status") == "success":
                        evt = {"type": r.get("type"), "name": r.get("name"), "mime": r.get("mime")}
                        evt["date"] = datetime.fromtimestamp(r.get("created_at", time.time()), tz=user_tz).strftime("%Y-%m-%d %H:%M:%S")
                        if r.get("source_id"): evt["source_id"] = r["source_id"]
                        if evt not in sys_events: sys_events.append(evt)
                    elif r.get("status") == "error":
                        err_evt = {"name": r.get("name"), "error": r.get("error", "Erreur inconnue")}
                        if err_evt not in error_events: error_events.append(err_evt)

                # === AEC : Détection delta (ressources créées par outils/HUD hors-tour) ===
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
                                    "date": datetime.fromtimestamp(dr.get("created_at", time.time()), tz=user_tz).strftime("%Y-%m-%d %H:%M:%S"),
                                    "source": "outil/HUD"
                                })
                    # Sauvegarder le timestamp actuel pour le prochain delta
                    body["metadata"]["_echo_last_event_check_at"] = int(time.time())

                # Injection factorisée des évènements dans l'AEC
                events_text = build_aec_system_events(sys_events, error_events)
                if events_text:
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
