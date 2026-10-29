"""
title: ECHO Context Compaction Filter
author: Wilfried BARNAVON
version: 5.231.31.1
description: Composant système interne : Compaction contextuelle automatique in-place sur seuil.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTIgMjJ2LTYiLz48cGF0aCBkPSJNMTIgOFYyIi8+PHBhdGggZD0iTTQgMTJIMiIvPjxwYXRoIGQ9Ik0xMCAxMkg4Ii8+PHBhdGggZD0iTTE2IDEyaC0yIi8+PHBhdGggZD0iTTIyIDEyaC0yIi8+PHBhdGggZD0ibTE1IDE5LTMtMy0zIDMiLz48cGF0aCBkPSJtMTUgNS0zIDMtMy0zIi8+PC9zdmc+
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.31.1: Prise en charge asynchrone directe de EchoCompactionEngine.load_active_branch_messages, notification d'alerte et spinner CSS HUD.
# 5.231.30.1: Alignement sur le modèle UserValves (ENABLE_AUTO_COMPACTION uniquement, toggle=True, icône SVG fold-vertical) et utilisation stricte des constantes SSOT.
# 5.231.28.1: Création du filtre Inlet (priorité 25) de compaction contextuelle automatique in-place sur seuil (% de CONTEXT_MAX, désactivé par défaut).

import sys
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
            "    const styleEl = document.createElement('style');"
            "    styleEl.textContent = '@keyframes echo-compact-spin { to { transform: rotate(360deg); } }';"
            "    document.head.appendChild(styleEl);"
            "    el = document.createElement('div');"
            "    el.id = 'echo-compact-hud';"
            "    el.setAttribute('data-echo-scope', 'global');"
            "    el.style.cssText = 'position:fixed;inset:0;z-index:99999;background:rgba(10,12,18,0.72);"
            "backdrop-filter:blur(6px);display:flex;align-items:center;justify-content:center;font-family:system-ui,sans-serif;';"
            "    el.innerHTML = `<div style=\"background:#151924;border:1px solid rgba(56,189,248,0.35);border-radius:14px;"
            "padding:24px 32px;max-width:460px;color:#f1f5f9;box-shadow:0 20px 50px rgba(0,0,0,0.5);text-align:center;\">"
            "<div style=\"display:inline-block;width:22px;height:22px;border:2.5px solid rgba(56,189,248,0.25);border-top-color:#38bdf8;border-radius:50%;animation:echo-compact-spin 0.8s linear infinite;margin-bottom:10px;\"></div>"
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
                reason = result.get("reason", "Inconnue")
                logger.info(f"ECHO Compaction ignorée ({chat_id}): {reason}")
                await events.toast(f"ℹ️ Compaction différée : {reason}", "info")
        except Exception as e:
            logger.error(f"ECHO Compaction erreur ({chat_id}): {e}")
            await events.toast(f"⚠️ Échec de compaction : {e}", "warning")
        finally:
            try:
                await events.emit_execute(self._build_hud_js(False))
            except Exception:
                pass

        return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
