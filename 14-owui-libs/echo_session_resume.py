# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Session Resume Engine
author: Wilfried BARNAVON
version: 6.18.23.1
description: Logique pure de migration et distillation d'une conversation vers un nouveau chat (sans HUD JS).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.23.1: Extraction de la logique de reprise hors de resume_in_new_chat_action.py pour exposition REST /echo/v1/chats/{chat_id}/resume.

from __future__ import annotations

import asyncio
import inspect
import logging
import os
import re
import shutil
import sys
import time
import types
import uuid
from typing import Any, Dict, List, Optional, Tuple

sys.path.append("/app/backend/echo_libs")

from echo_constants import OWUI_ORCHESTRATOR_MODEL  # noqa: E402
from echo_core import split_thought_process  # noqa: E402
from echo_gemini_client import EchoGeminiClient  # noqa: E402
from echo_paths import get_chat_clone_lock, get_echo_session_path  # noqa: E402
from echo_prompts import USR_ACTION_RESUME_GLOBAL, USR_ACTION_RESUME_RECENT  # noqa: E402
from echo_session_clone import duplicate_session_rag, sync_resumed_session  # noqa: E402

try:
    from open_webui.models.chats import ChatForm, Chats
except ImportError:
    Chats = None  # type: ignore
    ChatForm = None  # type: ignore

logger = logging.getLogger(__name__)

_AEC_TAG_RE = re.compile(r"<artifact\s+id=[\"']AEC_[^>]+>.*?</artifact>", re.DOTALL | re.IGNORECASE)
_REQ_TAG_RE = re.compile(r"</?REQUETE_UTILISATEUR>", re.IGNORECASE)
_REASONING_DETAILS_RE = re.compile(
    r"<details\b[^>]*\btype=[\"']reasoning[\"'][^>]*>.*?</details>",
    re.DOTALL | re.IGNORECASE,
)


