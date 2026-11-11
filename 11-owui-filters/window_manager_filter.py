# -*- coding: utf-8 -*-
"""
title: ECHO Window Manager Filter
author: Wilfried BARNAVON
version: 6.3.0.1
description: Filtre Inlet/Outlet (Priorité 20) résolvant le contexte de session pour le Window Manager.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Allègement V6 : suppression de loader.js et register_echo_wm_routes, résolution sans injection JS.
# 5.231.55.1: Réétagement de la priorité à 20 (après tcp_keepalive_filter à 10 et avant edge_embed_bridge_filter à 30).
# 5.231.46.1: Acheminement strict de l'injection Inlet et de la synchronisation Outlet via EchoEvents.emit_execute.
# 5.231.11.1: Réétagement de la priorité à 2 (après tcp_keepalive_filter à 1 et avant edge_embed_bridge_filter à 3).
# 5.231.1.0: Injection inconditionnelle du bootstrap idempotent en Priorité 1 après rechargement de page.

import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_window_manager import extract_closure_context


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=20,
            hidden=True,
            description="Priorité d'exécution (20).",
        )
        enable_window_manager: bool = Field(
            default=True,
            description="Active la résolution de session pour le gestionnaire de fenêtres ECHO.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __request__: Optional[Any] = None,
    ) -> dict:
        """Résolution légère du contexte utilisateur et de chat en entrée de tour sans injection DOM."""
        if not self.valves.enable_window_manager:
            return body

        closure = extract_closure_context(__event_emitter__)
        user_id = closure.get("user_id") or (__user__.get("id") if __user__ else "") or "system"
        chat_id = closure.get("chat_id") or (__metadata__ or {}).get("chat_id") or body.get("chat_id") or ""

        meta = body.setdefault("metadata", {})
        if user_id and not meta.get("user_id"):
            meta["user_id"] = user_id
        if chat_id and not meta.get("chat_id"):
            meta["chat_id"] = chat_id

        return body

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Passe-plat de fin de tour sans injection DOM parasite."""
        return body
