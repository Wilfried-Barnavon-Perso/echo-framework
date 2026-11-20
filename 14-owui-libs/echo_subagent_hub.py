# -*- coding: utf-8 -*-
"""
title: ECHO Subagent Hub
author: Wilfried BARNAVON
version: 6.15.0.1
description: Contrôleur exécutif unifié des sous-agents, tranches supervisées, ToolDispatcher et file FIFO hybride.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.15.0.1: Propagation et télémétrie HTTP (http_status, http_status_text, navigation_statuses) dans BrowserToolDispatcher/EchoAgentRunner/deploy_browser_navigation_monitor, grâce passive min_age_sec=300.0 et isolation de enforce_transient_clip_budget.
# 6.13.1.1: Découplage live SSE (/screencast/stream + live_state/live_cursor), persistance et broadcast sans Base64 des clips MP4 dans deploy_browser_navigation_monitor, garde finally stop_screencast et fix mimeType image/jpeg.
# 6.7.11.1: Délégation du délestage dans StandardToolDispatcher._invoke_single_tool à apply_tool_spillover (echo_core).
# 6.7.10.1: Alignement de StandardToolDispatcher._invoke_single_tool sur get_tool_truncation_limit et respect de _skip_truncation (suppression de la re-troncature à 32 Ko).
# 6.7.7.1: Découpage des tranches par tours d'outils et timeout (slice_max_turns, slice_timeout_sec, autonomous_slice), suppression de la troncature zéro-batch dans execute_tool_calls et de la limite préalable de mission dans build_subagent_system_appendix.

from __future__ import annotations

import asyncio
import datetime
import functools
import inspect
import json
import re
import sys
import threading
import time
import uuid
from typing import Any, Dict, List, Literal, Optional, Protocol, Tuple

from echo_constants import (
    BROWSER_CLIP_EXT,
    BROWSER_CLIP_MIME,
    BROWSER_VIEWS_DIR_NAME,
    BROWSER_VIEW_STATUS,
    CONTEXT_TRUNCATE_THRESHOLD,
    DEFAULT_VISION_GRID_STEP,
    DELEGATE_AGENT_BLACKLIST,
    DELEGATE_AGENT_SYSTEM_TOOLS,
    ECHO_API_KEY_RETRIES,
    ECHO_API_MAX_RETRIES,
    ECHO_LLM_CALL_TIMEOUT,
    ECHO_MAX_CONCURRENT_BROWSERS,
    ECHO_MAX_CONTEXT_SIZE,
    ECHO_MAX_PARALLEL_SUBAGENTS,
    ECHO_MAX_PARALLEL_TOOLS_PER_TURN,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
    ECHO_SUBAGENT_CHECKPOINT_TURNS,
    ECHO_SUBAGENT_CONTEXT,
    ECHO_SUBAGENT_ENGINES_REGISTRY,
    ECHO_SUBAGENT_MAX_CONSECUTIVE_ERRORS,
    ECHO_TOOLS_CACHE_MAX_SIZE,
    PRUNE_CONTENT_THRESHOLD,
    build_subagent_system_appendix,
    get_generation_config,
)
from echo_core import split_thought_process

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
_VALID_SID_RE = re.compile(r"^[a-zA-Z0-9_-]{3,48}$")

SUBAGENT_META_PREFIX = "subagent_meta:"
SUBAGENT_QUEUE_PREFIX = "subagent_queue:"

SubagentStatus = Literal[
    "running",
    "idle",
    "completed",
    "pending_question",
    "suspended",
    "suspended_checkpoint",
    "suspended_pare_alert",
    "killed",
    "error",
]
SUBAGENT_STATUSES: tuple[str, ...] = (
    "running",
    "idle",
    "completed",
    "pending_question",
    "suspended",
    "suspended_checkpoint",
    "suspended_pare_alert",
    "killed",
    "error",
)

_REG_LOCK = threading.Lock()
_QUEUE_LOCK = threading.Lock()
_SUBAGENT_LOCKS: dict[tuple[int, str, str], asyncio.Lock] = {}
_CHAT_SEMAPHORES: dict[tuple[int, str], asyncio.Semaphore] = {}
_BROWSER_SEMAPHORES: dict[int, asyncio.Semaphore] = {}
_RUNNING_SUBAGENTS: set[tuple[str, str]] = set()
_RUNNING_TASKS: dict[tuple[str, str], asyncio.Task] = {}
_SUSPEND_FLAGS: set[tuple[str, str]] = set()
_KILL_FLAGS: set[tuple[str, str]] = set()
_COGNITION_SEQ: dict[str, int] = {}

# Cache partagé des outils injectés par Open WebUI : {chat_id: dict[fn_name, {callable, spec, ...}]}
_TOOLS_CACHE: dict[str, dict[str, Any]] = {}


def register_chat_tools(
    chat_id: str,
    tools_dict: Optional[dict[str, Any]],
    fresh_closures: Optional[dict[str, Any]] = None,
) -> None:
    """Enregistre et rafraîchit les callables d'outils d'un chat dans _TOOLS_CACHE."""
    if not chat_id:
        return
    with _REG_LOCK:
        if isinstance(tools_dict, dict) and tools_dict:
            _TOOLS_CACHE[chat_id] = tools_dict
            if len(_TOOLS_CACHE) > ECHO_TOOLS_CACHE_MAX_SIZE:
                _TOOLS_CACHE.pop(next(iter(_TOOLS_CACHE)))
        cached = _TOOLS_CACHE.get(chat_id)
        if isinstance(cached, dict) and isinstance(fresh_closures, dict):
            for entry in cached.values():
                if isinstance(entry, dict) and "callable" in entry:
                    cb = entry["callable"]
                    if hasattr(cb, "keywords") and isinstance(cb.keywords, dict):
                        for ck, cv in fresh_closures.items():
                            if ck in cb.keywords and cv is not None:
                                cb.keywords[ck] = cv


def get_chat_tools(
    chat_id: str, metadata: Optional[dict] = None
) -> dict[str, Any]:
    """Récupère le dictionnaire d'outils du chat depuis _TOOLS_CACHE ou metadata."""
    with _REG_LOCK:
        cached = _TOOLS_CACHE.get(chat_id or "")
        if isinstance(cached, dict) and cached:
            return dict(cached)
    if isinstance(metadata, dict):
        fallback = metadata.get("_echo_tools_dict")
        if isinstance(fallback, dict) and fallback:
            return dict(fallback)
    return {}


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


def validate_or_create_sub_sid(
    sub_sid: Optional[str] = None,
    prefix: str = "dlg",
    skill_id: Optional[str] = None,
) -> str:
    """Valide un sub_sid explicite (^[a-zA-Z0-9_-]{3,48}$) ou en génère un déterministe."""
    if sub_sid:
        candidate = str(sub_sid).strip()
        if _VALID_SID_RE.match(candidate):
            return candidate
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", candidate)[:48]
        if len(sanitized) >= 3:
            return sanitized
    if skill_id:
        safe_skill = re.sub(r"[^a-zA-Z0-9_-]", "_", str(skill_id).strip())[:24]
        return f"thread_{safe_skill}_{uuid.uuid4().hex[:8]}"
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _get_loop_id() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def get_subagent_lock(chat_id: str, sub_sid: str) -> asyncio.Lock:
    """Retourne un asyncio.Lock isolé par (event_loop, chat_id, sub_sid)."""
    key = (_get_loop_id(), str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        lock = _SUBAGENT_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _SUBAGENT_LOCKS[key] = lock
        return lock


def get_chat_subagent_semaphore(chat_id: str) -> asyncio.Semaphore:
    """Retourne le sémaphore de sous-agents parallèles pour un chat_id."""
    key = (_get_loop_id(), str(chat_id or ""))
    with _REG_LOCK:
        sem = _CHAT_SEMAPHORES.get(key)
        if sem is None:
            sem = asyncio.Semaphore(ECHO_MAX_PARALLEL_SUBAGENTS)
            _CHAT_SEMAPHORES[key] = sem
        return sem


def get_global_browser_semaphore() -> asyncio.Semaphore:
    """Retourne le sémaphore global d'instances Playwright Chromium."""
    loop_id = _get_loop_id()
    with _REG_LOCK:
        sem = _BROWSER_SEMAPHORES.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(ECHO_MAX_CONCURRENT_BROWSERS)
            _BROWSER_SEMAPHORES[loop_id] = sem
        return sem


def mark_subagent_running(chat_id: str, sub_sid: str, running: bool) -> None:
    """Met à jour le statut d'exécution live en mémoire d'un sous-agent."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        if running:
            _RUNNING_SUBAGENTS.add(key)
        else:
            _RUNNING_SUBAGENTS.discard(key)
            _RUNNING_TASKS.pop(key, None)
    bump_cognition_seq(chat_id)


def is_subagent_running(chat_id: str, sub_sid: str) -> bool:
    """Indique si un sous-agent est actuellement actif en mémoire."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        return key in _RUNNING_SUBAGENTS


def register_subagent_task(
    chat_id: str, sub_sid: str, task: Optional[asyncio.Task]
) -> None:
    """Associe la tâche asyncio courante à (chat_id, sub_sid) pour permettre un kill immédiat."""
    if task is None:
        return
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        _RUNNING_TASKS[key] = task


def request_subagent_suspend(chat_id: str, sub_sid: str) -> bool:
    """Arme une demande de pause gracieuse en fin de tour d'outil courant."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        _SUSPEND_FLAGS.add(key)
        is_run = key in _RUNNING_SUBAGENTS
    bump_cognition_seq(chat_id)
    return is_run


def clear_subagent_suspend(chat_id: str, sub_sid: str) -> None:
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        _SUSPEND_FLAGS.discard(key)


def is_subagent_suspend_requested(chat_id: str, sub_sid: str) -> bool:
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        return key in _SUSPEND_FLAGS


def kill_subagent_task(chat_id: str, sub_sid: str) -> bool:
    """Demande l'arrêt forcé immédiat (CancelledError) de la tâche asyncio en cours."""
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        _KILL_FLAGS.add(key)
        _SUSPEND_FLAGS.discard(key)
        task = _RUNNING_TASKS.get(key)
    if task is not None and not task.done():
        task.cancel()
        bump_cognition_seq(chat_id)
        return True
    bump_cognition_seq(chat_id)
    return False


def pop_kill_flag(chat_id: str, sub_sid: str) -> bool:
    key = (str(chat_id or ""), str(sub_sid or ""))
    with _REG_LOCK:
        if key in _KILL_FLAGS:
            _KILL_FLAGS.discard(key)
            return True
        return False


def save_subagent_meta(
    state_manager: Any, sub_sid: str, meta: dict
) -> dict:
    """Persiste les métadonnées d'un sous-agent dans echo_settings avec défauts SSOT."""
    payload = dict(meta or {})
    payload.setdefault("sub_sid", sub_sid)
    eng_cfg = ECHO_SUBAGENT_ENGINES_REGISTRY.get(
        str(payload.get("engine") or ""), {}
    )
    if eng_cfg:
        payload.setdefault("member_role", eng_cfg.get("member_role", "agent"))
        payload.setdefault("role_order", eng_cfg.get("role_order", 100))
        payload.setdefault("has_tool_loop", eng_cfg.get("has_tool_loop", True))
        payload.setdefault("is_addressable", eng_cfg.get("is_addressable", True))
        payload.setdefault("thread_type", eng_cfg.get("thread_type", "agent"))
        payload.setdefault("icon", eng_cfg.get("icon", "🤖"))
        payload.setdefault("color", eng_cfg.get("color", "emerald"))
    has_loop = bool(payload.get("has_tool_loop", True))
    default_limit = (
        0
        if not has_loop
        else int(
            eng_cfg.get("default_slice_limit", ECHO_SUBAGENT_CHECKPOINT_TURNS)
        )
    )
    default_timeout = (
        0
        if not has_loop
        else int(
            eng_cfg.get(
                "default_slice_timeout_sec", ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC
            )
        )
    )
    slice_turns_used = int(
        payload.get("slice_turns_used")
        if payload.get("slice_turns_used") is not None
        else (payload.get("slice_calls_used") or 0)
    )
    slice_turns_limit = int(
        payload.get("slice_turns_limit")
        if payload.get("slice_turns_limit") is not None
        else (
            payload.get("slice_calls_limit")
            if payload.get("slice_calls_limit") is not None
            else default_limit
        )
    )
    payload["slice_turns_used"] = slice_turns_used
    payload["slice_turns_limit"] = slice_turns_limit
    payload["slice_calls_used"] = slice_turns_used
    payload["slice_calls_limit"] = slice_turns_limit
    payload.setdefault("slice_timeout_sec", default_timeout)
    payload.setdefault("total_turns_used", 0)
    payload.setdefault("total_calls_used", 0)
    payload.setdefault("consecutive_errors", 0)
    payload.setdefault("checkpoint_reason", None)
    now_s = time.time()
    payload["updated_at"] = int(now_s)
    payload["updated_at_ms"] = int(now_s * 1000)
    state_manager.save_session_setting(
        f"{SUBAGENT_META_PREFIX}{sub_sid}",
        json.dumps(payload, ensure_ascii=False),
    )
    bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
    return payload


_THINKING_PROCESS_START_RE = re.compile(r"^\s*Thinking Process\s*:", re.IGNORECASE)
_THINKING_SPLIT_RE = re.compile(
    r"\n\s*(?:#{1,4}\s+\S|---\s*\n|\*\*(?:RAPPORT|SYNTHÈSE|RÉSUMÉ|CONCLUSION|ANALYSE)\b|(?:Final Response|Réponse finale)\s*:?)",
    re.IGNORECASE | re.MULTILINE,
)


def extract_response_parts_and_text(
    res: Optional[dict],
    fallback_error: str = "❌ Réponse vide.",
) -> Tuple[List[dict], str]:
    """
    Extrait les parts normalisées (isolant les réflexions internes en `thought: True`)
    et retourne `(normalized_parts, visible_text)` sans fuite de raisonnement.
    """
    if not isinstance(res, dict):
        return [{"text": fallback_error}], fallback_error
    candidates = res.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        return [{"text": fallback_error}], fallback_error
    raw_parts = (candidates[0].get("content") or {}).get("parts") or []
    if not isinstance(raw_parts, list):
        return [{"text": fallback_error}], fallback_error

    normalized_parts: List[dict] = []
    clean_chunks: List[str] = []

    for part in raw_parts:
        if not isinstance(part, dict):
            continue
        if part.get("thought"):
            normalized_parts.append(dict(part))
            continue
        if "text" in part and isinstance(part.get("text"), str):
            raw_txt = part["text"]
            clean_txt, xml_thought = split_thought_process(raw_txt)
            if xml_thought:
                normalized_parts.append({"thought": True, "text": xml_thought})
            visible_candidate = (clean_txt or "").strip()
            if visible_candidate and _THINKING_PROCESS_START_RE.match(visible_candidate):
                m_split = _THINKING_SPLIT_RE.search(visible_candidate)
                if m_split:
                    thought_block = visible_candidate[: m_split.start()].strip()
                    answer_block = visible_candidate[m_split.start():].strip()
                    if thought_block:
                        normalized_parts.append({"thought": True, "text": thought_block})
                    if answer_block:
                        normalized_parts.append({"text": answer_block})
                        clean_chunks.append(answer_block)
                else:
                    normalized_parts.append({"thought": True, "text": visible_candidate})
            elif visible_candidate:
                normalized_parts.append({"text": visible_candidate})
                clean_chunks.append(visible_candidate)
        else:
            normalized_parts.append(dict(part))

    if not normalized_parts:
        normalized_parts = [{"text": fallback_error}]
    visible_text = "\n".join(clean_chunks).strip() or fallback_error
    return normalized_parts, visible_text


def update_subagent_meta_fields(
    state_manager: Any, sub_sid: str, **fields: Any
) -> dict:
    """Fusionne les champs fournis dans les métadonnées du sous-agent et persiste."""
    meta = get_subagent_meta(state_manager, sub_sid) or {"sub_sid": sub_sid}
    for k, v in fields.items():
        if v is not None or k == "checkpoint_reason":
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
    state_manager: Any,
    sub_sid: str,
    status: str,
    checkpoint_reason: Optional[str] = None,
) -> None:
    """Met à jour le champ status (et optionnellement checkpoint_reason) des métadonnées."""
    meta = get_subagent_meta(state_manager, sub_sid) or {"sub_sid": sub_sid}
    meta["status"] = status
    if checkpoint_reason is not None or status in ("running", "idle", "completed"):
        meta["checkpoint_reason"] = checkpoint_reason
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
    """Retourne une copie de la file FIFO d'instructions sans la modifier."""
    with _QUEUE_LOCK:
        return list(_read_queue_unlocked(state_manager, sub_sid))


