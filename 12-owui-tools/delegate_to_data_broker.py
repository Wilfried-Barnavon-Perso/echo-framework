# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Delegate to Data Broker
author: ECHO
version: 6.23.0.1
description: Délégation de collecte de données externes structurées au Data Broker autonome.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.0.1: Ajout de sub_sid, cohort_id, allowed_tools et propagation hiérarchique (parent_sid, group_id, depth+1) dans ask_data_broker.
# 6.11.0.1: Exposition de upload_file_to_drive dans la liste allowed du Data Broker.
# 6.7.7.1: Passage de ask_data_broker sur ECHO_SUBAGENT_CHECKPOINT_TURNS (8) et ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC (120).
# 6.7.1.1: Délégation directe de ask_data_broker via EchoSubagentController.run_or_resume (engine="data_broker").
# 5.233.0.1: Généralisation de l'encapsulation @echo_tool sur ask_data_broker.
from pydantic import BaseModel, Field
from typing import Any, List, Optional
import sys

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    ECHO_SUBAGENT_CHECKPOINT_TURNS,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
    ECHO_SUBAGENT_CONTEXT,
)
from echo_core import echo_tool  # noqa: E402
from echo_events import EchoEvents  # noqa: E402
from echo_paths import sanitize_echo_id  # noqa: E402
from echo_prompts import SYS_BROKER_DELEGATE  # noqa: E402
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_subagent_hub import (  # noqa: E402
    EchoSubagentController,
    get_subagent_system_prompt,
    resolve_updated_allowed_tools,
    validate_or_create_sub_sid,
)


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
        sub_sid: Optional[str] = None,
        cohort_id: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
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

        user_id = __user__.get("id", "system")
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        sid = validate_or_create_sub_sid(sub_sid=sub_sid, prefix="thread_databroker")

        parent_ctx = ECHO_SUBAGENT_CONTEXT.get() or {}
        if parent_ctx.get("is_subagent"):
            parent_sid = parent_ctx.get("sub_sid")
            depth = int(parent_ctx.get("depth") or 1) + 1
            group_id = parent_ctx.get("group_id")
        else:
            parent_sid = None
            depth = 1
            group_id = None

        if cohort_id and str(cohort_id).strip():
            clean_cid = sanitize_echo_id(str(cohort_id).strip(), fallback="cohort").replace("_", "-")
            norm_gid = clean_cid if clean_cid.startswith("phx_") else f"phx_{clean_cid}"
            if (not parent_ctx.get("is_subagent")) or (not group_id):
                group_id = norm_gid

        # Outils autorisés pour le Data Broker (noms dans _TOOLS_CACHE unifié)
        default_allowed = [
            "list_identities_services", "list_identities",
            "list_mcp_functions", "call_mcp_function", "upload_file_to_drive",
            "search_web", "search_instant_answer",
        ]
        eff_allowed = (
            resolve_updated_allowed_tools(default_allowed, allowed_tools)
            if allowed_tools is not None
            else default_allowed
        )
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        eff_system_prompt = (
            None
            if (sub_sid and get_subagent_system_prompt(state, sid))
            else SYS_BROKER_DELEGATE
        )

        await events.status("🔌 Data Broker : Collecte en cours...")

        try:
            result = await EchoSubagentController.run_or_resume(
                user_id=user_id,
                chat_id=chat_id,
                task=query,
                system_prompt=eff_system_prompt,
                sub_sid=sid,
                target_model_key="MODEL_APOLLO",
                allowed_tools=eff_allowed,
                slice_max_turns=ECHO_SUBAGENT_CHECKPOINT_TURNS,
                slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
                autonomous_slice=False,
                engine="data_broker",
                group_id=group_id,
                role_name="Data Broker",
                parent_sid=parent_sid,
                depth=depth,
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
