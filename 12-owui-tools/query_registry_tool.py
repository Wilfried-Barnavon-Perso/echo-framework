"""
title: ECHO Resource Registry
author: Wilfried BARNAVON
version: 5.235.5.1
description: Composant système interne : ECHO Resource Registry.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.235.5.1: Calcul et affichage de la provenance des ressources de session (branche active vs hors branche).
# 5.233.0.1: Décoration @echo_tool sur query_registry et remplacement intégral des 7 appels wrap_tool_output par des retours directs.
# 5.232.5.1: Prise en charge de resource_type="generated", ajout du filtre workspace ("main", "plans", "generated") et de la colonne Localisation (storage_path).
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.

# ECHO CONFIG NAME : ECHO Registry

import sys
from typing import Optional, Any, Literal

sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, EchoCompactionEngine
from echo_session_gc import resource_provenance
from echo_state_manager import EchoStateManager

PROVENANCE_LABELS = {
    "active": "branche active",
    "hors_branche": "hors branche active (production conservée)",
    "inconnue": "inconnue",
}




class Tools:
    def __init__(self):
        pass

    @echo_tool
    async def query_registry(
        self,
        # [MAINTENANCE_AI] Avertissement: Toujours mettre à jour ces Literal en cas d'évolution du Registre V2.
        resource_type: Optional[Literal["codex", "plan", "generated", "media", "binary", "weburl", "n8n_workflow", "n8n_template"]] = None,
        status: Optional[str] = None,
        search_term: str = None,
        resource_id: str = None,
        workspace: Optional[Literal["main", "plans", "generated"]] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """Consulte l'état des ressources du Registre Unifié (fichiers, Codex, plans, médias générés, workflows N8N, URLs). Étape obligatoire avant manipulation."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")

        if not cid:
            return "❌ Contexte manquant (chat_id)."

        # Validation Stricte centralisée
        if status and resource_type:
            from echo_constants import RESOURCE_STATUS_MAP
            valid_statuses = RESOURCE_STATUS_MAP.get(resource_type, None)
            
            # Cas 1 : Le type est purement statique (pas de statut admis)
            if valid_statuses == []:
                return f"❌ [INTERRUPTION] Le type '{resource_type}' est statique et ne gère AUCUN statut. Retirez l'argument 'status'."
                
            # Cas 2 : Le statut fourni n'existe pas pour ce type
            if valid_statuses and status not in valid_statuses:
                return f"❌ [INTERRUPTION] Mismatch type/status. Pour '{resource_type}', les statuts autorisés sont : {valid_statuses}."

        session_state = EchoStateManager(user_id=uid, chat_id=cid)
        global_state = EchoStateManager(user_id=uid)
        active_ids = set(await EchoCompactionEngine.load_active_branch_ids(cid))

        # Mode détail : une ressource spécifique par ID
        if resource_id:
            resource = session_state.get_resource(resource_id)
            if resource:
                if resource.get("resource_type") != "n8n_template":
                    resource["provenance"] = resource_provenance(resource, active_ids)
            else:
                resource = global_state.get_resource(resource_id)
            if not resource:
                return f"❌ Ressource `{resource_id}` introuvable."
            return self._format_resource_detail(resource)

        # Mode liste : filtres combinés avec fusion session + global
        resources = []
        if resource_type in [None, "n8n_template"] and not workspace:
            resources.extend(global_state.get_resources(resource_type="n8n_template", status=status, search=search_term))
            
        if resource_type != "n8n_template":
            if resource_type == "n8n_workflow" and status == "deployed":
                all_wfs = session_state.get_resources(resource_type=resource_type, search=search_term, workspace=workspace)
                sess_res = [r for r in all_wfs if r.get("status", "").startswith("deployed_as_")]
            else:
                sess_res = session_state.get_resources(resource_type=resource_type, status=status, search=search_term, workspace=workspace)
            for r in sess_res:
                r["provenance"] = resource_provenance(r, active_ids)
            resources.extend(sess_res)

        if not resources:
            filters = []
            if resource_type:
                filters.append(f"type={resource_type}")
            if status:
                filters.append(f"status={status}")
            if workspace:
                filters.append(f"workspace={workspace}")
            if search_term:
                filters.append(f"search={search_term}")
            filter_str = ", ".join(filters) if filters else "aucun filtre"
            return f"Aucune ressource trouvée ({filter_str})."

        # Formatage en tableau compact
        lines = [f"**{len(resources)} ressource(s) trouvée(s)**\n"]
        lines.append("| ID | Nom | Type | Statut | Localisation | MIME | Provenance |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in resources:
            loc = r.get("storage_path") or "—"
            prov_lbl = PROVENANCE_LABELS.get(r.get("provenance"), "—") if r.get("provenance") else "—"
            lines.append(
                f"| `{r['id']}` | {r['name'][:60]} | {r['resource_type']} | {r['status']} | `{loc}` | {r.get('mime') or '—'} | {prov_lbl} |"
            )

        return "\n".join(lines)

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
        if r.get("provenance"):
            prov_lbl = PROVENANCE_LABELS.get(r["provenance"], r["provenance"])
            lines.append(f"- **Provenance** : {prov_lbl}")

        # Métadonnées Plan
        if r["resource_type"] == "plan":
            if r.get("plan_goal"):
                lines.append(f"- **Objectif** : {r['plan_goal']}")
            if r.get("author_model"):
                lines.append(f"- **Modèle** : {r['author_model']}")
            if r.get("last_commit"):
                lines.append(f"- **Dernier commit** : `{r['last_commit']}`")
            if r.get("commit_msg"):
                lines.append(f"- **Message** : {r['commit_msg']}")

        # Métadonnées Codex / Generated
        if r["resource_type"] in ("codex", "generated"):
            if r.get("language"):
                lines.append(f"- **Langage** : {r['language']}")
            if r.get("lines") is not None:
                lines.append(f"- **Lignes** : {r['lines']}")
            if r.get("author_model"):
                lines.append(f"- **Modèle** : {r['author_model']}")
            if r.get("last_commit"):
                lines.append(f"- **Dernier commit** : `{r['last_commit']}`")
            if r.get("commit_msg"):
                lines.append(f"- **Message** : {r['commit_msg']}")

        # Résumé (Smart Context)
        if r.get("summary"):
            lines.append(f"\n**Résumé** :\n{r['summary'][:500]}")

        return "\n".join(lines)
