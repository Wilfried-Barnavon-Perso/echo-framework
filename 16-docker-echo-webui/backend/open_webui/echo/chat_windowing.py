# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO V6 Chat Windowing & Lazy Message Details
author: Wilfried BARNAVON
version: 6.22.0.1
description: Fenêtrage arborescent des conversations par checkpoints et allègement paresseux des détails lourds.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.22.0.1: Schémas list[dict] d'output, scanner <details> à profondeur, exemptions et purge anti-fuite lazy.
# 6.21.12.1: Création du module de fenêtrage arborescent par checkpoints et allègement paresseux des détails.

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    from open_webui.echo.runtime import ensure_echo_sys_path

    ensure_echo_sys_path()
except Exception:
    pass

try:
    from echo_constants import (  # type: ignore
        ECHO_CHAT_EAGER_DETAILS_TAIL,
        ECHO_CHAT_WINDOW_MAX_MESSAGES,
        normalize_checkpoint_payload,
    )
    from echo_paths import get_echo_db_path  # type: ignore
except ImportError:
    _local_libs = Path(__file__).resolve().parents[4] / "14-owui-libs"
    if _local_libs.is_dir() and str(_local_libs) not in sys.path:
        sys.path.insert(0, str(_local_libs))
    try:
        from echo_constants import (  # type: ignore
            ECHO_CHAT_EAGER_DETAILS_TAIL,
            ECHO_CHAT_WINDOW_MAX_MESSAGES,
            normalize_checkpoint_payload,
        )
        from echo_paths import get_echo_db_path  # type: ignore
    except ImportError:
        ECHO_CHAT_WINDOW_MAX_MESSAGES = 80
        ECHO_CHAT_EAGER_DETAILS_TAIL = 8

        def normalize_checkpoint_payload(raw_or_dict: Any) -> dict[str, Any] | None:
            return None

        def get_echo_db_path(user_id: str, chat_id: str) -> str:
            return ""

_HEAVY_THRESHOLD_CHARS = 512
_LAZY_PLACEHOLDER = "<!-- echo-lazy -->"
_EMBED_SCHEME = "echo-embed://"

_DETAILS_TAG_RE = re.compile(
    r"<details\b(?:[^>\"']|\"[^\"]*\"|'[^']*')*>|</details\s*>",
    re.DOTALL | re.IGNORECASE,
)
_DETAILS_TARGET_TYPE_RE = re.compile(
    r"\btype\s*=\s*(?:\"reasoning\"|'reasoning'|reasoning|\"tool_calls\"|'tool_calls'|tool_calls)(?:\s|>|/|$)",
    re.IGNORECASE,
)
_DETAILS_EXEMPT_NAME_RE = re.compile(
    r"\bname\s*=\s*(?:\"display_file\"|'display_file'|display_file)(?:\s|>|/|$)",
    re.IGNORECASE,
)
_DETAILS_HEAVY_ATTR_RE = re.compile(
    r"(\b(?:arguments|result)\s*=\s*)(?:\"([^\"]*)\"|'([^']*)')",
    re.DOTALL | re.IGNORECASE,
)
_SUMMARY_BLOCK_RE = re.compile(
    r"^(\s*<summary\b[^>]*>.*?</summary>)(.*)$",
    re.DOTALL | re.IGNORECASE,
)


