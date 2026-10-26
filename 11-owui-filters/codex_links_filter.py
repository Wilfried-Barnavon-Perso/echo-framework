"""
title: ECHO Codex Links & URN Filter
author: Wilfried BARNAVON
version: 5.231.11.1
description: Filtre bidirectionnel (Inlet/Outlet: Pont DOM JS & window.echoOpenCodex / Outlet: Validation physique FS Fail-Closed et mutation d'ancres Codex).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.11.1: Réétagement de la priorité à 40 (après new_context_filter à 30 et avant conversation_rag_filter à 100).
# 5.231.6.1: Factorisation SSOT de _get_js_bridge via EchoCodexUI.get_codex_bridge_js et neutralisation target=_blank.
# 5.230.0.6: Hot-Update echoOpenCodex sans blocage _echoCodexBridgeAttached, CSS text-decoration-skip-ink et factorisation.
# 5.230.0.5: Encodage URL (quote/unquote) du chemin dans les ancres #codex: pour supporter espaces, accents et parenthèses.
# 5.230.0.4: Support de message["output"] (OutputItem[] Open WebUI) en complément de message["content"] et transmission du workspace.

import re
import sys
from urllib.parse import quote, unquote
from typing import Optional, Dict, Any, Callable, List
from pydantic import BaseModel, Field

# Imports ECHO standard
if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_constants import (
    CODEX_ANCHOR_PATTERN,
    CODEX_ANCHOR_PREFIX,
    CODEX_ICON_LOCAL,
    CODEX_ICON_REMOTE,
    CODEX_SCOPE_CURRENT,
    CODEX_URN_PATTERN,
    CODEX_URN_SCHEME,
    ECHO_CODE_FENCE_PATTERN,
    ECHO_CODEX_WORKSPACES,
)
from echo_paths import check_codex_file_exists, normalize_codex_rel_path
from echo_events import EchoEvents
from echo_codex_ui import EchoCodexUI

_URN_REGEX = re.compile(CODEX_URN_PATTERN)
_ANCHOR_REGEX = re.compile(CODEX_ANCHOR_PATTERN)


def _transform_outside_code_fences(text: str, transform_fn: Callable[[str], str]) -> str:
    """Applique `transform_fn` hors des blocs de code multilignes (```...```)."""
    if not isinstance(text, str) or not text:
        return text
    code_blocks: List[str] = []

    def _mask_code(match: re.Match) -> str:
        idx = len(code_blocks)
        code_blocks.append(match.group(0))
        return f"___ECHO_CODE_MASK_{idx}___"

    masked = re.sub(ECHO_CODE_FENCE_PATTERN, _mask_code, text)
    transformed = transform_fn(masked)
    for idx, block in enumerate(code_blocks):
        transformed = transformed.replace(f"___ECHO_CODE_MASK_{idx}___", block)
    return transformed


def _mutate_message_texts(msg: Dict[str, Any], transform_fn: Callable[[str], str], needle: str) -> bool:
    """Mute de manière unifiée `msg['content']` (str/list) et `msg['output']` (OutputItem[] natif Open WebUI)."""
    if not isinstance(msg, dict):
        return False
    mutated = False

    content = msg.get("content")
    if isinstance(content, str):
        if needle in content:
            new_content = transform_fn(content)
            if new_content != content:
                msg["content"] = new_content
                mutated = True
    elif isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str) and needle in part["text"]:
                new_txt = transform_fn(part["text"])
                if new_txt != part["text"]:
                    part["text"] = new_txt
                    mutated = True

    output_items = msg.get("output")
    if isinstance(output_items, list):
        for item in output_items:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            item_content = item.get("content")
            if not isinstance(item_content, list):
                continue
            for part in item_content:
                if isinstance(part, dict) and isinstance(part.get("text"), str) and needle in part["text"]:
                    new_txt = transform_fn(part["text"])
                    if new_txt != part["text"]:
                        part["text"] = new_txt
                        mutated = True

    return mutated


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=40, hidden=True, description="Priorité d'exécution globale (40 = après new_context_filter à 30 et avant conversation_rag_filter à 100).")
        ENABLE_CODEX_LINKS: bool = Field(default=True, description="Active les liens interactifs et la détection URN Codex.")

    def __init__(self):
        self.valves = self.Valves()

    def _get_js_bridge(self) -> str:
        return EchoCodexUI.get_codex_bridge_js()

    def _resolve_urns_in_text(self, text: str, user_id: str, current_chat_id: str) -> str:
        """Valide physiquement les URNs Codex et les mute en ancres DOM (#codex:...) encodées CommonMark ou les neutralise."""
        def _resolve_urn(match: re.Match) -> str:
            label = match.group("label")
            scope = match.group("scope")
            ws = match.group("ws")
            path = unquote(match.group("path")).strip("/")

            target_chat = current_chat_id if scope == CODEX_SCOPE_CURRENT else scope
            if not target_chat or ws not in ECHO_CODEX_WORKSPACES:
                return label

            if not check_codex_file_exists(user_id=user_id, chat_id=target_chat, workspace=ws, relative_path=path):
                return label

            clean_path = normalize_codex_rel_path(path, workspace=ws) or path
            encoded_path = quote(clean_path, safe="/")
            icon = CODEX_ICON_LOCAL if scope == CODEX_SCOPE_CURRENT else CODEX_ICON_REMOTE
            return f"[{icon} {label}]({CODEX_ANCHOR_PREFIX}{target_chat}:{ws}:{encoded_path})"

        return _transform_outside_code_fences(text, lambda s: _URN_REGEX.sub(_resolve_urn, s))

    def _restore_anchors_in_text(self, text: str, current_chat_id: str) -> str:
        """Restaure bijectivement les ancres DOM (#codex:...) en URNs canoniques (codex:...) pour la Suture."""
        def _restore_anchor(match: re.Match) -> str:
            icon = match.group("icon")
            label = match.group("label")
            target_chat = match.group("target_chat")
            ws = match.group("ws")
            path = unquote(match.group("path")).strip("/")

            scope = CODEX_SCOPE_CURRENT if (icon == CODEX_ICON_LOCAL or target_chat == current_chat_id) else target_chat
            return f"[{label}]({CODEX_URN_SCHEME}{scope}:{ws}/{path})"

        return _transform_outside_code_fences(text, lambda s: _ANCHOR_REGEX.sub(_restore_anchor, s))

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Injecte l'écouteur DOM JS client et restaure les ancres #codex: en URNs codex: dans l'historique."""
        if not self.valves.ENABLE_CODEX_LINKS:
            return body

        if __event_emitter__:
            events = EchoEvents(__event_emitter__)
            await events.emit_execute(self._get_js_bridge())

        messages = body.get("messages") or []
        if not messages:
            return body

        meta = __metadata__ or body.get("metadata", {})
        current_chat_id = meta.get("chat_id") or body.get("chat_id", "")

        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "assistant":
                _mutate_message_texts(
                    msg,
                    lambda txt: self._restore_anchors_in_text(txt, current_chat_id),
                    needle=CODEX_ANCHOR_PREFIX,
                )
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Valide physiquement l'existence (FS) et mute les URNs en ancres locales DOM sur content et output."""
        if not self.valves.ENABLE_CODEX_LINKS:
            return body

        if __event_emitter__:
            events = EchoEvents(__event_emitter__)
            await events.emit_execute(self._get_js_bridge())

        messages = body.get("messages") or []
        if not messages:
            return body

        last_msg = messages[-1]
        if last_msg.get("role") != "assistant":
            return body

        meta = __metadata__ or body.get("metadata", {})
        current_chat_id = meta.get("chat_id") or body.get("chat_id", "")
        user_id = __user__.get("id", "system") if __user__ else "system"

        _mutate_message_texts(
            last_msg,
            lambda txt: self._resolve_urns_in_text(txt, user_id, current_chat_id),
            needle=CODEX_URN_SCHEME,
        )
        return body
