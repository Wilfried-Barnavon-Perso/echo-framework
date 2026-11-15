# -*- coding: utf-8 -*-
"""
title: ECHO Subagent Hub
author: Wilfried BARNAVON
version: 6.6.2.1
description: Hub hybride orchestrateur/humain, balisage XML et file FIFO.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.2.1: Ajout du compteur monotone _COGNITION_SEQ (bump_cognition_seq, get_cognition_seq) et de update_subagent_meta_fields.
# 6.5.3.1: Création du hub sous-agents hybrides (XML, verrous, file FIFO).

from __future__ import annotations

import asyncio
import json
import re
import threading
import time
import uuid
from typing import Any, Optional

ORCH_OPEN_TAG = "<REQUETE_ORCHESTRATEUR>"
ORCH_CLOSE_TAG = "</REQUETE_ORCHESTRATEUR>"
HUMAN_OPEN_TAG = "<REQUETE_UTILISATEUR>"
HUMAN_CLOSE_TAG = "</REQUETE_UTILISATEUR>"

_LEGACY_ORCH_PREFIX = "RÉPONSE DE L'ORCHESTRATEUR :"
_HUMAN_RE = re.compile(
    r"<REQUETE_UTILISATEUR>\s*(.*?)\s*</REQUETE_UTILISATEUR>", re.DOTALL
)
_ORCH_RE = re.compile(
    r"<REQUETE_ORCHESTRATEUR>\s*(.*?)\s*</REQUETE_ORCHESTRATEUR>", re.DOTALL
)

SUBAGENT_META_PREFIX = "subagent_meta:"
SUBAGENT_QUEUE_PREFIX = "subagent_queue:"

_REG_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_SUBAGENT_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}
_RUNNING_SUBAGENTS: set[tuple[str, str]] = set()
_COGNITION_SEQ: dict[str, int] = {}


def _norm_chat_key(chat_id: str) -> str:
    raw = str(chat_id or "")
    if not raw:
        return ""
    try:
        from echo_paths import sanitize_echo_id
        return sanitize_echo_id(raw)
    except Exception:
        return raw


def bump_cognition_seq(chat_id: str) -> int:
    """Incrémente le compteur monotone en RAM signalant un changement cognitif pour ce chat."""
    key = _norm_chat_key(chat_id)
    if not key:
        return 0
    with _REG_LOCK:
        val = _COGNITION_SEQ.get(key, 0) + 1
        _COGNITION_SEQ[key] = val
        return val


def get_cognition_seq(chat_id: str) -> int:
    """Retourne le numéro de séquence cognitif courant en RAM pour ce chat."""
    key = _norm_chat_key(chat_id)
    if not key:
        return 0
    with _REG_LOCK:
        return _COGNITION_SEQ.get(key, 0)


def _strip_legacy_orch_prefix(text: str) -> str:
    clean = (text or "").strip()
    if clean.startswith(_LEGACY_ORCH_PREFIX):
        return clean[len(_LEGACY_ORCH_PREFIX):].strip()
    return clean


def wrap_orchestrator_turn(text: str) -> str:
    """Encapsule une directive orchestrateur dans les balises XML dédiées."""
    clean = _strip_legacy_orch_prefix(text)
    if clean.startswith(ORCH_OPEN_TAG) and clean.endswith(ORCH_CLOSE_TAG):
        return clean
    return f"{ORCH_OPEN_TAG}\n{clean}\n{ORCH_CLOSE_TAG}"


def wrap_human_turn(text: str) -> str:
    """Encapsule une requête humaine dans les balises XML dédiées."""
    clean = (text or "").strip()
    if clean.startswith(HUMAN_OPEN_TAG) and clean.endswith(HUMAN_CLOSE_TAG):
        return clean
    return f"{HUMAN_OPEN_TAG}\n{clean}\n{HUMAN_CLOSE_TAG}"


def extract_turn_origin(text: str) -> tuple[str, str]:
    """Détermine l'origine ('human' | 'orchestrator') et le texte nettoyé."""
    raw = text or ""
    h_match = _HUMAN_RE.search(raw)
    if h_match:
        return "human", h_match.group(1).strip()
    o_match = _ORCH_RE.search(raw)
    if o_match:
        return "orchestrator", o_match.group(1).strip()
    return "orchestrator", _strip_legacy_orch_prefix(raw)