def enqueue_subagent_message(
    state_manager: Any,
    sub_sid: str,
    content: str,
    user_id: str = "",
    origin: Literal["human", "orchestrator"] = "human",
) -> list[dict]:
    """Ajoute une instruction (humaine ou orchestrateur) en fin de file FIFO."""
    with _QUEUE_LOCK:
        queue = _read_queue_unlocked(state_manager, sub_sid)
        queue.append({
            "id": uuid.uuid4().hex[:12],
            "content": content,
            "user_id": user_id,
            "origin": origin,
            "created_at": int(time.time()),
        })
        state_manager.save_session_setting(
            f"{SUBAGENT_QUEUE_PREFIX}{sub_sid}",
            json.dumps(queue, ensure_ascii=False),
        )
        bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
        return list(queue)


def enqueue_human_message(
    state_manager: Any,
    sub_sid: str,
    content: str,
    user_id: str = "",
) -> list[dict]:
    """Ajoute un message humain en fin de file FIFO dans echo_settings."""
    return enqueue_subagent_message(
        state_manager, sub_sid, content, user_id=user_id, origin="human"
    )


def pop_next_human_message(
    state_manager: Any, sub_sid: str
) -> Optional[dict]:
    """Dépile et retourne le prochain message de la file FIFO."""
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


def pop_all_queued_messages(
    state_manager: Any, sub_sid: str
) -> list[dict]:
    """Dépile atomiquement tous les messages en attente dans la file FIFO."""
    with _QUEUE_LOCK:
        queue = _read_queue_unlocked(state_manager, sub_sid)
        if not queue:
            return []
        state_manager.save_session_setting(
            f"{SUBAGENT_QUEUE_PREFIX}{sub_sid}",
            json.dumps([], ensure_ascii=False),
        )
        bump_cognition_seq(getattr(state_manager, "chat_id", "") or "")
        return list(queue)


def _resolve_sub_tools_from_runtime(
    body_tools_specs: Optional[list],
    __user__: Optional[dict],
) -> dict[str, dict[str, Any]]:
    """
    Reconstruit le dictionnaire sub_tools depuis les modules tool_* chargés dans sys.modules
    (avec chargement paresseux depuis /opt/ECHO/owui-tools ou 12-owui-tools si aucun tool_* n'est en RAM).
    Fonctionne même à froid hors d'un tour Pipe.pipe() lorsque body_tools_specs est vide.
    """
    import importlib.util
    from pathlib import Path

    fn_to_callable: dict[str, Any] = {}
    user_valves_data: dict = (__user__ or {}).get("valves", {}) or {}

    has_tool_mods = any(
        m.startswith("tool_") and mod is not None
        for m, mod in list(sys.modules.items())
    )
    if not has_tool_mods:
        candidates_dirs = [
            Path("/opt/ECHO/owui-tools"),
            Path(__file__).resolve().parents[1] / "12-owui-tools",
        ]
        for tdir in candidates_dirs:
            if tdir.is_dir():
                for py_file in sorted(tdir.glob("*.py")):
                    if py_file.name.startswith("_"):
                        continue
                    mod_key = f"tool_{py_file.stem}"
                    if mod_key in sys.modules:
                        continue
                    try:
                        spec = importlib.util.spec_from_file_location(mod_key, str(py_file))
                        if spec and spec.loader:
                            mod = importlib.util.module_from_spec(spec)
                            sys.modules[mod_key] = mod
                            spec.loader.exec_module(mod)
                    except Exception:
                        continue
                break

    for mod_name, module in list(sys.modules.items()):
        if not mod_name.startswith("tool_") or module is None:
            continue
        try:
            tools_class = getattr(module, "Tools", None)
            if tools_class is None:
                continue
            instance = tools_class()
            if (
                user_valves_data
                and hasattr(instance, "user_valves")
                and hasattr(instance, "UserValves")
            ):
                try:
                    instance.user_valves = instance.UserValves(
                        **{
                            k: v
                            for k, v in user_valves_data.items()
                            if k in instance.UserValves.model_fields
                        }
                    )
                except Exception:
                    pass
            for attr_name in dir(instance):
                if attr_name.startswith("_") or attr_name in (
                    "valves",
                    "user_valves",
                    "Valves",
                    "UserValves",
                ):
                    continue
                try:
                    attr = getattr(instance, attr_name)
                    if asyncio.iscoroutinefunction(attr) or (
                        callable(attr) and not isinstance(attr, type)
                    ):
                        fn_to_callable[attr_name] = attr
                except Exception:
                    continue
        except Exception:
            continue

    sub_tools: dict[str, dict[str, Any]] = {}
    if body_tools_specs:
        for t in body_tools_specs:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            f = t.get("function", {})
            fn_name = f.get("name", "")
            if not fn_name or fn_name in DELEGATE_AGENT_BLACKLIST:
                continue
            sub_tools[fn_name] = {
                "spec": {
                    "name": fn_name,
                    "description": f.get("description", ""),
                    "parameters": f.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                },
                "callable": fn_to_callable.get(fn_name),
            }
        return sub_tools

    # Fallback à froid (reprise UI hors Pipe.pipe() sans body_tools_specs) : dériver la spec minimale depuis la signature Python
    for fn_name, cb in fn_to_callable.items():
        if fn_name in DELEGATE_AGENT_BLACKLIST:
            continue
        props: dict[str, Any] = {}
        req: list[str] = []
        try:
            sig = inspect.signature(cb)
            for pname, param in sig.parameters.items():
                if pname == "self" or pname.startswith("__"):
                    continue
                props[pname] = {"type": "string"}
                if param.default is inspect.Parameter.empty:
                    req.append(pname)
        except Exception:
            pass
        sub_tools[fn_name] = {
            "spec": {
                "name": fn_name,
                "description": (inspect.getdoc(cb) or "").split("\n")[0],
                "parameters": {
                    "type": "object",
                    "properties": props,
                    **({"required": req} if req else {}),
                },
            },
            "callable": cb,
        }
    return sub_tools


def resolve_delegate_tools(
    chat_id: str,
    allowed_tools: Optional[List[str]] = None,
    __metadata__: Optional[dict] = None,
    __user__: Optional[dict] = None,
) -> tuple[dict[str, dict[str, Any]], list]:
    """Résout les outils disponibles et leurs specs pour un sous-agent sans dépendre de sys.modules['function_echo_orchestrator']."""
    tools_dict = get_chat_tools(chat_id, __metadata__)
    body_tools_specs: list = (__metadata__ or {}).get("_echo_body_tools", []) or []

    if tools_dict:
        sub_tools = {
            k: v
            for k, v in tools_dict.items()
            if k not in DELEGATE_AGENT_BLACKLIST
            and (
                not allowed_tools
                or k in allowed_tools
                or k in DELEGATE_AGENT_SYSTEM_TOOLS
            )
        }
    else:
        raw_sub = _resolve_sub_tools_from_runtime(body_tools_specs, __user__)
        sub_tools = {
            k: v
            for k, v in raw_sub.items()
            if k not in DELEGATE_AGENT_BLACKLIST
            and (
                not allowed_tools
                or k in allowed_tools
                or k in DELEGATE_AGENT_SYSTEM_TOOLS
            )
        }
    return sub_tools, body_tools_specs


def build_function_declarations(
    sub_tools: dict[str, dict[str, Any]],
    body_tools_specs: Optional[list] = None,
) -> List[dict]:
    """Construit la liste des functionDeclarations Gemini pour les outils du sous-agent."""
    decls: List[dict] = []
    if sub_tools:
        for name, tool_data in sub_tools.items():
            if not isinstance(tool_data, dict):
                continue
            spec = tool_data.get("spec") or tool_data.get("function") or {}
            decls.append({
                "name": name,
                "description": spec.get("description", ""),
                "parameters": spec.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            })
    elif body_tools_specs:
        for t in body_tools_specs:
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            f = t.get("function", {})
            fn_name = f.get("name", "")
            if not fn_name or fn_name in DELEGATE_AGENT_BLACKLIST:
                continue
            decls.append({
                "name": fn_name,
                "description": f.get("description", ""),
                "parameters": f.get(
                    "parameters", {"type": "object", "properties": {}}
                ),
            })
    return decls


def _classify_sid_filter_type(sid: str, meta: Optional[dict] = None) -> str:
    engine = (meta or {}).get("engine") or ""
    if sid.startswith("thread_council_") or engine.startswith("council"):
        return "council"
    if sid.startswith("thread_supervisor_") or engine.startswith("supervisor"):
        return "supervisor"
    if sid.startswith("thread_web_") or engine == "navigator":
        return "web"
    return "agent"


def resolve_target_sub_sids(
    state: Any,
    chat_id: str,
    target_ids: Optional[List[str]] = None,
    filter_type: str = "all",
) -> List[str]:
    """
    Résout une liste d'identifiants (sub_sid unitaires, council_id, sup_id) appartenant à chat_id.
    Supporte également les préfixes de groupe UI ('ccl_<cid>', 'sup_sup_<tid>').
    """
    threads = state.list_threads(chat_id) or []
    all_sids: list[str] = [t["sub_sid"] for t in threads if t.get("sub_sid")]

    if not target_ids:
        if filter_type == "all":
            return all_sids
        filtered: list[str] = []
        for sid in all_sids:
            meta = get_subagent_meta(state, sid)
            if _classify_sid_filter_type(sid, meta) == filter_type:
                filtered.append(sid)
        return filtered

    resolved: list[str] = []
    all_set = set(all_sids)
    for raw_tid in target_ids:
        tid = str(raw_tid or "").strip()
        if not tid:
            continue
        if tid.startswith("ccl_"):
            tid = tid[4:]
        elif tid.startswith("sup_sup_"):
            tid = tid[4:]
        elif tid.startswith("sup_") and f"thread_supervisor_{tid}_" not in "".join(all_sids):
            alt_sup = tid[4:]
            if any(s.startswith(f"thread_supervisor_{alt_sup}_") for s in all_sids):
                tid = alt_sup

        if tid in all_set:
            if tid not in resolved:
                resolved.append(tid)
            continue

        # Résolution de groupe (council_id ou task_id sup_...)
        clean_sup = (
            tid[len("thread_supervisor_"):]
            if tid.startswith("thread_supervisor_")
            else tid
        )
        clean_ccl = (
            tid[len("thread_council_"):]
            if tid.startswith("thread_council_")
            else tid
        )
        norm_ccl = clean_ccl.replace("_", "-")
        prefixes = (
            f"thread_council_{clean_ccl}_",
            f"thread_council_{norm_ccl}_",
            f"thread_supervisor_{clean_sup}_",
        )
        for sid in all_sids:
            meta = get_subagent_meta(state, sid) or {}
            if (
                sid.startswith(prefixes)
                or sid == f"thread_supervisor_critic_{clean_sup}"
                or meta.get("group_id") in (tid, clean_ccl, clean_sup)
            ):
                if sid not in resolved:
                    resolved.append(sid)

    if filter_type != "all":
        resolved = [
            sid
            for sid in resolved
            if _classify_sid_filter_type(sid, get_subagent_meta(state, sid))
            == filter_type
        ]
    return resolved


class ToolDispatcher(Protocol):
    """Interface de dispatch d'outils pour EchoAgentRunner (Standard ou Navigateur Web)."""

    def get_tool_declarations(self) -> List[dict]:
        ...

    def get_thinking_override(
        self, is_slice_exhausted: bool, last_fn_name: str
    ) -> Optional[str]:
        ...

    async def execute_tool_calls(
        self,
        tool_calls: List[dict],
        remaining_slice_calls: int,
        current_model_key: str,
        history: List[dict],
    ) -> tuple[List[dict], int, int, Optional[str], str]:
        """
        Exécute les functionCalls du tour.
        Retourne: (response_parts, calls_consumed, new_consecutive_errors, new_model_key_if_escalated, last_fn_name)
        """
        ...

    async def on_before_slice(self) -> None:
        ...

    async def on_after_slice(self, final_status: str) -> None:
        ...

    async def on_cleanup(self) -> None:
        ...

    def get_telemetry_metadata(self) -> dict:
        ...


def _is_tool_response_error(result: Any) -> bool:
    if isinstance(result, dict):
        st = result.get("status")
        if st == "error" or (isinstance(st, dict) and st.get("status") == "error"):
            return True
        if result.get("error"):
            return True
        txt = str(result.get("text") or "").lstrip()
        if txt.startswith("❌"):
            return True
    elif isinstance(result, str):
        txt = result.lstrip()
        if txt.startswith("❌") or txt.lower().startswith("error:"):
            return True
    return False


class StandardToolDispatcher:
    """Dispatcher standard des outils Open WebUI / ECHO avec parallélisme borné (Semaphore(5))."""

    def __init__(
        self,
        sid: str,
        chat_id: str,
        user_id: str,
        state: Any,
        sub_tools: dict[str, dict[str, Any]],
        body_tools_specs: Optional[list],
        events: Any,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ):
        self.sid = sid
        self.chat_id = chat_id
        self.user_id = user_id
        self.state = state
        self.sub_tools = sub_tools or {}
        self.body_tools_specs = body_tools_specs or []
        self.events = events
        self._user = __user__
        self._metadata = __metadata__
        self._event_emitter = __event_emitter__
        self._event_call = __event_call__
        self._fn_decls = build_function_declarations(
            self.sub_tools, self.body_tools_specs
        )

    def get_tool_declarations(self) -> List[dict]:
        return self._fn_decls

    def get_thinking_override(
        self, is_slice_exhausted: bool, last_fn_name: str
    ) -> Optional[str]:
        return None

    async def on_before_slice(self) -> None:
        pass

    async def on_after_slice(self, final_status: str) -> None:
        pass

    async def on_cleanup(self) -> None:
        pass

    def get_telemetry_metadata(self) -> dict:
        return {}

    async def _invoke_single_tool(
        self, fn_name: str, fn_args: dict, fn_id: Optional[str]
    ) -> tuple[dict, bool, Optional[str]]:
        from echo_core import apply_tool_spillover, clamp_model

        callable_fn = (self.sub_tools.get(fn_name) or {}).get("callable")
        escalated_model: Optional[str] = None
        is_err = False

        if callable_fn is None:
            result: Any = {
                "status": "error",
                "message": (
                    f"L'outil '{fn_name}' est déclaré mais son exécution n'est pas disponible "
                    "dans ce contexte de sous-agent."
                ),
            }
            is_err = True
        else:
            try:
                infra_kwargs = {
                    "__user__": self._user,
                    "__chat_id__": self.chat_id,
                    "__metadata__": self._metadata,
                    "__event_emitter__": self._event_emitter,
                    "__event_call__": self._event_call,
                }
                exec_callable = callable_fn
                try:
                    target_fn = getattr(callable_fn, "func", callable_fn)
                    sig = inspect.signature(callable_fn)
                    already_frozen = getattr(callable_fn, "keywords", None)
                    if isinstance(already_frozen, dict):
                        local_frozen = dict(already_frozen)
                        for _k, _v in infra_kwargs.items():
                            if _k in local_frozen and _v is not None:
                                local_frozen[_k] = _v
                        exec_callable = functools.partial(
                            callable_fn.func,
                            *(getattr(callable_fn, "args", ()) or ()),
                            **local_frozen,
                        )
                    accepted_infra = {
                        k: v
                        for k, v in infra_kwargs.items()
                        if k in sig.parameters
                        and not (
                            isinstance(already_frozen, dict)
                            and k in already_frozen
                        )
                    }
                except (ValueError, TypeError):
                    target_fn = getattr(callable_fn, "func", callable_fn)
                    accepted_infra = {}

                token = ECHO_SUBAGENT_CONTEXT.set({
                    "is_subagent": True,
                    "sub_sid": self.sid,
                    "sub_tools_names": list(self.sub_tools.keys()),
                })
                try:
                    if inspect.iscoroutinefunction(target_fn):
                        result = await exec_callable(**(fn_args or {}), **accepted_infra)
                    else:
                        result = await asyncio.to_thread(
                            exec_callable, **(fn_args or {}), **accepted_infra
                        )
                finally:
                    ECHO_SUBAGENT_CONTEXT.reset(token)

                is_err = _is_tool_response_error(result)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                result = {"status": "error", "message": str(e)}
                is_err = True

        if fn_name == "new_cognitive_level" and not is_err:
            new_lvl = (fn_args or {}).get("niveau_requis")
            if new_lvl:
                escalated_model = clamp_model(
                    str(new_lvl), self._metadata or {}, self.user_id
                )

        if isinstance(result, dict):
            _resp = dict(result)
            _resp.pop("echo_tool_multiparts", None)
            _resp = apply_tool_spillover(
                _resp,
                uid=self.user_id,
                cid=self.chat_id,
                tool_name=fn_name,
                call_id=fn_id,
                is_subagent=True,
                is_error=is_err,
                text_key="text",
            )
        else:
            _resp = apply_tool_spillover(
                {"result": str(result)},
                uid=self.user_id,
                cid=self.chat_id,
                tool_name=fn_name,
                call_id=fn_id,
                is_subagent=True,
                is_error=is_err,
                text_key="result",
            )

        _fr: dict = {"name": fn_name, "response": _resp}
        if fn_id:
            _fr["id"] = fn_id
        return {"functionResponse": _fr}, is_err, escalated_model

    async def execute_tool_calls(
        self,
        tool_calls: List[dict],
        remaining_slice_calls: int,
        current_model_key: str,
        history: List[dict],
    ) -> tuple[List[dict], int, int, Optional[str], str]:
        # 1. Règle d'exclusivité de new_cognitive_level
        has_cog_switch = any(
            fc.get("name") == "new_cognitive_level" for fc in tool_calls
        )
        if has_cog_switch and len(tool_calls) > 1:
            response_parts: List[dict] = []
            escalated: Optional[str] = None
            for fc in tool_calls:
                fn_name = fc.get("name", "")
                fn_id = fc.get("id")
                if fn_name == "new_cognitive_level":
                    part_resp, _, esc = await self._invoke_single_tool(
                        fn_name, fc.get("args", {}) or {}, fn_id
                    )
                    if esc:
                        escalated = esc
                    response_parts.append(part_resp)
                else:
                    _cfr: dict = {
                        "name": fn_name,
                        "response": {
                            "status": "cancelled_cognitive_escalation",
                            "message": (
                                "Appel annulé : new_cognitive_level doit être exécuté seul. "
                                "Réémettre cet appel après la transition cognitive."
                            ),
                        },
                    }
                    if fn_id:
                        _cfr["id"] = fn_id
                    response_parts.append({"functionResponse": _cfr})
            return response_parts, 1, 0, escalated, "new_cognitive_level"

        # 2. Plafonnement par tour uniquement (ECHO_MAX_PARALLEL_TOOLS_PER_TURN = 5)
        allowed_calls = tool_calls[:ECHO_MAX_PARALLEL_TOOLS_PER_TURN]
        excess_calls = tool_calls[ECHO_MAX_PARALLEL_TOOLS_PER_TURN:]

        sem = asyncio.Semaphore(ECHO_MAX_PARALLEL_TOOLS_PER_TURN)

        async def _bounded_call(fc: dict) -> tuple[dict, bool, Optional[str]]:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {}) or {}
            fn_id = fc.get("id")
            if self.events:
                await self.events.status(f"🔧 [{self.sid}] {fn_name}...")
            async with sem:
                return await self._invoke_single_tool(fn_name, fn_args, fn_id)

        batch_results = await asyncio.gather(
            *(_bounded_call(fc) for fc in allowed_calls)
        )

        response_parts = []
        err_streak = 0
        escalated_model: Optional[str] = None
        last_fn_name = "action"

        for fc, (part_resp, is_err, esc) in zip(allowed_calls, batch_results):
            response_parts.append(part_resp)
            last_fn_name = fc.get("name", "action")
            if esc:
                escalated_model = esc
            if is_err:
                err_streak += 1
            else:
                err_streak = 0

        for fc in excess_calls:
            fn_id = fc.get("id")
            _efr: dict = {
                "name": fc.get("name", "unknown"),
                "response": {
                    "status": "exceeded_parallel_per_turn_limit",
                    "message": (
                        f"Limite par tour atteinte ({ECHO_MAX_PARALLEL_TOOLS_PER_TURN} appels simultanés max par tour). "
                        "Réémettre au tour suivant si nécessaire."
                    ),
                },
            }
            if fn_id:
                _efr["id"] = fn_id
            response_parts.append({"functionResponse": _efr})

        return (
            response_parts,
            len(allowed_calls),
            err_streak,
            escalated_model,
            last_fn_name,
        )


