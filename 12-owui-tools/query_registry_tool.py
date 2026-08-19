"""
title: ECHO Resource Registry
author: Wilfried BARNAVON
version: 1.6
description: Composant système interne : ECHO Resource Registry.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.0: Outil de consultation du registre unifié des ressources (echo_resources).
# Permet au modèle de requêter l'état complet des fichiers, plans, documents Codex
# et pages web de la session.
# 1.1: Fix troncature ID UUID dans le tableau ([:20] supprimé).
# Fix fallback partiel dans get_resource (LIKE si ID exact échoue).
# 1.2: Suppression des Valves inutilisées (code mort).
# 1.5: Nettoyage PEP8 : F841 (Variables locales inutilisées préfixées par _ ou retirées).
# 1.6: Suppression d'assignations obsolètes.

# ECHO CONFIG NAME : ECHO Registry

import sys
from typing import Optional, Any, Literal

sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents, EchoStateManager




class Tools:
    def __init__(self):
        pass

    async def query_registry(
        self,
        # [MAINTENANCE_AI] Avertissement: Toujours mettre à jour ces Literal en cas d'évolution du Registre V2.
        resource_type: Optional[Literal["codex", "plan", "media", "binary", "weburl", "n8n_workflow", "n8n_template"]] = None,
        status: Optional[str] = None,
        search_term: str = None,
        resource_id: str = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Consultation centralisée de l'état des ressources du Registre (fichiers, URLs, agents, Codex, plans, etc.). Étape de validation obligatoire AVANT toute manipulation.
        RÈGLES DE STATUTS PAR TYPE :
        - Fichiers (media/binary/weburl/codex) : put_in_context, vectorized_sum_up, indexed, pending_ingestion
        - Plans (plan) : draft, ready, executing, success, partial, failed, abandoned
        - N8N Workflows (n8n_workflow) : 
          * ready : Le workflow est préparé et en attente.
          * executing : Le workflow est en cours d'exécution unique (One-Shot).
          * error : Le workflow ou le déploiement est en échec.
          * deployed : Le workflow tourne en autonomie comme démon persistant en arrière-plan.
        - n8n_template : NE PREND AUCUN STATUT (laisser None).
        """
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")

        if not cid:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Validation Stricte centralisée
        if status and resource_type:
            from echo_constants import RESOURCE_STATUS_MAP
            valid_statuses = RESOURCE_STATUS_MAP.get(resource_type, None)
            
            # Cas 1 : Le type est purement statique (pas de statut admis)
            if valid_statuses == []:
                return wrap_tool_output(
                    text=f"❌ [INTERRUPTION] Le type '{resource_type}' est statique et ne gère AUCUN statut. Retirez l'argument 'status'.",
                    user_id=uid, chat_id=cid, metadata=__metadata__
                )
                
            # Cas 2 : Le statut fourni n'existe pas pour ce type
            if valid_statuses and status not in valid_statuses:
                return wrap_tool_output(
                    text=f"❌ [INTERRUPTION] Mismatch type/status. Pour '{resource_type}', les statuts autorisés sont : {valid_statuses}.",
                    user_id=uid, chat_id=cid, metadata=__metadata__
                )

        session_state = EchoStateManager(user_id=uid, chat_id=cid)
        global_state = EchoStateManager(user_id=uid)

        # Mode détail : une ressource spécifique par ID
        if resource_id:
            resource = session_state.get_resource(resource_id)
            if not resource:
                resource = global_state.get_resource(resource_id)
            if not resource:
                return wrap_tool_output(text=f"❌ Ressource `{resource_id}` introuvable.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            return wrap_tool_output(text=self._format_resource_detail(resource), user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Mode liste : filtres combinés avec fusion session + global
        resources = []
        if resource_type in [None, "n8n_template"]:
            resources.extend(global_state.get_resources(resource_type="n8n_template", status=status, search=search_term))
            
        if resource_type != "n8n_template":
            if resource_type == "n8n_workflow" and status == "deployed":
                all_wfs = session_state.get_resources(resource_type=resource_type, search=search_term)
                resources.extend([r for r in all_wfs if r.get("status", "").startswith("deployed_as_")])
            else:
                resources.extend(session_state.get_resources(resource_type=resource_type, status=status, search=search_term))

        if not resources:
            filters = []
            if resource_type: filters.append(f"type={resource_type}")
            if status: filters.append(f"status={status}")
            if search_term: filters.append(f"search={search_term}")
            filter_str = ", ".join(filters) if filters else "aucun filtre"
            return wrap_tool_output(text=f"Aucune ressource trouvée ({filter_str}).", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Formatage en tableau compact
        lines = [f"**{len(resources)} ressource(s) trouvée(s)**\n"]
        lines.append("| ID | Nom | Type | Statut | MIME |")
        lines.append("|---|---|---|---|---|")
        for r in resources:
            lines.append(f"| `{r['id']}` | {r['name'][:60]} | {r['resource_type']} | {r['status']} | {r.get('mime') or '—'} |")

        return wrap_tool_output(text="\n".join(lines), user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    @staticmethod
    def _format_resource_detail(r: dict) -> str:
        """Formate les détails complets d'une ressource."""
        lines = [f"### Ressource `{r['id']}`\n"]
        lines.append(f"- **Nom** : {r['name']}")
        lines.append(f"- **Type** : {r['resource_type']}")
        lines.append(f"- **Statut** : {r['status']}")
        if r.get("mime"): lines.append(f"- **MIME** : {r['mime']}")
        if r.get("storage_path"): lines.append(f"- **Chemin** : `{r['storage_path']}`")
        if r.get("git_tracked"): lines.append("- **Git** : ✅ Versionné")
        if r.get("message_id"): lines.append(f"- **Message** : `{r['message_id']}`")

        # Métadonnées Plan
        if r["resource_type"] == "plan":
            if r.get("plan_goal"): lines.append(f"- **Objectif** : {r['plan_goal']}")
            if r.get("author_model"): lines.append(f"- **Modèle** : {r['author_model']}")

        # Métadonnées Codex
        if r["resource_type"] == "codex":
            if r.get("language"): lines.append(f"- **Langage** : {r['language']}")
            if r.get("lines") is not None: lines.append(f"- **Lignes** : {r['lines']}")
            if r.get("last_commit"): lines.append(f"- **Dernier commit** : `{r['last_commit']}`")
            if r.get("commit_msg"): lines.append(f"- **Message** : {r['commit_msg']}")

        # Résumé (Smart Context)
        if r.get("summary"):
            lines.append(f"\n**Résumé** :\n{r['summary'][:500]}")

        return "\n".join(lines)
