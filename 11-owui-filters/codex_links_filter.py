"""
title: ECHO Codex Links & URN Filter
author: Wilfried BARNAVON
version: 5.230.0.1
description: Filtre bidirectionnel (Inlet: Pont DOM JS idempotent / Outlet: Validation Git Fail-Closed et mutation d'ancres Codex interactives).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.230.0.1: Création initiale du filtre bidirectionnel URN Codex (priorité 30, Outlet Fail-Closed, JS Bridge).

import re
import sys
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field

# Imports ECHO standard
if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_constants import ECHO_CODEX_WORKSPACES
from echo_codex_git import CodexRepo
from echo_events import EchoEvents


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=30, hidden=True, description="Priorité d'exécution globale.")
        ENABLE_CODEX_LINKS: bool = Field(default=True, description="Active les liens interactifs et la détection URN Codex.")

    def __init__(self):
        self.valves = self.Valves()

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Injecte l'écouteur DOM JS client de manière silencieuse et idempotente."""
        if not self.valves.ENABLE_CODEX_LINKS or not __event_emitter__:
            return body

        js_bridge = r"""
        (function initCodexLinksHandler() {
            if (window._echoCodexLinkHandlerAttached) return;
            window._echoCodexLinkHandlerAttached = true;

            document.addEventListener('click', async (e) => {
                const link = e.target.closest('a[href^="#codex:"]');
                if (!link) return;

                e.preventDefault();
                e.stopPropagation();

                const href = link.getAttribute('href') || '';
                const raw = href.substring(7); // Supprime '#codex:'
                const firstColon = raw.indexOf(':');
                const secondColon = raw.indexOf(':', firstColon + 1);
                if (firstColon === -1 || secondColon === -1) return;

                const targetChatId = raw.substring(0, firstColon);
                const targetWorkspace = raw.substring(firstColon + 1, secondColon);
                const targetFile = raw.substring(secondColon + 1);

                const codexEl = document.getElementById('echo-codex-hud');
                const currentPath = window.location.pathname;
                const currentChatId = currentPath.startsWith('/c/') ? currentPath.substring(3) : '';

                // Mode Chaud : HUD déjà présent dans le DOM
                if (codexEl && typeof window.sendCodexAction === 'function') {
                    if (targetChatId && targetChatId !== currentChatId) {
                        window.sendCodexAction({
                            action: 'switch_chat',
                            target_chat_id: targetChatId,
                            target_file: targetFile
                        });
                    } else {
                        window.sendCodexAction({
                            action: 'switch_workspace',
                            workspace: targetWorkspace,
                            current_file: targetFile
                        });
                    }
                    codexEl.classList.remove('is-minimized');
                    return;
                }

                // Mode Froid : Préparation de la cible et déclenchement de l'action native
                window._echoCodexTarget = {
                    file: targetFile,
                    workspace: targetWorkspace,
                    target_chat_id: targetChatId
                };

                const token = localStorage.token ? localStorage.token.replace(/^"|"$/g, '') : '';
                const modelId = window._currentModelId || 'echo_orchestrator';

                await fetch('/api/chat/actions/echo_codex_action', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Authorization': `Bearer ${token}`
                    },
                    body: JSON.stringify({
                        chat_id: currentChatId || targetChatId,
                        model: modelId,
                        session_id: window._echoSessionId || '',
                        messages: []
                    })
                });
            });
        })();
        """
        events = EchoEvents(__event_emitter__)
        await events.emit_execute(js_bridge)
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[Dict] = None
    ) -> dict:
        """Valide physiquement l'existence Git et mute les URNs en ancres locales DOM."""
        if not self.valves.ENABLE_CODEX_LINKS:
            return body

        messages = body.get("messages") or []
        if not messages:
            return body

        last_msg = messages[-1]
        if last_msg.get("role") != "assistant":
            return body

        content = last_msg.get("content", "")
        if not isinstance(content, str) or "codex:" not in content:
            return body

        meta = __metadata__ or body.get("metadata", {})
        current_chat_id = meta.get("chat_id") or body.get("chat_id", "")
        user_id = __user__.get("id", "system") if __user__ else "system"

        # 1. Masquage des blocs de code multilignes
        code_blocks = []

        def _mask_code(match):
            idx = len(code_blocks)
            code_blocks.append(match.group(0))
            return f"___ECHO_CODE_MASK_{idx}___"

        masked_content = re.sub(r'```[\s\S]*?```', _mask_code, content)

        # 2. Construction de la Regex URN Codex (capture large pour neutralisation Fail-Closed)
        urn_regex = re.compile(
            r'\[(?P<label>[^\]]+)\]\(codex:(?P<scope>current|[a-zA-Z0-9_\-]+):(?P<ws>[a-zA-Z0-9_\-]+)/(?P<path>[^\)]+)\)'
        )

        # Cache d'existence Git local pour le tour d'Outlet
        repo_cache = {}

        def _resolve_urn(match):
            label = match.group("label")
            scope = match.group("scope")
            ws = match.group("ws")
            path = match.group("path").strip("/")

            target_chat = current_chat_id if scope == "current" else scope
            if not target_chat or ws not in ECHO_CODEX_WORKSPACES:
                return label

            cache_key = (target_chat, ws)
            if cache_key not in repo_cache:
                repo_cache[cache_key] = CodexRepo(user_id=user_id, chat_id=target_chat, workspace=ws)
            repo = repo_cache[cache_key]

            # Validation Fail-Closed
            file_data = repo.read_file(path)
            if not file_data:
                return label  # Anti-hallucination : neutralisation en texte brut

            icon = "📄" if scope == "current" else "🔒"
            return f"[{icon} {label}](#codex:{target_chat}:{ws}:{path})"

        # 3. Remplacement des URNs validées
        transformed = urn_regex.sub(_resolve_urn, masked_content)

        # 4. Restauration des blocs de code
        for idx, block in enumerate(code_blocks):
            transformed = transformed.replace(f"___ECHO_CODE_MASK_{idx}___", block)

        last_msg["content"] = transformed
        return body
