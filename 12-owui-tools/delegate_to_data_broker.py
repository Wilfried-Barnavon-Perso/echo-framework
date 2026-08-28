"""
title: ECHO Delegate to Data Broker
author: ECHO
version: 1.1
description: Délégation de collecte de données externes structurées au Data Broker autonome.
"""
from pydantic import BaseModel, Field
from typing import Any
import sys

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents


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

        system_prompt = (
            "Identité : ECHO Data Broker (Agent Spécialisé Autonome).\n"
            "Objectif : Fournir des données externes structurées et fiables à l'Agent appelant.\n"
            "Capacités : Accès exclusif au registre sécurisé du système (Identity Vault) "
            "et aux serveurs MCP publics.\n"
            "Protocole d'Exécution Strict :\n"
            "1. Pour les services complexes (Bodacc, emplois, documents académiques), exploration SYSTEMATIQUE du broker local "
            "(via list_internal_mcp_tools et call_internal_mcp_tool).\n"
            "2. Si le besoin n'est pas couvert par l'interne, vérification de l'existence d'un serveur distant dans le registre local "
            "(list_identities, service='remote_mcp'), puis interrogation (list_remote_mcp_tools / call_remote_mcp_tool).\n"
            "3. Si inexistant, recherche autonome sur internet d'un serveur MCP public "
            "(search_web), ajout au registre (manage_identity), et exécution.\n"
            "4. Formatage du flux JSON brut en une réponse technique structurée et factuelle.\n"
            "Règle de Communication : Toute interaction directe avec l'utilisateur humain "
            "est proscrite. La réponse doit être formulée exclusivement sous forme de "
            "compte-rendu technique destiné à l'Agent appelant."
        )

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