def prune_browser_heavy_context(
    history_list: List[dict], threshold: int, is_zooming: bool = False
) -> None:
    """Élagage proactif des payloads DOM/A11y/inlineData anciens dans l'historique navigateur."""
    heavy_keys = ("dom_map", "a11y_tree", "content", "html", "search_dom")
    for i, msg in enumerate(history_list):
        if "parts" in msg and not is_zooming and i < len(history_list) - 4:
            msg["parts"] = [
                p for p in msg["parts"] if "inlineData" not in p
            ]
        for part in msg.get("parts", []):
            if "functionResponse" in part:
                fr = part["functionResponse"]
                resp = fr.get("response", {})
                if isinstance(resp, dict):
                    for key in heavy_keys:
                        if key in resp:
                            val = resp[key]
                            if isinstance(val, str) and (
                                "[PURGED" in val or "[TRUNCATED" in val
                            ):
                                continue
                            if isinstance(val, str) and len(val) > threshold:
                                resp[key] = (
                                    val[:threshold]
                                    + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"
                                )
                            elif (
                                not isinstance(val, str)
                                and len(str(val)) > threshold
                            ):
                                resp[key] = "[PURGED_OBSOLETE_UI_STATE]"
            if "text" in part:
                text = part["text"]
                if (
                    isinstance(text, str)
                    and text.startswith("Voici les éléments interactifs actuels")
                    and "[TRUNCATED" not in text
                    and len(text) > threshold
                ):
                    part["text"] = (
                        text[:threshold] + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"
                    )


def format_browser_dom_view(res_view: dict) -> str:
    """Formate la carte DOM et l'état de navigation pour injection dans l'historique navigateur."""
    dom_data = res_view.get("metadata", [])
    dom_text = json.dumps(dom_data, ensure_ascii=False)[:60000]
    vp = res_view.get("viewport")
    vp_text = (
        f"\nDimensions de l'écran (Viewport) : {vp['width']}x{vp['height']} pixels."
        if vp
        else ""
    )
    url = res_view.get("url") or ""
    url_text = f"\nURL active : {url}" if url else ""
    http_status = res_view.get("http_status")
    http_status_text = (res_view.get("http_status_text") or "").strip()
    http_line = ""
    if http_status is not None:
        status_str = f"{http_status} {http_status_text}".strip()
        http_line = (
            f"\n⚠️ Statut HTTP : {status_str}"
            if int(http_status) >= 400
            else f"\nStatut HTTP : {status_str}"
        )
    nav_error = res_view.get("nav_error")
    nav_warning = res_view.get("nav_warning")
    alert_lines = ""
    if nav_error:
        alert_lines += f"\n⚠️ Erreur de navigation : {nav_error}"
    if nav_warning:
        alert_lines += f"\n⚠️ Avertissement de navigation : {nav_warning}"
    return (
        f"Voici les éléments interactifs actuels (Carte DOM) :"
        f"{url_text}{http_line}{alert_lines}{vp_text}\n{dom_text}"
    )


async def deploy_browser_navigation_monitor(
    res_view: dict,
    chat_id: str,
    uid: str,
    show_browser_hud: bool = True,
    browser_session_id: Optional[str] = None,
    open_tab_on_deploy: bool = False,
) -> None:
    """Enregistre la capture dans browserviews/ et la diffuse via EchoWindowManager."""
    import os
    import pybase64 as base64
    from echo_browser_lib import enforce_transient_clip_budget
    from echo_paths import (
        generate_echo_file_id,
        get_echo_session_path,
        sanitize_echo_id,
    )
    from echo_state_manager import EchoStateManager
    from echo_window_manager import get_window_manager

    b64 = res_view.get("screenshot_b64", "")
    clip_b64 = res_view.get("clip_b64", "")
    clip_duration_ms = int(res_view.get("clip_duration_ms") or 0)
    clip_size_bytes = int(res_view.get("clip_size_bytes") or 0)
    eff_session_id = browser_session_id or chat_id
    file_id = ""
    filename = ""
    clip_filename = ""
    ts_now = int(time.time())

    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.jpg"
            state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
            vault_path = get_echo_session_path(
                uid, chat_id, BROWSER_VIEWS_DIR_NAME
            )
            os.makedirs(vault_path, exist_ok=True)
            filepath = os.path.join(vault_path, filename)

            def _write_bytes(data: bytes, path: str) -> None:
                with open(path, "wb") as f:
                    f.write(data)

            img_data = base64.b64decode(b64)
            await asyncio.to_thread(_write_bytes, img_data, filepath)

            if file_id and clip_b64:
                clip_data = base64.b64decode(clip_b64)
                clip_filename = f"{file_id}{BROWSER_CLIP_EXT}"
                clip_filepath = os.path.join(vault_path, clip_filename)
                await asyncio.to_thread(_write_bytes, clip_data, clip_filepath)
                clip_size_bytes = len(clip_data)

            state_manager.save_resource(
                id=file_id,
                name=filename,
                resource_type="browserview",
                status=BROWSER_VIEW_STATUS["TRANSIENT"],
                mime="image/jpeg",
                storage_path=f"{BROWSER_VIEWS_DIR_NAME}/{filename}",
                metadata={
                    "kind": "browser_frame",
                    "session_id": eff_session_id,
                    "url": res_view.get("url", ""),
                    "action": res_view.get("action", ""),
                    "http_status": res_view.get("http_status"),
                    "http_status_text": res_view.get("http_status_text") or "",
                    "timestamp": ts_now,
                    "retained": False,
                    "clip_filename": clip_filename or None,
                    "clip_mime": BROWSER_CLIP_MIME if clip_filename else None,
                    "clip_duration_ms": clip_duration_ms if clip_filename else 0,
                    "clip_size_bytes": clip_size_bytes if clip_filename else 0,
                },
            )
        except Exception as e:
            print(f"[BrowserMonitor] Échec persistance capture ({uid}/{chat_id}): {e}")
            file_id = ""
            clip_filename = ""
        try:
            await asyncio.to_thread(enforce_transient_clip_budget, uid, chat_id)
        except Exception as e:
            print(f"[BrowserMonitor] Échec purge quota clips ({uid}/{chat_id}): {e}")

    if not show_browser_hud:
        return

    wm = get_window_manager()
    if open_tab_on_deploy and file_id:
        await wm.open_tab(
            uid,
            chat_id,
            "echo-browser",
            {"sessionId": eff_session_id, "frameId": file_id, "auto": True},
        )
    safe_cid = sanitize_echo_id(chat_id)
    await wm.broadcast_browser_frame(
        uid,
        chat_id,
        {
            "session_id": eff_session_id,
            "file_id": file_id,
            "filename": filename,
            "url": res_view.get("url", ""),
            "action": res_view.get("action", ""),
            "http_status": res_view.get("http_status"),
            "http_status_text": res_view.get("http_status_text") or "",
            "timestamp": ts_now,
            "status": BROWSER_VIEW_STATUS["TRANSIENT"],
            "retained": False,
            "image_url": (
                f"/echo/v1/cognition/{safe_cid}/browser-frames/{file_id}/image"
                if file_id
                else ""
            ),
            "clip_url": (
                f"/echo/v1/cognition/{safe_cid}/browser-frames/{file_id}/clip"
                if clip_filename
                else ""
            ),
            "clip_duration_ms": clip_duration_ms if clip_filename else 0,
            "clip_size_bytes": clip_size_bytes if clip_filename else 0,
        },
    )


