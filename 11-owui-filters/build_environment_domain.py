# -*- coding: utf-8 -*-
"""
title: ECHO Build Environment Domain Filter
author: Wilfried BARNAVON
version: 1.0
description: Crée l'arborescence complète (fichiers, rag, codex, etc.) en priorité 0 au premier appel WebUI.
"""
import os
from typing import Optional, Any
from pydantic import BaseModel, Field
from echo_constants import ECHO_GLOBAL_DOMAINS, ECHO_SESSION_DOMAINS, ECHO_USERS_ROOT, ECHO_CODEX_WORKSPACES
from echo_paths import get_echo_global_path, get_echo_session_path

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=0, 
            hidden=True,
            description="Priorité d'exécution (0 = premier)."
        )

    def __init__(self):
        self.valves = self.Valves()

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

        # Création des domaines de session
        if chat_id:
            for domain in ECHO_SESSION_DOMAINS:
                if domain != "db":
                    try:
                        domain_path = get_echo_session_path(user_id, chat_id, domain)
                        os.makedirs(domain_path, exist_ok=True)
                        if domain == "codex":
                            for ws in ECHO_CODEX_WORKSPACES.keys():
                                os.makedirs(os.path.join(domain_path, ws), exist_ok=True)
                    except Exception:
                        pass
        return body
