# -*- coding: utf-8 -*-
"""
title: ECHO Context Compaction Filter
author: Wilfried BARNAVON
version: 5.231.28.1
description: Filtre Inlet (priorité 25) de compaction contextuelle automatique in-place sur seuil (% de CONTEXT_MAX).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.28.1: Création du filtre Inlet (priorité 25) de compaction contextuelle automatique in-place sur seuil (% de CONTEXT_MAX, désactivé par défaut).

import sys
import asyncio
import logging
from typing import Optional, Callable, Any
from pydantic import BaseModel, Field

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
from echo_core import EchoCompactionEngine
from echo_state_manager import EchoStateManager
from echo_events import EchoEvents

logger = logging.getLogger("ECHO_COMPACTION_FILTER")


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=25,
            description=(
                "Priorité d'exécution Inlet (25 : après context_gauge_filter=10 et "
                "user_native_context_filter=20, avant new_context_filter=30)."
            ),
        )

    class UserValves(BaseModel):
        ENABLE_AUTO_COMPACTION: bool = Field(
            default=False,
            description="Activer la compaction automatique in-place du contexte lorsque le seuil est atteint (désactivé par défaut).",
        )
        TRIGGER_THRESHOLD_PCT: float = Field(
            default=COMPACTION_TRIGGER_THRESHOLD_PCT * 100.0,
            ge=10.0,
            le=90.0,
            description="Seuil de déclenchement de la compaction en % de CONTEXT_MAX (défaut: 50%).",
        )
        TARGET_CONTEXT_PCT: float = Field(
            default=COMPACTION_TARGET_CONTEXT_PCT * 100.0,
            ge=5.0,
            le=80.0,
            description="Enveloppe cible post-compaction en % de CONTEXT_MAX (défaut: 20%).",
        )
        COMPRESSED_SHARE_PCT: float = Field(
            default=COMPACTION_COMPRESSED_SHARE_PCT * 100.0,
            ge=10.0,
            le=90.0,
            description="Part (en %) de l'enveloppe cible allouée au contexte compressé HEAD (défaut: 63%). La part gardée brute TAIL vaut automatiquement 100% - COMPRESSED_SHARE_PCT.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    @staticmethod
    def _resolve_user_valves(__user__: Optional[dict]) -> Any:
        uv = (__user__ or {}).get("valves") if isinstance(__user__, dict) else None
        return uv

    @staticmethod
    def _build_hud_js(active: bool, subtitle: str = "") -> str:
        if not active:
            return "(() => { const el = document.getElementById('echo-compact-hud'); if (el) el.remove(); })();"
        safe_sub = (
            subtitle.replace("\\", "\\\\")
            .replace("'", "\\'")
            .replace("\n", " ")
        )
        return (
            "(() => {"
            "  let el = document.getElementById('echo-compact-hud');"
            "  if (!el) {"
            "    el = document.createElement('div');"
            "    el.id = 'echo-compact-hud';"
            "    el.setAttribute('data-echo-scope', 'global');"
            "    el.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(10,12,18,0.72);"
            "backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;';"
            "    el.innerHTML = `<div style=\"background:#151924;border:1px solid rgba(56,189,248,0.35);border-radius:14px;"
            "padding:24px 32px;max-width:460px;color:#f1f5f9;box-shadow:0 20px 50px rgba(0,0,0,0.5);text-align:center;\">"
            "<div style=\"font-size:16px;font-weight:600;margin-bottom:8px;color:#38bdf8;\">🗜️ Compaction Contextuelle en cours...</div>"
            "<div id=\"echo-compact-hud-sub\" style=\"font-size:13px;color:#94a3b8;\"></div></div>`;"
            "    document.body.appendChild(el);"
            "  }"
            f"  const sub = document.getElementById('echo-compact-hud-sub'); if (sub) sub.textContent = '{safe_sub}';"
            "})();"
        )

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
        trigger_pct = (
            float(
                getattr(
                    uv, "TRIGGER_THRESHOLD_PCT", self.user_valves.TRIGGER_THRESHOLD_PCT
                )
            )
            / 100.0
        )
        target_pct = (
            float(getattr(uv, "TARGET_CONTEXT_PCT", self.user_valves.TARGET_CONTEXT_PCT))
            / 100.0
        )
        raw_compressed = (
            float(
                getattr(
                    uv, "COMPRESSED_SHARE_PCT", self.user_valves.COMPRESSED_SHARE_PCT
                )
            )
            / 100.0
        )
        # Dérivation fonctionnelle stricte de kept_share via 1 - compressed_share
        compressed_share, kept_share = get_compaction_shares(raw_compressed)

        trigger_tokens = int(ECHO_MAX_CONTEXT_SIZE * trigger_pct)
        if current_tokens < trigger_tokens:
            return body

        # Séparation des tours passés complets et du nouveau message utilisateur entrant
        incoming_msg = messages[-1]
        past_messages = messages[:-1]
        try:
            branch = await asyncio.to_thread(
                EchoCompactionEngine.load_active_branch_messages, chat_id
            )
            if branch:
                # Si le dernier message de branch est déjà le message entrant sans réponse assistant, l'exclure de past_messages
                if branch[-1].get("role") == "user":
                    past_messages = branch[:-1]
                else:
                    past_messages = branch
        except Exception:
            pass

        events = EchoEvents(
            __event_emitter__, __event_call__, user_id=user_id, chat_id=chat_id
        )
        pct_display = round((current_tokens / ECHO_MAX_CONTEXT_SIZE) * 100, 1)
        try:
            await events.emit_execute(
                self._build_hud_js(
                    True,
                    f"Seuil atteint ({pct_display}% >= {round(trigger_pct * 100, 1)}%). Distillation structurée en cours...",
                )
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
            )
            if result.get("ok"):
                await events.toast(
                    f"🗜️ Contexte compacté (#{result['compaction_count']} : "
                    f"{result['compacted_turns']} tours distillés, {result['keep_recent_turns']} gardés).",
                    "info",
                )
            else:
                logger.info(
                    f"ECHO Compaction ignorée ({chat_id}): {result.get('reason')}"
                )
        except Exception as e:
            logger.error(f"ECHO Compaction erreur ({chat_id}): {e}")
        finally:
            try:
                await events.emit_execute(self._build_hud_js(False))
            except Exception:
                pass

        return body