class ResumeSessionError(Exception):
    """Erreur métier lors de la reprise d'une conversation dans un nouveau chat."""

    def __init__(self, message: str, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


async def _call_owui(func, *args, **kwargs) -> Any:
    """Exécute une routine publiée d'Open WebUI en supportant transparentement sync et async."""
    res = func(*args, **kwargs)
    if inspect.isawaitable(res):
        return await res
    return res


def _extract_active_branch(chat_obj: Any) -> List[Dict]:
    """Reconstruit la branche active complète jusqu'à history.currentId."""
    if not chat_obj or not hasattr(chat_obj, "chat") or not isinstance(chat_obj.chat, dict):
        return []

    chat_dict = chat_obj.chat
    history = chat_dict.get("history") if isinstance(chat_dict.get("history"), dict) else {}
    messages_map = history.get("messages") if isinstance(history.get("messages"), dict) else {}
    current_id = getattr(chat_obj, "current_message_id", None) or history.get("currentId")

    if messages_map and current_id and current_id in messages_map:
        chain: List[Dict] = []
        visited = set()
        curr = current_id
        while curr and curr in messages_map and curr not in visited:
            visited.add(curr)
            msg_node = messages_map[curr]
            if isinstance(msg_node, dict):
                chain.append({**msg_node, "id": msg_node.get("id") or curr})
                curr = msg_node.get("parentId")
            else:
                break
        chain.reverse()
        if chain:
            return chain

    db_msgs = chat_dict.get("messages")
    return db_msgs if isinstance(db_msgs, list) else []


def _extract_message_text_and_meta(m: Dict, max_raw_scan: int) -> Tuple[str, str]:
    """Extrait et nettoie le texte d'un message (après purge du bloc reasoning et pré-troncature)."""
    content = m.get("content", "")
    if isinstance(content, list):
        parts_txt = [
            str(p.get("text", ""))
            for p in content
            if isinstance(p, dict) and p.get("type") == "text"
        ]
        raw_text = "\n".join(parts_txt)
    elif isinstance(content, str):
        raw_text = content
    else:
        raw_text = str(content or "")

    outputs = m.get("output")
    if not raw_text.strip() and isinstance(outputs, list):
        msg_parts = []
        for item in outputs:
            if isinstance(item, dict) and item.get("type") == "message":
                for part in item.get("content") or []:
                    if isinstance(part, dict) and part.get("text") is not None:
                        msg_parts.append(str(part.get("text")))
        if msg_parts:
            raw_text = "\n".join(msg_parts)

    raw_text, _ = split_thought_process(raw_text)
    raw_text = _REASONING_DETAILS_RE.sub("", raw_text)
    clipped = raw_text[:max_raw_scan]
    cleaned = _AEC_TAG_RE.sub("", clipped)
    cleaned = _REQ_TAG_RE.sub("", cleaned).strip()

    if isinstance(outputs, list):
        tool_snippets = []
        for item in outputs:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                out_val = item.get("output")
                if isinstance(out_val, str) and out_val.strip():
                    tool_snippets.append(out_val.strip()[:1500])
                elif isinstance(out_val, list):
                    parts_str = "".join(
                        str(p.get("text", ""))
                        for p in out_val
                        if isinstance(p, dict) and p.get("type") != "input_image"
                    ).strip()
                    if parts_str:
                        tool_snippets.append(parts_str[:1500])
        if tool_snippets:
            cleaned = (
                f"{cleaned}\n[Résultats Outils : {' | '.join(tool_snippets)}]"
                if cleaned
                else f"[Résultats Outils : {' | '.join(tool_snippets)}]"
            )

    files = m.get("files") or []
    file_names = [
        f.get("name") or (f.get("file") or {}).get("name") or "inconnu"
        for f in files
        if isinstance(f, dict)
    ]
    file_meta = f" [Fichiers : {', '.join(file_names)}]" if file_names else ""
    return cleaned, file_meta


def _prepare_transcripts(messages: List[Dict]) -> Tuple[str, str]:
    """Construit les transcriptions globale (3000 chars/msg) et récente (80 derniers, 10000 chars/msg)."""
    full_chunks = []
    for m in messages:
        role = str(m.get("role", "user")).upper()
        cleaned, file_meta = _extract_message_text_and_meta(m, max_raw_scan=12000)
        if cleaned or file_meta:
            full_chunks.append(f"{role}: {cleaned[:3000]}{file_meta}")

    recent_chunks = []
    for m in messages[-80:]:
        role = str(m.get("role", "user")).upper()
        cleaned, file_meta = _extract_message_text_and_meta(m, max_raw_scan=25000)
        if cleaned or file_meta:
            recent_chunks.append(f"{role}: {cleaned[:10000]}{file_meta}")

    return "\n\n".join(full_chunks), "\n\n".join(recent_chunks)


async def _insert_new_chat(
    user_id: str,
    pre_generated_id: str,
    new_chat_payload: dict,
    folder_id: Optional[str] = None,
) -> Tuple[Optional[str], Any]:
    """Insère la nouvelle conversation via Chats.insert_new_chat."""
    try:
        form_kwargs: Dict[str, Any] = {"chat": new_chat_payload}
        if folder_id and hasattr(ChatForm, "model_fields") and "folder_id" in ChatForm.model_fields:
            form_kwargs["folder_id"] = folder_id
        form = ChatForm(**form_kwargs)
    except Exception:
        form = ChatForm(chat=new_chat_payload)

    sig = inspect.signature(Chats.insert_new_chat)
    params = [
        p
        for p in sig.parameters.values()
        if p.name != "self"
        and p.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(params) >= 3:
        res = await _call_owui(Chats.insert_new_chat, pre_generated_id, user_id, form)
    else:
        res = await _call_owui(Chats.insert_new_chat, user_id, form)

    return pre_generated_id, res


async def resume_chat_in_new_session(
    user_id: str,
    old_chat_id: str,
    user_name: str = "",
    user_email: str = "",
) -> Dict[str, Any]:
    """Distille une conversation existante et clone son état ECHO dans un nouveau chat."""
    if not old_chat_id or not user_id:
        raise ResumeSessionError("Identifiants de conversation ou d'utilisateur manquants.", 400)

    if not Chats or not ChatForm:
        raise ResumeSessionError("API Open WebUI Chats indisponible.", 503)

    if hasattr(Chats, "get_chat_by_id_and_user_id"):
        old_chat = await _call_owui(Chats.get_chat_by_id_and_user_id, old_chat_id, user_id)
    else:
        old_chat = await _call_owui(Chats.get_chat_by_id, old_chat_id)
        if old_chat and getattr(old_chat, "user_id", None) != user_id:
            old_chat = None

    if not old_chat:
        raise ResumeSessionError("Conversation introuvable ou accès refusé.", 404)

    messages = _extract_active_branch(old_chat)
    if not messages:
        raise ResumeSessionError("Aucun message trouvé dans la branche active de cette session.", 400)

    full_history_text, recent_history_text = await asyncio.to_thread(_prepare_transcripts, messages)
    prompt_global = USR_ACTION_RESUME_GLOBAL.format(full_history=full_history_text)
    prompt_recent = USR_ACTION_RESUME_RECENT.format(recent_history=recent_history_text)

    user_ctx = {"id": user_id, "name": user_name, "email": user_email}
    distill_meta = {"chat_id": old_chat_id}

    async def _distill(prompt_text: str, label: str) -> str:
        try:
            res = await EchoGeminiClient.call_distillation(
                prompt=prompt_text,
                __user__=user_ctx,
                __metadata__=distill_meta,
                is_json=False,
            )
            if isinstance(res, str):
                return res.strip()
            return str(res).strip() if res is not None else ""
        except Exception as exc:
            logger.error(f"[ECHO-RESUME] Erreur distillation {label}: {exc}")
            return ""

    distilled_global, distilled_recent = await asyncio.gather(
        _distill(prompt_global, "globale"),
        _distill(prompt_recent, "récente"),
    )

    if not distilled_global and not distilled_recent:
        raise ResumeSessionError(
            "Échec de la distillation cognitive (API indisponible). La session actuelle reste intacte.",
            502,
        )

    if not distilled_global:
        distilled_global = "*(Synthèse globale indisponible — voir l'état détaillé ci-dessous)*"
    if not distilled_recent:
        distilled_recent = "*(Synthèse récente indisponible — voir le macro-objectif ci-dessus)*"

    distilled_summary = f"{distilled_global}\n\n{distilled_recent}"

    old_chat_dict = old_chat.chat if isinstance(getattr(old_chat, "chat", None), dict) else {}
    old_title = getattr(old_chat, "title", None) or old_chat_dict.get("title") or "Ancienne Session"
    new_title = f"[Suite] {old_title}"
    old_folder_id = getattr(old_chat, "folder_id", None)
    old_tags = (old_chat.meta or {}).get("tags", []) if isinstance(getattr(old_chat, "meta", None), dict) else []

    old_models = old_chat_dict.get("models")
    if not old_models or not isinstance(old_models, list):
        old_models = [OWUI_ORCHESTRATOR_MODEL]

    routine_reprise = (
        "\n\n---\n"
        "**ROUTINE DE REPRISE OBLIGATOIRE**\n"
        "Le Modèle DOIT ABSOLUMENT exécuter les actions suivantes dès le premier tour de parole via les Outils et Sous-Agents :\n"
        "1. Prendre connaissance du profil de L'Utilisateur et de l'environnement (AEC).\n"
        "2. Explorer les ressources du registre (`query_registry`).\n"
        "3. Interroger la mémoire inter-session (`search_sessions_context`) pour affiner la compréhension du contexte.\n"
        "4. Reprendre la conversation en continuité stricte avec le résumé cognitif ci-dessus."
    )
    new_content = f"**Session migrée et distillée.**\n\n*Résumé cognitif :*\n{distilled_summary}{routine_reprise}"

    user_msg_id = str(uuid.uuid4())
    assistant_msg_id = str(uuid.uuid4())
    now_sec = int(time.time())
    user_root_msg = {
        "id": user_msg_id,
        "parentId": None,
        "childrenIds": [assistant_msg_id],
        "role": "user",
        "content": f"🔄 [Système ECHO] Migration et distillation du contexte de la session précédente ({old_title}).",
        "timestamp": now_sec - 1,
    }
    assistant_msg = {
        "id": assistant_msg_id,
        "parentId": user_msg_id,
        "childrenIds": [],
        "role": "assistant",
        "content": new_content,
        "model": old_models[0],
        "modelName": "Écho",
        "done": True,
        "timestamp": now_sec,
    }
    new_chat_payload = {
        "title": new_title,
        "models": old_models,
        "params": old_chat_dict.get("params", {}),
        "files": old_chat_dict.get("files", []),
        "timestamp": now_sec * 1000,
        "history": {
            "currentId": assistant_msg_id,
            "messages": {
                user_msg_id: user_root_msg,
                assistant_msg_id: assistant_msg,
            },
        },
        "messages": [user_root_msg, assistant_msg],
    }

    pre_generated_id = str(uuid.uuid4())
    try:
        _, created_chat = await _insert_new_chat(
            user_id,
            pre_generated_id,
            new_chat_payload,
            old_folder_id,
        )
    except Exception as exc:
        logger.error(f"[ECHO-RESUME] Erreur insert_new_chat: {exc}")
        raise ResumeSessionError(f"Échec de la création de la nouvelle conversation : {exc}", 500) from exc

    new_chat_id = (
        getattr(created_chat, "id", None)
        or (created_chat.get("id") if isinstance(created_chat, dict) else None)
    )
    if not new_chat_id:
        raise ResumeSessionError("Échec de la création de la nouvelle conversation (ID nul).", 500)

    if old_tags and hasattr(Chats, "update_chat_tags_by_id"):
        try:
            user_obj = types.SimpleNamespace(id=user_id)
            await _call_owui(Chats.update_chat_tags_by_id, new_chat_id, old_tags, user_obj)
        except Exception as tag_err:
            logger.warning(f"[ECHO-RESUME] Avertissement propagation tags : {tag_err}")

    live_msg_ids = {
        str(m["id"]) for m in messages if isinstance(m, dict) and m.get("id")
    } or None
    rag_report = None
    clone_lock = get_chat_clone_lock(new_chat_id)
    try:
        async with clone_lock:
            await asyncio.to_thread(
                sync_resumed_session,
                user_id,
                old_chat_id,
                new_chat_id,
                [user_msg_id, assistant_msg_id],
                live_msg_ids,
                assistant_msg_id,
            )
            try:
                rag_report = await duplicate_session_rag(
                    user_id,
                    old_chat_id,
                    new_chat_id,
                    live_msg_ids=live_msg_ids,
                    anchor_msg_id=assistant_msg_id,
                )
            except Exception as rag_err:
                logger.warning(f"[ECHO-RESUME] Avertissement clonage Qdrant SESSION_RAG : {rag_err}")
    except Exception as exc:
        logger.error(f"[ECHO-RESUME] Erreur duplication conteneur: {exc}")
        try:
            new_session_dir = os.path.dirname(get_echo_session_path(user_id, new_chat_id, "db"))
            if os.path.exists(new_session_dir):
                shutil.rmtree(new_session_dir, ignore_errors=True)
            if hasattr(Chats, "delete_chat_by_id_and_user_id"):
                await _call_owui(Chats.delete_chat_by_id_and_user_id, new_chat_id, user_id)
        except Exception:
            pass
        raise ResumeSessionError(f"Échec lors de la duplication du conteneur de session : {exc}", 500) from exc

    if rag_report is not None:
        rag_payload = {
            "complete": bool(rag_report.complete),
            "cloned": int(rag_report.cloned),
            "eligible": int(rag_report.eligible),
            "error": rag_report.error,
        }
    else:
        rag_payload = {
            "complete": False,
            "cloned": 0,
            "eligible": 0,
            "error": "exception lors du clonage (voir journaux)",
        }

    return {
        "status": "ok",
        "new_chat_id": new_chat_id,
        "title": new_title,
        "rag_report": rag_payload,
    }
