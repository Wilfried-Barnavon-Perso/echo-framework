"""
title: N8N Orchestrator
author: ECHO
version: 1.7
description: Outil agentique de cycle de vie et d'exécution N8N (Phase 2 & 3).
--- CHANGELOG 1.7 ---
- Fix : Gestion du status "warning" retourné par le worker v2.16 (workflow créé
  mais activation partielle). Mise à jour du State Manager dans les deux cas.
- Fix : Guard __metadata__ manquant dans deploy_n8n_daemon (crash si None).
- Fix : Timeout réduit de 300s à 60s (cohérence avec le timeout worker de 30s).
"""

import sys
import orjson as json
import uuid
import re
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoStateManager, wrap_tool_output
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

    def _wrap(self, text: str, user: dict = None, meta: dict = None) -> dict:
        uid = user.get("id", "system") if user else "system"
        cid = meta.get("chat_id") if meta else None
        return wrap_tool_output(text=text, user_id=uid, chat_id=cid, metadata=meta)

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
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Utilisateur non identifié.", __user__, __metadata__)
            
        path = self._get_template_path(__user__["id"], template_id)
        if path.exists():
            return self._wrap(f"Erreur : Le template '{template_id}' existe déjà.", __user__, __metadata__)
            
        try:
            parsed = json.loads(content)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=__user__["id"])
            wf_name = parsed.get("name", template_id)
            state.save_resource(
                id=template_id, name=wf_name, resource_type="n8n_template", 
                status="-", storage_path=str(path), mime="application/json"
            )
            return self._wrap(f"Succès : Template '{template_id}' créé.", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur lors de la création : {str(e)}", __user__, __metadata__)

    def modify_n8n_template(self, template_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle d'altérer la structure JSON d'un template N8N global existant.
        
        :param template_id: L'identifiant du template à modifier.
        :param new_content: Le nouveau JSON complet du template N8N.
        """
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Utilisateur non identifié.", __user__, __metadata__)
            
        path = self._get_template_path(__user__["id"], template_id)
        if not path.exists():
            return self._wrap(f"Erreur : Le template '{template_id}' n'existe pas.", __user__, __metadata__)
            
        try:
            parsed = json.loads(new_content)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=__user__["id"])
            state.update_resource_fields(template_id, name=parsed.get("name", template_id))
            return self._wrap(f"Succès : Template '{template_id}' modifié.", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur lors de la modification : {str(e)}", __user__, __metadata__)

    def delete_n8n_template(self, template_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de purger un template N8N global de l'espace utilisateur.
        
        :param template_id: L'identifiant du template à supprimer.
        """
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Utilisateur non identifié.", __user__, __metadata__)
            
        path = self._get_template_path(__user__["id"], template_id)
        if path.exists():
            path.unlink()
            state = EchoStateManager(user_id=__user__["id"])
            state.delete_resource(template_id)
            return self._wrap(f"Succès : Template '{template_id}' supprimé.", __user__, __metadata__)
        return self._wrap(f"Erreur : Le template '{template_id}' n'existe pas.", __user__, __metadata__)

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
            
            if has_native_credentials:
                errors.append("Architecture Invalide : Ce workflow utilise des credentials N8N natifs. Notre worker N8N étant headless et stateless, il ne possède aucun Vault interne. Le Modèle doit supprimer les nœuds natifs concernés et les réécrire en requêtes brutes (ex: nœud HTTP Request) en injectant l'authentification dans les paramètres (Headers/Query) via la macro __ECHO_SECRET_XXX__ (qui sera résolue par l'ECHO Identity Vault avant l'exécution).")
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
            final_err = "\n".join(errors)
            return f"[Action Requise] Problème d'identifiants ou d'architecture détecté :\n{final_err}\n\nLe Modèle doit interrompre la tâche, analyser le problème, et si nécessaire demander à l'Utilisateur d'ajouter les secrets via l'application « ECHO Identity Vault » (accessible via l'App Drawer)."
        return None

    def prepare_n8n_workflow(self, content: str = None, from_template_id: str = None, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle d'instancier un workflow N8N exécutable spécifiquement pour la session en cours.
        Requiert soit l'injection directe d'un JSON (content), soit l'identifiant d'un template source (from_template_id).
        Le Modèle DOIT comprendre que les noeuds "Webhook" sont STRICTEMENT INUTILISABLES dans cette infrastructure isolée.
        Invoque la barrière de sécurité Vault avant enregistrement.
        
        :param content: Optionnel. Le JSON complet du workflow.
        :param from_template_id: Optionnel. L'identifiant d'un template existant à utiliser comme source.
        """
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Utilisateur non identifié.", __user__, __metadata__)
        if not __metadata__ or "chat_id" not in __metadata__:
            return self._wrap("Erreur : chat_id introuvable dans __metadata__.", __user__, __metadata__)

        user_id = __user__["id"]
        chat_id = __metadata__["chat_id"]
        
        workflow_content = ""
        if from_template_id:
            tpl_path = self._get_template_path(user_id, from_template_id)
            if not tpl_path.exists():
                return self._wrap(f"Erreur : Template '{from_template_id}' introuvable.", __user__, __metadata__)
            workflow_content = tpl_path.read_text(encoding="utf-8")
        elif content:
            workflow_content = content
        else:
            return self._wrap("Erreur : Fournissez 'content' ou 'from_template_id'.", __user__, __metadata__)

        # Barrière Vault (Fail-fast)
        missing = self._check_secrets(workflow_content, user_id)
        if missing:
            return self._wrap(missing, __user__, __metadata__)

        try:
            parsed = json.loads(workflow_content) # Valide le format
            wf_id = str(uuid.uuid4())
            path = self._get_workflow_path(user_id, chat_id, wf_id)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=user_id, chat_id=chat_id)
            wf_name = parsed.get("name", wf_id)
            state.save_resource(
                id=wf_id, name=wf_name, resource_type="n8n_workflow", 
                status="ready", storage_path=str(path), mime="application/json"
            )
            return self._wrap(f"Succès : Workflow préparé localement. n8n_workflow_id={wf_id}", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur : {str(e)}", __user__, __metadata__)

    def modify_n8n_workflow(self, n8n_workflow_id: str, new_content: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de surcharger l'intégralité du JSON d'un workflow de session instancié.
        
        :param n8n_workflow_id: L'UUID du workflow à modifier.
        :param new_content: Le nouveau JSON complet du workflow.
        """
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Utilisateur non identifié.", __user__, __metadata__)
        if not __metadata__ or "chat_id" not in __metadata__:
            return self._wrap("Erreur : chat_id introuvable dans __metadata__.", __user__, __metadata__)

        user_id = __user__["id"]
        
        # Barrière Vault
        missing = self._check_secrets(new_content, user_id)
        if missing:
            return self._wrap(missing, __user__, __metadata__)

        path = self._get_workflow_path(user_id, __metadata__["chat_id"], n8n_workflow_id)
        if not path.exists():
            return self._wrap(f"Erreur : Workflow {n8n_workflow_id} introuvable.", __user__, __metadata__)
            
        try:
            parsed = json.loads(new_content)
            path.write_text(json.dumps(parsed).decode('utf-8'), encoding="utf-8")
            
            state = EchoStateManager(user_id=user_id, chat_id=__metadata__["chat_id"])
            state.update_resource_fields(n8n_workflow_id, name=parsed.get("name", n8n_workflow_id))
            return self._wrap(f"Succès : Workflow {n8n_workflow_id} modifié.", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur : {str(e)}", __user__, __metadata__)

    def delete_n8n_workflow(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de supprimer l'instance d'un workflow de la session, d'arrêter son exécution et de purger les fichiers qu'il a générés.
        
        :param n8n_workflow_id: L'UUID du workflow à supprimer.
        """
        if not __user__ or "id" not in __user__: return self._wrap("Erreur auth.", __user__, __metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: return self._wrap("Erreur chat.", __user__, __metadata__)
        
        user_id = __user__["id"]
        chat_id = __metadata__["chat_id"]
        path = self._get_workflow_path(user_id, chat_id, n8n_workflow_id)
        
        logs = []
        if path.exists():
            path.unlink()
            state = EchoStateManager(user_id=user_id, chat_id=chat_id)
            
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
                        symlink_path = Path("/app/backend/data/users") / user_id / "chats" / chat_id / "files" / r.get("name", "")
                        if symlink_path.exists() or symlink_path.is_symlink():
                            symlink_path.unlink(missing_ok=True)
                            
                        # Suppression BDD
                        state.delete_resource(r["id"])
                        deleted_files += 1
                if deleted_files > 0:
                    logs.append(f"{deleted_files} fichiers générés par ce workflow ont été purgés.")
            except Exception as e:
                logs.append(f"Avertissement lors de la purge des fichiers: {e}")
                
            return self._wrap("\n".join(logs), __user__, __metadata__)
            
        return self._wrap("Erreur : Workflow introuvable.", __user__, __metadata__)

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
        Permet au modèle de déclencher l'exécution ÉPHÉMÈRE CLI d'un workflow préparé via prepare_n8n_workflow.
        Procède à l'injection algorithmique des secrets via Vault. Les noeuds Triggers/Cron sont forcés et exécutés une seule fois.
        
        :param n8n_workflow_id: L'UUID retourné par prepare_n8n_workflow.
        :param sync: Si True, l'agent attend (bloquant) la fin du processus et reçoit stdout/stderr. Si False (par défaut), lance en tâche de fond (Fire&Forget). Utilisez sync=False pour le scraping, les tâches longues, ou quand il y a de la récursivité.
        :param parameters_override: Optionnel. Dictionnaire pour surcharger dynamiquement des paramètres.
        """
        if not __user__ or "id" not in __user__: return self._wrap("Erreur: Auth requise.", __user__, __metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: return self._wrap("Erreur: chat_id requis.", __user__, __metadata__)

        user_id = __user__["id"]
        chat_id = __metadata__["chat_id"]
        
        path = self._get_workflow_path(user_id, chat_id, n8n_workflow_id)
        if not path.exists():
            return self._wrap("Erreur: Ce workflow n'existe pas. Créez-le d'abord avec create_n8n_workflow.", __user__, __metadata__)

        workflow_json = path.read_text(encoding="utf-8")
        
        # 1. Surcharge dynamique
        if parameters_override:
            try:
                data = json.loads(workflow_json)
                data = self._apply_override(data, parameters_override)
                workflow_json = json.dumps(data).decode('utf-8')
            except Exception as e:
                return self._wrap(f"Erreur lors de la surcharge: {str(e)}", __user__, __metadata__)
                
        # 2. Injection des Secrets via Vault
        workflow_json = self._inject_secrets(workflow_json, user_id)
        
        # Vérification qu'aucun secret n'a été laissé
        missing = self._check_secrets(workflow_json, user_id)
        if missing:
            return self._wrap(missing, __user__, __metadata__)

        # 3. Appel du Worker API
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.update_resource_status(n8n_workflow_id, "executing")
        
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
                payload = {
                    "user_id": user_id,
                    "chat_id": chat_id,
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
                        return self._wrap(f"[N8N EXECUTION : {status.upper()}]\n{logs}\n\n[INFO SYSTEM] Si ce workflow génère des fichiers, ils apparaîtront dans le Download Broker.", __user__, __metadata__)
                    else:
                        exec_id = res.get("execution_id", "inconnu")
                        return self._wrap(f"[N8N EXECUTION : ASYNCHRONE DÉMARRÉE]\nL'exécution de la tâche (ID: {exec_id}) a bien été lancée en tâche de fond.\n\n[INFO SYSTEM] Le workflow N8N tourne en arrière-plan. Ses résultats (et ses logs stdout/stderr) seront écrits dans des fichiers qui seront automatiquement ingérés dès la fin du traitement. Vous pouvez passer à la tâche suivante !", __user__, __metadata__)
                else:
                    return self._wrap(f"Erreur API Worker HTTP {resp.status_code}: {resp.text}", __user__, __metadata__)
                    
        except Exception as e:
            return self._wrap(f"Erreur de communication avec le worker: {str(e)}", __user__, __metadata__)
        finally:
            state.update_resource_status(n8n_workflow_id, "ready")

    async def deploy_n8n_daemon(self, n8n_workflow_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de DÉPLOYER un workflow de façon PERSISTANTE dans N8N.
        Indispensable pour les workflows qui doivent tourner en autonomie (Triggers : Schedule, Cron, Webhook, Email).
        
        :param n8n_workflow_id: L'UUID du workflow préparé via prepare_n8n_workflow.
        """
        if not __user__ or "id" not in __user__: return self._wrap("Erreur auth.", __user__, __metadata__)
        if not __metadata__ or "chat_id" not in __metadata__: return self._wrap("Erreur chat.", __user__, __metadata__)

        user_id = __user__["id"]
        chat_id = __metadata__["chat_id"]
        path = self._get_workflow_path(user_id, chat_id, n8n_workflow_id)
        
        if not path.exists():
            return self._wrap("Workflow introuvable.", __user__, __metadata__)
            
        workflow_json = path.read_text(encoding="utf-8")
        
        # 1. Injection des Secrets via Vault (En dur dans le JSON)
        workflow_json = self._inject_secrets(workflow_json, user_id)
        
        # 2. Appel au Worker API pour le déploiement
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
                payload = {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "n8n_workflow_id": n8n_workflow_id,
                    "workflow_json": workflow_json
                }
                resp = await client.post(f"{ECHO_N8N_WORKER_URL}/deploy", json=payload)

                if resp.status_code != 200:
                    return self._wrap(f"Erreur Déploiement HTTP {resp.status_code} : {resp.text}", __user__, __metadata__)

                result = resp.json()
                status = result.get("status")
                real_n8n_id = result.get("n8n_id")

                if status in ("success", "warning"):
                    # Mise à jour du State Manager avec le VRAI ID N8N
                    state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                    state.update_resource_status(n8n_workflow_id, f"deployed_as_{real_n8n_id}")

                    if status == "success":
                        return self._wrap(
                            f"[DÉMON DÉPLOYÉ ET ACTIF]\n"
                            f"Workflow actif dans N8N avec l'ID natif {real_n8n_id}.",
                            __user__, __metadata__
                        )
                    # warning : créé mais activation partielle
                    detail = result.get("detail", "Activation non confirmée.")
                    return self._wrap(
                        f"[DÉMON CRÉÉ — ACTIVATION PARTIELLE]\n"
                        f"ID natif : {real_n8n_id}. Détail : {detail}",
                        __user__, __metadata__
                    )
                else:
                    return self._wrap(f"Erreur Déploiement : {resp.text}", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Exception : {repr(e)}", __user__, __metadata__)

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
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                url = f"https://api.n8n.io/templates/search?search={query}"
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                
                workflows = data.get("workflows", [])
                if not workflows:
                    return self._wrap("Aucun résultat trouvé pour cette recherche sur le Hub N8N.", __user__, __metadata__)
                
                results = []
                for wf in workflows[:limit]:
                    w_id = wf.get("id")
                    name = wf.get("name", "Sans nom")
                    desc = wf.get("description", "Pas de description")
                    if not desc:
                        desc = "Pas de description"
                    results.append(f"- ID: {w_id} | Name: {name} | Desc: {desc[:100]}...")
                
                response_text = "Résultats de la recherche N8N Hub:\n" + "\n".join(results)
                return self._wrap(response_text, __user__, __metadata__)
                
        except httpx.HTTPStatusError as e:
            return self._wrap(f"Erreur HTTP lors de la recherche : {e.response.status_code}", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur de recherche Hub N8N : {str(e)}", __user__, __metadata__)

    async def download_n8n_hub_template(self, hub_id: str, template_id: str, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au modèle de télécharger un workflow depuis le Hub officiel N8N et de le forger en tant que template local.
        L'identifiant du template enregistré pourra ensuite être utilisé pour instancier des workflows de session.
        
        :param hub_id: L'identifiant public du workflow sur le Hub N8N (obtenu via search_n8n_hub).
        :param template_id: L'identifiant métier unique sous lequel enregistrer le template localement.
        """
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
                url = f"https://api.n8n.io/workflows/templates/{hub_id}"
                resp = await client.get(url)
                
                if resp.status_code == 404:
                    return self._wrap(f"Le template avec l'ID {hub_id} est introuvable sur le Hub N8N.", __user__, __metadata__)
                    
                resp.raise_for_status()
                data = resp.json()
                
                workflow_node = data.get("workflow")
                if not workflow_node or "nodes" not in workflow_node:
                    return self._wrap("Format JSON invalide: Nœud 'workflow' ou 'nodes' manquant dans la réponse du Hub.", __user__, __metadata__)
                
                name = data.get("name", "Sans nom")
                
                # Délégation interne pour la création
                creation_response = self.create_n8n_template(
                    template_id=template_id,
                    content=json.dumps(workflow_node).decode('utf-8'),
                    __user__=__user__,
                    __metadata__=__metadata__
                )
                
                # Extraction du texte du _wrap pour concaténer
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
                
                return self._wrap(final_text, __user__, __metadata__)
                
        except httpx.HTTPStatusError as e:
            return self._wrap(f"Erreur HTTP lors du téléchargement : {e.response.status_code}", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur lors du téléchargement Hub N8N : {str(e)}", __user__, __metadata__)

    async def query_n8n_documentation(self, __user__: dict = None, __metadata__: dict = None) -> dict:
        """
        Permet au Modèle de consulter la documentation architecturale et la version exacte de l'instance N8N cible.
        À utiliser impérativement AVANT la création ou la modification d'un workflow N8N pour garantir le respect des contraintes d'exécution (Triggers requis, règles de Mocking, isolation Sandbox).
        
        :param __user__: (Système) Dictionnaire contenant l'identité de l'Utilisateur.
        :param __metadata__: (Système) Dictionnaire contenant les métadonnées de la session (chat_id).
        """
        if not __user__ or "id" not in __user__:
            return self._wrap("Erreur : Authentification requise.", __user__, __metadata__)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{ECHO_N8N_WORKER_URL}/system-info")
                if resp.status_code == 200:
                    data = resp.json()
                    content = f"### N8N Version : {data.get('version', 'inconnue')}\n\n{data.get('documentation', '')}"
                    return self._wrap(content, __user__, __metadata__)
                else:
                    return self._wrap(f"Erreur : Impossible de joindre la documentation N8N (HTTP {resp.status_code}).", __user__, __metadata__)
        except Exception as e:
            return self._wrap(f"Erreur technique lors de la requête de documentation : {str(e)}", __user__, __metadata__)
