"""
title: ECHO Universal API Client
author: Wilfried BARNAVON
version: 5.233.0.1
description: Composant système interne : ECHO Universal API Client.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.233.0.1: Décoration @echo_tool sur execute_http_request et suppression du boilerplate AEC dans wrap_tool_output.
# 5.231.49.1: Délégation de _is_safe_url à la garde SSRF factorisée is_safe_public_url (echo_http.py).
# 5.231.20.1: Résolution DNS asynchrone non-bloquante (getaddrinfo IPv4/IPv6) et durcissement SSRF (blocage conteneurs internes echo-*).
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.

import httpx
import orjson as json
import sys
from typing import Optional, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool, wrap_tool_output  # noqa: E402
from echo_constants import ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS  # noqa: E402
from echo_http import is_safe_public_url  # noqa: E402


class Tools:
    def __init__(self):
        pass

    async def _is_safe_url(self, url: str) -> bool:
        """Vérifie de manière asynchrone si l'URL pointe vers un réseau privé ou conteneur interne (SSRF protection IPv4/IPv6)."""
        return await is_safe_public_url(url)

    @echo_tool
    async def call_api(
        self,
        url: str,
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = "GET",
        headers: Optional[dict] = None,
        body: Optional[dict] = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> str:
        """Exécute une requête HTTP REST directe vers une URL publique externe (hors serveurs MCP)."""
        if not await self._is_safe_url(url):
            return wrap_tool_output(
                text="❌ Accès réseau non autorisé (SSRF ou domaine non whitelisté).",
                status={"status": "error", "domain": url},
            )

        actual_headers = headers if headers else {}
        actual_body = body if body else None

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.request(
                    method=method.upper(),
                    url=url,
                    headers=actual_headers,
                    json=actual_body
                )

            status_meta = {"status": response.status_code, "url": url, "method": method}

            try:
                res_json = response.json()
                text_out = json.dumps(res_json, option=json.OPT_INDENT_2).decode('utf-8')
            except Exception:
                text_out = response.text

            if len(text_out) > ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS:
                text_out = text_out[:ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS] + "\n\n... [Avertissement ECHO : Retour tronqué à 131Ko (Anti-OOM). Délégation à 'code_executor' requise pour analyse intégrale.]"

            if response.status_code >= 400:
                text_out = f"❌ Erreur API {response.status_code}\n\n{text_out}"

            return wrap_tool_output(text=text_out, status=status_meta)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Exception API : {str(e)}", status={"status": "error", "error": str(e)})
