# -*- coding: utf-8 -*-
"""
title: ECHO Window Manager Filter
author: Wilfried BARNAVON
version: 5.231.11.1
description: Filtre Inlet/Outlet (Priorité 2) amorçant le gestionnaire de fenêtres agnostique multi-chats et multi-navigateurs (EchoWindowManager).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.11.1: Réétagement de la priorité à 2 (après tcp_keepalive_filter à 1 et avant edge_embed_bridge_filter à 3).
# 5.231.1.0: Injection inconditionnelle du bootstrap idempotent en Priorité 1 après rechargement de page.
# 5.230.1.1: Création du filtre dédié Window Manager à la priorité 1 (après build_environment_domain à 0 et avant edge_embed_bridge_filter à 2).

import sys
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field

if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_window_manager import (
    get_full_loader_bundle_js,
    get_window_manager,
    register_echo_wm_routes,
)


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=2,
            hidden=True,
            description="Priorité d'exécution (2 = après tcp_keepalive_filter à 1 et avant edge_embed_bridge_filter à 3).",
        )
        enable_window_manager: bool = Field(
            default=True,
            description="Active le gestionnaire de fenêtres agnostique multi-chats et multi-navigateurs (EchoWM).",
        )

    def __init__(self):
        self.valves = self.Valves()
        try:
            register_echo_wm_routes()
        except Exception:
            pass

    async def inlet(
        self,
        body: dict,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
        __request__: Optional[Any] = None,
    ) -> dict:
        """
        Amorçage en Priorité 1 :
        1. Garantit l'enregistrement des routes FastAPI /echo/v1/wm/* et le patch de /static/loader.js.
        2. Si l'utilisateur n'a pas encore de flux SSE EchoWM actif (ex: 1er chargement avant mise en cache de loader.js),
           injecte à chaud window.EchoWM + primitives EchoUI via __event_emitter__.
        """
        if not self.valves.enable_window_manager:
            return body

        register_echo_wm_routes()

        if not __event_emitter__:
            return body

        try:
            await __event_emitter__(
                {"type": "execute", "data": {"code": get_full_loader_bundle_js()}}
            )
        except Exception:
            pass

        return body

    async def outlet(
        self,
        body: dict,
        __event_emitter__: Optional[Any] = None,
        __user__: Optional[Dict[str, Any]] = None,
        __metadata__: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Synchronise la visibilité des fenêtres en sortie de tour (Priorité 1, avant codex_links_filter à 30)."""
        if not self.valves.enable_window_manager:
            return body

        user_id = (__user__ or {}).get("id", "")
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id") or ""
        wm = get_window_manager()
        if wm.has_active_clients(user_id):
            await wm.emit_js(
                user_id=user_id,
                chat_id=chat_id,
                message_id="",
                code="if (window.EchoWM && typeof window.EchoWM.syncAllWindowsVisibility === 'function') window.EchoWM.syncAllWindowsVisibility();",
            )
        return body
