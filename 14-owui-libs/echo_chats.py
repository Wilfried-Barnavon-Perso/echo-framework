# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Conversations & Runtime Inspector
author: Wilfried BARNAVON
version: 6.23.11.1
description: Découverte SQL projetée des conversations, résolution batch de leur état runtime et lecture paginée/assainie des transcripts.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.11.1: Création du module d'inspection des conversations, d'état runtime batch et de lecture paginée/assainie des transcripts.

from __future__ import annotations

import datetime
import json
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from echo_constants import ECHO_CODEX_MAX_READ_BYTES

ChatRuntimeStatus = Literal["running", "pending_approval", "queued", "error", "idle"]
ChatStatusFilter = Literal["all", "running", "pending_approval", "queued", "error", "idle"]
TranscriptOrder = Literal["newest_first", "oldest_first"]

VALID_STATUS_FILTERS = ("all", "running", "pending_approval", "queued", "error", "idle")
VALID_TRANSCRIPT_ORDERS = ("newest_first", "oldest_first")
MAX_CHAT_LIST_LIMIT = 50
MAX_TRANSCRIPT_LIMIT = 50

_DETAILS_TAG_RE = re.compile(
    r"<details\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>|</details\s*>",
    re.DOTALL | re.IGNORECASE,
)
_DETAILS_REASONING_RE = re.compile(
    r"\btype\s*=\s*(?:\"reasoning\"|'reasoning'|reasoning)(?:\s|>|/|$)",
    re.IGNORECASE,
)
_DETAILS_TOOL_CALLS_RE = re.compile(
    r"\btype\s*=\s*(?:\"tool_calls\"|'tool_calls'|tool_calls)(?:\s|>|/|$)",
    re.IGNORECASE,
)
_DETAILS_NAME_ATTR_RE = re.compile(
    r"\bname\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>/]+))",
    re.IGNORECASE,
)
_INLINE_THINK_RE = re.compile(
    r"<(think|thought|thinking)\b[^>]*>.*?</\1\s*>",
    re.DOTALL | re.IGNORECASE,
)
_DATA_BASE64_RE = re.compile(
    r"data:[a-zA-Z0-9/+.-]+;base64,[A-Za-z0-9+/=\s]+",
    re.IGNORECASE,
)


def _parse_iso_date_bound(date_str: Optional[str], end_of_day: bool = False) -> Tuple[Optional[int], Optional[str]]:
    """Convertit une date ISO YYYY-MM-DD en timestamp Unix UTC (secondes)."""
    if not date_str or not isinstance(date_str, str) or not date_str.strip():
        return None, None
    clean = date_str.strip()
    try:
        dt = datetime.date.fromisoformat(clean)
    except ValueError:
        return None, f"Format de date invalide ('{clean}'). Format attendu : YYYY-MM-DD."
    t = datetime.time(23, 59, 59) if end_of_day else datetime.time(0, 0, 0)
    combined = datetime.datetime.combine(dt, t, tzinfo=datetime.timezone.utc)
    return int(combined.timestamp()), None


