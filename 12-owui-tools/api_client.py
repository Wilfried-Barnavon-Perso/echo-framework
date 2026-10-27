"""
title: ECHO Universal API Client
author: Wilfried BARNAVON
version: 5.231.20.1
description: Composant système interne : ECHO Universal API Client.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Résolution DNS asynchrone non-bloquante (getaddrinfo IPv4/IPv6) et durcissement SSRF (blocage conteneurs internes echo-*).
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.9: Troncature anti-OOM (131K chars) et prévention via Docstring.
# 1.8: Switched complex objects to JSON strings to avoid 400 errors with strict Gemini REST schemas.

import asyncio
import httpx
import orjson as json
import sys
import ipaddress
from urllib.parse import urlparse
from typing import Optional, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_constants import ECHO_ALLOWED_DOMAINS
from echo_constants import ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS


class Tools:
    def __init__(self):
        pass

    async def _is_safe_url(self, url: str) -> bool:
        """Vérifie de manière asynchrone si l'URL pointe vers un réseau privé ou conteneur interne (SSRF protection IPv4/IPv6)."""
        try:
            parsed = urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return False
            hostname = (parsed.hostname or "").strip().lower()
            if not hostname:
                return False

            # Blocage explicite des noms d'hôtes internes Docker / loopback
            if hostname in ("localhost", "host.docker.internal") or hostname.startswith("echo-") or hostname.endswith(".local") or hostname.endswith(".internal"):
                return False

            # Vérification du domaine autorisé
            if ECHO_ALLOWED_DOMAINS != "*":
                allowed = [d.strip().lower() for d in ECHO_ALLOWED_DOMAINS.split(",") if d.strip()]
                if hostname not in allowed:
                    return False

            # Résolution DNS asynchrone complète (IPv4 + IPv6)
            loop = asyncio.get_running_loop()
            addr_infos = await loop.getaddrinfo(hostname, None)
            if not addr_infos:
                return False

            for info in addr_infos:
                ip_str = info[4][0].split("%")[0]
                ip = ipaddress.ip_address(ip_str)
                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_multicast
                    or ip.is_reserved
                    or ip.is_unspecified
                ):
                    return False

            return True
        except Exception:
            return False

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
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__,
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
            except:
                text_out = response.text

            if len(text_out) > ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS:
                text_out = text_out[:ECHO_MAX_TOOL_TEXT_OUTPUT_CHARS] + "\n\n... [Avertissement ECHO : Retour tronqué à 131Ko (Anti-OOM). Délégation à 'code_executor' requise pour analyse intégrale.]"

            if response.status_code >= 400:
                text_out = f"❌ Erreur API {response.status_code}\n\n{text_out}"
            
            return wrap_tool_output(text=text_out, status=status_meta, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Exception API : {str(e)}", status={"status": "error", "error": str(e)}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
