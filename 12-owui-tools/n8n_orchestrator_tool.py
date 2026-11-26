# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO N8N Orchestrator
author: ECHO
version: 6.24.2.1
description: Outil agentique de cycle de vie et d'exécution N8N (Phase 2 & 3). Refactorisation institutionnelle et N8N Grapher.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.24.2.1: Priorité execution_failed sur payload_too_large en run_oneshot(sync=True) avec troncature Head+Tail UTF-8 (_truncate_sync_error_logs).
# 6.24.1.1: Ajout de manage_n8n_workflow(action="stop", execution_id=...) non-destructif et normalisation intégrale des retours d'erreur via _n8n_error et wrap_tool_output.
# 6.24.0.1: Consolidation polymorphe en manage_n8n_template, manage_n8n_workflow et n8n_hub (5 outils) avec gardes PAS et masquage Vault.
# 6.23.0.1: Ajout de sub_sid, cohort_id, allowed_tools et propagation hiérarchique (parent_sid, group_id, depth+1) dans delegate_to_n8n_grapher.
# 6.7.7.1: Passage de delegate_to_n8n_grapher sur ECHO_SUBAGENT_CHECKPOINT_TURNS (8) et ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC (120).

import sys
import asyncio
import orjson as json
import uuid
import re
import httpx
from pathlib import Path
from typing import Optional, Any, List, Literal

sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager
from echo_core import echo_tool, wrap_tool_output
from echo_events import EchoEvents
from echo_constants import (
    ECHO_N8N_WORKER_URL,
    ECHO_SUBAGENT_CHECKPOINT_TURNS,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
    ECHO_SUBAGENT_CONTEXT,
    ECHO_SUBAGENT_ENGINES_REGISTRY,
)
from echo_paths import get_echo_global_path, get_echo_session_path, sanitize_echo_id
from echo_subagent_hub import (
    EchoSubagentController,
    get_subagent_system_prompt,
    resolve_updated_allowed_tools,
    validate_or_create_sub_sid,
)


