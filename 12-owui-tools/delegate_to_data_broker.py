"""
title: ECHO Delegate to Data Broker
author: ECHO
version: 6.7.1.1
description: Délégation de collecte de données externes structurées au Data Broker autonome.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.1.1: Délégation directe de ask_data_broker via EchoSubagentController.run_or_resume (engine="data_broker").
# 5.233.0.1: Généralisation de l'encapsulation @echo_tool sur ask_data_broker.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.
from pydantic import BaseModel, Field
from typing import Any
import sys
import uuid

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    ECHO_SUBAGENT_CHECKPOINT_CALLS,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
)
from echo_core import echo_tool  # noqa: E402
from echo_events import EchoEvents  # noqa: E402
from echo_prompts import SYS_BROKER_DELEGATE  # noqa: E402
from echo_subagent_hub import EchoSubagentController  # noqa: E402


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    @echo_tool
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
        """Délègue la collecte multi-sources de données externes (MCP et Web) à l'Agent Data Broker."""
        if not __user__:
            return "Erreur : contexte OWUI manquant."

        events = EchoEvents(__event_emitter__, __event_call__)

        system_prompt = SYS_BROKER_DELEGATE
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""

        # Outils autorisés pour le Data Broker (noms dans _TOOLS_CACHE unifié)
        allowed = [
            "list_identities_services", "list_identities",
            "list_mcp_functions", "call_mcp_function",
            "search_web", "search_instant_answer",
        ]

        await events.status("🔌 Data Broker : Collecte en cours...")

        try:
            result = await EchoSubagentController.run_or_resume(
                user_id=__user__.get("id", "system"),
                chat_id=chat_id,
                task=query,
                system_prompt=system_prompt,
                sub_sid=f"thread_databroker_{uuid.uuid4().hex[:8]}",
                target_model_key="MODEL_APOLLO",
                allowed_tools=allowed,
                slice_max_calls=ECHO_SUBAGENT_CHECKPOINT_CALLS,
                slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
                autonomous_slice=False,
                engine="data_broker",
                role_name="Data Broker",
                __user__=__user__,
                __metadata__=__metadata__,
                __event_emitter__=__event_emitter__,
                __event_call__=__event_call__,
            )
            await events.status("✅ Data Broker : Collecte terminée.", done=True)
            if isinstance(result, dict) and "text" in result:
                result["text"] = f"Rapport du Data Broker :\n{result['text']}"
                return result
            return f"Rapport du Data Broker :\n{result}"
        except Exception as e:
            return f"Erreur lors de la délégation au Data Broker : {str(e)}"
