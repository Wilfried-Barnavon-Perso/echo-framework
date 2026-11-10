"""
title: ECHO Context Compaction Filter
author: Wilfried BARNAVON
version: 5.237.20.1
description: Composant système interne : Compaction contextuelle automatique in-place sur seuil.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTIgMjJ2LTYiLz48cGF0aCBkPSJNMTIgOFYyIi8+PHBhdGggZD0iTTQgMTJIMiIvPjxwYXRoIGQ9Ik0xMCAxMkg4Ii8+PHBhdGggZD0iTTE2IDEyaC0yIi8+PHBhdGggZD0iTTIyIDEyaC0yIi8+PHBhdGggZD0ibTE1IDE5LTMtMy0zIDMiLz48cGF0aCBkPSJtMTUgNS0zIDMtMy0zIi8+PC9zdmc+
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.237.20.1: Retrait de tous les messages user finaux consécutifs et recalcul préventif du contexte actif après suppression de messages.
# 5.231.58.2: Remplacement de l'encart modal #echo-compact-hud et des toasts par des notifications natives EchoEvents.status.
# 5.231.58.1: Affichage du modèle clampé dans le HUD et le toast de compaction suite à la sélection du modèle le plus haut disponible.
# 5.231.55.1: Réétagement de la priorité à 60 (après user_native_context_filter à 50 et avant new_context_filter à 70).
# 5.231.38.1: Activation par défaut de la compaction contextuelle automatique (ENABLE_AUTO_COMPACTION=True).

import sys
import logging
from typing import Optional, Callable, Any
from pydantic import BaseModel, Field
import orjson as std_json

for _p in ("/app/backend/echo_libs", "/opt/ECHO/owui-libs"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from echo_constants import (
    ECHO_MAX_CONTEXT_SIZE,
    COMPACTION_TRIGGER_THRESHOLD_PCT,
    COMPACTION_TARGET_CONTEXT_PCT,
    COMPACTION_COMPRESSED_SHARE_PCT,
    COMPACTION_MIN_TURNS_REQUIRED,
    get_compaction_shares,
)
from echo_core import EchoCompactionEngine, clamp_model, estimate_token_size
from echo_state_manager import EchoStateManager
from echo_events import EchoEvents

logger = logging.getLogger("ECHO_COMPACTION_FILTER")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=60,
            description=(
                "Priorité d'exécution Inlet (60 : après user_native_context_filter=50, "
                "avant new_context_filter=70)."
            ),
        )

    class UserValves(BaseModel):
        ENABLE_AUTO_COMPACTION: bool = Field(
            default=True,
            description="🗜️ Activer la compaction automatique du contexte lorsque le seuil est atteint.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

        # --- CONFIGURATION UI OPEN WEBUI ---
        self.toggle = True  # Affiche le switch et l'accès aux UserValves dans l'interface
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTIgMjJ2LTYiLz48cGF0aCBkPSJNMTIgOFYyIi8+PHBhdGggZD0iTTQgMTJIMiIvPjxwYXRoIGQ9Ik0xMCAxMkg4Ii8+PHBhdGggZD0iTTE2IDEyaC0yIi8+PHBhdGggZD0iTTIyIDEyaC0yIi8+PHBhdGggZD0ibTE1IDE5LTMtMy0zIDMiLz48cGF0aCBkPSJtMTUgNS0zIDMtMy0zIi8+PC9zdmc+"

    def _resolve_user_valves(self, __user__: Optional[dict]) -> Any:
        if isinstance(__user__, dict) and __user__.get("valves") is not None:
            return __user__["valves"]
        return self.user_valves

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Callable] = None,
        __event_call__: Optional[Callable] = None,
    ) -> dict:
        uv = self._resolve_user_valves(__user__)
        enabled = getattr(
            uv, "ENABLE_AUTO_COMPACTION", self.user_valves.ENABLE_AUTO_COMPACTION
        )
        if not enabled:
            return body

        user_id = (__user__ or {}).get("id")
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        if not user_id or not chat_id or str(chat_id).startswith("local:"):
            return body

        messages = body.get("messages") or []
        if not messages:
            return body

        # Ne jamais déclencher pendant un sous-tour d'outil (role == 'tool')
        last_role = messages[-1].get("role", "")
        if last_role == "tool":
            return body

        state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
        last_stats = state_mgr.get_last_context_stats()
        if not last_stats:
            return body

        current_tokens = int(last_stats.get("promptTokenCount", 0) or 0)
        trigger_pct = COMPACTION_TRIGGER_THRESHOLD_PCT
        target_pct = COMPACTION_TARGET_CONTEXT_PCT
        compressed_share, kept_share = get_compaction_shares(
            COMPACTION_COMPRESSED_SHARE_PCT
        )

        trigger_tokens = int(ECHO_MAX_CONTEXT_SIZE * trigger_pct)
        if current_tokens < trigger_tokens:
            return body

        # Séparation des tours passés complets et du nouveau message utilisateur entrant
        incoming_msg = messages[-1]
        past_messages = messages[:-1]
        try:
            branch = await EchoCompactionEngine.load_active_branch_messages(chat_id)
            if branch:
                branch_ids = {
                    str(m.get("id"))
                    for m in branch
                    if isinstance(m, dict) and m.get("id")
                }
                past_messages = list(branch)
                trailing_users = []
                while past_messages and EchoCompactionEngine.is_real_user_turn(past_messages[-1]):
                    trailing_users.insert(0, past_messages.pop())
                if len(trailing_users) > 1:
                    incoming_msg = {
                        "role": "user",
                        "content": "\n\n".join(str(u.get("content", "")) for u in trailing_users),
                    }

                last_turn_id = last_stats.get("last_turn_msg_id")
                if last_turn_id and str(last_turn_id) not in branch_ids:
                    raw_ckpt = state_mgr.get_session_setting("context_checkpoint")
                    ckpt = std_json.loads(raw_ckpt) if raw_ckpt else None
                    turns = EchoCompactionEngine.split_into_turns(past_messages, state_mgr=state_mgr)
                    p_idx = EchoCompactionEngine.resolve_pivot_turn_index(turns, ckpt) if ckpt else 0
                    ckpt_toks = int((ckpt or {}).get("checkpoint_tokens", 0)) if p_idx > 0 else 0
                    tail_toks = sum(int(t.get("tokens_est", 0)) for t in turns[p_idx:])
                    inc_toks = estimate_token_size(incoming_msg)
                    current_tokens = ckpt_toks + tail_toks + inc_toks
                    if current_tokens < trigger_tokens:
                        updated_stats = dict(last_stats)
                        updated_stats["promptTokenCount"] = current_tokens
                        updated_stats["totalTokenCount"] = current_tokens
                        updated_stats["last_turn_msg_id"] = turns[-1]["user_msg_id"] if turns else None
                        state_mgr.save_context_stats(updated_stats)
                        return body
        except Exception:
            pass

        events = EchoEvents(
            __event_emitter__, __event_call__, user_id=user_id, chat_id=chat_id
        )
        pct_display = round((current_tokens / ECHO_MAX_CONTEXT_SIZE) * 100, 1)
        target_model_hint = clamp_model("MODEL_PROMETHEUS", __metadata__, user_id=user_id)
        try:
            await events.status(
                f"🗜️ Compaction contextuelle en cours ({pct_display}% — {target_model_hint})...",
                done=False,
            )
            result = await EchoCompactionEngine.execute_compaction(
                user_id=user_id,
                chat_id=chat_id,
                past_messages=past_messages,
                incoming_user_msg=incoming_msg,
                metadata=__metadata__,
                events=events,
                target_context_pct=target_pct,
                compressed_share_pct=compressed_share,
                kept_share_pct=kept_share,
                min_turns_required=COMPACTION_MIN_TURNS_REQUIRED,
                target_model=target_model_hint,
            )
            if result.get("ok"):
                await events.status(
                    f"✅ Compaction #{result['compaction_count']} terminée "
                    f"({result['compacted_turns']} tours consolidés, "
                    f"{result['keep_recent_turns']} récents intacts).",
                    done=True,
                )
            else:
                reason = result.get("reason", "Inconnue")
                logger.info(f"ECHO Compaction ignorée ({chat_id}): {reason}")
                await events.status(f"⚠️ Compaction reportée : {reason}", done=True)
        except Exception as e:
            logger.error(f"ECHO Compaction erreur ({chat_id}): {e}")
            await events.status(f"⚠️ Échec de compaction : {e}", done=True)

        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