class BrowserToolDispatcher:
    """
    Dispatcher spécialisé pour le Navigateur Autonome Playwright.
    Peut être instancié directement par navigation_engine_tool.py OU automatiquement
    par EchoSubagentController lors d'un resume/kill/close à froid sur un thread_web_*.
    """

    def __init__(
        self,
        sid: str,
        browser_session_id: str,
        chat_id: str,
        user_id: str,
        events: Any,
        http_timeout: int = 120,
        browser_mode: str = "desktop",
        show_browser_hud: bool = True,
        use_multimodal_vision: bool = True,
        vision_grid_step: int = DEFAULT_VISION_GRID_STEP,
        prune_threshold: int = PRUNE_CONTENT_THRESHOLD,
        analyze_page_cb: Any = None,
        archive_page_cb: Any = None,
    ):
        from echo_browser_lib import BROWSER_TOOLS_SCHEMA, EchoBrowserLib

        self.sid = sid
        self.browser_session_id = browser_session_id or sid
        self.chat_id = chat_id
        self.user_id = user_id
        self.events = events
        self.http_timeout = http_timeout
        self.browser_mode = browser_mode
        self.show_browser_hud = show_browser_hud
        self.use_vision = use_multimodal_vision
        self.vision_grid_step = vision_grid_step
        self.prune_threshold = prune_threshold
        self.analyze_page_cb = analyze_page_cb
        self.archive_page_cb = archive_page_cb

        self.browser = EchoBrowserLib(
            self.http_timeout,
            self.browser_session_id,
            self.user_id,
            vision_grid_step=self.vision_grid_step,
            chat_id=self.chat_id,
        )
        self.registry = self.browser.get_registry()
        self.vision_requested = False
        self.is_zooming = False
        self.zoom_attempts = 0
        self.last_view: Optional[dict] = None
        self._sem_acquired = False
        self._stop_streaming = asyncio.Event()
        self._proxy_task: Optional[asyncio.Task] = None
        self.last_url: str = ""
        self.last_http_status: Optional[int] = None
        self.last_http_status_text: str = ""
        self.navigation_statuses: List[dict] = []

        schema_extensions = [
            {
                "name": "action_analyze_page",
                "description": "Analyse intelligemment le contenu textuel complet de la page actuelle en tâche de fond sans saturer la mémoire de travail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "L'objectif de l'analyse.",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "action_archive_page",
                "description": "Archive silencieusement tout le texte de la page actuelle dans la mémoire à long terme (RAG).",
                "parameters": {"type": "object", "properties": {}},
            },
        ]
        self._fn_decls = list(BROWSER_TOOLS_SCHEMA) + schema_extensions

    def get_tool_declarations(self) -> List[dict]:
        return self._fn_decls

    def _record_http_telemetry(self, payload: Optional[dict]) -> None:
        if not isinstance(payload, dict):
            return
        url = str(payload.get("url") or "").strip()
        code = payload.get("http_status")
        text = str(payload.get("http_status_text") or "").strip()
        if url:
            self.last_url = url
        if code is not None:
            self.last_http_status = code
            self.last_http_status_text = text
            entry = {
                "url": url or self.last_url,
                "http_status": code,
                "http_status_text": text,
            }
            if not self.navigation_statuses or self.navigation_statuses[-1] != entry:
                self.navigation_statuses.append(entry)
                if len(self.navigation_statuses) > 25:
                    self.navigation_statuses = self.navigation_statuses[-25:]

    def get_telemetry_metadata(self) -> dict:
        if (
            not self.last_url
            and self.last_http_status is None
            and not self.navigation_statuses
        ):
            return {}
        return {
            "final_url": self.last_url,
            "http_status": self.last_http_status,
            "http_status_text": self.last_http_status_text,
            "navigation_statuses": list(self.navigation_statuses),
        }

    def get_thinking_override(
        self, is_slice_exhausted: bool, last_fn_name: str
    ) -> Optional[str]:
        return (
            "high"
            if (
                is_slice_exhausted
                or last_fn_name in ("action", "action_inspect_page")
            )
            else "low"
        )

    async def acquire_browser_slot(self) -> None:
        if not self._sem_acquired:
            sem = get_global_browser_semaphore()
            await sem.acquire()
            self._sem_acquired = True

    def release_browser_slot(self) -> None:
        if self._sem_acquired:
            self._sem_acquired = False
            try:
                get_global_browser_semaphore().release()
            except ValueError:
                pass

    async def ensure_engine_ready(
        self, start_url: Optional[str] = None, open_tab: bool = False
    ) -> Optional[dict]:
        from echo_browser_lib import (
            cleanup_expired_browserviews,
            req_to_browser,
        )

        res = await req_to_browser(
            self.http_timeout,
            "/action",
            {"session_id": self.browser_session_id, "action": "ping"},
            self.user_id,
        )
        if (
            res.get("message") == "RESTART_REQUIRED"
            or res.get("error_type") == "SESSION_NOT_FOUND"
        ):
            if self.events:
                await self.events.status(
                    "🌐 Moteur de navigation initialisation...", done=False
                )
            start_res = await req_to_browser(
                self.http_timeout,
                "/start_session",
                {
                    "session_id": self.browser_session_id,
                    "chat_id": self.chat_id or self.browser_session_id,
                    "idle_timeout": 900,
                    "mode": self.browser_mode,
                },
                self.user_id,
            )
            if start_res.get("status") != "success":
                return None

        await cleanup_expired_browserviews(self.user_id, self.chat_id, min_age_sec=300.0)
        nav_res: Optional[dict] = None
        if start_url:
            if self.events:
                await self.events.status(
                    f"🌐 Navigation vers {start_url}...", done=False
                )
            nav_res = await self.browser.action_browser_control(
                command="navigate", value=start_url
            )
        if self.events:
            await self.events.status(
                f"📸 [{self.sid}] Extraction de la carte DOM...", done=False
            )
        res_view = await self.browser.highlight()
        if isinstance(res_view, dict) and isinstance(nav_res, dict):
            if nav_res.get("status") == "error":
                res_view["nav_error"] = nav_res.get("message")
            if nav_res.get("warning"):
                res_view["nav_warning"] = nav_res.get("warning")
            if res_view.get("http_status") is None and nav_res.get("http_status") is not None:
                res_view["http_status"] = nav_res.get("http_status")
                res_view["http_status_text"] = nav_res.get("http_status_text") or ""
        if isinstance(res_view, dict) and res_view.get("http_status") is not None:
            self._record_http_telemetry(res_view)
        else:
            self._record_http_telemetry(nav_res)
            self._record_http_telemetry(res_view)
        self.last_view = res_view
        await deploy_browser_navigation_monitor(
            res_view,
            self.chat_id,
            self.user_id,
            show_browser_hud=self.show_browser_hud,
            browser_session_id=self.browser_session_id,
            open_tab_on_deploy=open_tab,
        )
        return res_view

    async def on_before_slice(self) -> None:
        from echo_browser_lib import iter_screencast_stream
        from echo_window_manager import get_window_manager

        await self.acquire_browser_slot()
        self._stop_streaming.clear()

        if self.show_browser_hud:
            async def _stream_proxy() -> None:
                wm = get_window_manager()
                while not self._stop_streaming.is_set():
                    if not wm.has_browser_live_subscribers(
                        self.user_id, self.chat_id
                    ):
                        await asyncio.sleep(0.5)
                        continue
                    try:
                        async for ev in iter_screencast_stream(
                            self.browser_session_id,
                            self.user_id,
                            self._stop_streaming,
                        ):
                            if (
                                self._stop_streaming.is_set()
                                or not wm.has_browser_live_subscribers(
                                    self.user_id, self.chat_id
                                )
                            ):
                                break
                            ev_type = ev.get("type")
                            if ev_type == "frame":
                                await wm.broadcast_browser_live(
                                    self.user_id,
                                    self.chat_id,
                                    {
                                        "type": "live_frame",
                                        "session_id": self.browser_session_id,
                                        "frame_id": ev.get("frame_id", 0),
                                        "b64": ev.get("b64", ""),
                                        "url": ev.get("url", ""),
                                        "vp_w": ev.get("vp_w", 1280),
                                        "vp_h": ev.get("vp_h", 800),
                                        "ts": ev.get("ts", time.time()),
                                    },
                                )
                            elif ev_type == "cursor":
                                await wm.broadcast_browser_live(
                                    self.user_id,
                                    self.chat_id,
                                    {
                                        "type": "live_cursor",
                                        "session_id": self.browser_session_id,
                                        "x": ev.get("x"),
                                        "y": ev.get("y"),
                                        "vp_w": ev.get("vp_w", 1280),
                                        "vp_h": ev.get("vp_h", 800),
                                        "clicking": bool(ev.get("clicking")),
                                        "ts": ev.get("ts", time.time()),
                                    },
                                )
                            elif ev_type == "eof":
                                break
                    except asyncio.CancelledError:
                        break
                    except Exception:
                        pass
                    if not self._stop_streaming.is_set():
                        await asyncio.sleep(0.2)

            self._proxy_task = asyncio.create_task(_stream_proxy())

    async def on_after_slice(self, final_status: str) -> None:
        from echo_window_manager import get_window_manager

        self._stop_streaming.set()
        if self._proxy_task:
            self._proxy_task.cancel()
            self._proxy_task = None
        if self.show_browser_hud:
            try:
                wm = get_window_manager()
                if wm.has_browser_live_subscribers(self.user_id, self.chat_id):
                    await wm.broadcast_browser_live(
                        self.user_id,
                        self.chat_id,
                        {
                            "type": "live_state",
                            "session_id": self.browser_session_id,
                            "active": False,
                        },
                    )
            except Exception:
                pass
        self.release_browser_slot()

    async def on_cleanup(self) -> None:
        from echo_browser_lib import cleanup_expired_browserviews

        await self.on_after_slice("killed")
        try:
            await self.browser.reset_session()
        except Exception:
            pass
        try:
            await cleanup_expired_browserviews(
                self.user_id,
                self.chat_id,
                force_discard_sids={self.browser_session_id, self.sid},
            )
        except Exception:
            pass

    async def execute_tool_calls(
        self,
        tool_calls: List[dict],
        remaining_slice_calls: int,
        current_model_key: str,
        history: List[dict],
    ) -> tuple[List[dict], int, int, Optional[str], str]:
        allowed_calls = tool_calls[:ECHO_MAX_PARALLEL_TOOLS_PER_TURN]
        excess_calls = tool_calls[ECHO_MAX_PARALLEL_TOOLS_PER_TURN:]

        response_parts: List[dict] = []
        last_view = None
        last_fn_name = "action"
        err_streak = 0

        from echo_window_manager import get_window_manager

        wm = get_window_manager()
        if self.show_browser_hud and wm.has_browser_live_subscribers(
            self.user_id, self.chat_id
        ):
            try:
                await wm.broadcast_browser_live(
                    self.user_id,
                    self.chat_id,
                    {
                        "type": "live_state",
                        "session_id": self.browser_session_id,
                        "active": True,
                    },
                )
            except Exception:
                pass

        try:
            await self.browser.start_screencast()
        except Exception:
            pass

        screencast_stopped = False

        async def _stop_and_collect_clip() -> dict:
            nonlocal screencast_stopped
            if screencast_stopped:
                return {}
            screencast_stopped = True
            sc_res: Any = {}
            try:
                sc_res = await self.browser.stop_screencast()
            except Exception:
                sc_res = {}
            if self.show_browser_hud and wm.has_browser_live_subscribers(
                self.user_id, self.chat_id
            ):
                try:
                    await wm.broadcast_browser_live(
                        self.user_id,
                        self.chat_id,
                        {
                            "type": "live_state",
                            "session_id": self.browser_session_id,
                            "active": False,
                        },
                    )
                except Exception:
                    pass
            return sc_res if isinstance(sc_res, dict) else {}

        def _attach_clip_fields(target_view: dict, sc_res: dict) -> None:
            if not isinstance(target_view, dict) or not isinstance(sc_res, dict):
                return
            for k in ("clip_b64", "clip_mime", "clip_duration_ms", "clip_size_bytes"):
                if sc_res.get(k):
                    target_view[k] = sc_res[k]

        def _propagate_http_fields(target_view: Any, src_res: Any) -> None:
            if isinstance(target_view, dict) and isinstance(src_res, dict):
                if target_view.get("http_status") is None and src_res.get("http_status") is not None:
                    target_view["http_status"] = src_res.get("http_status")
                    target_view["http_status_text"] = src_res.get("http_status_text") or ""
                if not target_view.get("url") and src_res.get("url"):
                    target_view["url"] = src_res.get("url")

        try:
            for index, fc in enumerate(allowed_calls):
                fn_name = fc.get("name", "")
                fn_args = dict(fc.get("args", {}) or {})
                fn_id = fc.get("id")
                is_last_tool = index == len(allowed_calls) - 1

                if fn_args.get("action_type") in ("download", "save_target"):
                    fn_args["download_file_id"] = f"DL_{uuid.uuid4().hex[:8]}"

                if self.events:
                    await self.events.status(
                        f"🖱️ [{self.sid}] {fn_name}({fn_args})", done=False
                    )

                _resp: dict = {"status": "error", "message": "Outil inconnu."}
                is_err = False

                if fn_name in self.registry:
                    try:
                        action_res = await self.registry[fn_name](**fn_args)
                        if action_res.get("_trigger_vision"):
                            self.vision_requested = True
                            grid = action_res.get("grid", False)
                            zb = action_res.get("zoom_box")
                            if action_res.get("is_zoom"):
                                self.is_zooming = True
                                self.zoom_attempts += 1
                                cx, cy = action_res.get("cx"), action_res.get("cy")
                                _resp = {
                                    "status": "success",
                                    "message": (
                                        f"Zoom appliqué (Essai {self.zoom_attempts}/3). L'image jointe est rognée sur la zone cible. "
                                        f"Un VISEUR ROUGE a été dessiné au centre absolu (X={cx}, Y={cy}). "
                                        f"Si le point central de ce viseur est STRICTEMENT sur la cible, le Sous-Agent DOIT utiliser "
                                        f"action_interact_dom(x={cx}, y={cy}, action_type='click')."
                                    ),
                                }
                            elif action_res.get("is_zoom_out"):
                                self.is_zooming = False
                                self.zoom_attempts = 0
                                _resp = {
                                    "status": "success",
                                    "message": "Zoom annulé. Retour à la vue globale.",
                                }
                            else:
                                _resp = {
                                    "status": "success",
                                    "message": "Capture d'écran demandée. Elle est jointe à ce message.",
                                }

                            if grid:
                                last_view = await self.browser.vision_capture(
                                    grid=True, zoom_box=zb
                                )
                            else:
                                last_view = await self.browser.highlight()

                            hud_view = (
                                await self.browser.highlight() if grid else last_view
                            )
                            _propagate_http_fields(last_view, action_res)
                            if hud_view is not last_view:
                                _propagate_http_fields(hud_view, action_res)
                            if is_last_tool:
                                sc_res = await _stop_and_collect_clip()
                                _attach_clip_fields(hud_view, sc_res)
                            await deploy_browser_navigation_monitor(
                                hud_view,
                                self.chat_id,
                                self.user_id,
                                show_browser_hud=self.show_browser_hud,
                                browser_session_id=self.browser_session_id,
                            )
                            self._record_http_telemetry(hud_view or last_view)
                            last_fn_name = fn_name
                        elif action_res.get("status") != "error":
                            if fn_name in (
                                "action_interact_dom",
                                "action_interact_a11y",
                                "action_browser_control",
                            ):
                                self.is_zooming = False
                                self.zoom_attempts = 0
                            if is_last_tool:
                                if (
                                    "metadata" in action_res
                                    and "screenshot_b64" in action_res
                                ):
                                    last_view = action_res
                                else:
                                    last_view = await self.browser.highlight()
                                _propagate_http_fields(last_view, action_res)
                                sc_res = await _stop_and_collect_clip()
                                _attach_clip_fields(last_view, sc_res)
                                await deploy_browser_navigation_monitor(
                                    last_view,
                                    self.chat_id,
                                    self.user_id,
                                    show_browser_hud=self.show_browser_hud,
                                    browser_session_id=self.browser_session_id,
                                )
                                _resp = {
                                    "status": "success",
                                    "dom_map": last_view.get("metadata", []),
                                }
                            else:
                                _resp = {
                                    "status": "success",
                                    "message": "Action exécutée avec succès.",
                                }
                            primary = last_view if (is_last_tool and isinstance(last_view, dict)) else {}
                            url_val = primary.get("url") or action_res.get("url")
                            if url_val:
                                _resp["url"] = url_val
                            http_code = primary.get("http_status")
                            http_text = primary.get("http_status_text") or ""
                            if http_code is None:
                                http_code = action_res.get("http_status")
                                http_text = action_res.get("http_status_text") or ""
                            if http_code is not None:
                                _resp["http_status"] = http_code
                                _resp["http_status_text"] = http_text
                            warn_val = primary.get("warning") or action_res.get("warning")
                            if warn_val:
                                _resp["warning"] = warn_val
                            self._record_http_telemetry(last_view if is_last_tool else action_res)
                            self._record_http_telemetry(action_res)
                            if action_res.get("status") == "downloading":
                                _resp["message"] = (
                                    f"📥 Téléchargement asynchrone démarré. Identifiant : {fn_args.get('download_file_id')}."
                                )
                            for k_extra in ("search_result", "content", "value"):
                                if k_extra in action_res:
                                    _resp[k_extra] = action_res[k_extra]
                            last_fn_name = fn_name
                        else:
                            _resp = {
                                "status": "error",
                                "error": action_res.get("message"),
                            }
                            is_err = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                elif fn_name == "action_analyze_page" and self.analyze_page_cb:
                    try:
                        current_url_resp = await self.browser.action_inspect_page(
                            target="url"
                        )
                        raw_res = await self.analyze_page_cb(
                            current_url_resp.get("url", ""),
                            fn_args.get("query", ""),
                        )
                        _resp = {"status": "success", "result": raw_res}
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                elif fn_name == "action_archive_page" and self.archive_page_cb:
                    try:
                        current_url_resp = await self.browser.action_inspect_page(
                            target="url"
                        )
                        raw_res = await self.archive_page_cb(
                            current_url_resp.get("url", "")
                        )
                        _resp = {"status": "success", "result": raw_res}
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                else:
                    is_err = True

                if is_err:
                    err_streak += 1
                else:
                    err_streak = 0

                _fr: dict = {"name": fn_name, "response": _resp}
                if fn_id:
                    _fr["id"] = fn_id
                response_parts.append({"functionResponse": _fr})
        finally:
            if not screencast_stopped:
                await _stop_and_collect_clip()

        for fc in excess_calls:
            fn_id = fc.get("id")
            _efr: dict = {
                "name": fc.get("name", "unknown"),
                "response": {
                    "status": "exceeded_parallel_per_turn_limit",
                    "message": (
                        f"Limite par tour atteinte ({ECHO_MAX_PARALLEL_TOOLS_PER_TURN} appels simultanés max par tour). "
                        "Réémettre au tour suivant si nécessaire."
                    ),
                },
            }
            if fn_id:
                _efr["id"] = fn_id
            response_parts.append({"functionResponse": _efr})

        if last_view:
            self.last_view = last_view
        if (
            last_view
            and self.use_vision
            and self.vision_requested
            and last_view.get("screenshot_b64")
        ):
            target_fr = None
            for rp in reversed(response_parts):
                if (
                    "functionResponse" in rp
                    and rp["functionResponse"].get("name") == last_fn_name
                ):
                    target_fr = rp["functionResponse"]
                    break
            if not target_fr and response_parts:
                target_fr = response_parts[-1]["functionResponse"]
            if target_fr:
                target_fr["response"]["message"] = (
                    "Capture d'écran disponible. Le Sous-Agent DOIT analyser la capture."
                )
                response_parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": last_view["screenshot_b64"],
                    }
                })
            self.vision_requested = False

        prune_browser_heavy_context(
            history, self.prune_threshold, self.is_zooming
        )
        return (
            response_parts,
            len(allowed_calls),
            err_streak,
            None,
            last_fn_name,
        )


