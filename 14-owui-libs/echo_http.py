# -*- coding: utf-8 -*-
"""
title: ECHO Echo Http
author: Wilfried BARNAVON
version: 1.0
description: Client HTTP bas niveau (H2).
"""
import httpx
import time
from typing import Dict, Optional
from echo_constants import ECHO_HTTP_CLIENT_TIMEOUT, ECHO_HTTP_KEEPALIVE_EXPIRY, ECHO_HTTP_MAX_CONNECTIONS, ECHO_HTTP_MAX_KEEPALIVE

class FatalAPIError(Exception):
    """Erreur API fatale (ex: 400 Bad Request) ne nécessitant aucun backoff réseau."""
    pass

async def _get_global_client(
    timeout: int = None,
    max_connections: int = None,
    max_keepalive: int = None,
    keepalive_expiry: int = None
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT (Mutualisé)."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    
    timeout = timeout or ECHO_HTTP_CLIENT_TIMEOUT
    max_connections = max_connections or ECHO_HTTP_MAX_CONNECTIONS
    max_keepalive = max_keepalive or ECHO_HTTP_MAX_KEEPALIVE
    keepalive_expiry = keepalive_expiry or ECHO_HTTP_KEEPALIVE_EXPIRY
    
    now = time.time()

    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > timeout):
        old_client = _SHARED_ASYNC_CLIENT; _SHARED_ASYNC_CLIENT = None
        try: await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(
            max_keepalive_connections=max_keepalive,
            max_connections=max_connections,
            keepalive_expiry=keepalive_expiry
        )
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=timeout, limits=limits, http2=True)

    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

def get_stealth_headers(url: Optional[str] = None) -> Dict[str, str]:
    """Génère des en-têtes HTTP de haute fidélité pour simuler un navigateur réel (Stealth)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",    
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "cross-site",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1"
    }
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Host"] = parsed.netloc
        if any(x in parsed.netloc for x in ["wikimedia", "wikipedia"]):
             headers["sec-fetch-dest"] = "document"
             headers["sec-fetch-mode"] = "navigate"
             headers["sec-fetch-site"] = "none"
             headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    return headers

