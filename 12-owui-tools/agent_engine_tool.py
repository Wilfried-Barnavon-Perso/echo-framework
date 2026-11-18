"""
title: ECHO Agent Engine
author: ECHO Framework
version: 6.7.1.1
description: Composant système interne : ECHO Agent Engine (Délégation par tranches supervisées & Contrôleur unifié).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.7.1.1: Mise en conformité PAS des prompts et directives.
# 6.6.30.1: Refactorisation de delegate_to_agent sur EchoSubagentController (tranches supervisées) et création de l'outil unifié control_subagents (fusion des 8 outils de maintenance).
# 6.6.2.1: Mise à jour temps réel de subagent_meta.model_key via update_subagent_meta_fields lors des cascades et appels à new_cognitive_level.
# 6.6.0.1: Persistance de model_used/is_cascaded_fallback, passage du timeout à ECHO_LLM_CALL_TIMEOUT (600s) et filet de sécurité idempotent truncate_and_spill_tool_output vers tools_output/.
# 6.5.3.1: Support des tours hybrides Orchestrateur/Humain (<REQUETE_ORCHESTRATEUR> / <REQUETE_UTILISATEUR>), verrou par sub_sid et file FIFO SQLite via echo_subagent_hub.

import sys
from typing import Any, List, Literal, Optional
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_constants import (  # noqa: E402
    ECHO_API_KEY_RETRIES,
    ECHO_API_MAX_RETRIES,
    ECHO_SUBAGENT_CHECKPOINT_CALLS,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
)
from echo_core import (  # noqa: E402
    EchoCompactionEngine,
    echo_tool,
    wrap_tool_output,
)
from echo_events import EchoEvents  # noqa: E402
from echo_gemini_client import EchoGeminiClient  # noqa: E402
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_subagent_hub import (  # noqa: E402
    EchoSubagentController,
    run_human_subagent_turn,
)

__all__ = ["Tools", "run_human_subagent_turn"]


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(
            default=ECHO_API_KEY_RETRIES,
            description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.",
        )
        MAX_RETRIES: int = Field(
            default=ECHO_API_MAX_RETRIES,
            description="Nombre de tentatives maximum.",
        )

    class UserValves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    @echo_tool
    async def delegate_to_agent(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        skill_id: Optional[str] = None,
        sub_sid: Optional[str] = None,
        with_context_distillate: bool = False,
        target_model_key: Literal[
            "MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"
        ] = "MODEL_APOLLO",
        allowed_tools: Optional[List[str]] = None,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Délègue une tâche à un sous-agent autonome outillé exécuté par tranches supervisées (avec skill_id et sub_sid optionnels)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = (__user__ or {}).get("id", "system")
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""

        if not user_id or user_id == "system":
            return wrap_tool_output(
                text="❌ Contexte utilisateur manquant.",
                status={"status": "error", "message": "user_id requis"},
            )

        distillate = ""
        if with_context_distillate and not sub_sid and chat_id:
            try:
                state = EchoStateManager(user_id=user_id, chat_id=chat_id)
                await events.status("🧠 Distillation du contexte principal...")
                distillate = await _distill_main_context(state, chat_id, user_id)
            except Exception:
                distillate = ""

        return await EchoSubagentController.run_or_resume(
            user_id=user_id,
            chat_id=chat_id,
            task=task,
            system_prompt=system_prompt,
            skill_id=skill_id,
            sub_sid=sub_sid,
            context_distillate=distillate,
            target_model_key=target_model_key,
            allowed_tools=allowed_tools,
            slice_max_calls=ECHO_SUBAGENT_CHECKPOINT_CALLS,
            slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
            autonomous_slice=False,
            engine="delegate",
            key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

    @echo_tool
    async def control_subagents(
        self,
        action: Literal[
            "status",
            "collect",
            "steer",
            "resume",
            "suspend",
            "kill",
            "summarize",
            "close",
        ] = "status",
        target_ids: Optional[List[str]] = None,
        filter_type: Literal["all", "agent", "council", "supervisor", "web"] = "all",
        directive: Optional[str] = None,
        target_model_key: Optional[
            Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"]
        ] = None,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Inspecte, collecte, réoriente (steer), relance (resume), met en pause (suspend), stoppe (kill), résume ou ferme (close) un ou plusieurs sous-agents ou groupes."""
        user_id = (__user__ or {}).get("id", "system")
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        return await EchoSubagentController.execute_control_action(
            action=action,
            user_id=user_id,
            chat_id=chat_id,
            target_ids=target_ids,
            filter_type=filter_type,
            directive=directive,
            target_model_key=target_model_key,
            key_switch_threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )


async def _distill_main_context(
    state: EchoStateManager,
    chat_id: str,
    user_id: str,
) -> str:
    """Distille les N derniers messages de la branche active du chat principal (via shadows)."""
    try:
        ids = await EchoCompactionEngine.load_active_branch_ids(chat_id)
        rows = state.get_active_branch_shadows(chat_id, limit=10, active_message_ids=ids)
        if not rows:
            return ""
        raw = ""
        for r in rows:
            text = "".join(p.get("text", "") for p in r.get("parts", []) if "text" in p)
            if text:
                raw += f"{r['role'].upper()}: {text[:500]}\n---\n"
        if not raw:
            return ""
        result = await EchoGeminiClient.call_distillation(
            f"Le Système DOIT résumer factuellement cet historique en 5 points clés pour un Sous-Agent :\n\n{raw}",
            {"id": user_id},
            {},
            is_json=False,
        )
        return result or ""
    except Exception:
        return ""