class EchoAgentRunner:
    """
    Moteur d'exécution unifié par tranches supervisées pour tous les sous-agents ECHO.
    Gère :
    - Dépilage inter-tours de la file FIFO (Orchestrateur <REQUETE_ORCHESTRATEUR> & Humain <REQUETE_UTILISATEUR>)
    - Tranches coopératives par tours d'outils (8 tours / 120s) avec synthèse d'étape en mode NONE -> status='suspended_checkpoint'
    - Disjoncteur PARE (3 erreurs consécutives) -> status='suspended_pare_alert'
    - Pause manuelle gracieuse (request_subagent_suspend) -> status='suspended'
    - Interruption immédiate (kill / CancelledError) avec scellement synthétique des functionCall ouverts -> status='killed'
    """

    @staticmethod
    def _extract_thought_sig(parts_list: List[dict]) -> Optional[str]:
        return next(
            (
                p["thoughtSignature"]
                for p in parts_list
                if isinstance(p, dict) and "thoughtSignature" in p
            ),
            None,
        )

    @staticmethod
    def _seal_dangling_function_calls(
        state: Any,
        sid: str,
        chat_id: str,
        role_id: str,
        history: List[dict],
        reason_msg: str = "Exécution interrompue par ordre de supervision (kill/cancel).",
    ) -> bool:
        """
        Si le dernier message dans history est un message 'model' contenant des functionCall
        sans functionResponse subséquente, insère et persiste immédiatement les functionResponse
        synthétiques avec leurs IDs Gemini exacts pour prévenir tout HTTP 400 ultérieur.
        """
        if not history:
            return False
        last_msg = history[-1]
        if last_msg.get("role") != "model":
            return False
        open_fcs = [
            p["functionCall"]
            for p in (last_msg.get("parts") or [])
            if isinstance(p, dict) and "functionCall" in p
        ]
        if not open_fcs:
            return False

        seal_parts: List[dict] = []
        for fc in open_fcs:
            _fr: dict = {
                "name": fc.get("name", "unknown"),
                "response": {
                    "status": "interrupted_by_kill",
                    "message": reason_msg,
                },
            }
            if fc.get("id"):
                _fr["id"] = fc["id"]
            seal_parts.append({"functionResponse": _fr})

        history.append({"role": "user", "parts": seal_parts})
        state.save_thread_step(
            sid, chat_id, role_id, len(history) - 1, "user", seal_parts
        )
        return True

    @classmethod
    async def run_slice(
        cls,
        *,
        sid: str,
        chat_id: str,
        user_id: str,
        role_id: str,
        history: List[dict],
        state: Any,
        dispatcher: ToolDispatcher,
        final_system: Optional[str],
        current_model_key: str,
        slice_max_turns: Optional[int] = None,
        slice_max_calls: int = ECHO_SUBAGENT_CHECKPOINT_TURNS,
        slice_timeout_sec: int = ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
        autonomous_slice: bool = False,
        events: Any = None,
        key_switch_threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        from echo_core import (
            estimate_token_size,
            smart_truncate_history,
            wrap_tool_output,
        )
        from echo_gemini_client import EchoGeminiClient

        eff_max_turns = int(
            slice_max_turns if slice_max_turns is not None else slice_max_calls
        )
        meta = get_subagent_meta(state, sid) or {"sub_sid": sid}
        total_turns_used = int(meta.get("total_turns_used") or 0)
        total_calls_used = int(meta.get("total_calls_used") or 0)
        slice_turns_used = 0
        consecutive_errors = 0
        checkpoint_reason: Optional[str] = None
        is_cascaded_fallback = False
        slice_started_at = time.monotonic()
        last_fn_name = "action"

        clear_subagent_suspend(chat_id, sid)
        pop_kill_flag(chat_id, sid)

        update_subagent_meta_fields(
            state,
            sid,
            status="running",
            model_key=current_model_key,
            slice_turns_used=0,
            slice_turns_limit=eff_max_turns,
            slice_timeout_sec=slice_timeout_sec,
            slice_calls_used=0,
            slice_calls_limit=eff_max_turns,
            total_turns_used=total_turns_used,
            total_calls_used=total_calls_used,
            consecutive_errors=0,
            checkpoint_reason=None,
        )

        def _merge_telemetry(status_dict: dict) -> dict:
            tele = dispatcher.get_telemetry_metadata()
            if isinstance(tele, dict):
                for k, v in tele.items():
                    status_dict.setdefault(k, v)
            return status_dict

        await dispatcher.on_before_slice()
        final_status_for_hook = "idle"
        try:
            while True:
                # 0. Dépilage inter-tours de la file FIFO (steer Orchestrateur & messages Humains)
                queued_items = pop_all_queued_messages(state, sid)
                if queued_items:
                    injected_parts: List[dict] = []
                    for q_item in queued_items:
                        q_text = str(q_item.get("content") or "").strip()
                        if not q_text:
                            continue
                        if q_item.get("origin") == "orchestrator":
                            injected_parts.append({
                                "text": wrap_orchestrator_turn(q_text)
                            })
                        else:
                            injected_parts.append({
                                "text": wrap_human_turn(q_text)
                            })
                    if injected_parts:
                        history.append({"role": "user", "parts": injected_parts})
                        state.save_thread_step(
                            sid,
                            chat_id,
                            role_id,
                            len(history) - 1,
                            "user",
                            injected_parts,
                        )
                        if events:
                            await events.status(
                                f"💬 [{sid}] {len(injected_parts)} directive(s) en file intégrée(s) en vol..."
                            )

                # 1. Vérification de pause manuelle (suspend)
                if is_subagent_suspend_requested(chat_id, sid):
                    clear_subagent_suspend(chat_id, sid)
                    checkpoint_reason = "manual_suspend"
                    final_status_for_hook = "suspended"
                    update_subagent_meta_fields(
                        state,
                        sid,
                        status="suspended",
                        slice_turns_used=slice_turns_used,
                        slice_turns_limit=eff_max_turns,
                        slice_timeout_sec=slice_timeout_sec,
                        slice_calls_used=slice_turns_used,
                        slice_calls_limit=eff_max_turns,
                        total_turns_used=total_turns_used,
                        total_calls_used=total_calls_used,
                        consecutive_errors=consecutive_errors,
                        checkpoint_reason=checkpoint_reason,
                    )
                    if events:
                        await events.status(
                            f"⏸️ [{sid}] Mis en pause ({slice_turns_used}/{eff_max_turns} tours, {total_calls_used} outils).",
                            done=True,
                        )
                    return wrap_tool_output(
                        text=(
                            f"⏸️ Sous-agent `{sid}` mis en pause sur demande "
                            f"({slice_turns_used}/{eff_max_turns} tours dans la tranche, "
                            f"{total_calls_used} outils cumulés)."
                        ),
                        status={
                            "status": "suspended",
                            "sid": sid,
                            "sub_sid": sid,
                            "checkpoint_reason": checkpoint_reason,
                            "slice_turns_used": slice_turns_used,
                            "slice_turns_limit": eff_max_turns,
                            "slice_timeout_sec": slice_timeout_sec,
                            "total_turns_used": total_turns_used,
                            "total_calls_used": total_calls_used,
                            "slice_calls_used": slice_turns_used,
                            "slice_calls_limit": eff_max_turns,
                            "model_used": current_model_key,
                        },
                    )

                # 2. Vérification d'échéance de tranche (quota de tours d'outils ou timeout de tranche)
                elapsed_sec = time.monotonic() - slice_started_at
                is_turns_exhausted = slice_turns_used >= eff_max_turns
                is_timeout_exhausted = (
                    slice_timeout_sec is not None
                    and slice_timeout_sec > 0
                    and slice_turns_used > 0
                    and elapsed_sec >= slice_timeout_sec
                )
                is_slice_exhausted = is_turns_exhausted or is_timeout_exhausted
                if is_slice_exhausted and checkpoint_reason is None:
                    checkpoint_reason = (
                        "timeout" if is_timeout_exhausted else "turns_limit"
                    )

                fn_decls = dispatcher.get_tool_declarations()

                # 3. Défense passive : Troncature de l'historique
                sys_tokens = estimate_token_size(final_system) if final_system else 0
                current_size = estimate_token_size(history) + sys_tokens
                if current_size > ECHO_MAX_CONTEXT_SIZE * CONTEXT_TRUNCATE_THRESHOLD:
                    keep_idx = 1 if role_id == "navigator" else 0
                    while (
                        current_size
                        > ECHO_MAX_CONTEXT_SIZE * CONTEXT_TRUNCATE_THRESHOLD
                        and len(history) > 3
                    ):
                        removed = smart_truncate_history(history, keep_idx)
                        if not removed:
                            break
                        current_size -= removed

                # 4. Construction du payload Gemini
                override_think = dispatcher.get_thinking_override(
                    is_slice_exhausted, last_fn_name
                )
                gen_cfg = get_generation_config(
                    current_model_key,
                    override_thinking=override_think,
                    is_fallback_cascade=is_cascaded_fallback,
                )
                payload: dict = {
                    "contents": history,
                    "generationConfig": gen_cfg,
                }
                if final_system:
                    payload["systemInstruction"] = {
                        "parts": [{"text": final_system}]
                    }
                if fn_decls:
                    payload["tools"] = [{"functionDeclarations": fn_decls}]
                    payload["tool_config"] = {
                        "functionCallingConfig": {
                            "mode": "NONE" if is_slice_exhausted else "AUTO"
                        }
                    }

                # 5. Appel LLM via EchoGeminiClient.call_cascade
                if events:
                    if eff_max_turns == 0:
                        pre_status_msg = f"🧠 [{sid}] Délibération directe..."
                    elif is_slice_exhausted:
                        pre_status_msg = (
                            f"📝 [{sid}] Synthèse d'étape ({slice_turns_used}/{eff_max_turns} tours, {total_calls_used} outils)..."
                        )
                    else:
                        pre_status_msg = (
                            f"🧠 [{sid}] Raisonnement en cours (Tour {slice_turns_used + 1}/{eff_max_turns})..."
                        )
                    await events.status(pre_status_msg, done=False)
                try:
                    data, model_used, _ = await EchoGeminiClient.call_cascade(
                        target_model_key=current_model_key,
                        payload=payload,
                        user_id=user_id,
                        metadata=__metadata__,
                        events=events,
                        threshold=key_switch_threshold,
                        max_retries=max_retries,
                        timeout=ECHO_LLM_CALL_TIMEOUT,
                        include_thoughts=False,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    final_status_for_hook = "error"
                    update_subagent_status(state, sid, "error")
                    return wrap_tool_output(
                        text=f"❌ Erreur API dans le sous-agent [{sid}] : {str(e)}",
                        status={
                            "status": "error",
                            "sid": sid,
                            "sub_sid": sid,
                            "message": str(e),
                        },
                    )

                if not data:
                    final_status_for_hook = "error"
                    update_subagent_status(state, sid, "error")
                    return wrap_tool_output(
                        text=f"❌ Cascade épuisée pour le sous-agent [{sid}].",
                        status={
                            "status": "error",
                            "sid": sid,
                            "sub_sid": sid,
                            "message": "cascade exhausted",
                        },
                    )

                if model_used and model_used != current_model_key:
                    current_model_key = model_used
                    is_cascaded_fallback = True
                    update_subagent_meta_fields(
                        state, sid, model_key=current_model_key
                    )

                candidates = data.get("candidates", [])
                if not candidates or not candidates[0].get("content"):
                    final_status_for_hook = "error"
                    update_subagent_status(state, sid, "error")
                    return wrap_tool_output(
                        text=f"❌ Réponse vide du modèle pour le sous-agent [{sid}].",
                        status={"status": "error", "sid": sid, "sub_sid": sid},
                    )

                raw_parts = candidates[0]["content"].get("parts", [])
                tools_raw = [p for p in raw_parts if "functionCall" in p]
                text_raw = [
                    p
                    for p in raw_parts
                    if "text" in p and not p.get("thought")
                ]
                real_fc = [p["functionCall"] for p in tools_raw]
                text = "".join(p.get("text", "") for p in text_raw).strip()
                step_idx = len(history)

                # CAS A : Question de clarification (QUESTION:)
                if not real_fc and text:
                    q_match = re.search(
                        r"QUESTION:\s*(.+?)$", text, re.MULTILINE
                    )
                    if q_match:
                        question = q_match.group(1).strip()
                        progress = text[: text.rfind("QUESTION:")].strip()
                        model_raw = raw_parts if raw_parts else [{"text": text}]
                        history.append({"role": "model", "parts": model_raw})
                        state.save_thread_step(
                            sid,
                            chat_id,
                            role_id,
                            step_idx,
                            "model",
                            model_raw,
                            cls._extract_thought_sig(model_raw),
                        )
                        if peek_human_queue(state, sid):
                            continue
                        final_status_for_hook = "pending_question"
                        update_subagent_meta_fields(
                            state,
                            sid,
                            status="pending_question",
                            checkpoint_reason="question",
                            slice_turns_used=slice_turns_used,
                            slice_turns_limit=eff_max_turns,
                            slice_timeout_sec=slice_timeout_sec,
                            slice_calls_used=slice_turns_used,
                            slice_calls_limit=eff_max_turns,
                            total_turns_used=total_turns_used,
                            total_calls_used=total_calls_used,
                            last_output_preview=question[:1000],
                        )
                        return wrap_tool_output(
                            text=f"⚠️ Sous-agent [{sid}] en attente de clarification : {question}",
                            status={
                                "status": "pending_question",
                                "sid": sid,
                                "sub_sid": sid,
                                "question": question,
                                "progress": progress or "(en cours)",
                                "slice_turns_used": slice_turns_used,
                                "slice_turns_limit": eff_max_turns,
                                "slice_timeout_sec": slice_timeout_sec,
                                "total_turns_used": total_turns_used,
                                "total_calls_used": total_calls_used,
                                "slice_calls_used": slice_turns_used,
                                "slice_calls_limit": eff_max_turns,
                            },
                        )

                # CAS B : Réponse textuelle sans appel d'outil (fin de tâche ou synthèse de checkpoint)
                if not real_fc:
                    model_raw = (
                        raw_parts if raw_parts else [{"text": "(Réponse vide)"}]
                    )
                    history.append({"role": "model", "parts": model_raw})
                    state.save_thread_step(
                        sid,
                        chat_id,
                        role_id,
                        step_idx,
                        "model",
                        model_raw,
                        cls._extract_thought_sig(model_raw),
                    )

                    # Auto-relaunch sur saturation navigateur
                    if (
                        role_id == "navigator"
                        and "[SATURATION_CONTEXTE]" in text
                        and not is_slice_exhausted
                    ):
                        synthesis = text.replace(
                            "[SATURATION_CONTEXTE]", ""
                        ).strip()
                        first_parts = history[0].get("parts", [])
                        first_msg = (
                            {"role": "user", "parts": [first_parts[0]]}
                            if first_parts
                            else history[0]
                        )
                        history.clear()
                        history.append(first_msg)
                        resume_parts = [
                            {
                                "text": (
                                    f"Reprise de session interne après saturation.\n"
                                    f"Synthèse des recherches précédentes :\n{synthesis}\n\n"
                                    "Le Sous-Agent DOIT poursuivre la mission sans répéter les mêmes actions."
                                )
                            }
                        ]
                        history.append({"role": "user", "parts": resume_parts})
                        state.save_thread_step(
                            sid,
                            chat_id,
                            role_id,
                            len(history) - 1,
                            "user",
                            resume_parts,
                        )
                        continue

                    if peek_human_queue(state, sid) and not is_slice_exhausted:
                        continue

                    final_text = text or "(Réponse vide)"
                    if is_slice_exhausted and not autonomous_slice:
                        final_status_for_hook = "suspended_checkpoint"
                        eff_reason = checkpoint_reason or "turns_limit"
                        update_subagent_meta_fields(
                            state,
                            sid,
                            status="suspended_checkpoint",
                            checkpoint_reason=eff_reason,
                            slice_turns_used=slice_turns_used,
                            slice_turns_limit=eff_max_turns,
                            slice_timeout_sec=slice_timeout_sec,
                            slice_calls_used=slice_turns_used,
                            slice_calls_limit=eff_max_turns,
                            total_turns_used=total_turns_used,
                            total_calls_used=total_calls_used,
                            consecutive_errors=consecutive_errors,
                            last_output_preview=final_text[:1000],
                        )
                        if events:
                            await events.status(
                                f"⏸️ [{sid}] Point de contrôle atteint ({slice_turns_used}/{eff_max_turns} tours, {total_calls_used} outils cumulés).",
                                done=True,
                            )
                        header = (
                            f"⏸️ **[POINT DE CONTRÔLE SOUS-AGENT `{sid}` — Tranche {slice_turns_used}/{eff_max_turns} tours d'outils "
                            f"({total_calls_used} outils cumulés) | Raison : {eff_reason}]**\n"
                            f"Utiliser `control_subagents(action='resume', target_ids=['{sid}'])` pour accorder une nouvelle tranche (+{ECHO_SUBAGENT_CHECKPOINT_TURNS} tours), "
                            f"`action='steer'` pour réorienter, ou `action='close'` si le résultat suffit.\n\n"
                        )
                        return wrap_tool_output(
                            text=header + final_text,
                            status=_merge_telemetry({
                                "status": "suspended_checkpoint",
                                "sid": sid,
                                "sub_sid": sid,
                                "warning": "checkpoint_reached",
                                "checkpoint_reason": eff_reason,
                                "slice_turns_used": slice_turns_used,
                                "slice_turns_limit": eff_max_turns,
                                "slice_timeout_sec": slice_timeout_sec,
                                "total_turns_used": total_turns_used,
                                "total_calls_used": total_calls_used,
                                "turns_remaining": 0,
                                "slice_calls_used": slice_turns_used,
                                "slice_calls_limit": eff_max_turns,
                                "calls_remaining": 0,
                                "model_used": current_model_key,
                            }),
                        )

                    # Fin nominale de la mission (avant épuisement de la tranche, ou en mode autonomous_slice)
                    is_real_exhaustion = is_slice_exhausted and eff_max_turns > 0
                    target_done_status = (
                        "idle" if is_real_exhaustion else "completed"
                    )
                    final_status_for_hook = target_done_status
                    update_subagent_meta_fields(
                        state,
                        sid,
                        status=target_done_status,
                        checkpoint_reason=None,
                        slice_turns_used=slice_turns_used,
                        slice_turns_limit=eff_max_turns,
                        slice_timeout_sec=slice_timeout_sec,
                        slice_calls_used=slice_turns_used,
                        slice_calls_limit=eff_max_turns,
                        total_turns_used=total_turns_used,
                        total_calls_used=total_calls_used,
                        consecutive_errors=0,
                        last_output_preview=final_text[:1000],
                    )
                    if events:
                        await events.status(
                            f"✅ [{sid}] Terminé ({slice_turns_used}/{eff_max_turns} tours, {total_calls_used} outils au total).",
                            done=True,
                        )
                    turns_rem = max(0, eff_max_turns - slice_turns_used)
                    status_payload: dict = {
                        "status": "success",
                        "subagent_status": target_done_status,
                        "sid": sid,
                        "sub_sid": sid,
                        "slice_turns_used": slice_turns_used,
                        "slice_turns_limit": eff_max_turns,
                        "slice_timeout_sec": slice_timeout_sec,
                        "total_turns_used": total_turns_used,
                        "total_calls_used": total_calls_used,
                        "turns_remaining": turns_rem,
                        "slice_calls_used": slice_turns_used,
                        "slice_calls_limit": eff_max_turns,
                        "calls_remaining": turns_rem,
                        "model_used": current_model_key,
                    }
                    if is_real_exhaustion:
                        status_payload["warning"] = "budget_exhausted"
                    return wrap_tool_output(
                        text=final_text, status=_merge_telemetry(status_payload)
                    )

                # CAS C : Garde-fou hallucination — functionCall émis alors que mode="NONE" (tranche épuisée)
                if is_slice_exhausted and real_fc:
                    if not text:
                        cancel_parts = []
                        for fc in real_fc:
                            _cfr: dict = {
                                "name": fc.get("name", "unknown"),
                                "response": {
                                    "status": "cancelled_budget_exhausted",
                                    "message": (
                                        f"APPEL REFUSÉ : Quota de la tranche ({slice_turns_used}/{eff_max_turns} tours) atteint. "
                                        "Rédiger immédiatement la synthèse d'étape complète en texte pur."
                                    ),
                                },
                            }
                            if fc.get("id"):
                                _cfr["id"] = fc["id"]
                            cancel_parts.append({"functionResponse": _cfr})
                        rec_payload: dict = {
                            "contents": history
                            + [
                                {"role": "model", "parts": raw_parts},
                                {"role": "user", "parts": cancel_parts},
                            ],
                            "generationConfig": get_generation_config(
                                current_model_key,
                                is_fallback_cascade=is_cascaded_fallback,
                            ),
                        }
                        if final_system:
                            rec_payload["systemInstruction"] = {
                                "parts": [{"text": final_system}]
                            }
                        if fn_decls:
                            rec_payload["tools"] = [
                                {"functionDeclarations": fn_decls}
                            ]
                            rec_payload["tool_config"] = {
                                "functionCallingConfig": {"mode": "NONE"}
                            }
                        try:
                            rec_data, rec_model, _ = (
                                await EchoGeminiClient.call_cascade(
                                    target_model_key=current_model_key,
                                    payload=rec_payload,
                                    user_id=user_id,
                                    metadata=__metadata__,
                                    events=events,
                                    threshold=key_switch_threshold,
                                    max_retries=max_retries,
                                    timeout=ECHO_LLM_CALL_TIMEOUT,
                                    include_thoughts=False,
                                )
                            )
                            if rec_data:
                                rec_cands = rec_data.get("candidates", [])
                                if rec_cands and rec_cands[0].get("content"):
                                    rec_parts = rec_cands[0]["content"].get(
                                        "parts", []
                                    )
                                    text = "".join(
                                        p.get("text", "")
                                        for p in rec_parts
                                        if "text" in p and not p.get("thought")
                                    ).strip()
                                    if rec_model:
                                        current_model_key = rec_model
                        except Exception:
                            pass

                    final_text = (
                        text
                        or "(Synthèse d'étape — quota de la tranche supervisée atteint)"
                    )
                    clean_model_parts = [
                        p for p in raw_parts if "functionCall" not in p
                    ]
                    if not any(
                        "text" in p and not p.get("thought")
                        for p in clean_model_parts
                    ):
                        clean_model_parts.append({"text": final_text})
                    history.append({"role": "model", "parts": clean_model_parts})
                    state.save_thread_step(
                        sid,
                        chat_id,
                        role_id,
                        step_idx,
                        "model",
                        clean_model_parts,
                        cls._extract_thought_sig(clean_model_parts)
                        or cls._extract_thought_sig(raw_parts),
                    )
                    is_real_exhaustion = is_slice_exhausted and eff_max_turns > 0
                    eff_reason = checkpoint_reason or "turns_limit"
                    target_status = (
                        ("idle" if is_real_exhaustion else "completed")
                        if autonomous_slice
                        else "suspended_checkpoint"
                    )
                    final_status_for_hook = target_status
                    update_subagent_meta_fields(
                        state,
                        sid,
                        status=target_status,
                        checkpoint_reason=eff_reason,
                        slice_turns_used=slice_turns_used,
                        slice_turns_limit=eff_max_turns,
                        slice_timeout_sec=slice_timeout_sec,
                        slice_calls_used=slice_turns_used,
                        slice_calls_limit=eff_max_turns,
                        total_turns_used=total_turns_used,
                        total_calls_used=total_calls_used,
                        last_output_preview=final_text[:1000],
                    )
                    c_status_payload: dict = {
                        "status": (
                            "success"
                            if autonomous_slice
                            else "suspended_checkpoint"
                        ),
                        "sid": sid,
                        "sub_sid": sid,
                        "checkpoint_reason": eff_reason,
                        "slice_turns_used": slice_turns_used,
                        "slice_turns_limit": eff_max_turns,
                        "slice_timeout_sec": slice_timeout_sec,
                        "total_turns_used": total_turns_used,
                        "total_calls_used": total_calls_used,
                        "turns_remaining": 0,
                        "slice_calls_used": slice_turns_used,
                        "slice_calls_limit": eff_max_turns,
                        "calls_remaining": 0,
                        "model_used": current_model_key,
                    }
                    if not autonomous_slice:
                        c_status_payload["warning"] = "checkpoint_reached"
                    elif is_real_exhaustion:
                        c_status_payload["warning"] = "budget_exhausted"
                    return wrap_tool_output(
                        text=final_text,
                        status=_merge_telemetry(c_status_payload),
                    )

                # CAS D : Exécution des appels d'outils via le ToolDispatcher
                history.append({"role": "model", "parts": raw_parts})
                state.save_thread_step(
                    sid,
                    chat_id,
                    role_id,
                    step_idx,
                    "model",
                    raw_parts,
                    cls._extract_thought_sig(raw_parts),
                )

                remaining = max(0, eff_max_turns - slice_turns_used)
                (
                    response_parts,
                    consumed,
                    batch_err_streak,
                    escalated_model,
                    last_fn_name,
                ) = await dispatcher.execute_tool_calls(
                    real_fc, remaining, current_model_key, history
                )

                slice_turns_used += 1
                total_turns_used += 1
                total_calls_used += consumed
                if batch_err_streak > 0:
                    if batch_err_streak == consumed:
                        consecutive_errors += batch_err_streak
                    else:
                        consecutive_errors = batch_err_streak
                else:
                    consecutive_errors = 0

                if escalated_model and escalated_model != current_model_key:
                    current_model_key = escalated_model
                    is_cascaded_fallback = False

                update_subagent_meta_fields(
                    state,
                    sid,
                    model_key=current_model_key,
                    slice_turns_used=slice_turns_used,
                    slice_turns_limit=eff_max_turns,
                    slice_timeout_sec=slice_timeout_sec,
                    slice_calls_used=slice_turns_used,
                    slice_calls_limit=eff_max_turns,
                    total_turns_used=total_turns_used,
                    total_calls_used=total_calls_used,
                    consecutive_errors=consecutive_errors,
                )

                # Annoter la dernière functionResponse avec la télémétrie de tranche
                if response_parts:
                    last_fr = next(
                        (
                            rp["functionResponse"]
                            for rp in reversed(response_parts)
                            if "functionResponse" in rp
                        ),
                        None,
                    )
                    if last_fr and isinstance(last_fr.get("response"), dict):
                        will_exhaust_turns = slice_turns_used >= eff_max_turns
                        will_exhaust_timeout = (
                            slice_timeout_sec is not None
                            and slice_timeout_sec > 0
                            and (time.monotonic() - slice_started_at)
                            >= slice_timeout_sec
                        )
                        if will_exhaust_turns or will_exhaust_timeout:
                            if not autonomous_slice:
                                last_fr["response"]["_checkpoint_alert"] = (
                                    f"TRANCHE COOPÉRATIVE ATTEINTE ({slice_turns_used}/{eff_max_turns} tours d'outils, "
                                    f"{total_calls_used} outils exécutés au total). "
                                    "La mission n'est pas terminée si du travail reste à accomplir : "
                                    "le Modèle DOIT rédiger un rapport d'étape factuel (acquis, état courant, suite prévue) "
                                    "pour synchronisation avec l'Orchestrateur."
                                )
                            else:
                                last_fr["response"]["_budget_alert"] = (
                                    f"QUOTA DE TRANCHE ATTEINT ({slice_turns_used}/{eff_max_turns} tours, "
                                    f"cumul total {total_calls_used} outils). Plus aucun tour d'outil n'est disponible dans cette passe. "
                                    "Le Modèle DOIT impérativement rédiger immédiatement sa synthèse complète et détaillée."
                                )
                        else:
                            last_fr["response"]["_slice_progress"] = (
                                f"Tranche : {slice_turns_used}/{eff_max_turns} tours utilisés "
                                f"({eff_max_turns - slice_turns_used} restants | outils cumulés : {total_calls_used})"
                            )

                if (
                    role_id == "navigator"
                    and estimate_token_size(history)
                    > ECHO_MAX_CONTEXT_SIZE * 0.40
                ):
                    response_parts.append({
                        "text": "<system_alert>SATURATION DU CONTEXTE ATTEINTE. Le Sous-Agent DOIT appliquer la Règle 10.</system_alert>"
                    })

                history.append({"role": "user", "parts": response_parts})
                state.save_thread_step(
                    sid,
                    chat_id,
                    role_id,
                    len(history) - 1,
                    "user",
                    response_parts,
                )

                # Disjoncteur PARE : 3 erreurs d'outils consécutives -> pause immédiate
                if consecutive_errors >= ECHO_SUBAGENT_MAX_CONSECUTIVE_ERRORS:
                    checkpoint_reason = "pare_3_errors"
                    final_status_for_hook = "suspended_pare_alert"
                    pare_msg = (
                        f"⚠️ **[ALERTE DISJONCTEUR PARE — Sous-agent `{sid}`]** : "
                        f"{consecutive_errors} erreurs d'outils consécutives détectées "
                        f"({slice_turns_used}/{eff_max_turns} tours sur la tranche, {total_calls_used} outils cumulés). "
                        f"Exécution suspendue (`suspended_pare_alert`). Utiliser `control_subagents(action='steer' | 'resume' | 'close', target_ids=['{sid}'])`."
                    )
                    update_subagent_meta_fields(
                        state,
                        sid,
                        status="suspended_pare_alert",
                        checkpoint_reason=checkpoint_reason,
                        slice_turns_used=slice_turns_used,
                        slice_turns_limit=eff_max_turns,
                        slice_timeout_sec=slice_timeout_sec,
                        slice_calls_used=slice_turns_used,
                        slice_calls_limit=eff_max_turns,
                        total_turns_used=total_turns_used,
                        total_calls_used=total_calls_used,
                        consecutive_errors=consecutive_errors,
                        last_output_preview=pare_msg[:1000],
                    )
                    if events:
                        await events.status(
                            f"⚠️ [{sid}] Disjoncteur PARE (3 erreurs consécutives) — mise en pause.",
                            done=True,
                        )
                    return wrap_tool_output(
                        text=pare_msg,
                        status={
                            "status": "suspended_pare_alert",
                            "sid": sid,
                            "sub_sid": sid,
                            "warning": "pare_circuit_breaker",
                            "checkpoint_reason": checkpoint_reason,
                            "consecutive_errors": consecutive_errors,
                            "slice_turns_used": slice_turns_used,
                            "slice_turns_limit": eff_max_turns,
                            "slice_timeout_sec": slice_timeout_sec,
                            "total_turns_used": total_turns_used,
                            "total_calls_used": total_calls_used,
                            "slice_calls_used": slice_turns_used,
                            "slice_calls_limit": eff_max_turns,
                            "model_used": current_model_key,
                        },
                    )

        except asyncio.CancelledError:
            was_killed = pop_kill_flag(chat_id, sid)
            cls._seal_dangling_function_calls(
                state,
                sid,
                chat_id,
                role_id,
                history,
                reason_msg=(
                    "Exécution interrompue par ordre de supervision (kill)."
                    if was_killed
                    else "Exécution interrompue suite à l'annulation du flux parent."
                ),
            )
            target_cancel_status = "killed" if was_killed else "suspended"
            final_status_for_hook = target_cancel_status
            update_subagent_meta_fields(
                state,
                sid,
                status=target_cancel_status,
                checkpoint_reason="killed" if was_killed else "parent_cancelled",
                slice_turns_used=slice_turns_used,
                slice_turns_limit=eff_max_turns,
                slice_timeout_sec=slice_timeout_sec,
                slice_calls_used=slice_turns_used,
                slice_calls_limit=eff_max_turns,
                total_turns_used=total_turns_used,
                total_calls_used=total_calls_used,
            )
            if was_killed:
                await dispatcher.on_cleanup()
                return wrap_tool_output(
                    text=f"🛑 Sous-agent `{sid}` interrompu (`killed`) et historique scellé.",
                    status={
                        "status": "killed",
                        "sid": sid,
                        "sub_sid": sid,
                        "slice_turns_used": slice_turns_used,
                        "slice_turns_limit": eff_max_turns,
                        "slice_timeout_sec": slice_timeout_sec,
                        "total_turns_used": total_turns_used,
                        "total_calls_used": total_calls_used,
                        "slice_calls_used": slice_turns_used,
                        "slice_calls_limit": eff_max_turns,
                    },
                )
            raise
        finally:
            await dispatcher.on_after_slice(final_status_for_hook)


class EchoSubagentController:
    """Façade exécutive unifiée pour le lancement, la reprise et les 8 actions de pilotage des sous-agents."""

    @classmethod
    async def run_or_resume(
        cls,
        *,
        user_id: str,
        chat_id: str,
        task: Optional[str] = None,
        directive: Optional[str] = None,
        origin: Literal["orchestrator", "human"] = "orchestrator",
        system_prompt: Optional[str] = None,
        skill_id: Optional[str] = None,
        sub_sid: Optional[str] = None,
        context_distillate: str = "",
        target_model_key: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
        slice_max_turns: Optional[int] = None,
        slice_max_calls: Optional[int] = None,
        slice_timeout_sec: Optional[int] = None,
        autonomous_slice: bool = False,
        engine: Optional[str] = None,
        group_id: Optional[str] = None,
        role_name: Optional[str] = None,
        role_order: Optional[int] = None,
        start_url: Optional[str] = None,
        custom_dispatcher: Optional[ToolDispatcher] = None,
        key_switch_threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        from echo_aec import EchoAEC
        from echo_core import clamp_model, wrap_tool_output
        from echo_events import EchoEvents
        from echo_prompts import ECHO_FOUNDATION_KERNEL, SYS_NAV_BROWSER
        from echo_skills import get_skill_content, parse_skill_metadata
        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        events = EchoEvents(
            __event_emitter__, __event_call__, user_id=user_id, chat_id=chat_id
        )

        prefix = "thread_web" if engine == "navigator" else "dlg"
        sid = validate_or_create_sub_sid(
            sub_sid=sub_sid, prefix=prefix, skill_id=skill_id
        )
        existing_meta = get_subagent_meta(state, sid) or {}

        # Court-circuit sur les threads purement statiques de synthèse/critique lors d'un resume de groupe
        eff_engine = (
            engine
            or existing_meta.get("engine")
            or ("navigator" if sid.startswith("thread_web_") else "delegate")
        )
        if eff_engine in (
            "council_synthesis",
            "supervisor_critic",
            "supervisor_consolidator",
        ):
            return wrap_tool_output(
                text=f"ℹ️ Thread de synthèse `{sid}` ({eff_engine}) non exécutable en tranche.",
                status={"status": "idle", "sid": sid, "sub_sid": sid},
            )

        eng_cfg = ECHO_SUBAGENT_ENGINES_REGISTRY.get(eff_engine, {})
        eff_max_turns = int(
            slice_max_turns
            if slice_max_turns is not None
            else (
                slice_max_calls
                if slice_max_calls is not None
                else eng_cfg.get(
                    "default_slice_limit", ECHO_SUBAGENT_CHECKPOINT_TURNS
                )
            )
        )
        eff_timeout_sec = int(
            slice_timeout_sec
            if slice_timeout_sec is not None
            else eng_cfg.get(
                "default_slice_timeout_sec",
                ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
            )
        )

        eff_skill_id = skill_id or existing_meta.get("skill_id")
        eff_role_name = role_name or existing_meta.get("role_name")
        eff_allowed_tools = (
            allowed_tools
            if allowed_tools is not None
            else existing_meta.get("allowed_tools")
        )
        eff_group_id = group_id or existing_meta.get("group_id")

        requested_model = (
            target_model_key
            or existing_meta.get("model_key")
            or "MODEL_APOLLO"
        )
        current_model_key = clamp_model(
            requested_model, __metadata__ or {}, user_id
        )

        # Résolution du system_prompt
        final_system: Optional[str] = None
        if eff_engine == "navigator":
            eff_role_name = eff_role_name or "Navigateur Web"
            role_id = "navigator"
        else:
            role_id = "delegate"
            if (
                existing_meta.get("final_system")
                and not system_prompt
                and not skill_id
            ):
                final_system = existing_meta["final_system"]
            elif existing_meta.get("system_prompt") and not system_prompt and not skill_id:
                final_system = existing_meta["system_prompt"]
            else:
                base_system = system_prompt
                if eff_skill_id and eff_skill_id != "navigator":
                    skill_content = get_skill_content(user_id, eff_skill_id)
                    if not skill_content:
                        return wrap_tool_output(
                            text=f"❌ Skill '{eff_skill_id}' introuvable.",
                            status={
                                "status": "error",
                                "message": f"Skill '{eff_skill_id}' not found",
                            },
                        )
                    skill_meta = parse_skill_metadata(skill_content)
                    eff_role_name = eff_role_name or skill_meta.get(
                        "name", eff_skill_id
                    )
                    base_system = (
                        f"{skill_content}\n\n{base_system}"
                        if base_system
                        else skill_content
                    )
                elif not base_system:
                    base_system = ECHO_FOUNDATION_KERNEL

                current_time = datetime.datetime.now().isoformat()
                if (
                    "<directives_globales>" not in base_system
                    and "<context_temporel>" not in base_system
                ):
                    if "PRAF" in base_system:
                        appendix = (
                            f"\n\n<directives_globales>\n"
                            f"- Ancrage Temporel : {current_time}\n"
                            f"</directives_globales>"
                        )
                    else:
                        appendix = (
                            f"\n\n<directives_globales>\n"
                            f"- Ancrage Temporel : {current_time}\n"
                            f"- Rigueur Factuelle : Le Modèle DOIT asseoir son raisonnement sur des certitudes.\n"
                            f"</directives_globales>"
                        )
                    base_system = f"{base_system}{appendix}"

                final_system = base_system + build_subagent_system_appendix(
                    sub_sid=sid,
                    slice_max_turns=eff_max_turns,
                    slice_timeout_sec=eff_timeout_sec,
                    autonomous_slice=autonomous_slice,
                )
                if context_distillate:
                    final_system += f"\n\n## CONTEXTE DU CHAT PRINCIPAL (distillé)\n{context_distillate}"

        eff_role_name = eff_role_name or sid
        effective_task = (
            task if task is not None else (directive if directive is not None else "")
        ).strip()

        chat_sem = get_chat_subagent_semaphore(chat_id)
        async with chat_sem:
            async with get_subagent_lock(chat_id, sid):
                try:
                    curr_task = asyncio.current_task()
                except RuntimeError:
                    curr_task = None
                mark_subagent_running(chat_id, sid, True)
                register_subagent_task(chat_id, sid, curr_task)
                completed_normally = False
                dispatcher: Any = custom_dispatcher
                try:
                    history = state.get_thread_history(sid)

                    # Construction du ToolDispatcher (Standard ou Navigateur Web)
                    nav_res_view: Optional[dict] = None
                    is_initial_nav = False
                    if eff_engine == "navigator":
                        if dispatcher is None:
                            parent_sub_sid = (
                                ECHO_SUBAGENT_CONTEXT.get() or {}
                            ).get("sub_sid")
                            browser_sid = (
                                existing_meta.get("browser_session_id")
                                or parent_sub_sid
                                or sid
                            )
                            dispatcher = BrowserToolDispatcher(
                                sid=sid,
                                browser_session_id=browser_sid,
                                chat_id=chat_id,
                                user_id=user_id,
                                events=events,
                                browser_mode=existing_meta.get(
                                    "browser_mode", "desktop"
                                ),
                                show_browser_hud=existing_meta.get(
                                    "show_browser_hud", True
                                ),
                                use_multimodal_vision=existing_meta.get(
                                    "use_multimodal_vision", True
                                ),
                                vision_grid_step=existing_meta.get(
                                    "vision_grid_step", DEFAULT_VISION_GRID_STEP
                                ),
                                prune_threshold=existing_meta.get(
                                    "prune_threshold", PRUNE_CONTENT_THRESHOLD
                                ),
                            )
                        if events:
                            await events.status(
                                f"📡 Agent Navigateur [{dispatcher.browser_session_id[:12]}]: Prise de contrôle...",
                                done=False,
                            )
                        await dispatcher.acquire_browser_slot()
                        try:
                            nav_res_view = await dispatcher.ensure_engine_ready(
                                start_url=start_url,
                                open_tab=(len(history) == 0),
                            )
                        except Exception:
                            dispatcher.release_browser_slot()
                            raise
                        if nav_res_view is None:
                            dispatcher.release_browser_slot()
                            update_subagent_status(state, sid, "error")
                            return wrap_tool_output(
                                text="❌ Navigateur indisponible.",
                                status={
                                    "status": "error",
                                    "sid": sid,
                                    "sub_sid": sid,
                                },
                            )
                        if not history:
                            is_initial_nav = True
                            nav_prompt = SYS_NAV_BROWSER.format(
                                task_objective=effective_task
                                or "Explorer la page courante."
                            )
                            init_parts = [
                                {"text": nav_prompt},
                                {"text": format_browser_dom_view(nav_res_view)},
                            ]
                            history.append(
                                {"role": "user", "parts": init_parts}
                            )
                            state.save_thread_step(
                                sid,
                                chat_id,
                                "navigator",
                                0,
                                "user",
                                init_parts,
                            )
                            effective_task = ""
                    elif dispatcher is None:
                        sub_tools, body_specs = resolve_delegate_tools(
                            chat_id=chat_id,
                            allowed_tools=eff_allowed_tools,
                            __metadata__=__metadata__,
                            __user__=__user__ or {"id": user_id},
                        )
                        dispatcher = StandardToolDispatcher(
                            sid=sid,
                            chat_id=chat_id,
                            user_id=user_id,
                            state=state,
                            sub_tools=sub_tools,
                            body_tools_specs=body_specs,
                            events=events,
                            __user__=__user__ or {"id": user_id},
                            __metadata__=__metadata__
                            or {"chat_id": chat_id},
                            __event_emitter__=__event_emitter__,
                            __event_call__=__event_call__,
                        )

                    # Scellement préventif si l'historique existant avait une functionCall orpheline
                    EchoAgentRunner._seal_dangling_function_calls(
                        state,
                        sid,
                        chat_id,
                        role_id,
                        history,
                        reason_msg="Reprise de session après interruption : appel précédent clos.",
                    )

                    attach_resume_dom = (
                        eff_engine == "navigator"
                        and not is_initial_nav
                        and nav_res_view is not None
                        and bool(start_url or nav_res_view.get("nav_error"))
                    )

                    # Injection de la tâche/directive ou d'une instruction de continuation de tranche
                    if effective_task:
                        wrapped = (
                            wrap_human_turn(effective_task)
                            if origin == "human"
                            else wrap_orchestrator_turn(effective_task)
                        )
                        user_parts = [{"text": wrapped}]
                        if attach_resume_dom and nav_res_view is not None:
                            user_parts.append(
                                {"text": format_browser_dom_view(nav_res_view)}
                            )
                        history.append({"role": "user", "parts": user_parts})
                        state.save_thread_step(
                            sid,
                            chat_id,
                            role_id,
                            len(history) - 1,
                            "user",
                            user_parts,
                        )
                    elif (
                        history
                        and history[-1].get("role") == "model"
                        and not peek_human_queue(state, sid)
                    ):
                        cont_msg = (
                            "Point de contrôle validé. Nouvelle tranche d'exécution accordée : "
                            "poursuis la mission à partir de l'état courant sans répéter les étapes déjà accomplies."
                        )
                        wrapped = (
                            wrap_human_turn(cont_msg)
                            if origin == "human"
                            else wrap_orchestrator_turn(cont_msg)
                        )
                        user_parts = [{"text": wrapped}]
                        if attach_resume_dom and nav_res_view is not None:
                            user_parts.append(
                                {"text": format_browser_dom_view(nav_res_view)}
                            )
                        history.append({"role": "user", "parts": user_parts})
                        state.save_thread_step(
                            sid,
                            chat_id,
                            role_id,
                            len(history) - 1,
                            "user",
                            user_parts,
                        )

                    nav_meta_fields = (
                        {
                            "browser_session_id": dispatcher.browser_session_id,
                            "browser_mode": dispatcher.browser_mode,
                            "show_browser_hud": dispatcher.show_browser_hud,
                            "use_multimodal_vision": dispatcher.use_vision,
                            "vision_grid_step": dispatcher.vision_grid_step,
                            "prune_threshold": dispatcher.prune_threshold,
                        }
                        if eff_engine == "navigator"
                        and isinstance(dispatcher, BrowserToolDispatcher)
                        else {}
                    )

                    save_subagent_meta(
                        state,
                        sid,
                        {
                            **existing_meta,
                            "sub_sid": sid,
                            "chat_id": chat_id,
                            "engine": eff_engine,
                            "role_name": eff_role_name,
                            "skill_id": eff_skill_id,
                            "model_key": current_model_key,
                            "group_id": eff_group_id,
                            **(
                                {"role_order": role_order}
                                if role_order is not None
                                else {}
                            ),
                            "allowed_tools": eff_allowed_tools,
                            "system_prompt": final_system,
                            "final_system": final_system,
                            "slice_turns_limit": eff_max_turns,
                            "slice_timeout_sec": eff_timeout_sec,
                            "slice_calls_limit": eff_max_turns,
                            "status": "running",
                            **nav_meta_fields,
                        },
                    )

                    res = await EchoAgentRunner.run_slice(
                        sid=sid,
                        chat_id=chat_id,
                        user_id=user_id,
                        role_id=role_id,
                        history=history,
                        state=state,
                        dispatcher=dispatcher,
                        final_system=final_system,
                        current_model_key=current_model_key,
                        slice_max_turns=eff_max_turns,
                        slice_timeout_sec=eff_timeout_sec,
                        autonomous_slice=autonomous_slice,
                        events=events,
                        key_switch_threshold=key_switch_threshold,
                        max_retries=max_retries,
                        __metadata__=__metadata__,
                    )
                    completed_normally = True

                    if origin == "human":
                        reply_text = (
                            str(res.get("text") or "")
                            if isinstance(res, dict)
                            else str(res or "")
                        )
                        try:
                            EchoAEC.record_event(
                                state,
                                f"subagent_human_interaction:{sid}",
                                summary=(
                                    f"Intervention directe de l'utilisateur sur le sous-agent [{sid}] ({eff_role_name}). "
                                    f"Instruction : {(effective_task or f'Reprise de tranche (+{eff_max_turns} tours)')[:240]} | "
                                    f"Réponse : {reply_text[:400]}"
                                ),
                            )
                        except Exception:
                            pass
                    return res
                finally:
                    if eff_engine == "navigator" and isinstance(
                        dispatcher, BrowserToolDispatcher
                    ):
                        dispatcher.release_browser_slot()
                    if not completed_normally:
                        meta_after = get_subagent_meta(state, sid) or {}
                        if meta_after.get("status") == "running":
                            try:
                                update_subagent_status(state, sid, "error")
                            except Exception:
                                pass
                    mark_subagent_running(chat_id, sid, False)

    @classmethod
    async def execute_control_action(
        cls,
        *,
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
        user_id: str,
        chat_id: str,
        target_ids: Optional[List[str]] = None,
        filter_type: str = "all",
        directive: Optional[str] = None,
        target_model_key: Optional[str] = None,
        key_switch_threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        from echo_browser_lib import (
            EchoBrowserLib,
            cleanup_expired_browserviews,
        )
        from echo_core import clamp_model, wrap_tool_output
        from echo_events import EchoEvents
        from echo_gemini_client import EchoGeminiClient
        from echo_state_manager import EchoStateManager

        if not chat_id:
            return wrap_tool_output(
                text="❌ Aucun chat_id détecté.",
                status={"status": "error", "action": action},
            )

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        events = EchoEvents(
            __event_emitter__, __event_call__, user_id=user_id, chat_id=chat_id
        )
        resolved_sids = resolve_target_sub_sids(
            state, chat_id, target_ids=target_ids, filter_type=filter_type
        )

        # 1. ACTION : STATUS
        if action == "status":
            if not resolved_sids:
                return wrap_tool_output(
                    text="ℹ️ Aucune session de sous-agent active pour ces critères.",
                    status={
                        "status": "success",
                        "action": "status",
                        "sessions": [],
                        "affected_sids": [],
                    },
                )
            sessions_info = []
            md = "### 🤖 ÉTAT DES SOUS-AGENTS\n\n"
            for sid in resolved_sids:
                meta = get_subagent_meta(state, sid) or {"sub_sid": sid}
                st = (
                    "running"
                    if is_subagent_running(chat_id, sid)
                    else (meta.get("status") or "idle")
                )
                q_len = len(peek_human_queue(state, sid))
                s_used = int(
                    meta.get("slice_turns_used")
                    if meta.get("slice_turns_used") is not None
                    else (meta.get("slice_calls_used") or 0)
                )
                s_lim = int(
                    meta.get("slice_turns_limit")
                    if meta.get("slice_turns_limit") is not None
                    else (
                        meta.get("slice_calls_limit")
                        if meta.get("slice_calls_limit") is not None
                        else ECHO_SUBAGENT_CHECKPOINT_TURNS
                    )
                )
                s_timeout = int(
                    meta.get("slice_timeout_sec")
                    if meta.get("slice_timeout_sec") is not None
                    else ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC
                )
                t_turns = int(meta.get("total_turns_used") or 0)
                t_calls = int(meta.get("total_calls_used") or 0)
                c_err = int(meta.get("consecutive_errors") or 0)
                c_reason = meta.get("checkpoint_reason")
                role = meta.get("role_name") or sid
                mk = meta.get("model_key") or "MODEL_APOLLO"
                sessions_info.append({
                    "sub_sid": sid,
                    "group_id": meta.get("group_id"),
                    "engine": meta.get("engine") or "delegate",
                    "role_name": role,
                    "model_key": mk,
                    "status": st,
                    "slice_turns_used": s_used,
                    "slice_turns_limit": s_lim,
                    "slice_timeout_sec": s_timeout,
                    "total_turns_used": t_turns,
                    "total_calls_used": t_calls,
                    "slice_calls_used": s_used,
                    "slice_calls_limit": s_lim,
                    "consecutive_errors": c_err,
                    "checkpoint_reason": c_reason,
                    "queued_count": q_len,
                })
                md += (
                    f"- **`{sid}`** ({role} | `{mk}`) — Statut : **`{st}`** "
                    f"| Tranche : `{s_used}/{s_lim}` tours (Outils cumulés : `{t_calls}`) "
                    f"| File FIFO : `{q_len}`"
                    + (f" | Raison : `{c_reason}`" if c_reason else "")
                    + "\n"
                )
            return wrap_tool_output(
                text=md,
                status={
                    "status": "success",
                    "action": "status",
                    "sessions": sessions_info,
                    "affected_sids": resolved_sids,
                },
            )

        if not resolved_sids:
            return wrap_tool_output(
                text=f"❌ Aucun sous-agent trouvé pour `target_ids={target_ids}` (`filter_type='{filter_type}'`).",
                status={
                    "status": "error",
                    "action": action,
                    "affected_sids": [],
                },
            )

        # 2. ACTION : COLLECT
        if action == "collect":
            collected = {}
            md = "### 📦 COLLECTE DES RAPPORTS DE SOUS-AGENTS\n\n"
            for sid in resolved_sids:
                meta = get_subagent_meta(state, sid) or {"sub_sid": sid}
                history = state.get_thread_history(sid)
                last_text = ""
                for step in reversed(history):
                    if step.get("role") == "model":
                        last_text = "".join(
                            p.get("text", "")
                            for p in (step.get("parts") or [])
                            if isinstance(p, dict)
                            and "text" in p
                            and not p.get("thought")
                        ).strip()
                        if last_text:
                            break
                if not last_text:
                    last_text = meta.get("last_output_preview") or "(Aucune production textuelle)"
                collected[sid] = {
                    "status": (
                        "running"
                        if is_subagent_running(chat_id, sid)
                        else (meta.get("status") or "idle")
                    ),
                    "slice_turns_used": int(
                        meta.get("slice_turns_used")
                        if meta.get("slice_turns_used") is not None
                        else (meta.get("slice_calls_used") or 0)
                    ),
                    "total_turns_used": int(meta.get("total_turns_used") or 0),
                    "slice_calls_used": int(
                        meta.get("slice_turns_used")
                        if meta.get("slice_turns_used") is not None
                        else (meta.get("slice_calls_used") or 0)
                    ),
                    "total_calls_used": int(meta.get("total_calls_used") or 0),
                    "output": last_text,
                }
                md += f"#### `{sid}` ({meta.get('role_name') or sid} — `{collected[sid]['status']}`)\n{last_text}\n\n---\n"
            return wrap_tool_output(
                text=md,
                status={
                    "status": "success",
                    "action": "collect",
                    "affected_sids": resolved_sids,
                    "collected": collected,
                },
            )

        # 3. ACTION : STEER
        if action == "steer":
            if not directive and not target_model_key:
                return wrap_tool_output(
                    text="❌ `directive` ou `target_model_key` requis pour `action='steer'`.",
                    status={"status": "error", "action": "steer"},
                )
            for sid in resolved_sids:
                if target_model_key:
                    clamped = clamp_model(
                        target_model_key, __metadata__ or {}, user_id
                    )
                    update_subagent_meta_fields(state, sid, model_key=clamped)
                if directive:
                    enqueue_subagent_message(
                        state,
                        sid,
                        directive,
                        user_id=user_id,
                        origin="orchestrator",
                    )
            return wrap_tool_output(
                text=f"✅ Directive `steer` enregistrée dans la file FIFO pour : `{', '.join(resolved_sids)}`.",
                status={
                    "status": "success",
                    "action": "steer",
                    "affected_sids": resolved_sids,
                },
            )

        # 4. ACTION : SUSPEND
        if action == "suspend":
            for sid in resolved_sids:
                if is_subagent_running(chat_id, sid):
                    request_subagent_suspend(chat_id, sid)
                else:
                    update_subagent_status(
                        state, sid, "suspended", checkpoint_reason="manual_suspend"
                    )
            return wrap_tool_output(
                text=f"⏸️ Pause demandée pour : `{', '.join(resolved_sids)}`.",
                status={
                    "status": "success",
                    "action": "suspend",
                    "affected_sids": resolved_sids,
                },
            )

        # 5. ACTION : KILL
        if action == "kill":
            for sid in resolved_sids:
                was_running = kill_subagent_task(chat_id, sid)
                if not was_running:
                    history = state.get_thread_history(sid)
                    meta = get_subagent_meta(state, sid) or {}
                    role_id = (
                        "navigator"
                        if (
                            sid.startswith("thread_web_")
                            or meta.get("engine") == "navigator"
                        )
                        else "delegate"
                    )
                    EchoAgentRunner._seal_dangling_function_calls(
                        state, sid, chat_id, role_id, history
                    )
                    update_subagent_status(
                        state, sid, "killed", checkpoint_reason="killed"
                    )
                    if role_id == "navigator":
                        try:
                            await EchoBrowserLib(
                                30, sid, user_id, chat_id=chat_id
                            ).reset_session()
                            await cleanup_expired_browserviews(
                                user_id, chat_id, force_discard_sids={sid}
                            )
                        except Exception:
                            pass
            return wrap_tool_output(
                text=f"🛑 Interruption (`kill`) exécutée pour : `{', '.join(resolved_sids)}`.",
                status={
                    "status": "success",
                    "action": "kill",
                    "affected_sids": resolved_sids,
                },
            )

        # 6. ACTION : CLOSE
        if action == "close":
            for sid in resolved_sids:
                meta = get_subagent_meta(state, sid) or {}
                is_nav = (
                    sid.startswith("thread_web_")
                    or meta.get("engine") == "navigator"
                )
                kill_subagent_task(chat_id, sid)
                mark_subagent_running(chat_id, sid, False)
                state.delete_thread(sid)
                state.save_session_setting(f"{SUBAGENT_META_PREFIX}{sid}", "")
                state.save_session_setting(f"{SUBAGENT_QUEUE_PREFIX}{sid}", "")
                if is_nav:
                    try:
                        await EchoBrowserLib(
                            30, sid, user_id, chat_id=chat_id
                        ).reset_session()
                        await cleanup_expired_browserviews(
                            user_id, chat_id, force_discard_sids={sid}
                        )
                    except Exception:
                        pass
            bump_cognition_seq(chat_id)
            return wrap_tool_output(
                text=f"✅ Session(s) fermée(s) et purgée(s) : `{', '.join(resolved_sids)}`.",
                status={
                    "status": "success",
                    "action": "close",
                    "affected_sids": resolved_sids,
                },
            )

        # 7. ACTION : SUMMARIZE
        if action == "summarize":
            summaries: dict[str, str] = {}
            md = "### 📝 RÉSUMÉ DES SESSIONS\n\n"
            for sid in resolved_sids:
                history = state.get_thread_history(sid)
                if not history:
                    summaries[sid] = "(Session vide)"
                    md += f"#### `{sid}`\n*(Session vide)*\n\n"
                    continue
                raw = ""
                for step in history:
                    role = step.get("role", "user").upper()
                    for part in step.get("parts", []):
                        if "text" in part and not part.get("thought"):
                            raw += f"{role}: {part['text'][:800]}\n---\n"
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            raw += f"{role} [OUTIL→]: {fc.get('name', '?')} args={str(fc.get('args', {}))[:200]}\n---\n"
                        elif "functionResponse" in part:
                            fr = part["functionResponse"]
                            resp = str(fr.get("response", {}))[:300]
                            raw += f"{role} [←OUTIL]: {fr.get('name', '?')} → {resp}\n---\n"
                distill_prompt = (
                    "Le Système DOIT résumer cette session d'agent ECHO en 4 sections :\n"
                    "1. **Tâche initiale**\n"
                    "2. **Outils utilisés**\n"
                    "3. **Résultats clés**\n"
                    "4. **Conclusion / État actuel**\n\n"
                    f"### HISTORIQUE\n{raw}"
                )
                data, _, _ = await EchoGeminiClient.call_cascade(
                    target_model_key="MODEL_APOLLO",
                    payload={
                        "contents": [
                            {"role": "user", "parts": [{"text": distill_prompt}]}
                        ],
                        "generationConfig": {
                            **get_generation_config(
                                "MODEL_APOLLO", override_thinking="medium"
                            ),
                            "maxOutputTokens": 8192,
                        },
                    },
                    user_id=user_id,
                    metadata=__metadata__,
                    events=events,
                    threshold=key_switch_threshold,
                    max_retries=max_retries,
                    include_thoughts=False,
                )
                s_text = ""
                if data and data.get("candidates"):
                    for p in (
                        data["candidates"][0]
                        .get("content", {})
                        .get("parts", [])
                    ):
                        if "text" in p:
                            s_text += p["text"]
                summaries[sid] = s_text or "❌ Échec du résumé."
                md += f"#### `{sid}`\n{summaries[sid]}\n\n---\n"
            return wrap_tool_output(
                text=md,
                status={
                    "status": "success",
                    "action": "summarize",
                    "affected_sids": resolved_sids,
                    "summaries": summaries,
                },
            )

        # 8. ACTION : RESUME (Relance d'une nouvelle tranche supervisée fraîche, en parallèle si multi-cibles)
        if action == "resume":
            if len(resolved_sids) == 1:
                return await cls.run_or_resume(
                    user_id=user_id,
                    chat_id=chat_id,
                    sub_sid=resolved_sids[0],
                    directive=directive,
                    origin="orchestrator",
                    target_model_key=target_model_key,
                    slice_max_turns=ECHO_SUBAGENT_CHECKPOINT_TURNS,
                    slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
                    autonomous_slice=False,
                    key_switch_threshold=key_switch_threshold,
                    max_retries=max_retries,
                    __user__=__user__,
                    __metadata__=__metadata__,
                    __event_emitter__=__event_emitter__,
                    __event_call__=__event_call__,
                )

            results = await asyncio.gather(
                *(
                    cls.run_or_resume(
                        user_id=user_id,
                        chat_id=chat_id,
                        sub_sid=sid,
                        directive=directive,
                        origin="orchestrator",
                        target_model_key=target_model_key,
                        slice_max_turns=ECHO_SUBAGENT_CHECKPOINT_TURNS,
                        slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
                        autonomous_slice=False,
                        key_switch_threshold=key_switch_threshold,
                        max_retries=max_retries,
                        __user__=__user__,
                        __metadata__=__metadata__,
                        __event_emitter__=__event_emitter__,
                        __event_call__=__event_call__,
                    )
                    for sid in resolved_sids
                ),
                return_exceptions=True,
            )
            md = f"### ▶️ REPRISE PARALLÈLE ({len(resolved_sids)} SOUS-AGENTS)\n\n"
            per_sid: dict[str, Any] = {}
            for sid, r in zip(resolved_sids, results):
                if isinstance(r, Exception):
                    per_sid[sid] = {"status": "error", "error": str(r)}
                    md += f"#### `{sid}` — ❌ Erreur\n{r}\n\n---\n"
                elif isinstance(r, dict):
                    per_sid[sid] = r.get("status", {})
                    md += f"#### `{sid}`\n{r.get('text', '')}\n\n---\n"
                else:
                    per_sid[sid] = {"status": "ok"}
                    md += f"#### `{sid}`\n{r}\n\n---\n"
            return wrap_tool_output(
                text=md,
                status={
                    "status": "success",
                    "action": "resume",
                    "affected_sids": resolved_sids,
                    "results": per_sid,
                },
            )

        return wrap_tool_output(
            text=f"❌ Action `{action}` non reconnue.",
            status={"status": "error", "action": action},
        )

    extract_response_parts_and_text = staticmethod(extract_response_parts_and_text)

    @classmethod
    async def run_cognitive_step(
        cls,
        *,
        user_id: str,
        chat_id: str,
        sub_sid: str,
        engine: str,
        role_name: str,
        target_model_key: str,
        user_prompt: str,
        display_user_prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        group_id: Optional[str] = None,
        role_order: Optional[int] = None,
        step_index: int = 0,
        override_thinking: str = "high",
        max_output_tokens: int = 16000,
        response_schema: Any = None,
        timeout: int = ECHO_LLM_CALL_TIMEOUT,
        events: Any = None,
        metadata: Optional[dict] = None,
        fallback_error: str = "❌ Cascade épuisée.",
    ) -> Tuple[str, str, Any]:
        """Exécute un appel cognitif unitaire avec persistance meta + thread_step et isolation des pensées."""
        from echo_gemini_client import EchoGeminiClient
        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        base_meta: Dict[str, Any] = {
            "sub_sid": sub_sid,
            "chat_id": chat_id,
            "engine": engine,
            "role_name": role_name,
            "model_key": target_model_key,
            "group_id": group_id,
            "status": "running",
        }
        if role_order is not None:
            base_meta["role_order"] = role_order
        save_subagent_meta(state, sub_sid, base_meta)
        mark_subagent_running(chat_id, sub_sid, True)

        state.save_thread_step(
            sub_sid,
            chat_id,
            engine,
            step_index,
            "user",
            [{"text": display_user_prompt or user_prompt}],
        )

        gen_cfg = {
            **get_generation_config(target_model_key, override_thinking=override_thinking),
            "maxOutputTokens": max_output_tokens,
        }
        payload: Dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": gen_cfg,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        res = None
        model_used = None
        try:
            res, model_used, _ = await EchoGeminiClient.call_cascade(
                target_model_key=target_model_key,
                payload=payload,
                user_id=user_id,
                metadata=(metadata or {}),
                events=events,
                timeout=timeout,
                include_thoughts=False,
                response_schema=response_schema,
            )
        finally:
            mark_subagent_running(chat_id, sub_sid, False)

        normalized_parts, clean_text = extract_response_parts_and_text(
            res, fallback_error=fallback_error
        )
        final_status = "error" if not res else "completed"
        eff_model = model_used or target_model_key

        state.save_thread_step(
            sub_sid,
            chat_id,
            engine,
            step_index + 1,
            "model",
            normalized_parts,
            EchoAgentRunner._extract_thought_sig(normalized_parts),
        )
        base_meta["model_key"] = eff_model
        base_meta["status"] = final_status
        save_subagent_meta(state, sub_sid, base_meta)

        parsed_obj = None
        if response_schema is not None and res:
            try:
                parsed_obj = response_schema.model_validate_json(clean_text.strip())
            except Exception:
                parsed_obj = None

        return clean_text, eff_model, parsed_obj

    @classmethod
    async def _run_dispatch_batch(
        cls,
        *,
        user_id: str,
        chat_id: str,
        sids: List[str],
        directive: str,
        origin: str = "human",
    ) -> None:
        """Exécute en parallèle la relance des sous-agents ciblés par dispatch_message."""
        await asyncio.gather(
            *(
                cls.run_or_resume(
                    user_id=user_id,
                    chat_id=chat_id,
                    sub_sid=sid,
                    directive=directive,
                    origin=origin,
                )
                for sid in sids
            ),
            return_exceptions=True,
        )

    @classmethod
    def dispatch_message(
        cls,
        *,
        user_id: str,
        chat_id: str,
        target_ids: List[str],
        directive: str,
        origin: str = "human",
    ) -> Dict[str, Any]:
        """Résout les cibles adressables (unitaire, liste ou groupe) et prépare file FIFO ou relance."""
        from echo_state_manager import EchoStateManager

        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        raw_targets = None if target_ids == ["all"] else target_ids
        resolved_sids = resolve_target_sub_sids(state, chat_id, raw_targets)
        if not resolved_sids:
            return {"error": "Aucun sous-agent cible trouvé.", "status_code": 404}

        addressable_sids: List[str] = []
        for sid in resolved_sids:
            meta = get_subagent_meta(state, sid) or {}
            eng = str(meta.get("engine") or "")
            reg_addr = ECHO_SUBAGENT_ENGINES_REGISTRY.get(eng, {}).get("is_addressable", True)
            if meta.get("is_addressable", reg_addr):
                addressable_sids.append(sid)

        if not addressable_sids:
            return {
                "error": "Ce thread de synthèse ou d'évaluation n'accepte pas de consigne interactive.",
                "status_code": 400,
            }

        queued_sids: List[str] = []
        started_sids: List[str] = []
        total_queued = 0
        for sid in addressable_sids:
            if is_subagent_running(chat_id, sid):
                q_res = enqueue_subagent_message(
                    state, sid, directive, user_id=user_id, origin=origin
                )
                q_len = len(q_res) if isinstance(q_res, (list, tuple)) else int(q_res or 0)
                queued_sids.append(sid)
                total_queued = max(total_queued, q_len)
            else:
                started_sids.append(sid)

        run_coro = (
            cls._run_dispatch_batch(
                user_id=user_id,
                chat_id=chat_id,
                sids=started_sids,
                directive=directive,
                origin=origin,
            )
            if started_sids
            else None
        )
        return {
            "status": (
                "queued"
                if not started_sids
                else ("multi" if len(addressable_sids) > 1 else "started")
            ),
            "sub_sid": addressable_sids[0],
            "affected_sids": addressable_sids,
            "started_sids": started_sids,
            "queued_sids": queued_sids,
            "queue_length": total_queued,
            "run_coro": run_coro,
        }


async def run_human_subagent_turn(
    user_id: str, chat_id: str, sub_sid: str, content: str
) -> dict:
    """Wrapper rétrocompatible exécutant un tour humain via EchoSubagentController.run_or_resume."""
    res = await EchoSubagentController.run_or_resume(
        user_id=user_id,
        chat_id=chat_id,
        sub_sid=sub_sid,
        directive=content,
        origin="human",
    )
    reply_text = (
        str(res.get("text") or "")
        if isinstance(res, dict)
        else str(res or "")
    )
    return {"status": "ok", "sub_sid": sub_sid, "reply": reply_text}
