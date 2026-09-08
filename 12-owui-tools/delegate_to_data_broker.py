"""
title: ECHO Delegate to Data Broker
author: ECHO
version: 1.2
description: Délégation de collecte de données externes structurées au Data Broker autonome.
"""
from pydantic import BaseModel, Field
from typing import Any
import sys

sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_prompts import SYS_BROKER_DELEGATE


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def ask_data_broker(
        self,
        query: str = Field(
            ...,
            description="La requête en langage naturel détaillant les informations externes à collecter."
        ),
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Permet au modèle de déléguer une recherche d'informations ou une extraction
        de données externes structurées à l'Agent Data Broker d'ECHO. Cet agent
        dispose d'un accès exclusif au système MCP et aux APIs externes.
        Exige une requête détaillée en langage naturel décrivant l'objectif de la recherche.
        """
        if not __user__:
            return "Erreur : contexte OWUI manquant."

        events = EchoEvents(__event_emitter__, __event_call__)

        system_prompt = SYS_BROKER_DELEGATE

        # Outils autorisés pour le Data Broker (noms dans _TOOLS_CACHE unifié)
        allowed = [
            "list_identities", "manage_identity",
            "list_internal_mcp_tools", "call_internal_mcp_tool",
            "list_remote_mcp_tools", "call_remote_mcp_tool",
            "search_web", "search_instant_answer",
        ]

        # Instanciation dynamique du moteur de délégation
        _delegate_mod = sys.modules.get("tool_agent_engine_tool")
        _delegate_cls = getattr(_delegate_mod, "Tools", None) if _delegate_mod else None
        if not _delegate_cls:
            return "Erreur : Module agent_engine_tool introuvable (Délégation impossible)."
        delegate = _delegate_cls()

        await events.status("🔌 Data Broker : Collecte en cours...")

        try:
            result = await delegate.delegate_to_agent(
                task=query,
                system_prompt=system_prompt,
                target_model_key="MODEL_PRO",
                allowed_tools=allowed,
                __user__=__user__,
                __chat_id__=__chat_id__,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__
            )
            await events.status("✅ Data Broker : Collecte terminée.", done=True)
            return f"Rapport du Data Broker :\n{result}"
        except Exception as e:
            return f"Erreur lors de la délégation au Data Broker : {str(e)}"