def load_session_checkpoints_readonly(user_id: str, chat_id: str) -> list[dict[str, Any]]:
    """Lit les checkpoints de compaction depuis session.db en lecture seule sans créer de fichier."""
    if not user_id or not chat_id:
        return []
    db_path = get_echo_db_path(user_id, chat_id)
    if not db_path or not os.path.isfile(db_path):
        return []

    conn: sqlite3.Connection | None = None
    try:
        db_uri = f"{Path(db_path).resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(db_uri, uri=True, timeout=2.0)
        cur = conn.execute(
            "SELECT value FROM echo_settings WHERE key = 'context_checkpoint' LIMIT 1"
        )
        row = cur.fetchone()
        if not row or not row[0]:
            return []
        normalized = normalize_checkpoint_payload(row[0])
        if not normalized:
            return []
        return list(normalized.get("checkpoints") or [])
    except Exception:
        return []
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def build_tree_index(messages_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Construit l'index léger de l'arbre des messages (parentId, childrenIds, role, timestamp)."""
    if not isinstance(messages_map, dict):
        return {}
    return {
        str(mid): {
            "parentId": msg.get("parentId"),
            "childrenIds": list(msg.get("childrenIds") or []),
            "role": msg.get("role"),
            "timestamp": msg.get("timestamp"),
        }
        for mid, msg in messages_map.items()
        if isinstance(msg, dict)
    }


def extract_branch_ids(
    messages_map: dict[str, dict[str, Any]],
    current_id: str | None,
) -> list[str]:
    """Remonte la lignée active par parentId avec garde anti-cycle et retourne [root, ..., current_id]."""
    if not isinstance(messages_map, dict) or not current_id:
        return []
    branch: list[str] = []
    visited: set[str] = set()
    cursor: str | None = str(current_id)
    while cursor and cursor in messages_map and cursor not in visited:
        visited.add(cursor)
        branch.append(cursor)
        msg = messages_map.get(cursor)
        parent = msg.get("parentId") if isinstance(msg, dict) else None
        cursor = str(parent) if parent else None
    branch.reverse()
    return branch


def _strip_open_tag_heavy_attrs(open_tag: str) -> tuple[str, bool]:
    attr_changed = [False]

    def _replace_attr(match: re.Match[str]) -> str:
        prefix = match.group(1)
        val = match.group(2) if match.group(2) is not None else (match.group(3) or "")
        if len(val) <= _HEAVY_THRESHOLD_CHARS:
            return match.group(0)
        attr_changed[0] = True
        return f'{prefix}"{_LAZY_PLACEHOLDER}"'

    updated = _DETAILS_HEAVY_ATTR_RE.sub(_replace_attr, open_tag)
    return updated, attr_changed[0]


def _strip_heavy_details_blocks(content: str) -> tuple[str, bool]:
    """Allège les blocs <details type=reasoning|tool_calls> racines avec suivi de profondeur."""
    if not content or "<details" not in content.lower():
        return content, False

    out_parts: list[str] = []
    cursor = 0
    depth = 0
    root_start = -1
    root_open_end = -1
    root_open_tag = ""
    changed = False

    for match in _DETAILS_TAG_RE.finditer(content):
        tag_text = match.group(0)
        if tag_text.lower().startswith("</details"):
            if depth <= 0:
                continue
            depth -= 1
            if depth > 0:
                continue

            out_parts.append(content[cursor:root_start])
            inner = content[root_open_end:match.start()]
            close_tag = tag_text
            full_original = content[root_start:match.end()]

            if (
                not _DETAILS_TARGET_TYPE_RE.search(root_open_tag)
                or _DETAILS_EXEMPT_NAME_RE.search(root_open_tag)
                or _EMBED_SCHEME in root_open_tag
                or _EMBED_SCHEME in inner
            ):
                out_parts.append(full_original)
                cursor = match.end()
                continue

            new_open_tag, attr_changed = _strip_open_tag_heavy_attrs(root_open_tag)
            summary_match = _SUMMARY_BLOCK_RE.match(inner)
            if summary_match:
                summary_part, inner_body = summary_match.group(1), summary_match.group(2)
            else:
                summary_part, inner_body = "", inner

            body_heavy = len(inner_body.strip()) > _HEAVY_THRESHOLD_CHARS
            if body_heavy:
                changed = True
                if summary_part:
                    out_parts.append(f"{new_open_tag}{summary_part}\n{_LAZY_PLACEHOLDER}\n{close_tag}")
                else:
                    out_parts.append(f"{new_open_tag}\n{_LAZY_PLACEHOLDER}\n{close_tag}")
            elif attr_changed:
                changed = True
                out_parts.append(f"{new_open_tag}{inner}{close_tag}")
            else:
                out_parts.append(full_original)

            cursor = match.end()
        else:
            if tag_text.rstrip().endswith("/>"):
                continue
            if depth == 0:
                root_start = match.start()
                root_open_end = match.end()
                root_open_tag = tag_text
            depth += 1

    if not changed:
        return content, False

    out_parts.append(content[cursor:])
    return "".join(out_parts), True


def _serialized_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _serialized_len(value: Any) -> int:
    return len(_serialized_str(value))


def _is_exempt_output_item(item: dict[str, Any]) -> bool:
    if item.get("name") == "display_file":
        return True
    for key in ("arguments", "output", "result", "embeds"):
        if key in item and _EMBED_SCHEME in _serialized_str(item.get(key)):
            return True
    return False


def strip_heavy_message_details(msg: dict[str, Any]) -> dict[str, Any]:
    """Allège les blocs reasoning/tool_calls (> 512 car.) d'un message assistant terminé."""
    if not isinstance(msg, dict):
        return msg
    if msg.get("role") == "user" or msg.get("done") is False:
        return msg

    changed = False
    new_content = msg.get("content")
    if isinstance(new_content, str) and new_content:
        stripped_content, content_changed = _strip_heavy_details_blocks(new_content)
        if content_changed:
            changed = True
            new_content = stripped_content

    raw_output = msg.get("output")
    new_output = raw_output
    if isinstance(raw_output, list) and raw_output:
        exempt_call_ids: set[str] = set()
        for item in raw_output:
            if isinstance(item, dict) and _is_exempt_output_item(item):
                cid = item.get("call_id") or (item.get("id") if item.get("type") == "function_call" else None)
                if cid:
                    exempt_call_ids.add(str(cid))

        output_changed = False
        processed_output: list[Any] = []
        for item in raw_output:
            if not isinstance(item, dict):
                processed_output.append(item)
                continue

            item_call_id = item.get("call_id") or (
                item.get("id") if item.get("type") == "function_call" else None
            )
            if _is_exempt_output_item(item) or (item_call_id and str(item_call_id) in exempt_call_ids):
                processed_output.append(item)
                continue

            item_type = item.get("type")
            if item_type == "reasoning":
                orig_summary = item.get("summary")
                orig_content = item.get("content")
                summary_heavy = _serialized_len(orig_summary) > _HEAVY_THRESHOLD_CHARS
                content_heavy = _serialized_len(orig_content) > _HEAVY_THRESHOLD_CHARS
                if summary_heavy or content_heavy:
                    output_changed = True
                    item_copy = dict(item)
                    if summary_heavy:
                        item_copy["summary"] = (
                            [{"type": "summary_text", "text": _LAZY_PLACEHOLDER}]
                            if isinstance(orig_summary, list)
                            else _LAZY_PLACEHOLDER
                        )
                    if content_heavy:
                        item_copy["content"] = (
                            [{"type": "output_text", "text": _LAZY_PLACEHOLDER}]
                            if isinstance(orig_content, list)
                            else _LAZY_PLACEHOLDER
                        )
                    processed_output.append(item_copy)
                    continue
            elif item_type in ("function_call", "function_call_output"):
                item_copy: dict[str, Any] | None = None
                if "arguments" in item and _serialized_len(item.get("arguments")) > _HEAVY_THRESHOLD_CHARS:
                    item_copy = dict(item)
                    orig_args = item.get("arguments")
                    if isinstance(orig_args, str):
                        item_copy["arguments"] = _LAZY_PLACEHOLDER
                    elif isinstance(orig_args, dict):
                        item_copy["arguments"] = {"_lazy": _LAZY_PLACEHOLDER}
                    elif isinstance(orig_args, list):
                        item_copy["arguments"] = [_LAZY_PLACEHOLDER]
                    else:
                        item_copy["arguments"] = _LAZY_PLACEHOLDER

                for field_key in ("output", "result"):
                    if field_key in item and _serialized_len(item.get(field_key)) > _HEAVY_THRESHOLD_CHARS:
                        if item_copy is None:
                            item_copy = dict(item)
                        orig_val = item.get(field_key)
                        if isinstance(orig_val, list):
                            image_parts = [
                                p
                                for p in orig_val
                                if isinstance(p, dict) and p.get("type") == "input_image"
                            ]
                            item_copy[field_key] = [
                                {"type": "input_text", "text": _LAZY_PLACEHOLDER},
                                *image_parts,
                            ]
                        else:
                            item_copy[field_key] = _LAZY_PLACEHOLDER

                if item_copy is not None:
                    output_changed = True
                    processed_output.append(item_copy)
                    continue

            processed_output.append(item)

        if output_changed:
            changed = True
            new_output = processed_output

    if not changed:
        return msg

    copy_msg = dict(msg)
    if isinstance(new_content, str):
        copy_msg["content"] = new_content
    if new_output is not raw_output:
        copy_msg["output"] = new_output
    copy_msg["_echoLazyDetails"] = True
    return copy_msg


def _purge_lazy_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace(f"\n{_LAZY_PLACEHOLDER}\n", "").replace(_LAZY_PLACEHOLDER, "")
    if isinstance(value, list):
        cleaned_list: list[Any] = []
        for elem in value:
            if elem == _LAZY_PLACEHOLDER:
                continue
            if isinstance(elem, dict) and elem.get("text") == _LAZY_PLACEHOLDER and set(elem.keys()) <= {
                "type",
                "text",
            }:
                cleaned_list.append({**elem, "text": ""})
            else:
                cleaned_list.append(_purge_lazy_value(elem))
        return cleaned_list
    if isinstance(value, dict):
        return {
            k: _purge_lazy_value(v)
            for k, v in value.items()
            if not (k == "_lazy" and v == _LAZY_PLACEHOLDER)
        }
    return value


def restore_lazy_message_fields(
    stored_msg: dict[str, Any] | None,
    incoming_msg: dict[str, Any],
) -> dict[str, Any]:
    """Restaure content/output complets depuis stored_msg ou purge tout placeholder lazy résiduel."""
    if not isinstance(incoming_msg, dict) or not incoming_msg.get("_echoLazyDetails"):
        return incoming_msg
    restored = {k: v for k, v in incoming_msg.items() if k != "_echoLazyDetails"}
    has_stored = isinstance(stored_msg, dict)

    if has_stored and "content" in stored_msg and stored_msg["content"] is not None:  # type: ignore[index]
        restored["content"] = stored_msg["content"]  # type: ignore[index]
    elif "content" in restored:
        restored["content"] = _purge_lazy_value(restored["content"])

    if has_stored and "output" in stored_msg and stored_msg["output"] is not None:  # type: ignore[index]
        restored["output"] = stored_msg["output"]  # type: ignore[index]
    elif "output" in restored:
        restored["output"] = _purge_lazy_value(restored["output"])

    return restored


def _convert_legacy_messages_list_to_history(legacy_messages: list[Any]) -> dict[str, Any]:
    messages_map: dict[str, dict[str, Any]] = {}
    prev_id: str | None = None
    for idx, raw_msg in enumerate(legacy_messages):
        if not isinstance(raw_msg, dict):
            continue
        mid = str(raw_msg.get("id") or f"msg_{idx}")
        parent_id = raw_msg.get("parentId", prev_id)
        parent_str = str(parent_id) if parent_id is not None else None
        raw_children = raw_msg.get("childrenIds")
        children_ids = [str(cid) for cid in raw_children if cid] if isinstance(raw_children, list) else []
        node = {
            **raw_msg,
            "id": mid,
            "parentId": parent_str,
            "childrenIds": children_ids,
        }
        messages_map[mid] = node
        if parent_str and parent_str in messages_map and mid not in messages_map[parent_str]["childrenIds"]:
            messages_map[parent_str]["childrenIds"].append(mid)
        prev_id = mid

    return {
        "messages": messages_map,
        "currentId": prev_id,
    }


def _select_latest_message_id(messages_map: dict[str, dict[str, Any]]) -> str | None:
    best_id: str | None = None
    best_ts = float("-inf")
    for mid, msg in messages_map.items():
        if not isinstance(msg, dict):
            continue
        raw_ts = msg.get("timestamp")
        try:
            ts = float(raw_ts) if raw_ts is not None else float("-inf")
        except (TypeError, ValueError):
            ts = float("-inf")
        if best_id is None or ts >= best_ts:
            best_id = str(mid)
            best_ts = ts
    return best_id


def _normalize_chat_history_and_current_id(
    chat_inner: dict[str, Any],
    fallback_current_id: Any = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], str | None]:
    history = chat_inner.get("history")
    if not isinstance(history, dict):
        history = {}
        chat_inner["history"] = history

    messages_map = history.get("messages")
    if not isinstance(messages_map, dict):
        messages_map = {}
        history["messages"] = messages_map

    legacy_messages = chat_inner.get("messages")
    if not messages_map and isinstance(legacy_messages, list) and legacy_messages:
        converted = _convert_legacy_messages_list_to_history(legacy_messages)
        messages_map = converted["messages"]
        history["messages"] = messages_map
        if not history.get("currentId") and converted.get("currentId"):
            history["currentId"] = converted["currentId"]

    current_id_raw = chat_inner.get("current_message_id") or fallback_current_id or history.get("currentId")
    current_id = str(current_id_raw) if current_id_raw else None

    if messages_map:
        if not current_id or current_id not in messages_map:
            current_id = _select_latest_message_id(messages_map)
        if current_id:
            history["currentId"] = current_id

    return history, messages_map, current_id


