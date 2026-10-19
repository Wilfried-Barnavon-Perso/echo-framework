"""
title: Compacter le contexte de la session
author: ECHO Framework
version: 5.226.18.0
description: Condense l'historique ancien de la session dans un Checkpoint cognitif sur place (via MODEL_APOLLO) sans quitter la conversation.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik00IDE0aDZ2NiIvPjxwYXRoIGQ9Ik0yMCAxMGgtNnY0Ii8+PHBhdGggZD0ibTE0IDEwIDctNyIvPjxwYXRoIGQ9Im0zIDIxIDctNyIvPjxwYXRoIGQ9Ik00IDRoNnY2Ii8+PHBhdGggZD0iTTIwIDIwaC02di02Ii8+PHBhdGggZD0ibTEwIDEwLTctNyIvPjxwYXRoIGQ9Im0xNCAxNCA3IDciLz48L3N2Zz4=
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.1: Ajout du diagnostic pré-compactage systématique (modale d'état même si <= 4 tours), du HUD visuel de progression centré et de la mise à jour live de la jauge de contexte.
# 1.0: Création de l'action de compactage contextuel in-place (remplace resume_in_new_chat_action.py).

import sys
import asyncio
import logging
import orjson as std_json
from typing import Optional, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_state_manager import EchoStateManager
from echo_core import EchoCompactionEngine
from echo_constants import (
    ECHO_MAX_CONTEXT_SIZE,
    CONTEXT_COMPACTION_TARGET_RATIO,
    ECHO_COMPACTION_MIN_TURNS_TO_KEEP,
)

try:
    from open_webui.models.chats import Chats
except ImportError:
    Chats = None

logger = logging.getLogger("ECHO-COMPACT-ACTION")


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=20, description="Priorité d'affichage")

    def __init__(self):
        self.valves = self.Valves()

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        **kwargs,
    ) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = body.get("chat_id") or (__metadata__ or {}).get("chat_id")
        user_id = (__user__ or {}).get("id")
        messages = body.get("messages") or []

        if not chat_id or not user_id:
            await events.toast("❌ Compactage impossible : Identifiants de session manquants.", "error")
            return None

        if not messages and Chats:
            try:
                chat_obj = Chats.get_chat_by_id(chat_id)
                if asyncio.iscoroutine(chat_obj):
                    chat_obj = await chat_obj
                if chat_obj and hasattr(chat_obj, "chat") and isinstance(chat_obj.chat, dict):
                    messages = chat_obj.chat.get("messages") or []
            except Exception as e:
                logger.warning(f"[ECHO-COMPACT-ACTION] Fallback Chats.get_chat_by_id échoué : {e}")

        state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
        _, all_turns = EchoCompactionEngine.group_messages_into_turns(messages)
        active_turns, prev_state, already_evicted_count = EchoCompactionEngine.resolve_active_window(
            state_manager, all_turns
        )
        active_tokens = EchoCompactionEngine.estimate_active_tokens(active_turns, prev_state, state_manager)
        active_pct = (active_tokens / ECHO_MAX_CONTEXT_SIZE) * 100 if ECHO_MAX_CONTEXT_SIZE else 0.0

        prev_cp_num = int(prev_state.get("compaction_count", 0)) if prev_state else 0
        prev_cp_date = prev_state.get("updated_at", "—") if prev_state else "—"

        target_tokens = int(ECHO_MAX_CONTEXT_SIZE * CONTEXT_COMPACTION_TARGET_RATIO)
        split_idx = EchoCompactionEngine.find_split_index(
            active_turns=active_turns,
            target_tokens=target_tokens,
            min_turns_to_keep=ECHO_COMPACTION_MIN_TURNS_TO_KEEP,
            force_manual=True,
        )

        # Cas 1 : Fenêtre active déjà minimale (<= 4 tours actifs) -> Affichage d'un diagnostic clair
        if len(active_turns) <= ECHO_COMPACTION_MIN_TURNS_TO_KEEP or split_idx <= 0:
            if prev_cp_num > 0:
                status_msg = (
                    f"✅ Checkpoint Cognitif #{prev_cp_num} actif ({already_evicted_count} tours déjà condensés, "
                    f"scellé le {prev_cp_date}).\n\n"
                    f"• Tours actifs récents : {len(active_turns)} (seuil minimal préservé : {ECHO_COMPACTION_MIN_TURNS_TO_KEEP} tours)\n"
                    f"• Charge active estimée : ~{active_tokens:,} tokens ({active_pct:.1f}%)\n\n"
                    f"Aucun tour supplémentaire n'est à compacter pour le moment."
                )
            else:
                status_msg = (
                    f"ℹ️ Aucun Checkpoint actif sur cette session.\n\n"
                    f"• Tours dans la session : {len(active_turns)} (minimum requis : > {ECHO_COMPACTION_MIN_TURNS_TO_KEEP} tours)\n"
                    f"• Charge active estimée : ~{active_tokens:,} tokens ({active_pct:.1f}%)\n\n"
                    f"Les {ECHO_COMPACTION_MIN_TURNS_TO_KEEP} derniers tours sont toujours conservés intacts. "
                    f"Poursuivez la conversation avant de lancer un compactage."
                )
            await events.toast(
                f"ℹ️ Compactage non requis : {len(active_turns)} tour(s) actif(s) (minimum requis : > {ECHO_COMPACTION_MIN_TURNS_TO_KEEP}).",
                "info",
            )
            await events.confirm("🗜️ État du Compactage Cognitif", status_msg)
            return None

        # Cas 2 : Compactage possible -> Modale de confirmation avec bilan prévisionnel détaillé
        turns_to_keep = len(active_turns) - split_idx
        cp_status_line = (
            f"Checkpoint #{prev_cp_num} actif ({already_evicted_count} tours déjà condensés)"
            if prev_cp_num > 0
            else "Aucun (1ère compression de la session)"
        )
        confirm_msg = (
            f"État actuel : {cp_status_line}\n"
            f"• Charge active estimée : ~{active_tokens:,} tokens ({active_pct:.1f}%)\n"
            f"• Tours qui vont être condensés : {split_idx} tour(s) (total condensé : {already_evicted_count + split_idx})\n"
            f"• Tours récents gardés intacts : {turns_to_keep} tour(s)\n\n"
            f"Confirmer la compression cognitive sur place via MODEL_APOLLO ?"
        )

        if not await events.confirm("🗜️ Compactage Cognitif In-Place", confirm_msg):
            await events.toast("Compactage annulé.", "info")
            return None

        # Déploiement du HUD visuel centré (Progression en temps réel)
        hud_js = """
        (function() {
            let div = document.getElementById('echo-compaction-hud');
            if (div) div.remove();
            div = document.createElement('div');
            div.id = 'echo-compaction-hud';
            div.style.cssText = 'position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:99999; background:rgba(15,23,42,0.96); backdrop-filter:blur(12px); padding:22px 26px; border-radius:14px; color:#f8fafc; border:1px solid rgba(56, 189, 248, 0.45); text-align:center; min-width:340px; max-width:90vw; box-shadow:0 15px 35px rgba(0,0,0,0.85); font-family:system-ui,sans-serif;';
            div.innerHTML = `
                <div style="font-size:14px; color:#38bdf8; font-weight:700; text-transform:uppercase; letter-spacing:0.5px; margin-bottom:12px;">
                    🗜️ Compactage Cognitif ECHO
                </div>
                <div style="width:100%; height:6px; background:rgba(255,255,255,0.1); border-radius:3px; margin:12px 0; overflow:hidden;">
                    <div id="echo-compaction-progress" style="width:10%; height:100%; background:linear-gradient(90deg, #38bdf8, #8b5cf6); transition:width 0.35s ease; box-shadow:0 0 10px #38bdf8;"></div>
                </div>
                <div id="echo-compaction-step" style="font-size:12px; color:#cbd5e1; font-weight:500; line-height:1.4;">
                    Initialisation de la compression...
                </div>
            `;
            document.body.appendChild(div);
            window.updateEchoCompactionHud = function(pct, stepText, borderColor) {
                const p = document.getElementById('echo-compaction-progress');
                const s = document.getElementById('echo-compaction-step');
                const d = document.getElementById('echo-compaction-hud');
                if (p) p.style.width = pct + '%';
                if (s) s.innerText = stepText;
                if (d && borderColor) d.style.borderColor = borderColor;
            };
        })();
        """
        if __event_call__:
            await events.call_execute(hud_js)

        async def update_hud(pct: int, step_text: str, border_color: str = ""):
            if __event_call__:
                safe_step = std_json.dumps(step_text).decode("utf-8")
                safe_color = std_json.dumps(border_color).decode("utf-8")
                await events.call_execute(
                    f"if (window.updateEchoCompactionHud) window.updateEchoCompactionHud({pct}, {safe_step}, {safe_color});"
                )

        new_state = await EchoCompactionEngine.execute_compaction(
            messages=messages,
            user_id=user_id,
            chat_id=chat_id,
            __user__=__user__,
            __metadata__=__metadata__ or {"chat_id": chat_id},
            force_manual=True,
            events=events,
            progress_callback=update_hud,
        )

        if not new_state:
            await update_hud(100, "❌ Échec de la synthèse cognitive MODEL_APOLLO.", "rgba(239, 68, 68, 0.6)")
            await asyncio.sleep(1.8)
            if __event_call__:
                await events.call_execute("const h = document.getElementById('echo-compaction-hud'); if (h) h.remove();")
            await events.toast("❌ Le compactage n'a pas pu être généré (erreur API ou réponse vide).", "error")
            return None

        cp_num = new_state["compaction_count"]
        c_turns = new_state["compacted_turns"]
        t_before = new_state["tokens_before"]
        t_after = new_state["tokens_after"]

        await update_hud(
            100,
            f"✅ Checkpoint #{cp_num} scellé ({c_turns} tours condensés : {t_before:,} ➔ {t_after:,} tokens)",
            "rgba(16, 185, 129, 0.65)",
        )
        await events.toast(
            f"✅ Checkpoint #{cp_num} scellé ({c_turns} tours condensés : {t_before:,} ➔ {t_after:,} tokens).",
            "success",
        )

        # Mise à jour immédiate du badge de Checkpoint dans la jauge de contexte du haut (si présente)
        if __event_call__:
            gauge_update_js = f"""
            (function() {{
                const hud = document.getElementById('echo-nav-context-hud');
                if (hud) {{
                    let badge = document.getElementById('echo-nav-checkpoint-badge');
                    if (!badge) {{
                        badge = document.createElement('span');
                        badge.id = 'echo-nav-checkpoint-badge';
                        badge.style.cssText = 'font-size:10px; font-weight:700; color:#38bdf8; background:rgba(56,189,248,0.15); border:1px solid rgba(56,189,248,0.35); padding:1px 6px; border-radius:10px; white-space:nowrap;';
                        hud.appendChild(badge);
                    }}
                    badge.innerText = '🗜️ #{cp_num}';
                    badge.title = 'Checkpoint Cognitif #{cp_num} actif ({c_turns} tours condensés)';
                }}
            }})();
            """
            await events.call_execute(gauge_update_js)

        await asyncio.sleep(1.8)
        if __event_call__:
            await events.call_execute("const h = document.getElementById('echo-compaction-hud'); if (h) h.remove();")

        return None
