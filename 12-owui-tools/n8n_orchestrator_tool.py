"""
title: ECHO N8N Orchestrator
author: ECHO
version: 5.231.21.3
description: Outil agentique de cycle de vie et d'exécution N8N (Phase 2 & 3). Refactorisation institutionnelle et N8N Grapher.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.21.3: Délégation Vault chiffré à EchoStateManager, désencapsulation/échappement JSON dans _inject_secrets, masquage des secrets dans les logs et alignement ask_user.
# 5.231.20.2: Conversion en async def des 6 outils synchrones, remplacement de httpx.Client bloquant par httpx.AsyncClient, sécurisation anti-traversal des IDs et alignement de allowed_tools (code_executor) dans delegate_to_n8n_grapher.
# 5.229.0.0: Compression des docstrings OpenAPI et externalisation de la documentation vers 14-owui-libs/tool_guides/n8n_workflows.md.
# 5.226.18.1: Alignement global ECHO et harmonisation impersonnelle (PAS) du docstring de run_n8n_oneshot_workflow.
# 1.18: Nomenclature : Alignement de la délégation N8N Grapher sur MODEL_PROMETHEUS.

import sys
import asyncio
import orjson as json
import uuid
import re
import httpx
from pathlib import Path
from typing import Optional, Any

sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from echo_constants import ECHO_N8N_WORKER_URL
from echo_paths import sanitize_echo_id


class Tools:
    def __init__(self):
        self.valves = None

    def _get_template_path(self, user_id: str, template_id: str) -> Path:
        safe_uid = sanitize_echo_id(user_id)
        safe_tid = sanitize_echo_id(template_id, fallback="template")
        base = Path("/app/backend/data/users") / safe_uid / "n8n_workflow_templates"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{safe_tid}.json"

    def _get_workflow_path(self, user_id: str, chat_id: str, workflow_id: str) -> Path:
        safe_uid = sanitize_echo_id(user_id)
        safe_cid = sanitize_echo_id(chat_id)
        safe_wid = sanitize_echo_id(workflow_id, fallback="workflow")
        base = Path("/app/backend/data/users") / safe_uid / "chats" / safe_cid / "n8n_workflows"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{safe_wid}.json"

    # =========================================================================
    # A. GESTION DES TEMPLATES (GLOBALE)
    # =========================================================================
    
    async def create_n8n_template(self, template_id: str, content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Enregistre un JSON N8N comme template global réutilisable sous `template_id`."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if path.exists():
            return wrap_tool_output(f"Erreur : Le template '{template_id}' existe déjà.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        try:
            parsed = json.loads(content)
            await asyncio.to_thread(path.write_text, json.dumps(parsed).decode('utf-8'), "utf-8")
            
            state = EchoStateManager(user_id=uid)
            wf_name = parsed.get("name", template_id)
            state.save_resource(
                id=template_id, name=wf_name, resource_type="n8n_template", 
                status="-", storage_path=str(path), mime="application/json"
            )
            return wrap_tool_output(f"Succès : Template '{template_id}' créé.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors de la création : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def modify_n8n_template(self, template_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Remplace la structure JSON d'un template N8N global existant (`template_id`)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if not path.exists():
            return wrap_tool_output(f"Erreur : Le template '{template_id}' n'existe pas.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        try:
            parsed = json.loads(new_content)
            await asyncio.to_thread(path.write_text, json.dumps(parsed).decode('utf-8'), "utf-8")
            
            state = EchoStateManager(user_id=uid)
            state.update_resource_fields(template_id, name=parsed.get("name", template_id))
            return wrap_tool_output(f"Succès : Template '{template_id}' modifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors de la modification : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def delete_n8n_template(self, template_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Supprime un template N8N global (`template_id`) de l'espace utilisateur."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if path.exists():
            await asyncio.to_thread(path.unlink)
            state = EchoStateManager(user_id=uid)
            state.delete_resource(template_id)
            return wrap_tool_output(f"Succès : Template '{template_id}' supprimé.", user_id=uid, chat_id=cid, metadata=__metadata__)
        return wrap_tool_output(f"Erreur : Le template '{template_id}' n'existe pas.", user_id=uid, chat_id=cid, metadata=__metadata__)

    # =========================================================================
    # B. GESTION DES WORKFLOWS (SESSION)
    # =========================================================================

    def _check_secrets(self, content_str: str, user_id: str) -> Optional[str]:
        """Vérifie l'absence de credentials N8N natifs et la présence des macros ECHO dans le Vault."""
        errors = []
        
        # 1. Vérification architecturale (Credentials N8N natifs interdits)
        try:
            parsed = json.loads(content_str)
            nodes = parsed.get("nodes", [])
            has_native_credentials = False
            for node in nodes:
                if "credentials" in node and node["credentials"]:
                    has_native_credentials = True
                    break
                
                params = node.get("parameters", {})
                headers = params.get("headerParametersUi", {}).get("parameter", [])
                if not isinstance(headers, list): headers = []
                for header in headers:
                    name = header.get("name", "").lower()
                    value = header.get("value", "")
                    if name in ["cookie", "authorization", "api-key", "x-api-key", "token"]:
                        if "__ECHO_SECRET_" not in value:
                            errors.append(f"Header '{name}' codé en dur dans '{node.get('name')}'. Hardcoding strictement proscrit.")
            
            if has_native_credentials:
                errors.append("Credentials N8N natifs interdits (Worker stateless). Remplacer par requêtes brutes via macros __ECHO_SECRET_XXX__.")
        except Exception:
            pass # Si ce n'est pas un JSON valide, la création plantera plus loin de toute façon
            
        # 2. Vérification des macros ECHO dans le Vault
        matches = re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', content_str)
        if matches:
            state = EchoStateManager(user_id=user_id)
            for key in matches:
                if not state.has_vault_credential("n8n_workflows", key):
                    errors.append(f"Macro manquante : Le secret '{key}' est requis mais absent de l'ECHO Identity Vault de l'Utilisateur.")
                        
        if errors:
            final_err = " | ".join(errors)
            return f"[Action Requise] Violation architecturale bloquante : {final_err}\nLe Modèle DOIT utiliser `ask_user` pour collecter l'information, l'enregistrer via `manage_identity` (service='n8n_workflows'), puis injecter la macro __ECHO_SECRET_...__."
        return None

    async def prepare_n8n_workflow(self, content: str = None, from_template_id: str = None, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Instancie et valide un workflow N8N pour la session (depuis un JSON `content` ou un `from_template_id`) et retourne son `n8n_workflow_id`."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__:
            return wrap_tool_output("Erreur : chat_id introuvable dans __metadata__.", user_id=uid, chat_id=cid, metadata=__metadata__)

        workflow_content = ""
        if from_template_id:
            tpl_path = self._get_template_path(uid, from_template_id)
            if not tpl_path.exists():
                return wrap_tool_output(f"Erreur : Template '{from_template_id}' introuvable.", user_id=uid, chat_id=cid, metadata=__metadata__)
            workflow_content = await asyncio.to_thread(tpl_path.read_text, "utf-8")
        elif content:
            workflow_content = content
        else:
            return wrap_tool_output("Erreur : Fournissez 'content' ou 'from_template_id'.", user_id=uid, chat_id=cid, metadata=__metadata__)

        # Barrière Vault (Fail-fast)
        missing = self._check_secrets(workflow_content, uid)
        if missing:
            return wrap_tool_output(missing, user_id=uid, chat_id=cid, metadata=__metadata__)

        try:
            parsed = json.loads(workflow_content) # Valide le format
            wf_id = str(uuid.uuid4())
            path = self._get_workflow_path(uid, cid, wf_id)
            await asyncio.to_thread(path.write_text, json.dumps(parsed).decode('utf-8'), "utf-8")
            
            state = EchoStateManager(user_id=uid, chat_id=cid)
            wf_name = parsed.get("name", wf_id)
            state.save_resource(
                id=wf_id, name=wf_name, resource_type="n8n_workflow", 
                status="ready", storage_path=str(path), mime="application/json"
            )
            return wrap_tool_output(f"Succès : Workflow préparé localement. n8n_workflow_id={wf_id}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def modify_n8n_workflow(self, n8n_workflow_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Met à jour le JSON complet d'un workflow de session instancié (`n8n_workflow_id`)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__:
            return wrap_tool_output("Erreur : chat_id introuvable dans __metadata__.", user_id=uid, chat_id=cid, metadata=__metadata__)

        # Barrière Vault
        missing = self._check_secrets(new_content, uid)
        if missing:
            return wrap_tool_output(missing, user_id=uid, chat_id=cid, metadata=__metadata__)

        path = self._get_workflow_path(uid, cid, n8n_workflow_id)
        if not path.exists():
            return wrap_tool_output(f"Erreur : Workflow {n8n_workflow_id} introuvable.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        try:
            parsed = json.loads(new_content)
            await asyncio.to_thread(path.write_text, json.dumps(parsed).decode('utf-8'), "utf-8")
            
            state = EchoStateManager(user_id=uid, chat_id=cid)
            state.update_resource_fields(n8n_workflow_id, name=parsed.get("name", n8n_workflow_id))
            return wrap_tool_output(f"Succès : Workflow {n8n_workflow_id} modifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def delete_n8n_workflow(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Supprime un workflow de session (`n8n_workflow_id`), arrête ses processus/démons actifs et purge ses fichiers générés."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__: 
            return wrap_tool_output("Erreur auth.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: 
            return wrap_tool_output("Erreur chat.", user_id=uid, chat_id=cid, metadata=__metadata__)
        
        path = self._get_workflow_path(uid, cid, n8n_workflow_id)
        
        logs = []
        if path.exists():
            await asyncio.to_thread(path.unlink)
            state = EchoStateManager(user_id=uid, chat_id=cid)
            
            # Extraction du statut pour vérifier si c'est un démon (deployed_as_xxx)
            wf_resource = state.get_resource(n8n_workflow_id)
            status = wf_resource.get("status") if wf_resource else ""
            if not isinstance(status, str): status = ""
            
            state.delete_resource(n8n_workflow_id)
            logs.append(f"Workflow {n8n_workflow_id} supprimé de la session.")
            
            target_delete_id = n8n_workflow_id
            if status.startswith("deployed_as_"):
                target_delete_id = status.replace("deployed_as_", "")
            
            # Appel API asynchrone non-bloquant pour tuer les process N8N en cours ou purger le démon
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    resp = await client.delete(
                        f"{ECHO_N8N_WORKER_URL}/workflow/{target_delete_id}",
                        params={"user_id": uid, "chat_id": cid},
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        logs.append(f"Processus N8N tués: {data.get('killed_processes', 0)}")
            except Exception as e:
                logs.append(f"Avertissement: Impossible de contacter le worker N8N ({e})")
                
            # Nettoyage des fichiers générés par ce workflow (Pollution Chat)
            try:
                resources = state.get_resources(resource_type="binary")
                deleted_files = 0
                for r in resources:
                    if f"_{n8n_workflow_id}_" in r.get("name", ""):
                        # Suppression fichier physique (Vault global)
                        file_path = Path(r["storage_path"])
                        if file_path.exists():
                            file_path.unlink()
                        
                        # Suppression lien symbolique (Chat)
                        symlink_path = Path("/app/backend/data/users") / uid / "chats" / cid / "files" / r.get("name", "")
                        if symlink_path.exists() or symlink_path.is_symlink():
                            symlink_path.unlink(missing_ok=True)
                            
                        # Suppression BDD
                        state.delete_resource(r["id"])
                        deleted_files += 1
                if deleted_files > 0:
                    logs.append(f"{deleted_files} fichiers générés par ce workflow ont été purgés.")
            except Exception as e:
                logs.append(f"Avertissement lors de la purge des fichiers: {e}")
                
            return wrap_tool_output("\n".join(logs), user_id=uid, chat_id=cid, metadata=__metadata__)
            
        return wrap_tool_output("Erreur : Workflow introuvable.", user_id=uid, chat_id=cid, metadata=__metadata__)

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

    async def run_n8n_oneshot_workflow(self, n8n_workflow_id: str, sync: bool = False, parameters_override: Optional[dict] = None, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Exécute de façon éphémère un workflow préparé (`n8n_workflow_id`) en mode synchrone (`sync=True`) ou tâche de fond (`sync=False`)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__: 
            return wrap_tool_output("Erreur: Auth requise.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: 
            return wrap_tool_output("Erreur: chat_id requis.", user_id=uid, chat_id=cid, metadata=__metadata__)

        path = self._get_workflow_path(uid, cid, n8n_workflow_id)
        if not path.exists():
            return wrap_tool_output("Erreur: Ce workflow n'existe pas. Créez-le d'abord avec create_n8n_workflow.", user_id=uid, chat_id=cid, metadata=__metadata__)

        workflow_json = path.read_text(encoding="utf-8")
        
        # 1. Surcharge dynamique
        if parameters_override:
            try:
                data = json.loads(workflow_json)
                data = self._apply_override(data, parameters_override)
                workflow_json = json.dumps(data).decode('utf-8')
            except Exception as e:
                return wrap_tool_output(f"Erreur lors de la surcharge: {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)
                
        # 2. Injection des Secrets via Vault
        original_workflow_json = workflow_json
        try:
            workflow_json = self._inject_secrets(workflow_json, uid)
        except Exception as e:
            return wrap_tool_output(f"Erreur de déchiffrement Vault lors de l'injection des secrets: {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)
        
        # Vérification qu'aucun secret ni macro non résolue n'a été laissé
        missing = self._check_secrets(workflow_json, uid)
        if missing:
            return wrap_tool_output(missing, user_id=uid, chat_id=cid, metadata=__metadata__)
        leftover = re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', workflow_json)
        if leftover:
            return wrap_tool_output(
                f"[Action Requise] Impossible de résoudre ou déchiffrer les secrets Vault suivants : {', '.join(leftover)}.",
                user_id=uid, chat_id=cid, metadata=__metadata__
            )

        # 3. Appel du Worker API
        state = EchoStateManager(user_id=uid, chat_id=cid)
        state.update_resource_status(n8n_workflow_id, "executing")
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                payload = {
                    "user_id": uid,
                    "chat_id": cid,
                    "n8n_workflow_id": n8n_workflow_id,
                    "workflow_json": workflow_json,
                    "sync": sync
                }
                resp = await client.post(f"{ECHO_N8N_WORKER_URL}/execute", json=payload)
                
                if resp.status_code == 200:
                    res = resp.json()
                    status = res.get("status")
                    if sync:
                        state.update_resource_status(n8n_workflow_id, "ready")
                        logs = res.get("stdout", "") + "\n" + res.get("stderr", "")
                        logs = self._redact_injected_secrets(logs, uid, original_workflow_json)
                        if len(logs.encode('utf-8')) >= 8192:
                            err_msg = "[Erreur Système] Payload excessif (>8Ko) bloqué en mode Synchrone. Le Modèle DOIT réexécuter l'instance en mode asynchrone (sync=False) ou modifier l'architecture du graphe N8N pour filtrer/stocker la donnée en interne."
                            return wrap_tool_output(err_msg, user_id=uid, chat_id=cid, metadata=__metadata__)
                        
                        success_msg = f"[N8N EXECUTION : {status.upper()}]\n{logs}\n\n[INFO SYSTEM] Tâche synchrone terminée."
                        return wrap_tool_output(success_msg, user_id=uid, chat_id=cid, metadata=__metadata__)
                    else:
                        exec_id = res.get("execution_id", "inconnu")
                        return wrap_tool_output(f"[N8N EXECUTION : ASYNCHRONE DÉMARRÉE]\nL'exécution de la tâche (ID: {exec_id}) a bien été lancée en tâche de fond.\n\n[INFO SYSTEM] Le workflow N8N tourne en arrière-plan. Ses résultats (et ses logs stdout/stderr) seront écrits dans des fichiers qui seront automatiquement ingérés dès la fin du traitement. Le Modèle PEUT passer à la tâche suivante.", user_id=uid, chat_id=cid, metadata=__metadata__)
                else:
                    state.update_resource_status(n8n_workflow_id, "ready")
                    safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                    return wrap_tool_output(f"Erreur API Worker HTTP {resp.status_code}: {safe_err}", user_id=uid, chat_id=cid, metadata=__metadata__)
                    
        except Exception as e:
            state.update_resource_status(n8n_workflow_id, "ready")
            return wrap_tool_output(f"Erreur de communication avec le worker: {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)


    async def deploy_n8n_daemon(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Déploie et active de façon persistante un workflow N8N préparé et préalablement testé (`n8n_workflow_id`)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__: 
            return wrap_tool_output("Erreur auth.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: 
            return wrap_tool_output("Erreur chat.", user_id=uid, chat_id=cid, metadata=__metadata__)

        path = self._get_workflow_path(uid, cid, n8n_workflow_id)
        
        if not path.exists():
            return wrap_tool_output("Workflow introuvable.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        workflow_json = path.read_text(encoding="utf-8")
        
        # 1. Injection des Secrets via Vault (En dur dans le JSON)
        original_workflow_json = workflow_json
        try:
            workflow_json = self._inject_secrets(workflow_json, uid)
        except Exception as e:
            return wrap_tool_output(f"Erreur de déchiffrement Vault lors de l'injection des secrets: {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

        missing = self._check_secrets(workflow_json, uid)
        if missing:
            return wrap_tool_output(missing, user_id=uid, chat_id=cid, metadata=__metadata__)
        leftover = re.findall(r'__ECHO_SECRET_([A-Z0-9_]+)__', workflow_json)
        if leftover:
            return wrap_tool_output(
                f"[Action Requise] Impossible de résoudre ou déchiffrer les secrets Vault suivants : {', '.join(leftover)}.",
                user_id=uid, chat_id=cid, metadata=__metadata__
            )
        
        # 2. Appel au Worker API pour le déploiement
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                payload = {
                    "user_id": uid,
                    "chat_id": cid,
                    "n8n_workflow_id": n8n_workflow_id,
                    "workflow_json": workflow_json
                }
                resp = await client.post(f"{ECHO_N8N_WORKER_URL}/deploy", json=payload)

                if resp.status_code != 200:
                    safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                    return wrap_tool_output(f"Erreur Déploiement HTTP {resp.status_code} : {safe_err}", user_id=uid, chat_id=cid, metadata=__metadata__)

                result = resp.json()
                status = result.get("status")
                real_n8n_id = result.get("n8n_id")

                if status in ("success", "warning"):
                    # Mise à jour du State Manager avec le VRAI ID N8N
                    state = EchoStateManager(user_id=uid, chat_id=cid)
                    state.update_resource_status(n8n_workflow_id, f"deployed_as_{real_n8n_id}")

                    if status == "success":
                        return wrap_tool_output(
                            f"[DÉMON DÉPLOYÉ ET ACTIF]\n"
                            f"Workflow actif dans N8N avec l'ID natif {real_n8n_id}.",
                            user_id=uid, chat_id=cid, metadata=__metadata__
                        )
                    # warning : créé mais activation partielle
                    detail = result.get("detail", "Activation non confirmée.")
                    return wrap_tool_output(
                        f"[DÉMON CRÉÉ — ACTIVATION PARTIELLE]\n"
                        f"ID natif : {real_n8n_id}. Détail : {detail}",
                        user_id=uid, chat_id=cid, metadata=__metadata__
                    )
                else:
                    safe_err = self._redact_injected_secrets(resp.text, uid, original_workflow_json)
                    return wrap_tool_output(f"Erreur Déploiement : {safe_err}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Exception : {repr(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    # =========================================================================
    # D. AUTO-APPRENTISSAGE N8N (PHASE 3)
    # =========================================================================

    async def search_n8n_hub(self, query: str, limit: int = 3, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Recherche des modèles de workflows sur le Hub officiel N8N par mot-clé (`query`)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                url = f"https://api.n8n.io/templates/search?search={query}"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                
                workflows = data.get("workflows", [])
                if not workflows:
                    return wrap_tool_output("Aucun résultat trouvé pour cette recherche sur le Hub N8N.", user_id=uid, chat_id=cid, metadata=__metadata__)
                
                results = []
                for wf in workflows[:limit]:
                    w_id = wf.get("id")
                    name = wf.get("name", "Sans nom")
                    desc = wf.get("description", "Pas de description")
                    if not desc:
                        desc = "Pas de description"
                    results.append(f"- ID: {w_id} | Name: {name} | Desc: {desc[:100]}...")
                
                response_text = "Résultats de la recherche N8N Hub:\n" + "\n".join(results)
                return wrap_tool_output(response_text, user_id=uid, chat_id=cid, metadata=__metadata__)
                
        except httpx.HTTPStatusError as e:
            return wrap_tool_output(f"Erreur HTTP lors de la recherche : {e.response.status_code}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur de recherche Hub N8N : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def download_n8n_hub_template(self, hub_id: str, template_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Télécharge un workflow depuis le Hub N8N (`hub_id`) et l'enregistre comme template local sous `template_id`."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                url = f"https://api.n8n.io/workflows/templates/{hub_id}"
                resp = await client.get(url)
                
                if resp.status_code == 404:
                    return wrap_tool_output(f"Le template avec l'ID {hub_id} est introuvable sur le Hub N8N.", user_id=uid, chat_id=cid, metadata=__metadata__)
                    
                resp.raise_for_status()
                data = resp.json()
                
                workflow_node = data.get("workflow")
                if not workflow_node or "nodes" not in workflow_node:
                    return wrap_tool_output("Format JSON invalide: Nœud 'workflow' ou 'nodes' manquant dans la réponse du Hub.", user_id=uid, chat_id=cid, metadata=__metadata__)
                
                name = data.get("name", "Sans nom")
                
                # Délégation interne pour la création
                creation_response = await self.create_n8n_template(
                    template_id=template_id,
                    content=json.dumps(workflow_node).decode('utf-8'),
                    __user__=__user__,
                    __metadata__=__metadata__
                )
                
                # Extraction du texte du wrap_tool_output
                creation_text = creation_response.get("text", "") if isinstance(creation_response, dict) else str(creation_response)
                
                # Avertissement Architectural proactif
                warning_suffix = ""
                has_native_credentials = False
                for node in workflow_node.get("nodes", []):
                    if "credentials" in node and node["credentials"]:
                        has_native_credentials = True
                        break
                
                if has_native_credentials:
                    warning_suffix = "\n\n[Avertissement Architectural] Ce template utilise des credentials N8N natifs. Notre worker N8N étant stateless (sans Vault interne), le Modèle doit supprimer ces nœuds natifs et les remplacer par des requêtes brutes (ex: nœuds HTTP Request) en utilisant les macros __ECHO_SECRET_XXX__ pour l'authentification, avant toute instanciation ou exécution."
                
                final_text = f"Téléchargement du Hub réussi pour '{name}'.\nLog interne : {creation_text}{warning_suffix}"
                
                return wrap_tool_output(final_text, user_id=uid, chat_id=cid, metadata=__metadata__)
                
        except httpx.HTTPStatusError as e:
            return wrap_tool_output(f"Erreur HTTP lors du téléchargement : {e.response.status_code}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors du téléchargement Hub N8N : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    async def query_n8n_documentation(self, __user__: dict = None, __metadata__: dict = None) -> dict:
        """Consulte la version et les règles architecturales N8N de l'infrastructure (obligatoire avant toute création manuelle de graphe)."""
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Authentification requise.", user_id=uid, chat_id=cid, metadata=__metadata__)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{ECHO_N8N_WORKER_URL}/system-info")
                if resp.status_code == 200:
                    data = resp.json()
                    content = f"### N8N Version : {data.get('version', 'inconnue')}\n\n{data.get('documentation', '')}"
                    return wrap_tool_output(content, user_id=uid, chat_id=cid, metadata=__metadata__)
                else:
                    return wrap_tool_output(f"Erreur : Impossible de joindre la documentation N8N (HTTP {resp.status_code}).", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur technique lors de la requête de documentation : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    # =========================================================================
    # E. AGENT N8N GRAPHER (Délégation Cognitive)
    # =========================================================================

    async def delegate_to_n8n_grapher(
        self,
        instructions: str,
        __user__: dict = None,
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Délègue la conception, le mocking et le test en Sandbox d'un workflow N8N complexe (> 2 nœuds) à l'agent spécialisé N8N Grapher."""
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __metadata__.get("chat_id") if __metadata__ else None
        
        if not __user__: 
            return wrap_tool_output("Erreur : contexte OWUI manquant.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)
        
        events = EchoEvents(__event_emitter__, __event_call__)

        # Arsenal complet N8N Grapher (aligné sur les noms de fonctions-outils réels d'ECHO)
        allowed = [
            "prepare_n8n_workflow", "run_n8n_oneshot_workflow", "deploy_n8n_daemon",
            "modify_n8n_workflow", "delete_n8n_workflow", "query_n8n_documentation",
            "create_n8n_template", "modify_n8n_template", "delete_n8n_template",
            "search_n8n_hub", "download_n8n_hub_template",
            "list_identities", "list_identities_services",
            "wait_timer", "query_registry",
            "code_executor", "create_codex", "read_codex", "edit_codex", "list_codex", "search_codex"
        ]

        import sys
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls: 
            return wrap_tool_output("Erreur : Délégation impossible (agent_engine introuvable).", user_id=user_id, chat_id=chat_id, metadata=__metadata__)
        
        delegate = _delegate_cls()
        await events.status("⚙️ N8N Grapher : Conception du graphe en cours...")

        try:
            from echo_prompts import SYS_ORCHESTRATOR_N8N_GRAPHER
            result = await delegate.delegate_to_agent(
                task=instructions,
                system_prompt=SYS_ORCHESTRATOR_N8N_GRAPHER,
                target_model_key="MODEL_PROMETHEUS",
                allowed_tools=allowed,
                __user__=__user__,
                __chat_id__=chat_id,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__
            )
            await events.status("✅ N8N Grapher : Diagramme achevé et testé.", done=True)
            return wrap_tool_output(f"Rapport N8N Grapher :\n{result}", user_id=user_id, chat_id=chat_id, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors de la délégation N8N Grapher : {str(e)}", user_id=user_id, chat_id=chat_id, metadata=__metadata__)