def _resolve_checkpoints_on_branch(
    raw_checkpoints: list[dict[str, Any]],
    branch_ids: list[str],
) -> list[dict[str, Any]]:
    branch_pos = {mid: idx for idx, mid in enumerate(branch_ids)}
    branch_len = len(branch_ids)
    resolved: list[dict[str, Any]] = []
    for ckpt in raw_checkpoints:
        entry = dict(ckpt)
        pivot_id = entry.get("pivot_msg_id")
        head_id = entry.get("head_last_msg_id")
        branch_index: int | None = None
        if pivot_id and str(pivot_id) in branch_pos:
            branch_index = branch_pos[str(pivot_id)]
        elif head_id and str(head_id) in branch_pos:
            candidate = branch_pos[str(head_id)] + 1
            if candidate < branch_len:
                branch_index = candidate
        entry["branch_index"] = branch_index
        resolved.append(entry)
    return resolved


def build_windowed_chat_payload(
    chat_data: dict[str, Any],
    *,
    user_id: str,
    chat_id: str,
    limit: int | None = None,
) -> dict[str, Any]:
    """Fenêtre le payload d'un chat autour des messages récents ou du dernier checkpoint."""
    if not isinstance(chat_data, dict):
        return chat_data

    chat_inner = chat_data["chat"] if isinstance(chat_data.get("chat"), dict) else chat_data
    history, messages_map, current_id = _normalize_chat_history_and_current_id(
        chat_inner,
        fallback_current_id=chat_data.get("current_message_id"),
    )

    tree_index = build_tree_index(messages_map)
    branch_ids = extract_branch_ids(messages_map, current_id)
    raw_checkpoints = load_session_checkpoints_readonly(user_id, chat_id)
    resolved_checkpoints = _resolve_checkpoints_on_branch(raw_checkpoints, branch_ids)

    max_msgs = max(1, int(limit or ECHO_CHAT_WINDOW_MAX_MESSAGES))
    total_len = len(branch_ids)
    valid_ckpt_indices = [
        c["branch_index"]
        for c in resolved_checkpoints
        if isinstance(c.get("branch_index"), int) and 0 < c["branch_index"] < total_len
    ]
    if valid_ckpt_indices:
        ckpt_idx = max(valid_ckpt_indices)
        start_idx = max(ckpt_idx, total_len - max_msgs)
    else:
        start_idx = max(0, total_len - max_msgs)

    window_ids = branch_ids[start_idx:]
    eager_start = max(0, len(window_ids) - ECHO_CHAT_EAGER_DETAILS_TAIL)
    windowed_messages = {
        mid: (messages_map[mid] if idx >= eager_start else strip_heavy_message_details(messages_map[mid]))
        for idx, mid in enumerate(window_ids)
        if mid in messages_map
    }

    history["messages"] = windowed_messages
    chat_inner.pop("messages", None)

    chat_data["tree_index"] = tree_index
    chat_data["window_meta"] = {
        "windowed": True,
        "has_older": start_idx > 0,
        "oldest_loaded_id": window_ids[0] if window_ids else None,
        "total_branch_messages": total_len,
        "loaded_branch_messages": len(window_ids),
        "checkpoints": resolved_checkpoints,
    }
    return chat_data


