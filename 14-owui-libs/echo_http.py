# -*- coding: utf-8 -*-
"""
title: ECHO Echo Http
author: Wilfried BARNAVON
version: 5.231.59.1
description: Client HTTP bas niveau (H2). Utilisation de l'UA furtif global.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.59.1: Factorisation de load_image_as_base64 (encapsulation pybase64 locale/distante avec garde SSRF, sniff_image_mime et option disallow_svg).
# 5.231.49.1: Factorisation de is_safe_public_url (garde SSRF IPv4/IPv6), sniff_image_mime (magic bytes) et extract_url_filename.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import asyncio
import ipaddress
import mimetypes
import os
import httpx
import pybase64
import time
from urllib.parse import urlparse, unquote
from typing import Any, Dict, Optional
from echo_constants import (
    ECHO_ALLOWED_DOMAINS,
    ECHO_HTTP_CLIENT_TIMEOUT,
    ECHO_HTTP_KEEPALIVE_EXPIRY,
    ECHO_HTTP_MAX_CONNECTIONS,
    ECHO_HTTP_MAX_KEEPALIVE,
    ECHO_STEALTH_USER_AGENT,
)
from echo_paths import extract_clean_filename, resolve_extended_file_path


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


async def load_image_as_base64(
    target: str,
    user_id: str,
    chat_id: Optional[str] = None,
    workspace: Optional[str] = None,
    max_bytes: int = 30 * 1024 * 1024,
    disallow_svg: bool = False,
) -> Dict[str, Any]:
    """
    Charge une image locale (Codex/Workspace/Vault) ou distante (HTTP/HTTPS anti-SSRF)
    et l'encode en Base64 via pybase64 sans passer par le contexte du LLM.
    """
    clean_target = (target or "").strip()
    if not clean_target:
        return {"ok": False, "error_code": "empty_target", "error": "Cible d'image vide."}

    is_url = clean_target.lower().startswith(("http://", "https://"))
    if not is_url and (clean_target.startswith("/") or (len(clean_target) > 1 and clean_target[1] == ":")):
        return {
            "ok": False,
            "is_url": False,
            "error_code": "forbidden_path",
            "error": "Sécurité : Accès restreint.",
        }

    if is_url:
        if not await is_safe_public_url(clean_target):
            return {
                "ok": False,
                "is_url": True,
                "error_code": "ssrf_blocked",
                "error": "Sécurité SSRF : L'accès aux adresses locales, privées ou internes est strictement interdit.",
            }
        headers = get_stealth_headers(clean_target)
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                async with c.stream("GET", clean_target, headers=headers) as r:
                    r.raise_for_status()
                    content_type = r.headers.get("content-type", "").lower()
                    chunks = []
                    total_read = 0
                    async for chunk in r.aiter_bytes(chunk_size=65536):
                        total_read += len(chunk)
                        if total_read > max_bytes:
                            return {
                                "ok": False,
                                "is_url": True,
                                "error_code": "too_large",
                                "error": f"L'image distante dépasse la limite autorisée ({max_bytes // 1024} Ko).",
                            }
                        chunks.append(chunk)
                    raw_bytes = b"".join(chunks)

            mime = sniff_image_mime(raw_bytes, content_type=content_type, url_or_path=clean_target)
            if not mime:
                return {
                    "ok": False,
                    "is_url": True,
                    "error_code": "invalid_mime",
                    "content_type": content_type,
                    "error": f"Ressource distante rejetée : type MIME détecté `{content_type or 'inconnu'}` invalide (image requise).",
                }
            if disallow_svg and mime == "image/svg+xml":
                return {
                    "ok": False,
                    "is_url": True,
                    "error_code": "svg_not_supported",
                    "error": "Le format vectoriel SVG n'est pas supporté en entrée vision raster (formats acceptés : PNG, JPEG, WEBP, GIF).",
                }
            fname = extract_url_filename(clean_target, mime=mime)
            b64 = pybase64.b64encode(raw_bytes).decode("utf-8")
            return {
                "ok": True,
                "is_url": True,
                "b64": b64,
                "mime": mime,
                "filename": fname,
                "raw_bytes": raw_bytes,
                "size_bytes": len(raw_bytes),
            }
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            fname = extract_url_filename(clean_target)
            if status_code == 404:
                return {
                    "ok": False,
                    "is_url": True,
                    "error_code": "http_404",
                    "http_status": 404,
                    "filename": fname,
                    "error": "Ressource distante introuvable (HTTP 404).",
                }
            return {
                "ok": False,
                "is_url": True,
                "error_code": "http_status",
                "http_status": status_code,
                "filename": fname,
                "remote_fallback": True,
                "error": f"Requête serveur filtrée par l'hôte distant (HTTP {status_code}).",
            }
        except Exception as e:
            fname = extract_url_filename(clean_target)
            return {
                "ok": False,
                "is_url": True,
                "error_code": "timeout_or_network",
                "filename": fname,
                "remote_fallback": True,
                "error": f"Délai ou erreur réseau lors du téléchargement de l'image distante : {str(e)}",
            }

    fpath = resolve_extended_file_path(user_id, chat_id, clean_target, workspace=workspace)
    if not fpath or not os.path.isfile(fpath):
        return {
            "ok": False,
            "is_url": False,
            "error_code": "not_found",
            "error": f"Ressource image locale `{clean_target}` introuvable dans l'Espace Personnel.",
        }
    try:
        fsize = os.path.getsize(fpath)
        if fsize > max_bytes:
            return {
                "ok": False,
                "is_url": False,
                "error_code": "too_large",
                "error": f"L'image locale dépasse la limite autorisée ({max_bytes // 1024} Ko).",
            }
        with open(fpath, "rb") as f:
            raw_bytes = f.read()
        mime = sniff_image_mime(raw_bytes, url_or_path=fpath)
        if not mime:
            return {
                "ok": False,
                "is_url": False,
                "error_code": "invalid_mime",
                "error": f"Le fichier local `{clean_target}` n'est pas une image valide.",
            }
        if disallow_svg and mime == "image/svg+xml":
            return {
                "ok": False,
                "is_url": False,
                "error_code": "svg_not_supported",
                "error": "Le format vectoriel SVG n'est pas supporté en entrée vision raster (formats acceptés : PNG, JPEG, WEBP, GIF).",
            }
        b64 = pybase64.b64encode(raw_bytes).decode("utf-8")
        fname = extract_clean_filename(fpath)
        return {
            "ok": True,
            "is_url": False,
            "b64": b64,
            "mime": mime,
            "filename": fname,
            "filepath": fpath,
            "raw_bytes": raw_bytes,
            "size_bytes": len(raw_bytes),
        }
    except Exception as e:
        return {
            "ok": False,
            "is_url": False,
            "error_code": "read_error",
            "error": f"Erreur lecture fichier image : {str(e)}",
        }

