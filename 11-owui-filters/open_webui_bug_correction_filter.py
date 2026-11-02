# -*- coding: utf-8 -*-
"""
title: ECHO Open WebUI Bug Correction Filter
author: Wilfried BARNAVON
version: 5.231.55.1
description: Filtre Inlet (Priorité 5) corrigeant les bugs natifs d'Open WebUI en amont du pipeline ECHO.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.55.1: Création du filtre de correction du bug d'omission de tool_ids d'Open WebUI (v0.12.5) à la priorité 5.

import logging
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger("ECHO_OWUI_BUG_CORRECTION_FILTER")


# ==============================================================================
# DOCUMENTATION TECHNIQUE DES BUGS OPEN WEBUI CORRIGÉS PAR CE FILTRE
# ==============================================================================
#
# [BUG-OWUI-01] Perte intermittente des outils natifs ("Error: Tool '<name>' not found.")
# - Version Open WebUI auditée : v0.12.5 (commit f7ac7ee10962ddec0017f5c29b6740d9b9c5faca)
# - Symptôme :
#   Lorsqu'un modèle appelle un outil natif (ex: search_web, consult_subagent), Open WebUI
#   échoue parfois en retournant au LLM : `Error: Tool "search_web" not found.` alors que
#   l'outil est bien attaché par défaut au modèle dans `model-config.json` (`meta.toolIds`).
#
# - Cause Racine (Triple défaut d'architecture dans Open WebUI) :
#   1. Couplage au front-end (`backend/open_webui/utils/middleware.py`, ligne 2447) :
#      Pour construire le dictionnaire exécutable `metadata["tools"]`, le backend Open WebUI
#      lit EXCLUSIVEMENT `form_data.get("tool_ids")` envoyé dans la requête HTTP POST du
#      navigateur (`if "tool_ids" in form_data and form_data["tool_ids"]:`), et IGNORE
#      totalement `model["info"]["meta"]["toolIds"]` pourtant présent en base de données
#      et déjà chargé dans `__model__` (`extra_params["__model__"]`, ligne 2245) par le backend.
#   2. Écrasement par liste vide au Cold-Start (`src/lib/components/chat/Chat.svelte`,
#      lignes 868-878 et 2847) :
#      Au chargement d'une page ou d'un chat existant (`initChatHandler`), le composant
#      Svelte exécute `$selectedToolIds = []` AVANT que `MessageInput.svelte` (lignes 643-655)
#      n'ait fini l'appel réseau asynchrone `await getTools($localStorage.token)`. De plus,
#      sur un chat existant (reprise par URL ou F5), `Chat.svelte` (lignes 1166-1230) ne
#      ré-exécute jamais l'initialisation des `toolIds` par défaut du modèle. Le navigateur
#      envoie alors `"tool_ids": []` (ou omet `tool_ids`).
#   3. Purge silencieuse sur échec transitoire de `/api/v1/tools/` (`src/routes/(app)/+layout.svelte`,
#      lignes 291-297, et `MessageInput.svelte`, lignes 647-652) :
#      Le front-end filtre `model.info.meta.toolIds` avec `.filter((id) => $tools.some((t) => t.id === id))`.
#      Si `getTools()` subit un timeout ou retourne temporairement `[]` au démarrage de
#      l'application, tous les `toolIds` du modèle sont éliminés côté client pour la session.
#
# - Mécanisme de Correction (Inlet, Priorité 5) :
#   Dans `middleware.py`, `process_filter_functions("inlet")` (ligne 2355) est exécuté
#   AVANT `if "tool_ids" in form_data and form_data["tool_ids"]:` (ligne 2447), et reçoit
#   `__model__` via `extra_params` (ligne 2245).
#   Ce filtre inspecte donc `__model__["info"]["meta"]["toolIds"]` en amont et fusionne
#   sans doublon les `toolIds` canoniques du modèle dans `body["tool_ids"]`.
#   Note : Dans l'architecture ECHO, tous les outils de `02-owui-tools/` sont des capacités
#   système obligatoires du modèle (`config-owui.sh` : `access_grants: read: *` + `meta.toolIds`).
#
# ==============================================================================
# PROCÉDURE D'AUDIT PÉRIODIQUE (POUR SUPPRESSION FUTURE DE CE FILTRE)
# ==============================================================================
# Ce filtre est un contournement temporaire d'un bug externe d'Open WebUI.
# Il FAUT vérifier régulièrement (à chaque mise à jour d'Open WebUI dans `.tmp_audit/open-webui`)
# la disparition de ce bug en amont afin de proposer la suppression de ce filtre :
#
# 1. Inspecter `.tmp_audit/open-webui/backend/open_webui/utils/middleware.py`
#    (autour de `process_chat_payload` / `get_tools`) : vérifier si le backend fusionne
#    désormais nativement `model.get("info", {}).get("meta", {}).get("toolIds", [])`
#    lorsque `form_data.get("tool_ids")` est vide ou absent.
# 2. Inspecter `.tmp_audit/open-webui/src/lib/components/chat/Chat.svelte` et
#    `MessageInput.svelte` : vérifier si la race condition entre `initChatHandler`
#    (`selectedToolIds = []`) et `initModelTools()` (`await getTools()`) ainsi que la
#    perte des `toolIds` au rechargement (`loadChat`) ont été corrigées.
# 3. Dès que le bug est corrigé dans Open WebUI :
#    -> Proposer à l'Utilisateur la suppression de `11-owui-filters/open_webui_bug_correction_filter.py`
#       et la mise à jour de `11-owui-filters/AGENTS.md` et `AGENTS.md`.
# ==============================================================================


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=5,
            hidden=True,
            description=(
                "Priorité d'exécution Inlet (5 = après build_environment_domain à 0 "
                "et avant tcp_keepalive_filter à 10)."
            ),
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __model__: Optional[Dict[str, Any]] = None,
    ) -> dict:
        if not isinstance(body, dict):
            return body

        # --- CORRECTIF [BUG-OWUI-01] : Réinjection serveur de model.info.meta.toolIds ---
        model_obj = __model__ if isinstance(__model__, dict) else None
        if model_obj is None and isinstance(__metadata__, dict):
            meta_model = __metadata__.get("model")
            if isinstance(meta_model, dict):
                model_obj = meta_model
        if model_obj is None and isinstance(body.get("model"), dict):
            model_obj = body.get("model")

        if isinstance(model_obj, dict):
            model_tool_ids = (
                ((model_obj.get("info") or {}).get("meta") or {}).get("toolIds")
                or (model_obj.get("meta") or {}).get("toolIds")
                or []
            )
            if isinstance(model_tool_ids, list) and model_tool_ids:
                current_tool_ids = body.get("tool_ids")
                if not isinstance(current_tool_ids, list) or not current_tool_ids:
                    body["tool_ids"] = list(dict.fromkeys(model_tool_ids))
                else:
                    body["tool_ids"] = list(
                        dict.fromkeys([*current_tool_ids, *model_tool_ids])
                    )

        return body