def get_subagent_lock(chat_id: str, sub_sid: str) -> asyncio.Lock:
    """Retourne un asyncio.Lock isolé par (event_loop, chat_id, sub_sid)."""
    try:
        loop_id = id(asyncio.get_running_loop())
    except RuntimeError:
        loop_id = 0
    key = (loop_id, str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        lock = _SUBAGENT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SUBAGENT_LOCKS[key] = lock
        return lock


def mark_subagent_running(chat_id: str, sub_sid: str, running: bool) -> None:
    """Met à jour le statut d'exécution live en mémoire d'un sous-agent."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        if running:
            _RUNNING_SUBAGENTS.add(key)
        else:
            _RUNNING_SUBAGENTS.discard(key)
    bump_cognition_seq(chat_id)


def is_subagent_running(chat_id: str, sub_sid: str) -> bool:
    """Indique si un sous-agent est actuellement actif en mémoire."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        return key in _RUNNING_SUBAGENTS


def save_subagent_meta(
    state_manager: Any, sub_sid: str, meta: dict
) -> dict:
    """Persiste les métadonnées d'un sous-agent dans echo_settings."""
    payload = dict(meta or {})
    payload.setdefault("sub_sid", sub_sid)
    payload["updated_at"] = int(time.time())
    state_manager.save_session_setting(
        f"{SUBAGENT_META_PREFIX}{sub_sid}",
        json.dumps(payload, ensure_ascii=False),
    )
    bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
    return payload


def update_subagent_meta_fields(
    state_manager: Any, sub_sid: str, **fields: Any
) -> dict:
    """Fusionne les champs non-None dans les métadonnées du sous-agent et persiste."""
    meta = get_subagent_meta(state_manager, sub_sid) or {"sub_sid": sub_sid}
    for k, v in fields.items():
        if v is not None:
            meta[k] = v
    return save_subagent_meta(state_manager, sub_sid, meta)


def get_subagent_meta(state_manager: Any, sub_sid: str) -> Optional[dict]:
    """Lit les métadonnées d'un sous-agent depuis echo_settings."""
    raw = state_manager.get_session_setting(f"{SUBAGENT_META_PREFIX}{sub_sid}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def update_subagent_status(
    state_manager: Any, sub_sid: str, status: str
) -> None:
    """Met à jour uniquement le champ status des métadonnées sous-agent."""
    meta = get_subagent_meta(state_manager, sub_sid) or {"sub_sid": sub_sid}
    meta["status"] = status
    save_subagent_meta(state_manager, sub_sid, meta)


def _read_queue_unlocked(state_manager: Any, sub_sid: str) -> list[dict]:
    raw = state_manager.get_session_setting(
        f"{SUBAGENT_QUEUE_PREFIX}{sub_sid}"
    )
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def peek_human_queue(state_manager: Any, sub_sid: str) -> list[dict]:
    """Retourne une copie de la file FIFO humaine sans la modifier."""
    with _QUEUE_LOCK:
        return list(_read_queue_unlocked(state_manager, sub_sid))


def enqueue_human_message(
    state_manager: Any,
    sub_sid: str,
    content: str,
    user_id: str = "",
) -> list[dict]:
    """Ajoute un message humain en fin de file FIFO dans echo_settings."""
    with _QUEUE_LOCK:
        queue = _read_queue_unlocked(state_manager, sub_sid)
        queue.append({
            "id": uuid.uuid4().hex[:12],
            "content": content,
            "user_id": user_id,
            "created_at": int(time.time()),
        })
        state_manager.save_session_setting(
            f"{SUBAGENT_QUEUE_PREFIX}{sub_sid}",
            json.dumps(queue, ensure_ascii=False),
        )
        bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
        return list(queue)


def pop_next_human_message(
    state_manager: Any, sub_sid: str
) -> Optional[dict]:
    """Dépile et retourne le prochain message humain de la file FIFO."""
    with _QUEUE_LOCK:
        queue = _read_queue_unlocked(state_manager, sub_sid)
        if not queue:
            return None
        item = queue.pop(0)
        state_manager.save_session_setting(
            f"{SUBAGENT_QUEUE_PREFIX}{sub_sid}",
            json.dumps(queue, ensure_ascii=False),
        )
        bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
        return item