def _find_descendant_leaf(messages_map: dict[str, dict[str, Any]], start_id: str) -> str:
    cursor = start_id
    visited: set[str] = set()
    while cursor in messages_map and cursor not in visited:
        visited.add(cursor)
        children = messages_map[cursor].get("childrenIds") or []
        valid_children = [str(cid) for cid in children if str(cid) in messages_map and str(cid) not in visited]
        if not valid_children:
            break
        cursor = valid_children[-1]
    return cursor


def extract_older_window_slice(
    chat_dict: dict[str, Any],
    *,
    user_id: str,
    chat_id: str,
    before_id: str | None = None,
    to_checkpoint_id: str | None = None,
    target_id: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Extrait une tranche antérieure (pagination/checkpoint) ou une branche sœur (target_id)."""
    if not isinstance(chat_dict, dict):
        chat_dict = {}
    chat_copy = dict(chat_dict)
    if isinstance(chat_copy.get("history"), dict):
        chat_copy["history"] = dict(chat_copy["history"])

    _, messages_map, current_id = _normalize_chat_history_and_current_id(chat_copy)
    tree_index = build_tree_index(messages_map)
    max_msgs = max(1, int(limit or ECHO_CHAT_WINDOW_MAX_MESSAGES))
    raw_checkpoints = load_session_checkpoints_readonly(user_id, chat_id)

    if target_id:
        t_id = str(target_id)
        if t_id not in messages_map:
            branch_ids = extract_branch_ids(messages_map, current_id)
            return {
                "messages": {},
                "has_older": False,
                "oldest_loaded_id": None,
                "checkpoints": _resolve_checkpoints_on_branch(raw_checkpoints, branch_ids),
                "tree_index": tree_index,
            }
        leaf_id = _find_descendant_leaf(messages_map, t_id)
        branch_ids = extract_branch_ids(messages_map, leaf_id)
        start_idx = max(0, len(branch_ids) - max_msgs)
        slice_ids = branch_ids[start_idx:]
        eager_start = max(0, len(slice_ids) - ECHO_CHAT_EAGER_DETAILS_TAIL)
        slice_map = {
            mid: (messages_map[mid] if idx >= eager_start else strip_heavy_message_details(messages_map[mid]))
            for idx, mid in enumerate(slice_ids)
            if mid in messages_map
        }
        if t_id not in slice_map and t_id in messages_map:
            slice_map[t_id] = strip_heavy_message_details(messages_map[t_id])
        resolved_checkpoints = _resolve_checkpoints_on_branch(raw_checkpoints, branch_ids)
        return {
            "messages": slice_map,
            "has_older": start_idx > 0,
            "oldest_loaded_id": slice_ids[0] if slice_ids else None,
            "checkpoints": resolved_checkpoints,
            "tree_index": tree_index,
        }

    if before_id is not None and before_id != "":
        b_id = str(before_id)
        if b_id not in messages_map:
            branch_ids = extract_branch_ids(messages_map, current_id)
            return {
                "messages": {},
                "has_older": False,
                "oldest_loaded_id": None,
                "checkpoints": _resolve_checkpoints_on_branch(raw_checkpoints, branch_ids),
                "tree_index": tree_index,
            }
        ancestors = extract_branch_ids(messages_map, b_id)
        before_idx = max(0, len(ancestors) - 1)
    else:
        ancestors = extract_branch_ids(messages_map, current_id)
        before_idx = len(ancestors)

    resolved_checkpoints = _resolve_checkpoints_on_branch(raw_checkpoints, ancestors)
    new_start_idx = max(0, before_idx - max_msgs)

    if to_checkpoint_id:
        for ckpt in resolved_checkpoints:
            idx = ckpt.get("branch_index")
            if ckpt.get("checkpoint_id") == to_checkpoint_id and isinstance(idx, int) and 0 <= idx < before_idx:
                new_start_idx = idx
                break
    elif limit is None:
        earlier_ckpts = [
            c["branch_index"]
            for c in resolved_checkpoints
            if isinstance(c.get("branch_index"), int) and 0 <= c["branch_index"] < before_idx
        ]
        if earlier_ckpts:
            prev_ckpt_idx = max(earlier_ckpts)
            new_start_idx = max(prev_ckpt_idx, before_idx - max_msgs)

    slice_ids = ancestors[new_start_idx:before_idx]
    slice_map = {
        mid: strip_heavy_message_details(messages_map[mid])
        for mid in slice_ids
        if mid in messages_map
    }
    return {
        "messages": slice_map,
        "has_older": new_start_idx > 0,
        "oldest_loaded_id": slice_ids[0] if slice_ids else None,
        "checkpoints": resolved_checkpoints,
        "tree_index": tree_index,
    }


def extract_full_message_details(
    chat_dict: dict[str, Any],
    message_ids: list[str],
) -> dict[str, dict[str, Any]]:
    """Retourne les messages complets (plafond 50) sans le marqueur _echoLazyDetails."""
    if not isinstance(chat_dict, dict):
        return {}
    chat_copy = dict(chat_dict)
    if isinstance(chat_copy.get("history"), dict):
        chat_copy["history"] = dict(chat_copy["history"])
    _, messages_map, _ = _normalize_chat_history_and_current_id(chat_copy)
    result: dict[str, dict[str, Any]] = {}
    for mid in (message_ids or [])[:50]:
        s_mid = str(mid)
        msg = messages_map.get(s_mid)
        if isinstance(msg, dict):
            result[s_mid] = {k: v for k, v in msg.items() if k != "_echoLazyDetails"}
    return result