def _format_epoch_iso(ts: Optional[int | float]) -> Optional[str]:
    """Formate un timestamp Unix (secondes, ms ou ns) en ISO 8601 UTC."""
    if ts is None:
        return None
    try:
        val = float(ts)
    except (TypeError, ValueError):
        return None
    if val <= 0:
        return None
    if val > 1e16:
        val /= 1e9
    elif val > 1e11:
        val /= 1e3
    return datetime.datetime.fromtimestamp(val, tz=datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_tag_id(tag_name: str) -> str:
    return tag_name.strip().replace(" ", "_").lower()


def _is_internal_meta(meta: Any) -> bool:
    return bool(isinstance(meta, dict) and meta.get("internal") is True)


def _get_running_subagents_counts(chat_ids: set[str]) -> Dict[str, int]:
    """Compte en O(1) mémoire les sous-agents actuellement en exécution par chat_id."""
    counts: Dict[str, int] = {}
    if not chat_ids:
        return counts
    try:
        from echo_subagent_hub import _RUNNING_SUBAGENTS
        for cid, _ in list(_RUNNING_SUBAGENTS):
            if cid in chat_ids:
                counts[cid] = counts.get(cid, 0) + 1
    except Exception:
        pass
    return counts


def _load_owui_deps() -> Dict[str, Any]:
    """Charge paresseusement les dépendances Open WebUI (préserve les tests hors conteneur)."""
    from sqlalchemy import or_, select, text
    from open_webui.internal.db import get_async_db_context
    from open_webui.models.automations import AutomationRun
    from open_webui.models.chat_messages import ChatMessage, ChatMessages
    from open_webui.models.chats import Chat, Chats
    from open_webui.models.folders import Folders
    from open_webui.tasks import get_response_streams_by_chat_id, has_active_tasks

    return {
        "or_": or_,
        "select": select,
        "text": text,
        "get_async_db_context": get_async_db_context,
        "AutomationRun": AutomationRun,
        "ChatMessage": ChatMessage,
        "ChatMessages": ChatMessages,
        "Chat": Chat,
        "Chats": Chats,
        "Folders": Folders,
        "get_response_streams_by_chat_id": get_response_streams_by_chat_id,
        "has_active_tasks": has_active_tasks,
    }


def sanitize_transcript_content(
    content: Any,
    include_reasoning: bool = False,
    include_tool_calls: bool = False,
) -> str:
    """Nettoie le contenu d'un message (pensées, blocs d'outils volumineux et URI Base64)."""
    if content is None:
        raw = ""
    elif isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                itype = item.get("type")
                if itype == "text" and isinstance(item.get("text"), str):
                    parts.append(item["text"])
                elif itype in ("image_url", "input_image"):
                    parts.append("[image jointe]")
        raw = "\n".join(p for p in parts if p)
    else:
        raw = str(content)

    if not raw:
        return ""

    raw = _DATA_BASE64_RE.sub("[donnée binaire base64 omise]", raw)
    if not include_reasoning:
        raw = _INLINE_THINK_RE.sub("", raw)

    if "<details" in raw.lower():
        out_parts: List[str] = []
        cursor = 0
        depth = 0
        root_start = -1
        root_open_tag = ""

        for match in _DETAILS_TAG_RE.finditer(raw):
            tag_txt = match.group(0)
            if tag_txt.lower().startswith("</details"):
                if depth <= 0:
                    continue
                depth -= 1
                if depth > 0:
                    continue
                out_parts.append(raw[cursor:root_start])
                full_block = raw[root_start:match.end()]
                if _DETAILS_REASONING_RE.search(root_open_tag):
                    if include_reasoning:
                        out_parts.append(full_block)
                elif _DETAILS_TOOL_CALLS_RE.search(root_open_tag):
                    if include_tool_calls:
                        out_parts.append(full_block)
                    else:
                        m_name = _DETAILS_NAME_ATTR_RE.search(root_open_tag)
                        t_name = (m_name.group(1) or m_name.group(2) or m_name.group(3)) if m_name else "inconnu"
                        out_parts.append(f"[Outil: {t_name}]")
                else:
                    out_parts.append(full_block)
                cursor = match.end()
            else:
                if tag_txt.rstrip().endswith("/>"):
                    continue
                if depth == 0:
                    root_start = match.start()
                    root_open_tag = tag_txt
                depth += 1

        if cursor > 0:
            out_parts.append(raw[cursor:])
            raw = "".join(out_parts)

    return re.sub(r"\n{3,}", "\n\n", raw).strip()


def extract_chat_branch(chat_payload: Any, current_message_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """Remonte la lignée causale active (root -> feuille) avec garde anti-cycle."""
    if not isinstance(chat_payload, dict):
        return []
    history = chat_payload.get("history") if isinstance(chat_payload.get("history"), dict) else {}
    messages_map = history.get("messages") if isinstance(history.get("messages"), dict) else {}

    if messages_map:
        leaf_id = current_message_id if (current_message_id and current_message_id in messages_map) else history.get("currentId")
        if not leaf_id or leaf_id not in messages_map:
            leaf_id = max(
                messages_map.keys(),
                key=lambda mid: (
                    (messages_map[mid].get("timestamp") or 0) if isinstance(messages_map.get(mid), dict) else 0,
                    str(mid),
                ),
            )
        chain: List[Dict[str, Any]] = []
        visited: set[str] = set()
        cursor: Optional[str] = str(leaf_id) if leaf_id else None
        while cursor and cursor in messages_map and cursor not in visited:
            visited.add(cursor)
            node = messages_map.get(cursor)
            if not isinstance(node, dict):
                break
            chain.append({**node, "id": str(node.get("id") or cursor)})
            parent = node.get("parentId")
            cursor = str(parent) if parent else None
        chain.reverse()
        return chain

    legacy_msgs = chat_payload.get("messages")
    if isinstance(legacy_msgs, list):
        return [dict(m) for m in legacy_msgs if isinstance(m, dict)]
    return []


async def resolve_chats_runtime_state(
    chat_rows: List[Dict[str, Any]],
    redis: Any = None,
) -> Dict[str, Dict[str, Any]]:
    """Résout en batch l'état runtime canonique d'une liste de conversations."""
    if not chat_rows:
        return {}

    deps = _load_owui_deps()
    select = deps["select"]
    get_async_db_context = deps["get_async_db_context"]
    ChatMessage = deps["ChatMessage"]
    ChatMessages = deps["ChatMessages"]
    AutomationRun = deps["AutomationRun"]
    has_active_tasks = deps["has_active_tasks"]

    cids = [str(r["id"]) for r in chat_rows if r.get("id")]
    cid_set = set(cids)

    task_active_ids: set[str] = set()
    for cid in cids:
        try:
            if await has_active_tasks(redis, cid):
                task_active_ids.add(cid)
        except Exception:
            pass

    subagent_counts = _get_running_subagents_counts(cid_set)

    try:
        pending_approval_ids = set(await ChatMessages.get_chat_ids_with_pending_approval(cids))
    except Exception:
        pending_approval_ids = set()

    unfinished_ids: set[str] = set()
    leaf_state_by_cid: Dict[str, Dict[str, Any]] = {}
    automation_by_cid: Dict[str, str] = {}

    composite_to_cid: Dict[str, str] = {}
    for r in chat_rows:
        cid = str(r["id"])
        curr_mid = r.get("current_message_id")
        if curr_mid:
            composite_to_cid[f"{cid}-{curr_mid}"] = cid
            composite_to_cid[str(curr_mid)] = cid

    async with get_async_db_context() as session:
        if task_active_ids:
            res_unf = await session.execute(
                select(ChatMessage.chat_id)
                .where(ChatMessage.chat_id.in_(list(task_active_ids)))
                .where(ChatMessage.role == "assistant")
                .where(ChatMessage.done.is_(False))
                .distinct()
            )
            unfinished_ids = {str(row[0]) for row in res_unf.all() if row[0]}

        if composite_to_cid:
            res_leaf = await session.execute(
                select(ChatMessage.id, ChatMessage.chat_id, ChatMessage.role, ChatMessage.done, ChatMessage.error)
                .where(ChatMessage.id.in_(list(composite_to_cid.keys())))
            )
            for mid, m_cid, role, done, err in res_leaf.all():
                target_cid = composite_to_cid.get(str(mid)) or str(m_cid)
                leaf_state_by_cid[target_cid] = {
                    "role": role,
                    "done": bool(done) if done is not None else True,
                    "has_error": bool(err),
                }

        res_auto = await session.execute(
            select(AutomationRun.chat_id, AutomationRun.status)
            .where(AutomationRun.chat_id.in_(cids))
            .where(AutomationRun.status.in_(["running", "queued"]))
        )
        for a_cid, a_status in res_auto.all():
            if not a_cid:
                continue
            s_cid = str(a_cid)
            if a_status == "running" or s_cid not in automation_by_cid:
                automation_by_cid[s_cid] = str(a_status)

    out: Dict[str, Dict[str, Any]] = {}
    for r in chat_rows:
        cid = str(r["id"])
        llm_running = (cid in task_active_ids) and (
            (cid in unfinished_ids) or (not leaf_state_by_cid.get(cid, {}).get("done", True))
        )
        sub_count = subagent_counts.get(cid, 0)
        auto_st = automation_by_cid.get(cid)
        timer_at = r.get("timer_at")
        has_timer = bool(timer_at is not None and int(timer_at) > 0)
        leaf = leaf_state_by_cid.get(cid, {})
        is_orphan_unfinished = (
            leaf.get("role") == "assistant"
            and leaf.get("done") is False
            and cid not in task_active_ids
            and cid not in pending_approval_ids
        )
        has_err = bool(leaf.get("has_error")) or is_orphan_unfinished

        if llm_running or sub_count > 0 or auto_st == "running":
            canon: ChatRuntimeStatus = "running"
        elif cid in pending_approval_ids:
            canon = "pending_approval"
        elif auto_st == "queued" or has_timer:
            canon = "queued"
        elif has_err:
            canon = "error"
        else:
            canon = "idle"

        runtime: Dict[str, Any] = {
            "llm_running": llm_running,
            "running_subagents": sub_count,
        }
        if auto_st:
            runtime["automation_status"] = auto_st
        if has_timer:
            runtime["timer_at"] = _format_epoch_iso(timer_at)

        out[cid] = {
            "status": canon,
            "runtime": runtime,
        }

    return out


async def list_user_chats(
    user_id: str,
    current_chat_id: Optional[str] = None,
    query: Optional[str] = None,
    status: str = "all",
    folder_id: Optional[str] = None,
    tag: Optional[str] = None,
    include_archived: bool = False,
    include_current: bool = True,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    redis: Any = None,
) -> Dict[str, Any]:
    """Liste et filtre les conversations existantes de l'utilisateur avec leur état runtime."""
    if not user_id or user_id in ("anonymous", "system"):
        return {"status": "error", "error_code": "missing_user", "error": "Contexte utilisateur valide requis."}

    if status not in VALID_STATUS_FILTERS:
        return {
            "status": "error",
            "error_code": "invalid_status",
            "error": f"Paramètre 'status' invalide ('{status}'). Valeurs autorisées : {', '.join(VALID_STATUS_FILTERS)}.",
        }
    if limit < 1 or limit > MAX_CHAT_LIST_LIMIT:
        return {
            "status": "error",
            "error_code": "invalid_limit",
            "error": f"Paramètre 'limit' invalide ({limit}). Doit être compris entre 1 et {MAX_CHAT_LIST_LIMIT}.",
        }
    if offset < 0:
        return {"status": "error", "error_code": "invalid_offset", "error": "Paramètre 'offset' invalide (>= 0 requis)."}

    ts_start, err_s = _parse_iso_date_bound(start_date, end_of_day=False)
    if err_s:
        return {"status": "error", "error_code": "invalid_start_date", "error": err_s}
    ts_end, err_e = _parse_iso_date_bound(end_date, end_of_day=True)
    if err_e:
        return {"status": "error", "error_code": "invalid_end_date", "error": err_e}
    if ts_start is not None and ts_end is not None and ts_start > ts_end:
        return {"status": "error", "error_code": "invalid_date_range", "error": "'start_date' est postérieure à 'end_date'."}

    deps = _load_owui_deps()
    select = deps["select"]
    get_async_db_context = deps["get_async_db_context"]
    Chat = deps["Chat"]
    Chats = deps["Chats"]
    Folders = deps["Folders"]

    clean_query = query.strip() if isinstance(query, str) and query.strip() else None
    clean_folder = folder_id.strip() if isinstance(folder_id, str) and folder_id.strip() else None
    clean_tag = _normalize_tag_id(tag) if isinstance(tag, str) and tag.strip() else None

    folders_list = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, "get_folders_by_user_id") else []
    folder_names_map = {str(f.id): f.name for f in (folders_list or [])}

    projected_cols = (
        Chat.id,
        Chat.title,
        Chat.folder_id,
        Chat.archived,
        Chat.pinned,
        Chat.meta,
        Chat.current_message_id,
        Chat.created_at,
        Chat.updated_at,
        Chat.last_read_at,
        Chat.timer_at,
    )

    def _row_to_dict(row: Any) -> Dict[str, Any]:
        meta = row[5] if isinstance(row[5], dict) else {}
        return {
            "id": str(row[0]),
            "title": row[1] or "",
            "folder_id": row[2],
            "archived": bool(row[3]),
            "pinned": bool(row[4]),
            "meta": meta,
            "current_message_id": row[6],
            "created_at": row[7],
            "updated_at": row[8],
            "last_read_at": row[9],
            "timer_at": row[10],
        }

    candidates: List[Dict[str, Any]] = []
    if clean_query:
        fetch_cap = max(60, min(300, (offset + limit) * 4))
        matched_items = await Chats.get_chats_by_user_id_and_search_text(
            user_id=user_id,
            search_text=clean_query,
            include_archived=include_archived,
            skip=0,
            limit=fetch_cap,
        )
        ordered_ids = [str(c.id) for c in (matched_items or []) if getattr(c, "id", None)]
        if ordered_ids:
            async with get_async_db_context() as session:
                res_proj = await session.execute(
                    select(*projected_cols)
                    .where(Chat.user_id == user_id)
                    .where(Chat.id.in_(ordered_ids))
                )
                by_id = {str(r[0]): _row_to_dict(r) for r in res_proj.all()}
            for cid in ordered_ids:
                item = by_id.get(cid)
                if item and not _is_internal_meta(item["meta"]):
                    candidates.append(item)
    else:
        async with get_async_db_context() as session:
            stmt = select(*projected_cols).where(Chat.user_id == user_id)
            if not include_archived:
                stmt = stmt.where(Chat.archived.is_(False))
            if clean_folder:
                stmt = stmt.where(Chat.folder_id == clean_folder)
            if not include_current and current_chat_id:
                stmt = stmt.where(Chat.id != current_chat_id)
            if ts_start is not None:
                stmt = stmt.where(Chat.updated_at >= ts_start)
            if ts_end is not None:
                stmt = stmt.where(Chat.updated_at <= ts_end)
            stmt = stmt.order_by(Chat.updated_at.desc(), Chat.id)
            res_all = await session.execute(stmt)
            for r in res_all.all():
                item = _row_to_dict(r)
                if not _is_internal_meta(item["meta"]):
                    candidates.append(item)

    filtered: List[Dict[str, Any]] = []
    for item in candidates:
        if not include_current and current_chat_id and item["id"] == current_chat_id:
            continue
        if not include_archived and item["archived"]:
            continue
        if clean_folder and item["folder_id"] != clean_folder:
            continue
        if ts_start is not None and (item["updated_at"] or 0) < ts_start:
            continue
        if ts_end is not None and (item["updated_at"] or 0) > ts_end:
            continue
        if clean_tag:
            raw_tags = [_normalize_tag_id(str(t)) for t in (item["meta"].get("tags") or [])]
            if clean_tag not in raw_tags:
                continue
        filtered.append(item)

    if status == "all":
        total_matching = len(filtered)
        page_rows = filtered[offset: offset + limit]
        runtime_map = await resolve_chats_runtime_state(page_rows, redis=redis)
    else:
        runtime_map = await resolve_chats_runtime_state(filtered, redis=redis)
        status_filtered = [
            it for it in filtered if runtime_map.get(it["id"], {}).get("status") == status
        ]
        total_matching = len(status_filtered)
        page_rows = status_filtered[offset: offset + limit]

    snippets_map: Dict[str, str] = {}
    if clean_query and page_rows and hasattr(Chats, "get_chat_search_snippets"):
        try:
            snippets_map = await Chats.get_chat_search_snippets(
                [r["id"] for r in page_rows], user_id, clean_query
            ) or {}
        except Exception:
            snippets_map = {}

    chats_out: List[Dict[str, Any]] = []
    for r in page_rows:
        cid = r["id"]
        rt_info = runtime_map.get(cid, {"status": "idle", "runtime": {"llm_running": False, "running_subagents": 0}})
        fid = r["folder_id"]
        upd = r["updated_at"] or 0
        last_rd = r["last_read_at"]
        entry: Dict[str, Any] = {
            "chat_id": cid,
            "title": r["title"],
            "status": rt_info["status"],
            "runtime": rt_info["runtime"],
            "is_current": bool(current_chat_id and cid == current_chat_id),
            "pinned": r["pinned"],
            "archived": r["archived"],
            "unread": bool(last_rd is not None and upd > last_rd),
            "folder_id": fid,
            "folder_name": folder_names_map.get(str(fid)) if fid else None,
            "tags": list(r["meta"].get("tags") or []),
            "created_at": _format_epoch_iso(r["created_at"]),
            "updated_at": _format_epoch_iso(upd),
        }
        if clean_query:
            entry["snippet"] = snippets_map.get(cid) or ""
        chats_out.append(entry)

    has_more = (offset + len(chats_out)) < total_matching
    return {
        "status": "success",
        "total_matching": total_matching,
        "offset": offset,
        "limit": limit,
        "returned_count": len(chats_out),
        "has_more": has_more,
        "next_offset": (offset + len(chats_out)) if has_more else None,
        "chats": chats_out,
    }


async def read_user_chat_transcript(
    user_id: str,
    chat_id: str,
    current_chat_id: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    order: str = "newest_first",
    search_pattern: Optional[str] = None,
    include_reasoning: bool = False,
    include_tool_calls: bool = False,
    max_bytes: int = ECHO_CODEX_MAX_READ_BYTES,
    redis: Any = None,
) -> Dict[str, Any]:
    """Lit de façon paginée et assainie le transcript de la branche active d'une conversation."""
    if not user_id or user_id in ("anonymous", "system"):
        return {"status": "error", "error_code": "missing_user", "error": "Contexte utilisateur valide requis."}

    clean_cid = (chat_id or "").strip()
    if clean_cid.lower() == "current" and current_chat_id:
        clean_cid = current_chat_id.strip()
    if not clean_cid or clean_cid.startswith("local:"):
        return {
            "status": "error",
            "error_code": "invalid_chat_id",
            "error": "Identifiant 'chat_id' invalide ou conversation temporaire ('local:') non persistée.",
        }
    if order not in VALID_TRANSCRIPT_ORDERS:
        return {
            "status": "error",
            "error_code": "invalid_order",
            "error": f"Paramètre 'order' invalide ('{order}'). Valeurs autorisées : {', '.join(VALID_TRANSCRIPT_ORDERS)}.",
        }
    if limit < 1 or limit > MAX_TRANSCRIPT_LIMIT:
        return {
            "status": "error",
            "error_code": "invalid_limit",
            "error": f"Paramètre 'limit' invalide ({limit}). Doit être compris entre 1 et {MAX_TRANSCRIPT_LIMIT}.",
        }
    if offset < 0:
        return {"status": "error", "error_code": "invalid_offset", "error": "Paramètre 'offset' invalide (>= 0 requis)."}

    deps = _load_owui_deps()
    Chats = deps["Chats"]
    get_response_streams_by_chat_id = deps["get_response_streams_by_chat_id"]

    chat_obj = await Chats.get_chat_by_id_and_user_id(clean_cid, user_id)
    if not chat_obj or _is_internal_meta(getattr(chat_obj, "meta", None)):
        return {
            "status": "error",
            "error_code": "not_found",
            "error": f"Conversation '{clean_cid}' introuvable ou accès refusé.",
        }

    rt_map = await resolve_chats_runtime_state(
        [{
            "id": clean_cid,
            "current_message_id": getattr(chat_obj, "current_message_id", None),
            "timer_at": getattr(chat_obj, "timer_at", None),
        }],
        redis=redis,
    )
    rt_info = rt_map.get(clean_cid, {"status": "idle", "runtime": {"llm_running": False, "running_subagents": 0}})

    branch = extract_chat_branch(getattr(chat_obj, "chat", None), getattr(chat_obj, "current_message_id", None))

    if rt_info["runtime"].get("llm_running"):
        try:
            streams = await get_response_streams_by_chat_id(redis, clean_cid)
        except Exception:
            streams = []
        if streams:
            latest_stream = streams[-1]
            s_mid = str(latest_stream.get("message_id") or "")
            s_content = latest_stream.get("content") or ""
            matched_live = False
            if branch and s_mid and str(branch[-1].get("id")) == s_mid:
                branch[-1] = {**branch[-1], "content": s_content or branch[-1].get("content", ""), "_live_stream": True}
                matched_live = True
            elif branch and branch[-1].get("role") == "assistant" and branch[-1].get("done") is False:
                branch[-1] = {**branch[-1], "content": s_content or branch[-1].get("content", ""), "_live_stream": True}
                matched_live = True
            if not matched_live and s_content:
                branch.append({
                    "id": s_mid or "live_stream",
                    "role": "assistant",
                    "content": s_content,
                    "done": False,
                    "_live_stream": True,
                })

    normalized_msgs: List[Dict[str, Any]] = []
    for idx, msg in enumerate(branch, start=1):
        role = str(msg.get("role") or "unknown")
        cleaned_text = sanitize_transcript_content(
            msg.get("content"),
            include_reasoning=include_reasoning,
            include_tool_calls=include_tool_calls,
        )
        raw_files = msg.get("files") if isinstance(msg.get("files"), list) else []
        files_summary = [
            {
                "id": f.get("id") or (f.get("file") or {}).get("id"),
                "name": f.get("name") or (f.get("file") or {}).get("filename") or "fichier",
            }
            for f in raw_files
            if isinstance(f, dict)
        ]
        err_obj = msg.get("error")
        err_text = None
        if err_obj:
            err_text = err_obj.get("content") if isinstance(err_obj, dict) else str(err_obj)

        is_in_progress = bool(
            msg.get("_live_stream")
            or (role == "assistant" and msg.get("done") is False and rt_info["runtime"].get("llm_running"))
        )
        if not cleaned_text and not files_summary and not err_text and not is_in_progress:
            continue

        entry_msg: Dict[str, Any] = {
            "index": idx,
            "message_id": str(msg.get("id") or ""),
            "role": role,
            "timestamp": _format_epoch_iso(msg.get("timestamp")),
            "content": cleaned_text,
        }
        if is_in_progress:
            entry_msg["in_progress"] = True
        if files_summary:
            entry_msg["files"] = files_summary
        if err_text:
            entry_msg["error"] = err_text
        normalized_msgs.append(entry_msg)

    total_branch_messages = len(normalized_msgs)

    regex = None
    if search_pattern and isinstance(search_pattern, str) and search_pattern.strip():
        pat = search_pattern.strip()
        try:
            regex = re.compile(pat, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pat), re.IGNORECASE)

    working_list = (
        [m for m in normalized_msgs if regex.search(m.get("content") or "") or regex.search(str(m.get("error") or ""))]
        if regex is not None
        else list(normalized_msgs)
    )

    if order == "newest_first":
        working_list = list(reversed(working_list))

    total_matching = len(working_list)
    sliced = working_list[offset: offset + limit]

    selected: List[Dict[str, Any]] = []
    acc_bytes = 0
    cap_bytes = max(4096, int(max_bytes))
    for m in sliced:
        serialized_len = len(json.dumps(m, ensure_ascii=False).encode("utf-8"))
        if acc_bytes + serialized_len > cap_bytes and selected:
            break
        selected.append(m)
        acc_bytes += serialized_len

    has_more = (offset + len(selected)) < total_matching
    return {
        "status": "success",
        "chat_id": clean_cid,
        "title": getattr(chat_obj, "title", "") or "",
        "chat_status": rt_info["status"],
        "runtime": rt_info["runtime"],
        "is_current": bool(current_chat_id and clean_cid == current_chat_id),
        "order": order,
        "total_branch_messages": total_branch_messages,
        "total_matching": total_matching,
        "offset": offset,
        "returned_count": len(selected),
        "has_more": has_more,
        "next_offset": (offset + len(selected)) if has_more else None,
        "messages": selected,
    }
