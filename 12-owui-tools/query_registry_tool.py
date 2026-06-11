"""
title: ECHO Resource Registry
author: Wilfried BARNAVON
version: 1.1
description: 1.0: Outil de consultation du registre unifié des ressources (echo_resources).
             Permet au modèle de requêter l'état complet des fichiers, plans, documents Codex
             et pages web de la session.
             1.1: Fix troncature ID UUID dans le tableau ([:20] supprimé).
             Fix fallback partiel dans get_resource (LIKE si ID exact échoue).
"""

# ECHO CONFIG NAME : ECHO Registry

import sys
from typing import Optional, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents, EchoStateManager
from echo_constants import ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD)
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES)

    def __init__(self):
        self.valves = self.Valves()

    async def query_registry(
        self,
        resource_type: str = None,
        status: str = None,
        search_term: str = None,
        resource_id: str = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """Consulte le registre des ressources de la session (fichiers, plans, code, pages web).

        Utilise cet outil pour :
        - Retrouver un fichier, un plan ou un document du Codex par nom ou ID.
        - Lister toutes les ressources d'un type donné.
        - Vérifier l'existence d'une ressource avant de la manipuler.
        - Obtenir les détails complets d'une ressource (statut, chemin, métadonnées).

        Types de ressources : 'codex' (fichiers texte/code), 'plan' (plans stratégiques),
        'media' (images/vidéos/PDF), 'binary' (fichiers non assimilables), 'weburl' (pages web distillées).

        :param resource_type: Filtre par type (optionnel).
        :param status: Filtre par statut (optionnel).
        :param search_term: Recherche par nom (optionnel).
        :param resource_id: ID exact pour détails complets (optionnel).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")

        if not cid:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        state = EchoStateManager(user_id=uid, chat_id=cid)

        # Mode détail : une ressource spécifique par ID
        if resource_id:
            resource = state.get_resource(resource_id)
            if not resource:
                return wrap_tool_output(text=f"❌ Ressource `{resource_id}` introuvable.")
            return wrap_tool_output(text=self._format_resource_detail(resource))

        # Mode liste : filtres combinés
        resources = state.get_resources(
            resource_type=resource_type,
            status=status,
            search=search_term,
        )

        if not resources:
            filters = []
            if resource_type: filters.append(f"type={resource_type}")
            if status: filters.append(f"status={status}")
            if search_term: filters.append(f"search={search_term}")
            filter_str = ", ".join(filters) if filters else "aucun filtre"
            return wrap_tool_output(text=f"Aucune ressource trouvée ({filter_str}).")

        # Formatage en tableau compact
        lines = [f"**{len(resources)} ressource(s) trouvée(s)**\n"]
        lines.append("| ID | Nom | Type | Statut | MIME |")
        lines.append("|---|---|---|---|---|")
        for r in resources:
            lines.append(f"| `{r['id']}` | {r['name'][:60]} | {r['resource_type']} | {r['status']} | {r.get('mime') or '—'} |")

        return wrap_tool_output(text="\n".join(lines))

    @staticmethod
    def _format_resource_detail(r: dict) -> str:
        """Formate les détails complets d'une ressource."""
        lines = [f"### Ressource `{r['id']}`\n"]
        lines.append(f"- **Nom** : {r['name']}")
        lines.append(f"- **Type** : {r['resource_type']}")
        lines.append(f"- **Statut** : {r['status']}")
        if r.get("mime"): lines.append(f"- **MIME** : {r['mime']}")
        if r.get("storage_path"): lines.append(f"- **Chemin** : `{r['storage_path']}`")
        if r.get("git_tracked"): lines.append(f"- **Git** : ✅ Versionné")
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
