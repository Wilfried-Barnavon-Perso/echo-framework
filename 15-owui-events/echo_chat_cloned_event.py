# -*- coding: utf-8 -*-
"""
title: ECHO Chat Cloned Event Sync
author: Wilfried BARNAVON
version: 5.231.27.2
description: Synchronisation atomique du conteneur de données ECHO lors du clonage ou du fork d'une conversation.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.27.2: HUD de progression bloquant (#echo-clone-hud) et sync Espace Utilisateur + RAG sous verrou de clonage.
# 5.227.1.1: Factorisation de la logique de clonage/fork dans la librairie partagée echo_session_clone.py.
# 5.226.18.1: Création de l'Event Function native pour la synchronisation bit-perfect des clones et forks de session.

from typing import Any, Optional
from pydantic import BaseModel, Field
import asyncio
import json
import logging
import sys

sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents  # noqa: E402
from echo_paths import get_chat_clone_lock  # noqa: E402
from echo_session_clone import duplicate_session_rag, sync_cloned_session  # noqa: E402

logger = logging.getLogger("ECHO-EVENT-SYNC")


async def _resolve_user_events(user_id: str) -> EchoEvents:
    """Construit un canal EchoEvents global pour l'utilisateur (SSE EchoWindowManager + Socket.IO)."""
    emitter = None
    try:
        from open_webui.socket.main import (
            SESSION_POOL,
            get_event_emitter,
            get_session_ids_by_user_id,
        )

        sids = get_session_ids_by_user_id(user_id)
        if sids:
            sids_sorted = sorted(
                sids,
                key=lambda s: (SESSION_POOL.get(s) or {}).get("last_seen_at", 0),
                reverse=True,
            )
            emitter = await get_event_emitter(
                {"user_id": user_id, "session_id": sids_sorted[0]}
            )
    except Exception:
        emitter = None
    return EchoEvents(emitter=emitter, user_id=user_id, chat_id="")


def _build_clone_hud_mount_js(is_fork: bool) -> str:
    """Génère le script JS d'affichage du HUD de progression bloquant (calqué sur resume_in_new_chat_action)."""
    title = (
        "🌿 Duplication (Fork) de Session ECHO"
        if is_fork
        else "🧬 Clonage de Session ECHO"
    )
    title_js = json.dumps(title)
    overlay_css = (
        "position:fixed; inset:0; z-index:999999; background:rgba(2, 6, 23, 0.72); "
        "backdrop-filter:blur(6px); display:flex; align-items:center; justify-content:center; "
        "pointer-events:all; cursor:wait; font-family: system-ui, sans-serif;"
    )
    card_css = (
        "background:rgba(15,23,42,0.95); backdrop-filter:blur(10px); padding:20px; "
        "border-radius:12px; color:white; border:1px solid rgba(0, 212, 255, 0.4); "
        "text-align:center; min-width:340px; max-width:420px; box-shadow: 0 10px 30px rgba(0,0,0,0.8);"
    )
    bar_bg_css = (
        "width:100%; height:6px; background:rgba(255,255,255,0.1); "
        "border-radius:3px; margin:15px 0; overflow:hidden;"
    )
    bar_fill_css = (
        "width:15%; height:100%; background:#00d4ff; "
        "transition:width 0.25s ease; box-shadow: 0 0 10px #00d4ff;"
    )
    return f"""
    (function() {{
        let overlay = document.getElementById('echo-clone-hud');
        if (overlay) overlay.remove();
        overlay = document.createElement('div');
        overlay.id = 'echo-clone-hud';
        overlay.setAttribute('data-echo-scope', 'global');
        overlay.style.cssText = '{overlay_css}';
        const blockEvt = function(e) {{ e.stopPropagation(); e.preventDefault(); }};
        ['click', 'mousedown', 'mouseup', 'keydown', 'keypress', 'submit'].forEach(function(evtName) {{
            overlay.addEventListener(evtName, blockEvt, true);
        }});
        overlay.innerHTML = `
            <div style="{card_css}">
                <h3 style="margin-top:0; color:#00d4ff; font-weight:600; text-transform:uppercase; font-size:14px;">
                    ${{{title_js}}}
                </h3>
                <div style="{bar_bg_css}">
                    <div id="echo-clone-progress" style="{bar_fill_css}"></div>
                </div>
                <div id="echo-clone-step" style="font-size:12px; color:#cbd5e1; font-weight:500;">
                    Initialisation de l'Espace Utilisateur...
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
        window.updateEchoCloneHud = function(pct, step) {{
            const p = document.getElementById('echo-clone-progress');
            const s = document.getElementById('echo-clone-step');
            if (p) p.style.width = pct + '%';
            if (s) s.innerText = step;
        }};
        window.removeEchoCloneHud = function() {{
            const el = document.getElementById('echo-clone-hud');
            if (el) el.remove();
        }};
        setTimeout(function() {{
            if (window.removeEchoCloneHud) window.removeEchoCloneHud();
        }}, 25000);
    }})();
    """


