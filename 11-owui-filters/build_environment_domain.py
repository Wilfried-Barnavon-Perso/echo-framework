# -*- coding: utf-8 -*-
"""
title: ECHO Build Environment Domain Filter
author: Wilfried BARNAVON
version: 5.235.5.1
description: Crée l'arborescence complète (fichiers, rag, codex, etc.) et applique le schéma SQLite (identity.db, session.db) en priorité 0.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.235.5.1: Application du schéma déclaratif SQLite v1 (identity.db et session.db) en priorité 0 via ensure_db et attente du clone.
# 5.232.5.1: Création des sous-dossiers pour l'ensemble des workspaces ECHO_CODEX_WORKSPACES et initialisation Git sur les workspaces versionnés (main, plans, generated).
# 5.226.18.0: Alignement sur le versioning global ECHO.
import os
from typing import Optional, Any
from pydantic import BaseModel, Field
from echo_constants import (
    ECHO_CODEX_WORKSPACES,
    ECHO_GLOBAL_DOMAINS,
    ECHO_SESSION_DOMAINS,
    is_codex_workspace_versioned,
)
from echo_codex_git import CodexRepo
from echo_db_schema import ensure_db, DB_KIND_IDENTITY, DB_KIND_SESSION
from echo_paths import (
    get_echo_global_path,
    get_echo_identity_db_path,
    get_echo_session_path,
    wait_for_chat_clone,
)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, 
            hidden=True,
            description="Priorité d'exécution (0 = premier)."
        )

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    def _ensure(path: str, kind: str) -> None:
        try:
            rep = ensure_db(path, kind)
            if rep.get("status") in ("locked", "error"):
                print(f"[ECHO-ENV] ensure_db {kind} {rep}")
        except Exception as e:
            print(f"[ECHO-ENV] ensure_db {kind} exception: {e}")

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        if not __user__:
            return body

        user_id = __user__.get("id")
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")

        if not user_id:
            return body

        # Création des domaines globaux
        for domain in ECHO_GLOBAL_DOMAINS:
            try:
                domain_path = get_echo_global_path(user_id, domain)
                os.makedirs(domain_path, exist_ok=True)
                
                # Masquage du dossier 'files' pour empêcher l'exploration par le LLM (bwrap)
                if domain == "files":
                    os.chmod(domain_path, 0o711)
            except Exception:
                pass

        self._ensure(get_echo_identity_db_path(user_id), DB_KIND_IDENTITY)

        # Création des domaines de session
        if chat_id:
            try:
                await wait_for_chat_clone(chat_id)
            except Exception:
                pass

            for domain in ECHO_SESSION_DOMAINS:
                if domain != "db":
                    try:
                        domain_path = get_echo_session_path(user_id, chat_id, domain)
                        os.makedirs(domain_path, exist_ok=True)
                        if domain == "codex":
                            for ws in ECHO_CODEX_WORKSPACES.keys():
                                os.makedirs(os.path.join(domain_path, ws), exist_ok=True)
                                if is_codex_workspace_versioned(ws):
                                    CodexRepo(user_id, chat_id, workspace=ws)
                    except Exception:
                        pass

            self._ensure(get_echo_session_path(user_id, chat_id, "db"), DB_KIND_SESSION)

        return body
