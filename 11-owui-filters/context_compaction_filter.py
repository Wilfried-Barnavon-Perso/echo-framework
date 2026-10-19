"""
title: ECHO Context Compaction Filter
author: ECHO Framework
version: 5.226.18.1
description: Composant système interne : Rejeu des Checkpoints cognitifs et compactage automatique (45% -> 25%).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.1: Attente du verrou de clonage (wait_for_chat_clone) avant l'instanciation de EchoStateManager.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.1: Émission d'un statut UI explicite lors du rejeu d'un Checkpoint cognitif actif sur chaque nouveau tour utilisateur.
# 1.0: Création du filtre Inlet (priorité 15) de compactage contextuel in-place et rejeu de Checkpoint SQLite.

import sys
import logging
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_constants import (
    ECHO_MAX_CONTEXT_SIZE,
    CONTEXT_COMPACTION_TRIGGER_RATIO,
    ECHO_COMPACTION_ENABLED_DEFAULT,
    ECHO_COMPACTION_MIN_TURNS_TO_KEEP,
)
from echo_paths import wait_for_chat_clone
from echo_state_manager import EchoStateManager
from echo_events import EchoEvents
from echo_core import EchoCompactionEngine

logger = logging.getLogger("ECHO-COMPACTION-FILTER")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=15,
            hidden=True,
            description="Priorité d'exécution (15 : après user_native_context_filter [10] et avant new_context_filter [20]).",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict] = None,
        __event_emitter__: Optional[Any] = None,
    ) -> dict:
        try:
            messages = body.get("messages") or []
            if not messages:
                return body

            meta = __metadata__ or body.get("metadata", {})
            chat_id = meta.get("chat_id") or body.get("chat_id")
            user_id = (__user__ or {}).get("id")
            if not chat_id or not user_id:
                return body

            await wait_for_chat_clone(chat_id)
            state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
            system_msgs, all_turns = EchoCompactionEngine.group_messages_into_turns(messages)
            if not all_turns:
                return body

            # 1. Rejeu systématique d'un Checkpoint existant (créé manuellement ou automatiquement)
            active_turns, active_state, evicted_count = EchoCompactionEngine.resolve_active_window(
                state_manager, all_turns
            )

            # 2. Déclenchement automatique conditionnel (si UserValve ENABLE_AUTO_COMPACTION == True)
            body_meta = body.setdefault("metadata", {})
            auto_enabled = body_meta.get(
                "_echo_auto_compaction_enabled",
                meta.get("_echo_auto_compaction_enabled", ECHO_COMPACTION_ENABLED_DEFAULT),
            )

            last_msg_role = messages[-1].get("role")
            if (
                auto_enabled
                and last_msg_role == "user"
                and len(active_turns) > ECHO_COMPACTION_MIN_TURNS_TO_KEEP
            ):
                active_tokens = EchoCompactionEngine.estimate_active_tokens(
                    active_turns, active_state, state_manager
                )
                trigger_threshold = int(ECHO_MAX_CONTEXT_SIZE * CONTEXT_COMPACTION_TRIGGER_RATIO)

                if active_tokens >= trigger_threshold:
                    events = EchoEvents(__event_emitter__)
                    await events.status(
                        "🗜️ Seuil contextuel (45%) atteint : Compactage cognitif via MODEL_APOLLO en cours...",
                        done=False,
                    )
                    new_state = await EchoCompactionEngine.execute_compaction(
                        messages=messages,
                        user_id=user_id,
                        chat_id=chat_id,
                        __user__=__user__,
                        __metadata__=meta,
                        force_manual=False,
                        events=events,
                    )
                    if new_state:
                        active_turns, active_state, evicted_count = EchoCompactionEngine.resolve_active_window(
                            state_manager, all_turns
                        )
                        await events.status(
                            f"✅ Checkpoint Cognitif #{new_state['compaction_count']} actif ({new_state['compacted_turns']} tours condensés).",
                            done=True,
                        )
                        await events.toast(
                            f"🗜️ Contexte compacté automatiquement (Checkpoint #{new_state['compaction_count']} : "
                            f"{new_state['tokens_before']:,} ➔ {new_state['tokens_after']:,} tokens).",
                            "info",
                        )
                    else:
                        await events.status("⚠️ Compactage automatique reporté (état inchangé).", done=True)

            # 3. Application effective du Checkpoint sur body["messages"] en mémoire
            if active_state and active_turns:
                EchoCompactionEngine.inject_checkpoint_into_turn(
                    active_turns[0], active_state.get("summary_xml", "")
                )
                body_meta["_echo_compacted_user_turns"] = int(evicted_count)
                body_meta["_echo_active_pivot_msg_id"] = active_state.get("pivot_msg_id")
                body["messages"] = system_msgs + [m for turn in active_turns for m in turn]
                if last_msg_role == "user" and __event_emitter__:
                    cp_num = active_state.get("compaction_count", 1)
                    await EchoEvents(__event_emitter__).status(
                        f"🗜️ Checkpoint Cognitif #{cp_num} actif ({evicted_count} tours condensés)",
                        done=True,
                    )

            return body
        except Exception as e:
            logger.error(f"[ECHO-COMPACTION-FILTER] ❌ Erreur : {e}")
            return body