class Event:
    class Valves(BaseModel):
        ENABLE_CHAT_CLONING_SYNC: bool = Field(
            default=True,
            description="Active la synchronisation automatique du conteneur de données ECHO lors d'un clone/fork.",
        )

    def __init__(self):
        self.valves = self.Valves()

    async def event(
        self,
        event: dict,
        __event_name__: Optional[str] = None,
        __request__: Optional[Any] = None,
        **kwargs,
    ) -> None:
        if not self.valves.ENABLE_CHAT_CLONING_SYNC or __event_name__ != "chat.cloned":
            return

        try:
            user_id = (event.get("actor") or {}).get("id")
            new_chat_id = (event.get("subject") or {}).get("id")
            data = event.get("data") or {}
            orig_chat_id = data.get("original_chat_id")
            forked_from_msg_id = data.get("forked_from_message_id")

            if not user_id or not new_chat_id or not orig_chat_id:
                return

            events = await _resolve_user_events(user_id)
            loop = asyncio.get_running_loop()

            async def update_hud(pct: int, step: str) -> None:
                step_js = json.dumps(step)
                await events.emit_execute(
                    f"if (window.updateEchoCloneHud) window.updateEchoCloneHud({int(pct)}, {step_js});"
                )

            def sync_progress_cb(pct: int, step: str) -> None:
                try:
                    asyncio.run_coroutine_threadsafe(update_hud(pct, step), loop)
                except Exception:
                    pass

            # Verrouillage asyncio sur new_chat_id pour protéger les filtres Inlet concurrents
            clone_lock = get_chat_clone_lock(new_chat_id)
            async with clone_lock:
                try:
                    await events.emit_execute(
                        _build_clone_hud_mount_js(bool(forked_from_msg_id))
                    )
                    cutoff_ts, is_partial_fork = await asyncio.to_thread(
                        sync_cloned_session,
                        user_id,
                        orig_chat_id,
                        new_chat_id,
                        forked_from_msg_id,
                        sync_progress_cb,
                    )
                    await update_hud(
                        85, "🧠 Synchronisation de la Mémoire Vectorielle (Qdrant)..."
                    )
                    try:
                        await duplicate_session_rag(
                            user_id,
                            orig_chat_id,
                            new_chat_id,
                            cutoff_ts=cutoff_ts if is_partial_fork else None,
                        )
                    except Exception as rag_err:
                        logger.warning(
                            f"[ECHO-EVENT-SYNC] ⚠️ Duplication RAG Qdrant ignorée : {rag_err}"
                        )
                    await update_hud(100, "✅ Session prête !")
                    await asyncio.sleep(0.25)
                finally:
                    new_cid_js = json.dumps(new_chat_id)
                    await events.emit_execute(
                        "if (window.removeEchoCloneHud) window.removeEchoCloneHud(); "
                        "if (window.EchoWM && typeof window.EchoWM.hydrateChatState === 'function' "
                        f"&& window.EchoWM.activeChatId === {new_cid_js}) {{ "
                        f"delete window.EchoWM.hydratedChats[{new_cid_js}]; "
                        f"window.EchoWM.hydrateChatState({new_cid_js}); "
                        "}"
                    )
        except Exception as e:
            logger.error(f"[ECHO-EVENT-SYNC] ❌ Erreur lors du clonage de session : {e}")
