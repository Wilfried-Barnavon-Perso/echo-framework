# -*- coding: utf-8 -*-
"""
title: ECHO Echo Http
author: Wilfried BARNAVON
version: 5.231.49.1
description: Client HTTP bas niveau (H2). Utilisation de l'UA furtif global.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.49.1: Factorisation de is_safe_public_url (garde SSRF IPv4/IPv6), sniff_image_mime (magic bytes) et extract_url_filename.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import asyncio
import ipaddress
import mimetypes
import os
import httpx
import time
from urllib.parse import urlparse, unquote
from typing import Dict, Optional
from echo_constants import (
    ECHO_ALLOWED_DOMAINS,
    ECHO_HTTP_CLIENT_TIMEOUT,
    ECHO_HTTP_KEEPALIVE_EXPIRY,
    ECHO_HTTP_MAX_CONNECTIONS,
    ECHO_HTTP_MAX_KEEPALIVE,
    ECHO_STEALTH_USER_AGENT,
)


class FatalAPIError(Exception):
    """Erreur API fatale (ex: 400 Bad Request) ne nécessitant aucun backoff réseau."""
    pass


_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0


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
        old_client = _SHARED_ASYNC_CLIENT
        _SHARED_ASYNC_CLIENT = None
        try:
            await old_client.aclose()
        except Exception:
            pass

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
        "User-Agent": ECHO_STEALTH_USER_AGENT,
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
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Host"] = parsed.netloc
        if any(x in parsed.netloc for x in ["wikimedia", "wikipedia"]):
            headers["sec-fetch-dest"] = "document"
            headers["sec-fetch-mode"] = "navigate"
            headers["sec-fetch-site"] = "none"
            headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    return headers


async def is_safe_public_url(url: str) -> bool:
    """Vérifie de manière asynchrone si l'URL pointe vers un réseau public autorisé (Protection SSRF IPv4/IPv6)."""
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False

        if (
            hostname in ("localhost", "host.docker.internal")
            or hostname.startswith("echo-")
            or hostname.endswith(".local")
            or hostname.endswith(".internal")
        ):
            return False

        if ECHO_ALLOWED_DOMAINS != "*":
            allowed = [d.strip().lower() for d in ECHO_ALLOWED_DOMAINS.split(",") if d.strip()]
            if hostname not in allowed:
                return False

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


def sniff_image_mime(data: bytes, content_type: str = "", url_or_path: str = "") -> Optional[str]:
    """Détecte et valide le type MIME réel d'une image par ses magic bytes, en-tête Content-Type ou extension."""
    if data:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data.startswith(b"\xff\xd8\xff"):
            return "image/jpeg"
        if data.startswith((b"GIF87a", b"GIF89a")):
            return "image/gif"
        if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            return "image/webp"
        if data.startswith(b"BM"):
            return "image/bmp"
        if data.startswith((b"\x00\x00\x01\x00", b"\x00\x00\x02\x00")):
            return "image/x-icon"
        if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (b"avif", b"avis", b"mif1"):
            return "image/avif"
        head_txt = data[:512].lstrip().lower()
        if head_txt.startswith(b"<!doctype html") or head_txt.startswith(b"<html"):
            return None
        if b"<svg" in head_txt:
            return "image/svg+xml"

    clean_ct = (content_type or "").split(";")[0].strip().lower()
    if clean_ct.startswith("image/"):
        return clean_ct

    if url_or_path and not clean_ct.startswith("text/html"):
        clean_path = urlparse(url_or_path).path if "://" in url_or_path else url_or_path
        guessed, _ = mimetypes.guess_type(clean_path)
        if guessed and guessed.startswith("image/"):
            return guessed

    return None


def extract_url_filename(url: str, mime: str = "image/png") -> str:
    """Extrait un nom de fichier propre et doté d'une extension cohérente depuis une URL."""
    ext_map = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/svg+xml": ".svg",
        "image/bmp": ".bmp",
        "image/x-icon": ".ico",
        "image/avif": ".avif",
    }
    default_ext = ext_map.get((mime or "").lower(), ".png")
    try:
        path = unquote(urlparse(url).path or "")
        base = os.path.basename(path.rstrip("/")).strip()
        base = "".join(c for c in base if c not in '<>:"/\\|?*')
        if base:
            _, ext = os.path.splitext(base)
            if not ext:
                base += default_ext
            return base
    except Exception:
        pass
    return f"image_distante{default_ext}"
