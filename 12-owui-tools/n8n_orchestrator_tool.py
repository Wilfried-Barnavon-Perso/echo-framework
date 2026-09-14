"""
title: ECHO N8N Orchestrator
author: ECHO
version: 1.15
description: Outil agentique de cycle de vie et d'exécution N8N (Phase 2 & 3). Refactorisation institutionnelle et N8N Grapher.
--- CHANGELOG 1.15 ---
- Refinement : MAJ docstrings pour incitation au N8N Grapher et correction casse.
--- CHANGELOG 1.14 ---
- Refactorisation : Éradication de l'anti-pattern _wrap, utilisation stricte de wrap_tool_output.
- Limite : Baisse de la taille de sortie autorisée à 8Ko (8192 octets) en mode Sandbox Synchrone (OOM Protection).
- Feature : Intégration de l'agent délégué (N8N Grapher) avec Règle 0 (Documentation requise).
"""

import sys
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

class Tools:
    def __init__(self):
        self.valves = None

    def _get_template_path(self, user_id: str, template_id: str) -> Path:
        base = Path("/app/backend/data/users") / user_id / "n8n_workflow_templates"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{template_id}.json"

    def _get_workflow_path(self, user_id: str, chat_id: str, workflow_id: str) -> Path:
        base = Path("/app/backend/data/users") / user_id / "chats" / chat_id / "n8n_workflows"
        base.mkdir(parents=True, exist_ok=True)
        return base / f"{workflow_id}.json"

    # =========================================================================
    # A. GESTION DES TEMPLATES (GLOBALE)
    # =========================================================================
    
    def create_n8n_template(self, template_id: str, content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de forger un template global réutilisable N8N (non exécutable).
        Enregistre le JSON brut sous un identifiant métier unique (ex: 'veille_techno').
        
        :param template_id: L'identifiant du template (ex: 'veille_techno').
        :param content: Le JSON complet du template N8N.
        """
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if path.exists():
            return wrap_tool_output(f"Erreur : Le template '{template_id}' existe déjà.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        try:
            parsed = json.loads(content)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=uid)
            wf_name = parsed.get("name", template_id)
            state.save_resource(
                id=template_id, name=wf_name, resource_type="n8n_template", 
                status="-", storage_path=str(path), mime="application/json"
            )
            return wrap_tool_output(f"Succès : Template '{template_id}' créé.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors de la création : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    def modify_n8n_template(self, template_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle d'altérer la structure JSON d'un template N8N global existant.
        
        :param template_id: L'identifiant du template à modifier.
        :param new_content: Le nouveau JSON complet du template N8N.
        """
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if not path.exists():
            return wrap_tool_output(f"Erreur : Le template '{template_id}' n'existe pas.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        try:
            parsed = json.loads(new_content)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=uid)
            state.update_resource_fields(template_id, name=parsed.get("name", template_id))
            return wrap_tool_output(f"Succès : Template '{template_id}' modifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur lors de la modification : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    def delete_n8n_template(self, template_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de purger un template N8N global de l'espace utilisateur.
        
        :param template_id: L'identifiant du template à supprimer.
        """
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__:
            return wrap_tool_output("Erreur : Utilisateur non identifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
            
        path = self._get_template_path(uid, template_id)
        if path.exists():
            path.unlink()
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
            with state._get_connection() as conn:
                for key in matches:
                    cursor = conn.execute(
                        "SELECT 1 FROM identity_vault WHERE user_id = ? AND service = 'n8n_workflows' AND account_id = ?",
                        (user_id, key)
                    )
                    if not cursor.fetchone():
                        errors.append(f"Macro manquante : Le secret '{key}' est requis mais absent de l'ECHO Identity Vault de l'Utilisateur.")
                        
        if errors:
            final_err = " | ".join(errors)
            return f"[Action Requise] Violation architecturale bloquante : {final_err}\nLe Modèle DOIT utiliser `ask_user_input` pour collecter l'information, l'enregistrer via `manage_identity` (service='n8n_workflows'), puis injecter la macro __ECHO_SECRET_...__."
        return None

    def prepare_n8n_workflow(self, content: str = None, from_template_id: str = None, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au Modèle d'instancier un workflow N8N exécutable spécifiquement pour la session en cours.
        Requiert soit l'injection directe d'un JSON (content), soit l'identifiant d'un template source (from_template_id).
        Le Modèle DOIT comprendre que les noeuds "Webhook" sont STRICTEMENT INUTILISABLES dans cette infrastructure isolée.
        Invoque la barrière de sécurité Vault avant enregistrement.
        
        :param content: Optionnel. Le JSON complet du workflow.
        :param from_template_id: Optionnel. L'identifiant d'un template existant à utiliser comme source.
        """
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
            workflow_content = tpl_path.read_text(encoding="utf-8")
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
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=uid, chat_id=cid)
            wf_name = parsed.get("name", wf_id)
            state.save_resource(
                id=wf_id, name=wf_name, resource_type="n8n_workflow", 
                status="ready", storage_path=str(path), mime="application/json"
            )
            return wrap_tool_output(f"Succès : Workflow préparé localement. n8n_workflow_id={wf_id}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    def modify_n8n_workflow(self, n8n_workflow_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de surcharger l'intégralité du JSON d'un workflow de session instancié.
        
        :param n8n_workflow_id: L'UUID du workflow à modifier.
        :param new_content: Le nouveau JSON complet du workflow.
        """
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
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=uid, chat_id=cid)
            state.update_resource_fields(n8n_workflow_id, name=parsed.get("name", n8n_workflow_id))
            return wrap_tool_output(f"Succès : Workflow {n8n_workflow_id} modifié.", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Erreur : {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    def delete_n8n_workflow(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de supprimer l'instance d'un workflow de la session, d'arrêter son exécution et de purger les fichiers qu'il a générés.
        
        :param n8n_workflow_id: L'UUID du workflow à supprimer.
        """
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None

        if not __user__ or "id" not in __user__: 
            return wrap_tool_output("Erreur auth.", user_id=uid, chat_id=cid, metadata=__metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: 
            return wrap_tool_output("Erreur chat.", user_id=uid, chat_id=cid, metadata=__metadata__)
        
        path = self._get_workflow_path(uid, cid, n8n_workflow_id)
        
        logs = []
        if path.exists():
            path.unlink()
            state = EchoStateManager(user_id=uid, chat_id=cid)
            
            # Extraction du statut pour vérifier si c'est un démon (deployed_as_xxx)
            wf_resource = state.get_resource(n8n_workflow_id)
            status = wf_resource.get("status", "") if wf_resource else ""
            
            state.delete_resource(n8n_workflow_id)
            logs.append(f"Workflow {n8n_workflow_id} supprimé de la session.")
            
            target_delete_id = n8n_workflow_id
            if status.startswith("deployed_as_"):
                target_delete_id = status.replace("deployed_as_", "")
            
            # Appel API synchrone pour tuer les process N8N en cours ou purger le démon
            try:
                with httpx.Client(timeout=15.0) as client:
                    resp = client.delete(f"{ECHO_N8N_WORKER_URL}/workflow/{target_delete_id}")
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

    def _inject_secrets(self, content_str: str, user_id: str) -> str:
        state = EchoStateManager(user_id=user_id)
        with state._get_connection() as conn:
            def replace_secret(match):
                key = match.group(1)
                cursor = conn.execute("SELECT credentials FROM identity_vault WHERE user_id = ? AND service = 'n8n_workflows' AND account_id = ?", (user_id, key))
                row = cursor.fetchone()
                return row[0] if row else match.group(0)
                
            return re.sub(r'__ECHO_SECRET_([A-Z0-9_]+)__', replace_secret, content_str)

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
        """
        Permet au Modèle de déclencher l'exécution ÉPHÉMÈRE CLI d'un workflow préparé via prepare_n8n_workflow.
        Procède à l'injection algorithmique des secrets via Vault. Les noeuds Triggers/Cron sont forcés et exécutés une seule fois.
        
        :param n8n_workflow_id: L'UUID retourné par prepare_n8n_workflow.
        :param sync: Si True, l'agent attend (bloquant) la fin du processus et reçoit stdout/stderr. Si False (par défaut), lance en tâche de fond (Fire&Forget). Utilisez sync=False pour le scraping, les tâches longues, ou quand il y a de la récursivité.
        :param parameters_override: Optionnel. Dictionnaire pour surcharger dynamiquement des paramètres.
        """
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
        workflow_json = self._inject_secrets(workflow_json, uid)
        
        # Vérification qu'aucun secret n'a été laissé
        missing = self._check_secrets(workflow_json, uid)
        if missing:
            return wrap_tool_output(missing, user_id=uid, chat_id=cid, metadata=__metadata__)

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
                        logs = res.get("stdout", "") + "\n" + res.get("stderr", "")
                        if len(logs.encode('utf-8')) >= 8192:
                            err_msg = "[Erreur Système] Payload excessif (>8Ko) bloqué en mode Synchrone. Le Modèle DOIT réexécuter l'instance en mode asynchrone (sync=False) ou modifier l'architecture du graphe N8N pour filtrer/stocker la donnée en interne."
                            return wrap_tool_output(err_msg, user_id=uid, chat_id=cid, metadata=__metadata__)
                        
                        success_msg = f"[N8N EXECUTION : {status.upper()}]\n{logs}\n\n[INFO SYSTEM] Tâche synchrone terminée."
                        return wrap_tool_output(success_msg, user_id=uid, chat_id=cid, metadata=__metadata__)
                    else:
                        exec_id = res.get("execution_id", "inconnu")
                        return wrap_tool_output(f"[N8N EXECUTION : ASYNCHRONE DÉMARRÉE]\nL'exécution de la tâche (ID: {exec_id}) a bien été lancée en tâche de fond.\n\n[INFO SYSTEM] Le workflow N8N tourne en arrière-plan. Ses résultats (et ses logs stdout/stderr) seront écrits dans des fichiers qui seront automatiquement ingérés dès la fin du traitement. Vous pouvez passer à la tâche suivante !", user_id=uid, chat_id=cid, metadata=__metadata__)
                else:
                    return wrap_tool_output(f"Erreur API Worker HTTP {resp.status_code}: {resp.text}", user_id=uid, chat_id=cid, metadata=__metadata__)
                    
        except Exception as e:
            return wrap_tool_output(f"Erreur de communication avec le worker: {str(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)
        finally:
            state.update_resource_status(n8n_workflow_id, "ready")

    async def deploy_n8n_daemon(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de DÉPLOYER un workflow de façon PERSISTANTE dans N8N.
        Indispensable pour les workflows qui doivent tourner en autonomie (Triggers : Schedule, Cron, Webhook, Email).
        
        :param n8n_workflow_id: L'UUID du workflow préparé via prepare_n8n_workflow.
        """
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
        workflow_json = self._inject_secrets(workflow_json, uid)
        
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
                    return wrap_tool_output(f"Erreur Déploiement HTTP {resp.status_code} : {resp.text}", user_id=uid, chat_id=cid, metadata=__metadata__)

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
                    return wrap_tool_output(f"Erreur Déploiement : {resp.text}", user_id=uid, chat_id=cid, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(f"Exception : {repr(e)}", user_id=uid, chat_id=cid, metadata=__metadata__)

    # =========================================================================
    # D. AUTO-APPRENTISSAGE N8N (PHASE 3)
    # =========================================================================

    async def search_n8n_hub(self, query: str, limit: int = 3, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de rechercher des workflows directement sur le Hub officiel N8N.
        Interroge l'API publique en temps réel et retourne les identifiants, noms et descriptions des meilleurs résultats.
        
        :param query: Le mot-clé ou le cas d'usage à rechercher (ex: 'scraper', 'google drive').
        :param limit: Optionnel. Le nombre maximum de résultats à retourner (défaut: 3).
        """
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
        """
        Permet au modèle de télécharger un workflow depuis le Hub officiel N8N et de le forger en tant que template local.
        L'identifiant du template enregistré pourra ensuite être utilisé pour instancier des workflows de session.
        
        :param hub_id: L'identifiant public du workflow sur le Hub N8N (obtenu via search_n8n_hub).
        :param template_id: L'identifiant métier unique sous lequel enregistrer le template localement.
        """
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
                creation_response = self.create_n8n_template(
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
        """
        Permet au Modèle de consulter la documentation architecturale et la version exacte de l'instance N8N cible.
        À utiliser impérativement AVANT la création ou la modification d'un workflow N8N pour garantir le respect des contraintes d'exécution (Triggers requis, règles de Mocking, isolation Sandbox).
        
        :param __user__: (Système) Dictionnaire contenant l'identité de l'Utilisateur.
        :param __metadata__: (Système) Dictionnaire contenant les métadonnées de la session (chat_id).
        """
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
        """
        Permet de déléguer la construction complexe d'un diagramme N8N, sa configuration (Mocking, gestion 
        des payloads volumineux) et ses tests en Sandbox à l'agent spécialisé (N8N Grapher).
        
        RÈGLE CRITIQUE : Le Modèle DOIT absolument privilégier cet outil et déléguer la tâche dès que le 
        workflow demandé dépasse 2 nœuds, nécessite du mocking ou un formatage complexe.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = __metadata__.get("chat_id") if __metadata__ else None
        
        if not __user__: 
            return wrap_tool_output("Erreur : contexte OWUI manquant.", user_id=user_id, chat_id=chat_id, metadata=__metadata__)
        
        events = EchoEvents(__event_emitter__, __event_call__)

        # Arsenal complet N8N Grapher
        allowed = [
            "prepare_n8n_workflow", "run_n8n_oneshot_workflow", "deploy_n8n_daemon",
            "modify_n8n_workflow", "delete_n8n_workflow", "query_n8n_documentation",
            "create_n8n_template", "modify_n8n_template", "delete_n8n_template",
            "search_n8n_hub", "download_n8n_hub_template",
            "ask_user_input", "manage_identity", "list_identities", "list_available_services", 
            "async_wait_timer", "query_registry",
            "code_executor", "create_codex_file", "read_codex_file", "update_codex_file", "search_codex"
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
                target_model_key="MODEL_PRO",
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
