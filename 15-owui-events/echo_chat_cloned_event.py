# -*- coding: utf-8 -*-
"""
title: ECHO Chat Cloned Event Sync
author: Wilfried BARNAVON
version: 5.227.1.1
description: Synchronisation atomique du conteneur de données ECHO lors du clonage ou du fork d'une conversation.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.227.1.1: Factorisation de la logique de clonage/fork dans la librairie partagée echo_session_clone.py.
# 5.226.18.1: Création de l'Event Function native pour la synchronisation bit-perfect des clones et forks de session.

from typing import Any, Optional
from pydantic import BaseModel, Field
import asyncio
import logging
import sys

sys.path.append("/app/backend/echo_libs")
from echo_paths import get_chat_clone_lock
from echo_session_clone import sync_cloned_session

logger = logging.getLogger("ECHO-EVENT-SYNC")


class Event:
    class Valves(BaseModel):
        ENABLE_CHAT_CLONING_SYNC: bool = Field(
            default=True,
            description="Active la synchronisation automatique du conteneur de données ECHO lors d'un clone/fork.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def event(
        self,
        event: dict,
        __event_name__: Optional[str] = None,
        __request__: Optional[Any] = None,
        **kwargs,
    ) -> None:
        if not self.valves.ENABLE_CHAT_CLONING_SYNC or __event_name__ != "chat.cloned":
            return

        try:
            user_id = (event.get("actor") or {}).get("id")
            new_chat_id = (event.get("subject") or {}).get("id")
            data = event.get("data") or {}
            orig_chat_id = data.get("original_chat_id")
            forked_from_msg_id = data.get("forked_from_message_id")

            if not user_id or not new_chat_id or not orig_chat_id:
                return

            # Verrouillage asyncio sur new_chat_id pour protéger les filtres Inlet concurrents
            clone_lock = get_chat_clone_lock(new_chat_id)
            async with clone_lock:
                await asyncio.to_thread(
                    sync_cloned_session,
                    user_id,
                    orig_chat_id,
                    new_chat_id,
                    forked_from_msg_id,
                )
        except Exception as e:
            logger.error(f"[ECHO-EVENT-SYNC] ❌ Erreur lors du clonage de session : {e}")
