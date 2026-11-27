# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Build Environment Domain Filter
author: Wilfried BARNAVON
version: 6.24.6.1
description: Crée l'arborescence complète (fichiers, rag, codex, etc.) et applique le schéma déclaratif SQLite v3 idempotent (identity.db, session.db) en priorité 0.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.24.6.1: Factorisation de l'initialisation des domaines et schémas SQLite via echo_terminals.ensure_echo_session_environment.
# 6.18.23.1: Alignement sur le schéma déclaratif SQLite v3 idempotent en priorité 0 et sécurisation OSError de chmod 0o711 sur les domaines files.
# 5.235.5.1: Application du schéma déclaratif SQLite v1 (identity.db et session.db) en priorité 0 via ensure_db et attente du clone.
# 5.232.5.1: Création des sous-dossiers pour l'ensemble des workspaces ECHO_CODEX_WORKSPACES et initialisation Git sur les workspaces versionnés (main, plans, generated).
# 5.226.18.0: Alignement sur le versioning global ECHO.
from typing import Optional, Any
from pydantic import BaseModel, Field
from echo_paths import wait_for_chat_clone
from echo_terminals import ensure_echo_session_environment


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0,
            hidden=True,
            description="Priorité d'exécution (0 = premier).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
    ) -> dict:
        if not __user__:
            return body

        user_id = __user__.get("id")
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")

        if not user_id:
            return body

        if chat_id:
            try:
                await wait_for_chat_clone(chat_id)
            except Exception:
                pass

        ensure_echo_session_environment(user_id, chat_id)
        return body
