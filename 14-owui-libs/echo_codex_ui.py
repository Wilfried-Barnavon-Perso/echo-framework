"""
title: ECHO Codex UI Bridge
author: Wilfried BARNAVON
version: 6.6.2.1
description: Pont DOM universel pour le routage des liens Codex vers l'onglet natif EchoCodexTab.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.2.1: Remplacement du déclenchement HUD modal (/api/chat/actions/echo_codex_action) par CustomEvent('echo:open-tab') et purge de _generate_codex_js.
# 5.238.1.1: Ajout de l'option MODEL_ATHENA dans le sélecteur de modèle IA du HUD Codex (#echo-codex-ai-model).
# 5.237.19.1: Centrage géométrique du label d'en-tête du volet de prévisualisation (Markdown Preview et équivalents).
# 5.237.18.1: Inversion ergonomique des icônes Import (flèche montante Export) et Export (flèche descendante Download) dans le header du HUD Codex.
# 5.232.5.1: Support déclaratif de ECHO_CODEX_WORKSPACES, verrouillage Read-Only automatique sur les workspaces non-inscriptibles (plans, generated) et rafraîchissement de l'aperçu image lors de la navigation d'historique Git (◀ ▶).

import sys

# Importations ECHO Standard
if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_constants import (
    CODEX_ANCHOR_PREFIX,
    CODEX_DEFAULT_WORKSPACE,
)
from echo_ui import EchoUI


class EchoCodexUI(EchoUI):
    """Pont DOM universel pour l'ouverture de l'onglet natif ECHO Codex."""

    @classmethod
    def get_codex_bridge_js(cls) -> str:
        """Génère le pont DOM universel Codex (window.echoOpenCodex + écouteur d'ancres en capture)."""
        return f"""
        (function initEchoCodexBridge() {{
            const ANCHOR_PREFIX = '{CODEX_ANCHOR_PREFIX}';
            const DEFAULT_WS = '{CODEX_DEFAULT_WORKSPACE}';

            if (!document.getElementById('echo-codex-links-style')) {{
                const st = document.createElement('style');
                st.id = 'echo-codex-links-style';
                st.textContent = 'a[href*="' + ANCHOR_PREFIX + '"] {{ text-decoration-skip-ink: none !important; cursor: pointer; }}';
                document.head.appendChild(st);
            }}

            // 1. Fonction universelle d'ouverture du Codex (routage vers l'onglet natif echo-codex)
            window.echoOpenCodex = function(targetChatId, targetWorkspace, targetFile) {{
                const urlMatch = window.location.pathname.match(/\\/c\\/([a-zA-Z0-9_-]+)/);
                const currentChatId = (urlMatch ? urlMatch[1] : '') || (window.EchoWM && window.EchoWM.activeChatId) || '';
                const ws = targetWorkspace || DEFAULT_WS;
                const effectiveCid = targetChatId || currentChatId;
                const hostChatId = currentChatId || effectiveCid;

                window.dispatchEvent(new CustomEvent('echo:open-tab', {{
                    detail: {{
                        chatId: hostChatId,
                        kind: 'echo-codex',
                        payload: {{
                            targetChatId: effectiveCid,
                            workspace: ws,
                            file: targetFile || ''
                        }}
                    }}
                }}));
            }};

            // 2. Micro-routeur d'ancres en phase de capture (court-circuite SvelteKit et neutralise target="_blank")
            window._echoCodexClickHandler = function(e) {{
                const link = e.target && e.target.closest ? e.target.closest('a[href*="' + ANCHOR_PREFIX + '"]') : null;
                if (!link) return;

                link.removeAttribute('target');
                link.setAttribute('target', '_self');
                e.preventDefault();
                e.stopPropagation();

                const href = link.getAttribute('href') || link.href || '';
                const codexIdx = href.indexOf(ANCHOR_PREFIX);
                if (codexIdx === -1) return;

                const raw = href.substring(codexIdx + ANCHOR_PREFIX.length);
                const parts = raw.split(':');
                if (parts.length < 3) return;

                const targetChatId = parts[0];
                const targetWorkspace = parts[1];
                let targetFile = parts.slice(2).join(':');
                try {{ targetFile = decodeURIComponent(targetFile); }} catch (err) {{}}

                window.echoOpenCodex(targetChatId, targetWorkspace, targetFile);
            }};

            if (!window._echoCodexBridgeAttached) {{
                window._echoCodexBridgeAttached = true;
                document.addEventListener('click', (e) => window._echoCodexClickHandler && window._echoCodexClickHandler(e), true);
            }}
        }})();
        """