class Tools:
    def __init__(self):
        self.valves = None

    def _get_template_path(self, user_id: str, template_id: str) -> Path:
        safe_tid = sanitize_echo_id(template_id, fallback="template")
        base = Path(get_echo_global_path(user_id, "n8n_workflow_templates"))
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{safe_tid}.json"

    def _get_workflow_path(self, user_id: str, chat_id: str, workflow_id: str) -> Path:
        safe_wid = sanitize_echo_id(workflow_id, fallback="workflow")
        base = Path(get_echo_session_path(user_id, chat_id, "n8n_workflows"))
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{safe_wid}.json"

    @staticmethod
    def _n8n_error(msg: str, error_code: str = "invalid_arguments") -> dict:
        return wrap_tool_output(
            text=msg,
            status={"status": "error", "error_code": error_code, "message": msg},
        )

    # =========================================================================
    # A. GESTION DES TEMPLATES (GLOBALE)
    # =========================================================================

    @echo_tool
    async def manage_n8n_template(
        self,
        action: Literal["create", "update", "delete"],
        template_id: str,
        content: Optional[str] = None,
        __user__: dict = None,
        __metadata__: dict = None,
    ) -> dict | str:
        """Gère le cycle de vie des templates N8N globaux (`create`, `update`, `delete`) sous `template_id`."""
        if not __user__ or "id" not in __user__:
            return self._n8n_error("Erreur : Utilisateur non identifié.", error_code="missing_context")
        uid = __user__.get("id", "system")

        if action not in ("create", "update", "delete"):
            return self._n8n_error(f"Erreur : Action template invalide '{action}'. Valeurs autorisées : create, update, delete.")
        if not isinstance(template_id, str) or not template_id.strip():
            return self._n8n_error("Erreur : Le paramètre `template_id` non vide est requis.")

        path = self._get_template_path(uid, template_id)

        if action == "delete":
            if content is not None:
                return self._n8n_error("Erreur : Paramètre hors-contrat `content` interdit pour action='delete'.")
            if path.exists():
                await asyncio.to_thread(path.unlink)
                state = EchoStateManager(user_id=uid)
                state.delete_resource(template_id)
                return f"Succès : Template '{template_id}' supprimé."
            return self._n8n_error(f"Erreur : Le template '{template_id}' n'existe pas.", error_code="not_found")

        if not isinstance(content, str) or not content.strip():
            return self._n8n_error(f"Erreur : Le paramètre `content` (JSON) est requis pour action='{action}'.")

        if action == "create":
            if path.exists():
                return self._n8n_error(f"Erreur : Le template '{template_id}' existe déjà.", error_code="already_exists")
            try:
                parsed = json.loads(content)
                await asyncio.to_thread(path.write_text, json.dumps(parsed).decode("utf-8"), "utf-8")

                state = EchoStateManager(user_id=uid)
                wf_name = parsed.get("name", template_id)
                state.save_resource(
                    id=template_id,
                    name=wf_name,
                    resource_type="n8n_template",
                    status="-",
                    storage_path=str(path),
                    mime="application/json",
                )
                return f"Succès : Template '{template_id}' créé."
            except Exception as e:
                return self._n8n_error(f"Erreur lors de la création : {str(e)}", error_code="execution_error")

        # action == "update"
        if not path.exists():
            return self._n8n_error(f"Erreur : Le template '{template_id}' n'existe pas.", error_code="not_found")
        try:
            parsed = json.loads(content)
            await asyncio.to_thread(path.write_text, json.dumps(parsed).decode("utf-8"), "utf-8")

            state = EchoStateManager(user_id=uid)
            state.update_resource_fields(template_id, name=parsed.get("name", template_id))
            return f"Succès : Template '{template_id}' modifié."
        except Exception as e:
            return self._n8n_error(f"Erreur lors de la modification : {str(e)}", error_code="execution_error")

    # =========================================================================
    # B. GESTION DES WORKFLOWS (SESSION)
    # =========================================================================

    @staticmethod
    def _truncate_sync_error_logs(logs: str, max_bytes: int = 7500) -> str:
        """Tronque les logs d'erreur synchrones en UTF-8 safe (Head + Tail) sous la limite 8Ko."""
        raw = logs.encode("utf-8")
        if len(raw) <= max_bytes:
            return logs
        half = max(512, (max_bytes // 2) - 40)
        head = raw[:half].decode("utf-8", errors="ignore")
        tail = raw[-half:].decode("utf-8", errors="ignore")
        return f"{head}\n\n... [sortie d'erreur tronquée pour limite 8Ko] ...\n\n{tail}"

    def _check_secrets(self, content_str: str, user_id: str) -> Optional[str]:
        """Vérifie l'absence de credentials N8N natifs, la validité des chemins d'écriture disque et la présence des macros ECHO dans le Vault."""
        secret_errors = []
        path_errors = []

        # 1. Vérification architecturale (Credentials N8N natifs interdits & chemins WriteBinaryFile/ReadWriteFile)
        try:
            parsed = json.loads(content_str)
            nodes = parsed.get("nodes", [])
            has_native_credentials = False
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if "credentials" in node and node["credentials"]:
                    has_native_credentials = True

                params = node.get("parameters", {}) or {}
                headers = params.get("headerParametersUi", {}).get("parameter", [])
                if not isinstance(headers, list):
                    headers = []
                for header in headers:
                    name = header.get("name", "").lower()
                    value = header.get("value", "")
                    if name in ["cookie", "authorization", "api-key", "x-api-key", "token"]:
                        if "__ECHO_SECRET_" not in value:
                            secret_errors.append(f"Header '{name}' codé en dur dans '{node.get('name')}'. Hardcoding strictement proscrit.")

                node_type = str(node.get("type", ""))
                is_write_binary = node_type.endswith(".writeBinaryFile")
                is_read_write_write = (
                    node_type.endswith(".readWriteFile")
                    and params.get("operation") == "write"
                )
                if is_write_binary or is_read_write_write:
                    raw_file_path = str(params.get("fileName") or params.get("filePath") or "").strip()
                    normalized_path = re.sub(r'^[=\s\{\'"`]+', '', raw_file_path)
                    if not normalized_path.startswith("__ECHO_DOWNLOAD_DIR__/"):
                        path_errors.append(
                            f"Noeud '{node.get('name')}' ({node_type}) : Le chemin d'écriture '{raw_file_path}' est invalide. "
                            "Tout fichier écrit sur disque DOIT impérativement commencer par '__ECHO_DOWNLOAD_DIR__/' "
                            "(ex: '__ECHO_DOWNLOAD_DIR__/rapport.json')."
                        )

            if has_native_credentials:
                secret_errors.append("Credentials N8N natifs interdits (Worker stateless). Remplacer par requêtes brutes via macros __ECHO_SECRET_XXX__.")
        except Exception:
            pass # Si ce n'est pas un JSON valide, la création plantera plus loin de toute façon

        # 2. Vérification des macros ECHO dans le Vault
        matches = re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', content_str)
        if matches:
            state = EchoStateManager(user_id=user_id)
            for key in matches:
                if not state.has_vault_credential("n8n_workflows", key):
                    secret_errors.append(f"Macro manquante : Le secret '{key}' est requis mais absent de l'ECHO Identity Vault de l'Utilisateur.")

        all_errors = secret_errors + path_errors
        if all_errors:
            final_err = " | ".join(all_errors)
            guidance = []
            if secret_errors:
                guidance.append(
                    "Le Modèle DOIT utiliser `ask_user_input` pour collecter l'information, "
                    "l'enregistrer via `manage_identity` (service='n8n_workflows'), puis injecter la macro __ECHO_SECRET_...__."
                )
            if path_errors:
                guidance.append(
                    "Le Modèle DOIT corriger les paramètres `fileName` / `filePath` des noeuds d'écriture "
                    "pour cibler `__ECHO_DOWNLOAD_DIR__/nom_du_fichier.ext`."
                )
            return f"[Action Requise] Violation architecturale bloquante : {final_err}\n" + "\n".join(guidance)
        return None

    def _resolve_workflow_secrets(self, workflow_json: str, uid: str) -> tuple[Optional[str], Optional[str]]:
        """Valide l'architecture et les macros sur le JSON brut, puis injecte les secrets Vault."""
        missing = self._check_secrets(workflow_json, uid)
        if missing:
            return None, missing
        try:
            injected = self._inject_secrets(workflow_json, uid)
        except Exception as e:
            return None, f"Erreur de déchiffrement Vault lors de l'injection des secrets: {str(e)}"
        leftover = re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', injected)
        if leftover:
            return None, f"[Action Requise] Impossible de résoudre ou déchiffrer les secrets Vault suivants : {', '.join(leftover)}."
        return injected, None

    @echo_tool
    async def manage_n8n_workflow(
        self,
        action: Literal["prepare", "update", "stop", "delete", "run_oneshot", "deploy_daemon"],
        n8n_workflow_id: Optional[str] = None,
        content: Optional[str] = None,
        from_template_id: Optional[str] = None,
        sync: bool = False,
        parameters_override: Optional[dict] = None,
        execution_id: Optional[str] = None,
        __user__: dict = None,
        __metadata__: dict = None,
    ) -> dict | str:
        """Gère le cycle de vie et l'exécution des workflows N8N de session (`prepare`, `update`, `stop`, `delete`, `run_oneshot`, `deploy_daemon`)."""
        if not __user__ or "id" not in __user__:
            return self._n8n_error("Erreur : Utilisateur non identifié.", error_code="missing_context")
        if not __metadata__ or "chat_id" not in __metadata__:
            return self._n8n_error("Erreur : chat_id introuvable dans __metadata__.", error_code="missing_context")

        uid = __user__.get("id", "system")
        cid = __metadata__.get("chat_id")

        if action not in ("prepare", "update", "stop", "delete", "run_oneshot", "deploy_daemon"):
            return self._n8n_error(
                f"Erreur : Action workflow invalide '{action}'. Valeurs autorisées : prepare, update, stop, delete, run_oneshot, deploy_daemon."
            )

        if action == "prepare":
            if n8n_workflow_id is not None or parameters_override is not None or sync or execution_id is not None:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='prepare' : `n8n_workflow_id`, `parameters_override`, `sync=True` et `execution_id` sont interdits."
                )
            if content is not None and from_template_id is not None:
                return self._n8n_error(
                    "Erreur : `content` et `from_template_id` sont mutuellement exclusifs pour action='prepare'."
                )
            if from_template_id:
                tpl_path = self._get_template_path(uid, from_template_id)
                if not tpl_path.exists():
                    return self._n8n_error(f"Erreur : Template '{from_template_id}' introuvable.", error_code="not_found")
                workflow_content = await asyncio.to_thread(tpl_path.read_text, "utf-8")
            elif isinstance(content, str) and content.strip():
                workflow_content = content
            else:
                return self._n8n_error("Erreur : `content` ou `from_template_id` est requis pour action='prepare'.")

            missing = self._check_secrets(workflow_content, uid)
            if missing:
                return self._n8n_error(missing, error_code="security_violation")
            try:
                parsed = json.loads(workflow_content)
                wf_id = str(uuid.uuid4())
                path = self._get_workflow_path(uid, cid, wf_id)
                await asyncio.to_thread(path.write_text, json.dumps(parsed).decode("utf-8"), "utf-8")

                state = EchoStateManager(user_id=uid, chat_id=cid)
                wf_name = parsed.get("name", wf_id)
                state.save_resource(
                    id=wf_id,
                    name=wf_name,
                    resource_type="n8n_workflow",
                    status="ready",
                    storage_path=str(path),
                    mime="application/json",
                )
                return f"Succès : Workflow préparé localement. n8n_workflow_id={wf_id}"
            except Exception as e:
                return self._n8n_error(f"Erreur : {str(e)}", error_code="execution_error")

        if action == "stop":
            if content is not None or from_template_id is not None or parameters_override is not None or sync:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='stop' : seuls `n8n_workflow_id` et `execution_id` sont autorisés."
                )
            wf_id_clean = n8n_workflow_id.strip() if isinstance(n8n_workflow_id, str) and n8n_workflow_id.strip() else ""
            exec_id_clean = sanitize_echo_id(execution_id.strip(), fallback="") if isinstance(execution_id, str) and execution_id.strip() else ""
            if not wf_id_clean and not exec_id_clean:
                return self._n8n_error("Erreur : `n8n_workflow_id` ou `execution_id` non vide est requis pour action='stop'.")

            state = EchoStateManager(user_id=uid, chat_id=cid)
            daemon_n8n_id = None
            if wf_id_clean:
                path = self._get_workflow_path(uid, cid, wf_id_clean)
                if not path.exists():
                    return self._n8n_error(f"Erreur : Workflow {wf_id_clean} introuvable.", error_code="not_found")
                wf_res = state.get_resource(wf_id_clean)
                if not exec_id_clean and isinstance(wf_res, dict) and str(wf_res.get("status") or "").startswith("deployed_as_"):
                    daemon_n8n_id = str(wf_res["status"]).replace("deployed_as_", "").strip() or None

            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    resp = await client.post(
                        f"{ECHO_N8N_WORKER_URL}/stop",
                        json={
                            "user_id": uid,
                            "chat_id": cid,
                            "n8n_workflow_id": wf_id_clean or None,
                            "execution_id": exec_id_clean or None,
                            "daemon_n8n_id": daemon_n8n_id,
                        },
                    )
                if resp.status_code != 200:
                    return self._n8n_error(
                        f"Erreur API Worker HTTP {resp.status_code} : {resp.text}",
                        error_code="execution_error",
                    )
                data = resp.json()
            except Exception as e:
                return self._n8n_error(f"Erreur de communication avec le worker lors de l'arrêt : {str(e)}", error_code="execution_error")

            killed_procs = int(data.get("killed_processes") or 0)
            killed_eids = data.get("killed_execution_ids") or []
            daemon_stopped = bool(data.get("daemon_stopped"))
            resolved_wid = wf_id_clean or str(data.get("resolved_workflow_id") or "")
            remaining_active = int(data.get("remaining_active") or 0)

            if resolved_wid:
                wf_res2 = state.get_resource(resolved_wid)
                if wf_res2 and (
                    daemon_stopped
                    or (remaining_active == 0 and not str(wf_res2.get("status") or "").startswith("deployed_as_"))
                ):
                    state.update_resource_status(resolved_wid, "ready")

            if killed_procs == 0 and not daemon_stopped:
                return wrap_tool_output(
                    text=f"⚠️ Aucune exécution active ni démon à interrompre ({exec_id_clean or wf_id_clean}).",
                    status={
                        "status": "warning",
                        "n8n_workflow_id": resolved_wid or None,
                        "killed_processes": 0,
                        "daemon_stopped": False,
                    },
                )
            return wrap_tool_output(
                text=f"[N8N STOP] Workflow {resolved_wid or 'résolu'} mis à l'arrêt (processus oneshot tués : {killed_procs}, démon désactivé : {daemon_stopped}). Le workflow JSON et ses fichiers sont conservés (statut='ready').",
                status={
                    "status": "success",
                    "n8n_workflow_id": resolved_wid,
                    "killed_processes": killed_procs,
                    "killed_execution_ids": killed_eids,
                    "daemon_stopped": daemon_stopped,
                },
            )

        # Toutes les autres actions exigent n8n_workflow_id non vide
        if not isinstance(n8n_workflow_id, str) or not n8n_workflow_id.strip():
            return self._n8n_error(f"Erreur : Le paramètre `n8n_workflow_id` non vide est requis pour action='{action}'.")
        wf_id_clean = n8n_workflow_id.strip()

        if action == "update":
            if from_template_id is not None or parameters_override is not None or sync or execution_id is not None:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='update' : `from_template_id`, `parameters_override`, `sync=True` et `execution_id` sont interdits."
                )
            if not isinstance(content, str) or not content.strip():
                return self._n8n_error("Erreur : Le paramètre `content` (JSON) est requis pour action='update'.")

            missing = self._check_secrets(content, uid)
            if missing:
                return self._n8n_error(missing, error_code="security_violation")
            path = self._get_workflow_path(uid, cid, wf_id_clean)
            if not path.exists():
                return self._n8n_error(f"Erreur : Workflow {wf_id_clean} introuvable.", error_code="not_found")
            try:
                parsed = json.loads(content)
                await asyncio.to_thread(path.write_text, json.dumps(parsed).decode("utf-8"), "utf-8")

                state = EchoStateManager(user_id=uid, chat_id=cid)
                state.update_resource_fields(wf_id_clean, name=parsed.get("name", wf_id_clean))
                return f"Succès : Workflow {wf_id_clean} modifié."
            except Exception as e:
                return self._n8n_error(f"Erreur : {str(e)}", error_code="execution_error")

        if action == "delete":
            if content is not None or from_template_id is not None or parameters_override is not None or sync or execution_id is not None:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='delete' : seul `n8n_workflow_id` est autorisé."
                )
            path = self._get_workflow_path(uid, cid, wf_id_clean)
            logs = []
            if path.exists():
                await asyncio.to_thread(path.unlink)
                state = EchoStateManager(user_id=uid, chat_id=cid)

                wf_resource = state.get_resource(wf_id_clean)
                status = wf_resource.get("status") if wf_resource else ""
                if not isinstance(status, str):
                    status = ""

                state.delete_resource(wf_id_clean)
                logs.append(f"Workflow {wf_id_clean} supprimé de la session.")

                target_delete_id = wf_id_clean
                if status.startswith("deployed_as_"):
                    target_delete_id = status.replace("deployed_as_", "")

                try:
                    safe_wf_id = sanitize_echo_id(wf_id_clean, "n8n_workflow_id")
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        resp = await client.delete(
                            f"{ECHO_N8N_WORKER_URL}/workflow/{target_delete_id}",
                            params={"user_id": uid, "chat_id": cid, "echo_workflow_id": safe_wf_id},
                        )
                        if resp.status_code == 200:
                            data = resp.json()
                            logs.append(f"Processus N8N tués: {data.get('killed_processes', 0)}")
                except Exception as e:
                    logs.append(f"Avertissement: Impossible de contacter le worker N8N ({e})")

                try:
                    resources = state.get_resources(resource_type="binary")
                    deleted_files = 0
                    files_dir = Path(get_echo_session_path(uid, cid, "files"))
                    for r in resources:
                        if f"_{wf_id_clean}_" in r.get("name", ""):
                            file_path = Path(r["storage_path"])
                            if file_path.exists():
                                file_path.unlink()

                            symlink_path = files_dir / r.get("name", "")
                            if symlink_path.exists() or symlink_path.is_symlink():
                                symlink_path.unlink(missing_ok=True)

                            state.delete_resource(r["id"])
                            deleted_files += 1
                    if deleted_files > 0:
                        logs.append(f"{deleted_files} fichiers générés par ce workflow ont été purgés.")
                except Exception as e:
                    logs.append(f"Avertissement lors de la purge des fichiers: {e}")

                return "\n".join(logs)
            return self._n8n_error("Erreur : Workflow introuvable.", error_code="not_found")

        if action == "run_oneshot":
            if content is not None or from_template_id is not None or execution_id is not None:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='run_oneshot' : `content`, `from_template_id` et `execution_id` sont interdits."
                )
            path = self._get_workflow_path(uid, cid, wf_id_clean)
            if not path.exists():
                return self._n8n_error(
                    "Erreur : Ce workflow n'existe pas. Le Modèle DOIT le créer au préalable via manage_n8n_workflow(action='prepare', ...).",
                    error_code="not_found",
                )

            workflow_json = path.read_text(encoding="utf-8")

            if parameters_override:
                try:
                    data = json.loads(workflow_json)
                    data = self._apply_override(data, parameters_override)
                    workflow_json = json.dumps(data).decode("utf-8")
                except Exception as e:
                    return self._n8n_error(f"Erreur lors de la surcharge: {str(e)}", error_code="execution_error")

            original_workflow_json = workflow_json
            workflow_json, sec_err = self._resolve_workflow_secrets(workflow_json, uid)
            if sec_err:
                return self._n8n_error(sec_err, error_code="security_violation")

            state = EchoStateManager(user_id=uid, chat_id=cid)
            state.update_resource_status(wf_id_clean, "executing")

            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                    payload = {
                        "user_id": uid,
                        "chat_id": cid,
                        "n8n_workflow_id": wf_id_clean,
                        "workflow_json": workflow_json,
                        "sync": sync,
                    }
                    resp = await client.post(f"{ECHO_N8N_WORKER_URL}/execute", json=payload)

                    if resp.status_code == 200:
                        res = resp.json()
                        status = res.get("status")
                        if sync:
                            logs = res.get("stdout", "") + "\n" + res.get("stderr", "")
                            logs = self._redact_injected_secrets(logs, uid, original_workflow_json)
                            if status != "success":
                                state.update_resource_status(wf_id_clean, "error")
                                bounded_logs = self._truncate_sync_error_logs(logs)
                                return self._n8n_error(
                                    f"[N8N EXECUTION : {str(status).upper()}]\n{bounded_logs}\n\n[INFO SYSTEM] Tâche synchrone terminée en erreur.",
                                    error_code="execution_failed",
                                )
                            if len(logs.encode("utf-8")) >= 8192:
                                state.update_resource_status(wf_id_clean, "ready")
                                return self._n8n_error(
                                    "[Erreur Système] Payload excessif (>8Ko) bloqué en mode Synchrone. Le Modèle DOIT réexécuter l'instance en mode asynchrone (sync=False) ou modifier l'architecture du graphe N8N pour filtrer/stocker la donnée en interne.",
                                    error_code="payload_too_large",
                                )
                            state.update_resource_status(wf_id_clean, "ready")
                            return wrap_tool_output(
                                text=f"[N8N EXECUTION : SUCCESS]\n{logs}\n\n[INFO SYSTEM] Tâche synchrone terminée.",
                                status={"status": "success"},
                            )
                        exec_id = res.get("execution_id", "inconnu")
                        return f"[N8N EXECUTION : ASYNCHRONE DÉMARRÉE]\nL'exécution de la tâche (ID: {exec_id}) a bien été lancée en tâche de fond.\n\n[INFO SYSTEM] Le workflow N8N tourne en arrière-plan. Ses résultats (et ses logs stdout/stderr) seront écrits dans des fichiers qui seront automatiquement ingérés dès la fin du traitement. Le Modèle PEUT passer à la tâche suivante."
                    state.update_resource_status(wf_id_clean, "ready")
                    safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                    return self._n8n_error(f"Erreur API Worker HTTP {resp.status_code}: {safe_err}", error_code="execution_error")
            except Exception as e:
                state.update_resource_status(wf_id_clean, "ready")
                return self._n8n_error(f"Erreur de communication avec le worker: {str(e)}", error_code="execution_error")

        # action == "deploy_daemon"
        if content is not None or from_template_id is not None or parameters_override is not None or sync or execution_id is not None:
            return self._n8n_error(
                "Erreur : Paramètre(s) hors-contrat pour action='deploy_daemon' : seul `n8n_workflow_id` est autorisé."
            )
        path = self._get_workflow_path(uid, cid, wf_id_clean)
        if not path.exists():
            return self._n8n_error("Workflow introuvable.", error_code="not_found")

        workflow_json = path.read_text(encoding="utf-8")
        original_workflow_json = workflow_json
        workflow_json, sec_err = self._resolve_workflow_secrets(workflow_json, uid)
        if sec_err:
            return self._n8n_error(sec_err, error_code="security_violation")

        try:
            state = EchoStateManager(user_id=uid, chat_id=cid)
            existing_res = state.get_resource(wf_id_clean)
            existing_status = existing_res.get("status", "") if isinstance(existing_res, dict) else ""
            old_n8n_id = (
                existing_status.replace("deployed_as_", "").strip()
                if isinstance(existing_status, str) and existing_status.startswith("deployed_as_")
                else ""
            )

            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                if old_n8n_id:
                    try:
                        await client.delete(f"{ECHO_N8N_WORKER_URL}/workflow/{old_n8n_id}")
                    except Exception:
                        pass

                payload = {
                    "user_id": uid,
                    "chat_id": cid,
                    "n8n_workflow_id": wf_id_clean,
                    "workflow_json": workflow_json,
                }
                resp = await client.post(f"{ECHO_N8N_WORKER_URL}/deploy", json=payload)

                if resp.status_code != 200:
                    safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                    return self._n8n_error(f"Erreur Déploiement HTTP {resp.status_code} : {safe_err}", error_code="deploy_failed")

                result = resp.json()
                status = result.get("status")
                real_n8n_id = result.get("n8n_id")

                if status in ("success", "warning"):
                    state.update_resource_status(wf_id_clean, f"deployed_as_{real_n8n_id}")

                    if status == "success":
                        return (
                            f"[DÉMON DÉPLOYÉ ET ACTIF]\n"
                            f"Workflow actif dans N8N avec l'ID natif {real_n8n_id}."
                        )
                    detail = result.get("detail", "Activation non confirmée.")
                    return wrap_tool_output(
                        text=f"[DÉMON CRÉÉ — ACTIVATION PARTIELLE]\nID natif : {real_n8n_id}. Détail : {detail}",
                        status={"status": "warning", "n8n_id": real_n8n_id},
                    )
                safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                return self._n8n_error(f"Erreur Déploiement : {safe_err}", error_code="deploy_failed")
        except Exception as e:
            return self._n8n_error(f"Exception : {repr(e)}", error_code="deploy_failed")

    # =========================================================================
    # C. EXECUTION (BOUCLE FERMEE)
    # =========================================================================

    @staticmethod
    def _extract_raw_n8n_secret(val: str) -> str:
        """Extrait la valeur brute d'un secret N8N (qu'il soit stocké en chaîne brute ou encapsulé en JSON par le HUD/Tool)."""
        if not val:
            return ""
        try:
            parsed = json.loads(val)
            if isinstance(parsed, dict):
                for k in ("value", "credentials", "secret", "token"):
                    if k in parsed and parsed[k] is not None:
                        return str(parsed[k])
        except Exception:
            pass
        return val

    def _inject_secrets(self, content_str: str, user_id: str) -> str:
        state = EchoStateManager(user_id=user_id)

        def replace_secret(match):
            key = match.group(1)
            val = state.get_vault_credential("n8n_workflows", key)
            secret_str = self._extract_raw_n8n_secret(val)
            if not secret_str:
                return match.group(0)
            return json.dumps(secret_str).decode("utf-8")[1:-1]

        return re.sub(r'__ECHO_SECRET_([A-Z0-9_]+)__', replace_secret, content_str)

    def _redact_injected_secrets(self, text: str, user_id: str, original_content: str) -> str:
        """Masque toute occurrence littérale d'un secret injecté dans les logs/erreurs retournés au Modèle."""
        if not text or not original_content:
            return text
        keys = set(re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', original_content))
        if not keys:
            return text
        state = EchoStateManager(user_id=user_id)
        redacted = text
        for key in keys:
            val = state.get_vault_credential("n8n_workflows", key)
            secret_str = self._extract_raw_n8n_secret(val)
            if secret_str and len(secret_str) >= 4:
                placeholder = f"[MASQUÉ:__ECHO_SECRET_{key}__]"
                escaped_str = json.dumps(secret_str).decode("utf-8")[1:-1]
                redacted = redacted.replace(secret_str, placeholder).replace(escaped_str, placeholder)
        return redacted

    def _apply_override(self, data: dict, overrides: dict) -> dict:
        """Surcharge intelligente des paramètres des noeuds N8N : overrides = {'Nom du Noeud': {'param': 'valeur'}}"""
        if isinstance(data, dict) and "nodes" in data and isinstance(data["nodes"], list):
            for node in data["nodes"]:
                node_name = node.get("name")
                if node_name in overrides and isinstance(overrides[node_name], dict):
                    if "parameters" not in node:
                        node["parameters"] = {}
                    for pk, pv in overrides[node_name].items():
                        node["parameters"][pk] = pv
        return data

    # =========================================================================
    # D. AUTO-APPRENTISSAGE N8N (PHASE 3)
    # =========================================================================

    @echo_tool
    async def n8n_hub(
        self,
        action: Literal["search", "download"] = "search",
        query: Optional[str] = None,
        limit: int = 3,
        hub_id: Optional[str] = None,
        template_id: Optional[str] = None,
        __user__: dict = None,
        __metadata__: dict = None,
    ) -> dict | str:
        """Recherche (`search`) ou télécharge (`download`) des modèles de workflows depuis le Hub communautaire N8N."""
        if action not in ("search", "download"):
            return self._n8n_error(f"Erreur : Action Hub invalide '{action}'. Valeurs autorisées : search, download.")

        if action == "search":
            if hub_id is not None or template_id is not None:
                return self._n8n_error(
                    "Erreur : Paramètre(s) hors-contrat pour action='search' : `hub_id` et `template_id` sont interdits."
                )
            if not isinstance(query, str) or not query.strip():
                return self._n8n_error("Erreur : Le paramètre `query` non vide est requis pour action='search'.")
            try:
                async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                    url = f"https://api.n8n.io/templates/search?search={query.strip()}"
                    resp = await client.get(url)
                    resp.raise_for_status()
                    data = resp.json()

                    workflows = data.get("workflows", [])
                    if not workflows:
                        return "Aucun résultat trouvé pour cette recherche sur le Hub N8N."

                    results = []
                    for wf in workflows[:limit]:
                        w_id = wf.get("id")
                        name = wf.get("name", "Sans nom")
                        desc = wf.get("description") or "Pas de description"
                        results.append(f"- ID: {w_id} | Name: {name} | Desc: {desc[:100]}...")

                    return "Résultats de la recherche N8N Hub:\n" + "\n".join(results)
            except httpx.HTTPStatusError as e:
                return self._n8n_error(f"Erreur HTTP lors de la recherche : {e.response.status_code}", error_code="hub_error")
            except Exception as e:
                return self._n8n_error(f"Erreur de recherche Hub N8N : {str(e)}", error_code="hub_error")

        # action == "download"
        if query is not None or limit != 3:
            return self._n8n_error(
                "Erreur : Paramètre(s) hors-contrat pour action='download' : `query` et `limit` sont interdits."
            )
        if not isinstance(hub_id, str) or not hub_id.strip() or not isinstance(template_id, str) or not template_id.strip():
            return self._n8n_error("Erreur : Les paramètres `hub_id` et `template_id` non vides sont requis pour action='download'.")

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                url = f"https://api.n8n.io/workflows/templates/{hub_id.strip()}"
                resp = await client.get(url)

                if resp.status_code == 404:
                    return self._n8n_error(f"Le template avec l'ID {hub_id} est introuvable sur le Hub N8N.", error_code="not_found")

                resp.raise_for_status()
                data = resp.json()

                workflow_node = data.get("workflow")
                if not workflow_node or "nodes" not in workflow_node:
                    return self._n8n_error(
                        "Format JSON invalide: Nœud 'workflow' ou 'nodes' manquant dans la réponse du Hub.",
                        error_code="invalid_hub_payload",
                    )

                name = data.get("name", "Sans nom")

                creation_response = await self.manage_n8n_template(
                    action="create",
                    template_id=template_id.strip(),
                    content=json.dumps(workflow_node).decode("utf-8"),
                    __user__=__user__,
                    __metadata__=__metadata__,
                )

                if isinstance(creation_response, dict) and (creation_response.get("status") or {}).get("status") == "error":
                    return creation_response

                creation_text = creation_response.get("text", "") if isinstance(creation_response, dict) else str(creation_response)

                warning_suffix = ""
                has_native_credentials = any(
                    isinstance(node, dict) and "credentials" in node and node["credentials"]
                    for node in workflow_node.get("nodes", [])
                )
                if has_native_credentials:
                    warning_suffix = "\n\n[Avertissement Architectural] Ce template utilise des credentials N8N natifs. Notre worker N8N étant stateless (sans Vault interne), le Modèle doit supprimer ces nœuds natifs et les remplacer par des requêtes brutes (ex: nœuds HTTP Request) en utilisant les macros __ECHO_SECRET_XXX__ pour l'authentification, avant toute instanciation ou exécution."

                return f"Téléchargement du Hub réussi pour '{name}'.\nLog interne : {creation_text}{warning_suffix}"
        except httpx.HTTPStatusError as e:
            return self._n8n_error(f"Erreur HTTP lors du téléchargement : {e.response.status_code}", error_code="hub_error")
        except Exception as e:
            return self._n8n_error(f"Erreur lors du téléchargement Hub N8N : {str(e)}", error_code="hub_error")

    @echo_tool
    async def query_n8n_documentation(self, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Consulte la version et les règles architecturales N8N de l'infrastructure (obligatoire avant toute création manuelle de graphe)."""
        if not __user__ or "id" not in __user__:
            return self._n8n_error("Erreur : Authentification requise.", error_code="missing_context")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{ECHO_N8N_WORKER_URL}/system-info")
                if resp.status_code == 200:
                    data = resp.json()
                    return f"### N8N Version : {data.get('version', 'inconnue')}\n\n{data.get('documentation', '')}"
                else:
                    return self._n8n_error(
                        f"Erreur : Impossible de joindre la documentation N8N (HTTP {resp.status_code}).",
                        error_code="execution_error",
                    )
        except Exception as e:
            return self._n8n_error(
                f"Erreur technique lors de la requête de documentation : {str(e)}",
                error_code="execution_error",
            )

    # =========================================================================
    # E. AGENT N8N GRAPHER (Délégation Cognitive)
    # =========================================================================

    @echo_tool
    async def delegate_to_n8n_grapher(
        self,
        instructions: str,
        sub_sid: Optional[str] = None,
        cohort_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        __user__: dict = None,
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Délègue la conception, le mocking et le test en Sandbox d'un workflow N8N complexe (> 2 nœuds) à l'agent spécialisé N8N Grapher."""
        chat_id = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__:
            return self._n8n_error("Erreur : contexte OWUI manquant.", error_code="missing_context")

        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        sid = validate_or_create_sub_sid(sub_sid=sub_sid, prefix="thread_n8n")

        parent_ctx = ECHO_SUBAGENT_CONTEXT.get() or {}
        if parent_ctx.get("is_subagent"):
            parent_sid = parent_ctx.get("sub_sid")
            depth = int(parent_ctx.get("depth") or 1) + 1
            group_id = parent_ctx.get("group_id")
        else:
            parent_sid = None
            depth = 1
            group_id = None

        if cohort_id and str(cohort_id).strip():
            clean_cid = sanitize_echo_id(str(cohort_id).strip(), fallback="cohort").replace("_", "-")
            norm_gid = clean_cid if clean_cid.startswith("phx_") else f"phx_{clean_cid}"
            if (not parent_ctx.get("is_subagent")) or (not group_id):
                group_id = norm_gid

        # Arsenal complet N8N Grapher (SSOT dans ECHO_SUBAGENT_ENGINES_REGISTRY)
        default_allowed = list(ECHO_SUBAGENT_ENGINES_REGISTRY["n8n_grapher"]["default_allowed_tools"])
        eff_allowed = (
            resolve_updated_allowed_tools(default_allowed, allowed_tools)
            if allowed_tools is not None
            else default_allowed
        )

        await events.status("⚙️ N8N Grapher : Conception du graphe en cours...")

        try:
            from echo_prompts import SYS_ORCHESTRATOR_N8N_GRAPHER
            state = EchoStateManager(user_id=user_id, chat_id=chat_id or "")
            eff_system_prompt = (
                None
                if (sub_sid and get_subagent_system_prompt(state, sid))
                else SYS_ORCHESTRATOR_N8N_GRAPHER
            )
            result = await EchoSubagentController.run_or_resume(
                user_id=user_id,
                chat_id=chat_id or "",
                task=instructions,
                system_prompt=eff_system_prompt,
                sub_sid=sid,
                target_model_key="MODEL_PROMETHEUS",
                allowed_tools=eff_allowed,
                slice_max_turns=ECHO_SUBAGENT_CHECKPOINT_TURNS,
                slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
                autonomous_slice=False,
                engine="n8n_grapher",
                group_id=group_id,
                role_name="Architecte N8N",
                parent_sid=parent_sid,
                depth=depth,
                __user__=__user__,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__,
            )
            await events.status("✅ N8N Grapher : Diagramme achevé et testé.", done=True)
            if isinstance(result, dict) and "text" in result:
                result["text"] = f"Rapport N8N Grapher :\n{result['text']}"
                return result
            return f"Rapport N8N Grapher :\n{result}"
        except Exception as e:
            return self._n8n_error(f"Erreur lors de la délégation N8N Grapher : {str(e)}", error_code="execution_error")
