# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# VERSION : 6.11.0.1
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.11.0.1: Moteur streaming résumable PPCM 5 MiB (_stream_resumable_put), upload_file_stream Google Drive & OneDrive, cache/lock OAuth2 Google.
# 6.7.18.3: Socle factorisé (HTML/SafeLinks, compactage schémas, Download Broker structuredContent), fix Google account_id/email/staging, cloisonnement multi-tenants & Graph M365.
# 5.239.12.1: Sémaphore MSA, client frais par retry, erreur list_mail_folders, fix KQL/OData et unified_search.
# 5.239.11.2: Alignement Graph sur microsoft-mcp (Prefer conditionnel, omission $skip=0 et retry 5xx/429).
# 5.239.11.1: Refresh MSAL async/fallback, transport Graph IPv4, URL quote, include_body=False et sync_credentials.
"""
ECHO MCP Broker - Adaptateurs de cycle de vie spécifiques aux fournisseurs MCP résidents.
Isole la logique d'authentification et de préparation d'environnement (Microsoft 365 MSAL,
Google Workspace OAuth2) hors de l'orchestrateur de transport générique m5_proxy_mcp.py.
"""
import ast
import asyncio
import base64
import hashlib
import html
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional, Tuple, Union

import httpx
try:
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover
    BeautifulSoup = None
from core.security import chat_id_var

logger = logging.getLogger(__name__)

MCP_CACHE_ROOT = os.environ.get("ECHO_MCP_CACHE_DIR", "/app/mcp_cache")
MCP_DOWNLOADS_ROOT = os.environ.get("ECHO_DOWNLOADS_DIR", "/app/downloads")
RESUMABLE_UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024  # 5 MiB (PPCM de 256 KiB Google et 320 KiB Microsoft Graph)
MAX_DRIVE_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024  # 4 Go
UPLOAD_CONCURRENCY_PER_ACCOUNT = 3

_GOOGLE_NATIVE_CONVERSION_MAP: dict[str, tuple[str, str]] = {
    ".md": ("text/markdown", "application/vnd.google-apps.document"),
    ".markdown": ("text/markdown", "application/vnd.google-apps.document"),
    ".txt": ("text/plain", "application/vnd.google-apps.document"),
    ".html": ("text/html", "application/vnd.google-apps.document"),
    ".htm": ("text/html", "application/vnd.google-apps.document"),
    ".rtf": ("application/rtf", "application/vnd.google-apps.document"),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/vnd.google-apps.document"),
    ".doc": ("application/msword", "application/vnd.google-apps.document"),
    ".odt": ("application/vnd.oasis.opendocument.text", "application/vnd.google-apps.document"),
    ".csv": ("text/csv", "application/vnd.google-apps.spreadsheet"),
    ".tsv": ("text/tab-separated-values", "application/vnd.google-apps.spreadsheet"),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.google-apps.spreadsheet"),
    ".xls": ("application/vnd.ms-excel", "application/vnd.google-apps.spreadsheet"),
    ".ods": ("application/vnd.oasis.opendocument.spreadsheet", "application/vnd.google-apps.spreadsheet"),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "application/vnd.google-apps.presentation"),
    ".ppt": ("application/vnd.ms-powerpoint", "application/vnd.google-apps.presentation"),
    ".odp": ("application/vnd.oasis.opendocument.presentation", "application/vnd.google-apps.presentation"),
}

_WORKSPACE_UUID_SUFFIX_RE = re.compile(
    r"_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=\.[^.]+$|$)"
)
_SAVED_TO_LINE_RE = re.compile(r"(Saved to:\s*)([^\r\n]+)")


class AuthRequiredError(Exception):
    """Levée lorsque des variables d'authentification obligatoires sont manquantes ou expirées."""
    pass


class MCPServiceProvider:
    """Interface de base (No-Op) pour les adaptateurs de services MCP."""

    @staticmethod
    def _make_tool_response(payload: Any, is_error: bool = False) -> dict:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        }

    @staticmethod
    def _sanitize_segment(val: Optional[str], fallback: str = "anonymous") -> str:
        cleaned = "".join(
            c for c in str(val or "") if c.isalnum() or c in ("-", "_", ".")
        ).strip(".")
        return cleaned or fallback

    @staticmethod
    def _sanitize_filename(filename: Optional[str], fallback: str = "downloaded_file") -> str:
        if not filename:
            return fallback
        base = os.path.basename(str(filename).replace("\\", "/")).strip()
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).rstrip(" .")
        if not sanitized or sanitized in (".", ".."):
            return fallback
        return sanitized

    @staticmethod
    def _extract_hex_fid(filename: str) -> Optional[str]:
        if "_" not in filename:
            return None
        prefix = filename.split("_", 1)[0].lower()
        if len(prefix) == 8 and all(c in "0123456789abcdef" for c in prefix):
            return prefix
        return None

    @classmethod
    def _get_mcp_drop_dir(cls, user_id: Optional[str], chat_id: Optional[str] = None) -> str:
        resolved_cid = chat_id or chat_id_var.get() or "default_chat"
        safe_uid = cls._sanitize_segment(user_id, "anonymous")
        safe_cid = cls._sanitize_segment(resolved_cid, "default_chat")
        drop_dir = os.path.join(MCP_DOWNLOADS_ROOT, safe_uid, safe_cid, "mcp")
        os.makedirs(drop_dir, exist_ok=True)
        for path_to_chmod in (
            MCP_DOWNLOADS_ROOT,
            os.path.join(MCP_DOWNLOADS_ROOT, safe_uid),
            os.path.join(MCP_DOWNLOADS_ROOT, safe_uid, safe_cid),
            drop_dir,
        ):
            try:
                os.chmod(path_to_chmod, 0o777)
            except Exception:
                pass
        return drop_dir

    @classmethod
    def _publish_file_to_broker(
        cls,
        src_or_bytes: Union[bytes, bytearray, str],
        user_id: Optional[str],
        chat_id: Optional[str],
        filename: Optional[str],
        fid: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Écrit ou déplace atomiquement un fichier vers la Drop-Zone MCP du Download Broker
        via un fichier temporaire `.part`, ajuste les permissions (0o666) et antidate `st_mtime`
        de 5s pour permettre une collecte sans latence par `download_broker.py`.
        Retourne `(fid, safe_name, final_path)`.
        """
        fid = (fid or uuid.uuid4().hex[:8]).lower()
        clean_name = cls._sanitize_filename(filename)
        if clean_name.lower().startswith(f"{fid}_"):
            safe_name = f"{fid}_{clean_name.split('_', 1)[1]}"
        else:
            safe_name = f"{fid}_{clean_name}"

        drop_dir = cls._get_mcp_drop_dir(user_id, chat_id)
        final_path = os.path.join(drop_dir, safe_name)
        part_path = f"{final_path}.part"

        if isinstance(src_or_bytes, (bytes, bytearray)):
            with open(part_path, "wb") as f_out:
                f_out.write(src_or_bytes)
        elif isinstance(src_or_bytes, str):
            if os.path.abspath(src_or_bytes) != os.path.abspath(part_path):
                shutil.move(src_or_bytes, part_path)
        else:
            raise TypeError("src_or_bytes doit être bytes ou un chemin de fichier (str).")

        try:
            os.chmod(part_path, 0o666)
        except Exception:
            pass
        try:
            ready_mtime = time.time() - 5.0
            os.utime(part_path, (ready_mtime, ready_mtime))
        except Exception:
            pass

        os.replace(part_path, final_path)
        return fid, safe_name, final_path

    @staticmethod
    def unwrap_tracking_url(url: str) -> str:
        """Décode les wrappers de tracking connus (ex: Outlook SafeLinks)."""
        raw = str(url or "").strip()
        if not raw:
            return ""
        try:
            parsed = urllib.parse.urlparse(raw)
            if "safelinks.protection.outlook.com" in (parsed.netloc or "").lower():
                qs = urllib.parse.parse_qs(parsed.query)
                target = (qs.get("url") or [None])[0]
                if target:
                    target_str = str(target).strip()
                    if target_str.lower().startswith(("http://", "https://")):
                        return target_str
        except Exception:
            pass
        return raw

    @staticmethod
    def clean_html_to_text(html_text: str) -> str:
        """Convertit un HTML en texte brut dense tout en préservant les liens et boutons."""
        if not html_text or not isinstance(html_text, str):
            return ""
        if "<" not in html_text or ">" not in html_text:
            return html.unescape(html_text).strip()

        if BeautifulSoup is not None:
            soup = BeautifulSoup(html_text, "html.parser")
            for tag in soup(["style", "script", "head", "noscript", "meta", "link"]):
                tag.decompose()
            for a_tag in soup.find_all("a", href=True):
                clean_href = MCPServiceProvider.unwrap_tracking_url(a_tag.get("href", ""))
                if clean_href.lower().startswith(("http://", "https://", "mailto:")):
                    label = " ".join(a_tag.get_text(" ", strip=True).split())
                    if not label:
                        img = a_tag.find("img", alt=True)
                        if img is not None:
                            label = " ".join(str(img.get("alt") or "").split())
                    if label and clean_href not in label:
                        replacement = f"{label} ({clean_href})"
                    else:
                        replacement = clean_href or label
                    a_tag.replace_with(f" {replacement} ")
            for br in soup.find_all("br"):
                br.replace_with("\n")
            for block in soup.find_all(
                [
                    "p",
                    "div",
                    "tr",
                    "li",
                    "h1",
                    "h2",
                    "h3",
                    "h4",
                    "h5",
                    "h6",
                    "section",
                    "article",
                    "blockquote",
                ]
            ):
                block.insert_before("\n")
                block.insert_after("\n")
            raw_text = html.unescape(soup.get_text())
        else:
            text = re.sub(r"(?is)<style.*?>.*?</style>", "", html_text)
            text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)
            text = re.sub(r"(?i)<br\s*/?>", "\n", text)
            text = re.sub(r"(?i)</(?:p|div|tr|li|h[1-6]|section|article|blockquote)>", "\n", text)
            text = re.sub(r"<[^>]+>", "", text)
            raw_text = html.unescape(text)

        lines: list[str] = []
        prev_empty = True
        for raw_line in raw_text.splitlines():
            line = " ".join(raw_line.split())
            if not line:
                if not prev_empty:
                    lines.append("")
                    prev_empty = True
            else:
                lines.append(line)
                prev_empty = False
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    @classmethod
    def _compact_tools_catalog(
        cls,
        result: dict,
        optional_props: Optional[set[str]] = None,
        prop_descriptions: Optional[dict[str, str]] = None,
        strip_defaults: Optional[set[str]] = None,
        max_desc_len: int = 280,
    ) -> dict:
        """Compacte le catalogue `tools` (descriptions, outputSchema, inputSchema)."""
        if not isinstance(result, dict) or not isinstance(result.get("tools"), list):
            return result
        opt_props = optional_props or set()
        desc_map = prop_descriptions or {}
        no_def = strip_defaults or set()

        def _truncate_desc(raw_desc: Any, limit: int) -> str:
            first_block = " ".join(str(raw_desc or "").strip().split("\n\n", 1)[0].split())
            if len(first_block) > limit:
                cut = first_block[: max(limit - 3, 1)].rstrip()
                if " " in cut:
                    cut = cut.rsplit(" ", 1)[0]
                return f"{cut}..."
            return first_block

        compacted_tools = []
        for tool in result["tools"]:
            if not isinstance(tool, dict):
                compacted_tools.append(tool)
                continue
            t_copy = dict(tool)
            t_copy.pop("outputSchema", None)
            if t_copy.get("description"):
                t_copy["description"] = _truncate_desc(t_copy["description"], max_desc_len)
            schema = t_copy.get("inputSchema")
            if isinstance(schema, dict):
                clean_schema = dict(schema)
                props = clean_schema.get("properties")
                if isinstance(props, dict):
                    clean_props = {}
                    for p_name, p_def in props.items():
                        if isinstance(p_def, dict):
                            p_copy = {k: v for k, v in p_def.items() if k != "title"}
                            if p_name in desc_map:
                                p_copy["description"] = desc_map[p_name]
                            if p_copy.get("description"):
                                p_copy["description"] = _truncate_desc(p_copy["description"], 140)
                            if p_name in no_def:
                                p_copy.pop("default", None)
                            clean_props[p_name] = p_copy
                        else:
                            clean_props[p_name] = p_def
                    clean_schema["properties"] = clean_props
                req = clean_schema.get("required")
                if isinstance(req, list) and opt_props:
                    clean_schema["required"] = [r for r in req if r not in opt_props]
                t_copy["inputSchema"] = clean_schema
            compacted_tools.append(t_copy)
        out = dict(result)
        out["tools"] = compacted_tools
        return out

    @classmethod
    def _build_broker_download_payload(
        cls,
        fid: str,
        safe_name: str,
        original_name: str,
        final_path: str,
        size_bytes: int,
        content_type: str = "application/octet-stream",
    ) -> dict:
        """Construit le payload unifié d'un fichier déposé dans le Download Broker."""
        return {
            "status": "downloaded_to_broker",
            "file_id": fid,
            "name": safe_name,
            "original_name": original_name,
            "path": final_path,
            "saved_to": final_path,
            "content_type": content_type,
            "mime_type": content_type,
            "size": size_bytes,
            "size_mb": round(size_bytes / (1024 * 1024), 2),
        }

    @classmethod
    def _rewrite_result_with_broker_downloads(
        cls,
        result: dict,
        moved_map: dict[str, Tuple[str, str, str]],
    ) -> dict:
        """Réécrit `content` et `structuredContent` après routage vers le Download Broker."""
        if not isinstance(result, dict) or not moved_map:
            return result
        summary_lines = "".join(
            f"\n📦 ECHO Download Broker: file_id='{fid}', name='{safe_name}', path='{new_path}'"
            for fid, safe_name, new_path in moved_map.values()
        )

        def _rewrite_text(txt: str) -> str:
            for old_path, (_fid, safe_name, new_path) in moved_map.items():
                txt = txt.replace(old_path, new_path)
                old_fname = os.path.basename(old_path)
                if old_fname != safe_name:
                    txt = txt.replace(old_fname, safe_name)
            return txt + summary_lines

        out = dict(result)
        content_list = out.get("content")
        if isinstance(content_list, list):
            new_content = []
            for item in content_list:
                if isinstance(item, dict) and item.get("type") == "text":
                    new_item = dict(item)
                    new_item["text"] = _rewrite_text(item.get("text", ""))
                    new_content.append(new_item)
                else:
                    new_content.append(item)
            out["content"] = new_content

        sc = out.get("structuredContent")
        if isinstance(sc, dict):
            sc_copy = dict(sc)
            if isinstance(sc_copy.get("result"), str):
                sc_copy["result"] = _rewrite_text(sc_copy["result"])
            else:
                sc_copy["echo_downloads"] = [
                    {"file_id": fid, "name": safe_name, "path": new_path}
                    for fid, safe_name, new_path in moved_map.values()
                ]
            out["structuredContent"] = sc_copy
        return out

    async def sync_credentials(self, user_id: str, credentials: dict) -> Optional[str]:
        """Synchronise la configuration d'identifiants du service avant appel d'outil ou lancement Stdio."""
        return None

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
        cache_key: Optional[str] = None,
    ) -> None:
        """Prépare le système de fichiers et les variables d'environnement avant lancement Stdio."""
        return None

    async def after_list_tools(
        self,
        user_id: str,
        result: dict,
        credentials: Optional[dict] = None,
    ) -> dict:
        """Hook exécuté après `session.list_tools()` sur le dictionnaire sérialisé."""
        return result

    async def before_tool_call(
        self,
        user_id: str,
        tool_name: str,
        args: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        account_id: Optional[str] = None,
    ) -> Tuple[dict, Optional[dict]]:
        """
        Hook exécuté avant `session.call_tool(tool_name, args)`.
        Retourne `(normalized_args, short_circuit_response)`.
        Si `short_circuit_response` n'est pas None, l'appel MCP est court-circuité.
        """
        return args, None

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        cache_key: Optional[str] = None,
    ) -> dict:
        """Hook exécuté après `session.call_tool(tool_name, args)` sur le résultat sérialisé."""
        return result

    _UPLOAD_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
    _UPLOAD_TARGET_LOCKS: dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_upload_semaphore(cls, service: str, user_id: str, account_sig: str) -> asyncio.Semaphore:
        key = f"{id(asyncio.get_running_loop())}:{service}:{cls._sanitize_segment(user_id)}:{account_sig}"
        sem = cls._UPLOAD_SEMAPHORES.get(key)
        if sem is None:
            sem = asyncio.Semaphore(UPLOAD_CONCURRENCY_PER_ACCOUNT)
            cls._UPLOAD_SEMAPHORES[key] = sem
        return sem

    @classmethod
    def _get_upload_target_lock(cls, service: str, user_id: str, account_sig: str, target_key: str) -> asyncio.Lock:
        key = f"{id(asyncio.get_running_loop())}:{service}:{cls._sanitize_segment(user_id)}:{account_sig}:{target_key}"
        lock = cls._UPLOAD_TARGET_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._UPLOAD_TARGET_LOCKS[key] = lock
        return lock

    @classmethod
    def _create_upload_http_client(cls, timeout_sec: float = 120.0) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0)
        timeout = httpx.Timeout(timeout_sec, connect=10.0)
        return httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False)

    @classmethod
    def _build_drive_upload_payload(
        cls,
        service: str,
        file_id: str,
        name: str,
        size: int,
        mime_type: str,
        folder_id: Optional[str] = None,
        web_url: Optional[str] = None,
        converted_from: Optional[str] = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "status": "uploaded",
            "service": service,
            "file_id": file_id,
            "name": name,
            "size": size,
            "mime_type": mime_type,
            "folder_id": folder_id,
            "web_url": web_url,
        }
        if converted_from is not None:
            payload["converted_from"] = converted_from
        return payload

    @classmethod
    async def _abort_resumable_session(cls, upload_url: str) -> None:
        if not upload_url:
            return
        try:
            async with cls._create_upload_http_client(timeout_sec=10.0) as client:
                await client.delete(upload_url)
        except Exception:
            pass

    @classmethod
    def _parse_retry_after(cls, resp: httpx.Response, attempt: int) -> float:
        raw = resp.headers.get("Retry-After")
        if raw:
            try:
                return max(0.5, min(float(raw), 15.0))
            except Exception:
                pass
        return min(2.0 ** attempt, 15.0)

    @classmethod
    async def _send_resumable_put_chunk(
        cls,
        upload_url: str,
        chunk: bytes,
        headers: dict[str, str],
        max_retries: int,
    ) -> Optional[dict]:
        last_err: Optional[Exception] = None
        for attempt in range(max_retries):
            try:
                async with cls._create_upload_http_client(timeout_sec=120.0) as client:
                    resp = await client.put(upload_url, content=chunk, headers=headers)
                if resp.status_code in (200, 201):
                    return resp.json() if resp.content else {}
                if resp.status_code in (202, 308):
                    return None
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt + 1 < max_retries:
                        await asyncio.sleep(cls._parse_retry_after(resp, attempt))
                        continue
                    await cls._abort_resumable_session(upload_url)
                    raise RuntimeError(f"Échec upload résumable HTTP {resp.status_code}: {resp.text[:400]}")
                if resp.status_code in (401, 403):
                    low = resp.text.lower()
                    if any(m in low for m in ("invalid", "expired", "unauthenticated", "insufficient", "scope", "permission")):
                        await cls._abort_resumable_session(upload_url)
                        raise AuthRequiredError(f"Authentification/permissions insuffisantes (HTTP {resp.status_code}): {resp.text[:300]}")
                await cls._abort_resumable_session(upload_url)
                raise RuntimeError(f"Erreur upload résumable HTTP {resp.status_code}: {resp.text[:400]}")
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_err = exc
                if attempt + 1 < max_retries:
                    await asyncio.sleep(min(2.0 ** attempt, 15.0))
                    continue
                await cls._abort_resumable_session(upload_url)
                raise RuntimeError(f"Erreur réseau upload résumable : {exc}") from exc
            except (AuthRequiredError, RuntimeError, ValueError):
                raise
            except Exception as exc:
                await cls._abort_resumable_session(upload_url)
                raise RuntimeError(f"Erreur inattendue upload résumable : {exc}") from exc
        await cls._abort_resumable_session(upload_url)
        raise RuntimeError(f"Échec upload résumable après {max_retries} tentatives: {last_err}")

    @classmethod
    async def _stream_resumable_put(
        cls,
        upload_url: str,
        byte_stream: AsyncIterator[bytes],
        total_size: int,
        content_type: str,
        chunk_size: int = RESUMABLE_UPLOAD_CHUNK_BYTES,
        max_retries: int = 3,
    ) -> dict:
        if total_size == 0:
            async for raw_chunk in byte_stream:
                if raw_chunk:
                    await cls._abort_resumable_session(upload_url)
                    raise ValueError("Flux non vide reçu alors que total_size == 0.")
            headers = {
                "Content-Type": content_type,
                "Content-Length": "0",
                "Content-Range": "bytes */0",
            }
            res = await cls._send_resumable_put_chunk(upload_url, b"", headers, max_retries)
            return res if isinstance(res, dict) else {}

        buf = bytearray()
        offset = 0
        final_json: Optional[dict] = None

        async for raw_chunk in byte_stream:
            if not raw_chunk:
                continue
            buf.extend(raw_chunk)
            while len(buf) >= chunk_size:
                chunk = bytes(buf[:chunk_size])
                del buf[:chunk_size]
                if offset + len(chunk) > total_size:
                    await cls._abort_resumable_session(upload_url)
                    raise ValueError("Flux d'entrée supérieur à total_size déclaré.")
                headers = {
                    "Content-Type": content_type,
                    "Content-Length": str(len(chunk)),
                    "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{total_size}",
                }
                res = await cls._send_resumable_put_chunk(upload_url, chunk, headers, max_retries)
                offset += len(chunk)
                if res is not None:
                    final_json = res

        if buf:
            chunk = bytes(buf)
            if offset + len(chunk) > total_size:
                await cls._abort_resumable_session(upload_url)
                raise ValueError("Flux d'entrée supérieur à total_size déclaré.")
            headers = {
                "Content-Type": content_type,
                "Content-Length": str(len(chunk)),
                "Content-Range": f"bytes {offset}-{offset + len(chunk) - 1}/{total_size}",
            }
            res = await cls._send_resumable_put_chunk(upload_url, chunk, headers, max_retries)
            offset += len(chunk)
            if res is not None:
                final_json = res

        if offset != total_size:
            await cls._abort_resumable_session(upload_url)
            raise ValueError(f"Flux incomplet : {offset} octets envoyés sur {total_size} attendus.")
        if final_json is None:
            await cls._abort_resumable_session(upload_url)
            raise RuntimeError("Aucune réponse finale 200/201 reçue après envoi de la dernière tranche.")
        return final_json

    async def upload_file_stream(
        self,
        user_id: str,
        chat_id: str,
        credentials: dict,
        byte_stream: AsyncIterator[bytes],
        filename: str,
        total_size: int,
        mime_type: str = "application/octet-stream",
        folder_id: Optional[str] = None,
        overwrite_file_id: Optional[str] = None,
        convert_to_native: bool = False,
        conflict_behavior: str = "replace",
        account_id: Optional[str] = None,
    ) -> dict:
        raise ValueError("Upload Drive non supporté pour ce fournisseur MCP.")


class GoogleWorkspaceProvider(MCPServiceProvider):
    """Adaptateur OAuth2 pour le serveur résident Google Workspace (`workspace-mcp`)."""

    OAUTH_ERROR_MARKERS = (
        "localhost:8001",
        "oauth2callback",
        "accounts.google.com/o/oauth2",
        "invalid_grant",
        "RefreshError",
        "Token has been expired or revoked",
        "Permission required for",
        "insufficient authentication scopes",
    )
    _SESSION_STAGING_DIRS: dict[str, str] = {}
    _GOOGLE_TOKEN_CACHE: dict[str, dict] = {}
    _GOOGLE_TOKEN_LOCKS: dict[str, asyncio.Lock] = {}

    @classmethod
    def _get_google_token_lock(cls, cache_id: str) -> asyncio.Lock:
        lock = cls._GOOGLE_TOKEN_LOCKS.get(cache_id)
        if lock is None:
            lock = asyncio.Lock()
            cls._GOOGLE_TOKEN_LOCKS[cache_id] = lock
        return lock

    @classmethod
    def _google_token_cache_id(cls, user_id: str, credentials: dict) -> str:
        client_id = str((credentials or {}).get("GOOGLE_OAUTH_CLIENT_ID") or "")
        refresh_token = str((credentials or {}).get("GOOGLE_OAUTH_REFRESH_TOKEN") or "")
        sig = hashlib.md5(f"{client_id}:{refresh_token}".encode()).hexdigest()[:16]
        return f"{id(asyncio.get_running_loop())}:{cls._sanitize_segment(user_id)}:{sig}"

    async def _refresh_google_access_token(
        self,
        user_id: str,
        credentials: dict,
        required_any_scopes: Optional[set[str]] = None,
    ) -> dict:
        refresh_token = credentials.get("GOOGLE_OAUTH_REFRESH_TOKEN")
        user_email = credentials.get("USER_GOOGLE_EMAIL")
        if not (refresh_token and user_email):
            raise AuthRequiredError(
                "Identifiants Google Workspace incomplets (USER_GOOGLE_EMAIL ou GOOGLE_OAUTH_REFRESH_TOKEN manquant)."
            )

        client_id = credentials.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = credentials.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        cache_id = self._google_token_cache_id(user_id, credentials)

        async with self._get_google_token_lock(cache_id):
            cached = self._GOOGLE_TOKEN_CACHE.get(cache_id)
            now = time.time()
            if cached and now < float(cached.get("expires_at", 0.0)) - 60.0:
                entry = cached
            else:
                try:
                    async with httpx.AsyncClient() as client:
                        token_resp = await client.post(
                            "https://oauth2.googleapis.com/token",
                            data={
                                "client_id": client_id,
                                "client_secret": client_secret,
                                "refresh_token": refresh_token,
                                "grant_type": "refresh_token",
                            },
                            timeout=10.0,
                        )
                except Exception as ex:
                    logger.warning("Google OAuth refresh network exception: %s", ex)
                    raise AuthRequiredError(
                        f"Échec réseau lors du rafraîchissement du jeton OAuth2 Google Workspace : {ex}"
                    ) from ex

                if token_resp.status_code != 200:
                    err_detail = f"HTTP {token_resp.status_code}"
                    try:
                        err_data = token_resp.json()
                        err_code = err_data.get("error", "unknown_error")
                        err_desc = err_data.get("error_description", "")
                        err_detail = f"{err_code} ({err_desc})" if err_desc else str(err_code)
                    except Exception:
                        pass
                    logger.warning("Google OAuth refresh failed: %s", err_detail)
                    raise AuthRequiredError(
                        f"Authentification Google Workspace invalide ou expirée ({err_detail}). "
                        "Le rafraîchissement automatique OAuth2 a échoué. Les flux d'authentification "
                        "interactive locale (localhost:8001) sont strictement proscrits en environnement conteneurisé."
                    )

                t_data = token_resp.json()
                access_token = t_data.get("access_token")
                if not access_token:
                    raise AuthRequiredError(
                        "Réponse OAuth2 Google invalide (aucun access_token retourné). "
                        "Veuillez régénérer GOOGLE_OAUTH_REFRESH_TOKEN."
                    )

                scopes = t_data["scope"].split(" ") if "scope" in t_data and t_data["scope"] else []
                expires_in = int(t_data.get("expires_in") or 3600)
                expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
                entry = {
                    "token": access_token,
                    "scopes": scopes,
                    "expires_in": expires_in,
                    "expires_at": now + expires_in,
                    "expiry": expiry_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                    "cache_id": cache_id,
                }
                self._GOOGLE_TOKEN_CACHE[cache_id] = entry

        if required_any_scopes and entry.get("scopes"):
            if not (set(entry["scopes"]) & set(required_any_scopes)):
                raise AuthRequiredError(
                    "Le jeton Google Workspace ne possède pas le scope d'écriture Drive requis (drive ou drive.file)."
                )
        return entry

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
        cache_key: Optional[str] = None,
    ) -> None:
        t_info = await self._refresh_google_access_token(user_id, credentials)
        user_email = credentials.get("USER_GOOGLE_EMAIL", "")
        token_data = {
            "token": t_info["token"],
            "refresh_token": credentials.get("GOOGLE_OAUTH_REFRESH_TOKEN"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": credentials.get("GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": credentials.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "expiry": t_info["expiry"],
        }
        if t_info.get("scopes"):
            token_data["scopes"] = t_info["scopes"]

        temp_dir_path = stack.enter_context(tempfile.TemporaryDirectory())
        safe_email = urllib.parse.quote(user_email, safe="@._-")
        token_file = os.path.join(temp_dir_path, f"{safe_email}.json")

        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        env["WORKSPACE_MCP_CREDENTIALS_DIR"] = temp_dir_path
        attachments_staging = os.path.join(temp_dir_path, "attachments")
        os.makedirs(attachments_staging, exist_ok=True)
        env["WORKSPACE_ATTACHMENT_DIR"] = attachments_staging
        session_key = cache_key or f"{self._sanitize_segment(user_id)}_default"
        self._SESSION_STAGING_DIRS[session_key] = attachments_staging
        stack.callback(self._SESSION_STAGING_DIRS.pop, session_key, None)

    async def after_list_tools(
        self,
        user_id: str,
        result: dict,
        credentials: Optional[dict] = None,
    ) -> dict:
        return self._compact_tools_catalog(
            result,
            optional_props={"user_google_email"},
            prop_descriptions={
                "user_google_email": "Auto-injected.",
            },
            strip_defaults={"user_google_email"},
            max_desc_len=115,
        )

    async def before_tool_call(
        self,
        user_id: str,
        tool_name: str,
        args: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        account_id: Optional[str] = None,
    ) -> Tuple[dict, Optional[dict]]:
        args = dict(args or {})
        args.pop("account_id", None)
        if not args.get("user_google_email") and (credentials or {}).get("USER_GOOGLE_EMAIL"):
            args["user_google_email"] = str(credentials["USER_GOOGLE_EMAIL"]).strip()
        return args, None

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        cache_key: Optional[str] = None,
    ) -> dict:
        """
        Bouclier anti-pollution : intercepte toute tentative de `workspace-mcp` d'exposer
        une URL locale (localhost:8001/oauth2callback) ou une erreur d'expiration OAuth2
        en cours de session, et lève `AuthRequiredError` pour évincer la session corrompue.
        Redirige également vers le Download Broker tout fichier écrit par `workspace-mcp`.
        """
        if not isinstance(result, dict):
            return result

        serialized = json.dumps(result, ensure_ascii=False)
        if any(marker in serialized for marker in self.OAUTH_ERROR_MARKERS):
            logger.warning(
                "Interception d'une fuite d'authentification locale/expirée Google Workspace sur l'outil '%s'.",
                tool_name,
            )
            raise AuthRequiredError(
                "Authentification Google Workspace invalide ou expirée en cours de session. "
                "Le flux interactif local (localhost:8001/oauth2callback) émis par workspace-mcp "
                "a été intercepté et bloqué car il est inaccessible depuis l'hôte."
            )

        moved_map: dict[str, Tuple[str, str, str]] = {}
        content_list = result.get("content")
        if isinstance(content_list, list):
            for item in content_list:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text_val = item.get("text", "")
                for match in _SAVED_TO_LINE_RE.finditer(text_val):
                    raw_path = match.group(2).strip()
                    if raw_path and raw_path not in moved_map and os.path.isfile(raw_path):
                        base_name = os.path.basename(raw_path)
                        clean_orig = _WORKSPACE_UUID_SUFFIX_RE.sub("", base_name)
                        try:
                            moved_map[raw_path] = self._publish_file_to_broker(
                                raw_path, user_id, chat_id, clean_orig
                            )
                        except Exception as e:
                            logger.warning(
                                "Échec transfert fichier Google Workspace vers Download Broker: %s", e
                            )

        session_key = cache_key or f"{self._sanitize_segment(user_id)}_default"
        staging_dir = self._SESSION_STAGING_DIRS.get(session_key)
        if staging_dir and os.path.isdir(staging_dir):
            for fname in os.listdir(staging_dir):
                fpath = os.path.join(staging_dir, fname)
                if os.path.isfile(fpath) and fpath not in moved_map:
                    clean_orig = _WORKSPACE_UUID_SUFFIX_RE.sub("", fname)
                    try:
                        moved_map[fpath] = self._publish_file_to_broker(
                            fpath, user_id, chat_id, clean_orig
                        )
                    except Exception as e:
                        logger.warning(
                            "Échec transfert reliquat staging Google Workspace: %s", e
                        )

        if moved_map:
            return self._rewrite_result_with_broker_downloads(result, moved_map)

        return result

    async def upload_file_stream(
        self,
        user_id: str,
        chat_id: str,
        credentials: dict,
        byte_stream: AsyncIterator[bytes],
        filename: str,
        total_size: int,
        mime_type: str = "application/octet-stream",
        folder_id: Optional[str] = None,
        overwrite_file_id: Optional[str] = None,
        convert_to_native: bool = False,
        conflict_behavior: str = "replace",
        account_id: Optional[str] = None,
    ) -> dict:
        if total_size < 0 or total_size > MAX_DRIVE_UPLOAD_BYTES:
            raise ValueError(f"Taille de fichier invalide pour upload Google Drive : {total_size}")
        safe_filename = self._sanitize_filename(filename)
        t_info = await self._refresh_google_access_token(
            user_id,
            credentials,
            required_any_scopes={
                "https://www.googleapis.com/auth/drive",
                "https://www.googleapis.com/auth/drive.file",
            },
        )
        access_token = t_info["token"]
        cache_id = t_info.get("cache_id") or self._google_token_cache_id(user_id, credentials)
        account_sig = hashlib.md5(
            f"{credentials.get('GOOGLE_OAUTH_CLIENT_ID', '')}:{credentials.get('USER_GOOGLE_EMAIL', '')}".encode()
        ).hexdigest()[:12]

        ext = os.path.splitext(safe_filename)[1].lower()
        target_name = safe_filename
        upload_mime = mime_type or "application/octet-stream"
        meta_mime = upload_mime
        if convert_to_native and ext in _GOOGLE_NATIVE_CONVERSION_MAP:
            mapped_upload_mime, meta_mime = _GOOGLE_NATIVE_CONVERSION_MAP[ext]
            upload_mime = mapped_upload_mime
            if ext:
                stem = safe_filename[:-len(ext)]
                target_name = stem or safe_filename

        target_key = overwrite_file_id or f"{folder_id or 'root'}:{target_name}"
        async with self._get_upload_target_lock("google_workspace", user_id, account_sig, target_key):
            async with self._get_upload_semaphore("google_workspace", user_id, account_sig):
                if overwrite_file_id:
                    method = "PATCH"
                    init_url = (
                        f"https://www.googleapis.com/upload/drive/v3/files/"
                        f"{urllib.parse.quote(overwrite_file_id, safe='')}"
                    )
                    meta_body: dict[str, Any] = {"name": target_name}
                    if convert_to_native:
                        meta_body["mimeType"] = meta_mime
                else:
                    method = "POST"
                    init_url = "https://www.googleapis.com/upload/drive/v3/files"
                    meta_body = {
                        "name": target_name,
                        "mimeType": meta_mime,
                        "parents": [folder_id or "root"],
                    }

                init_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Type": upload_mime,
                    "X-Upload-Content-Length": str(total_size),
                }
                init_params = {
                    "uploadType": "resumable",
                    "supportsAllDrives": "true",
                    "fields": "id,name,mimeType,size,parents,webViewLink",
                }
                async with self._create_upload_http_client(timeout_sec=30.0) as client:
                    init_resp = await client.request(
                        method, init_url, params=init_params, headers=init_headers, json=meta_body
                    )

                if init_resp.status_code >= 400:
                    err_txt = init_resp.text[:500]
                    if init_resp.status_code == 401 or (
                        init_resp.status_code == 403
                        and any(
                            m in err_txt
                            for m in ("insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficient authentication scopes")
                        )
                    ):
                        self._GOOGLE_TOKEN_CACHE.pop(cache_id, None)
                        raise AuthRequiredError(f"Authentification ou scope Google Drive insuffisant : {err_txt}")
                    if "accessNotConfigured" in err_txt or "API has not been used" in err_txt:
                        raise RuntimeError(
                            f"L'API Google Drive (drive.googleapis.com) doit être activée sur le projet GCP : {err_txt}"
                        )
                    raise RuntimeError(f"Échec initiation session Google Drive ({init_resp.status_code}): {err_txt}")

                upload_url = init_resp.headers.get("location")
                if not upload_url:
                    raise RuntimeError("En-tête Location manquant dans la réponse de session résumable Google Drive.")

                data = await self._stream_resumable_put(upload_url, byte_stream, total_size, upload_mime)
                return self._build_drive_upload_payload(
                    "google_workspace",
                    file_id=data.get("id", ""),
                    name=data.get("name", target_name),
                    size=int(data.get("size") or total_size),
                    mime_type=data.get("mimeType", meta_mime),
                    folder_id=(data.get("parents") or [folder_id or "root"])[0],
                    web_url=data.get("webViewLink"),
                    converted_from=(upload_mime if convert_to_native and meta_mime != upload_mime else None),
                )


class Microsoft365Provider(MCPServiceProvider):
    """
    Adaptateur MSAL pour le serveur résident Microsoft 365 (`microsoft-mcp`).
    Résout :
    1. La persistance du cache MSAL dans `/app/mcp_cache/microsoft_365/<user_id>`.
    2. La corruption de `_flow_cache` par le LLM lors de `complete_authentication`.
    3. Le blocage synchrone de 900s de `msal.acquire_token_by_device_flow` (`expires_at = 0`).
    4. Le blocage de 900s + corruption stdout dans `get_token()` lorsque `account_id` est un email
       ou que le cache MSAL est vide.
    """

    AUTH_EXEMPT_TOOLS = frozenset(
        {"list_accounts", "authenticate_account", "complete_authentication"}
    )
    WELL_KNOWN_PATHS = {
        "inbox": "inbox",
        "sent": "sentitems",
        "sentitems": "sentitems",
        "drafts": "drafts",
        "deleted": "deleteditems",
        "deleteditems": "deleteditems",
        "junk": "junkemail",
        "junkemail": "junkemail",
        "archive": "archive",
    }
    WELL_KNOWN_FOLDERS = frozenset(WELL_KNOWN_PATHS.keys())
    WELL_KNOWN_DISPLAY_NAMES = {
        "inbox": frozenset({"inbox", "boîte de réception", "boite de reception"}),
        "sentitems": frozenset({"sent items", "éléments envoyés", "elements envoyes"}),
        "drafts": frozenset({"drafts", "brouillons"}),
        "deleteditems": frozenset({"deleted items", "éléments supprimés", "elements supprimes"}),
        "junkemail": frozenset({"junk email", "courrier indésirable", "courrier indesirable"}),
        "archive": frozenset({"archive", "archives"}),
    }
    _FOLDERS_CACHE: dict[str, dict] = {}
    _TOKEN_LOCKS: dict[str, asyncio.Lock] = {}
    _MAILBOX_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
    _FOLDERS_INFLIGHT: dict[str, asyncio.Task] = {}

    @classmethod
    def _get_token_lock(cls, user_id: str) -> asyncio.Lock:
        key = f"{id(asyncio.get_running_loop())}:{user_id or 'anonymous'}"
        lock = cls._TOKEN_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            cls._TOKEN_LOCKS[key] = lock
        return lock

    @classmethod
    def _get_mailbox_semaphore(cls, mailbox_key: str) -> asyncio.Semaphore:
        key = f"{id(asyncio.get_running_loop())}:{mailbox_key or 'default'}"
        sem = cls._MAILBOX_SEMAPHORES.get(key)
        if sem is None:
            sem = asyncio.Semaphore(2)
            cls._MAILBOX_SEMAPHORES[key] = sem
        return sem

    @staticmethod
    def _normalize_iso_date(date_str: str, is_end_of_day: bool = False) -> Tuple[str, str]:
        """
        Normalise une date (YYYY-MM-DD ou ISO 8601) vers :
        - une chaîne ISO 8601 UTC ('YYYY-MM-DDTHH:MM:SSZ') pour OData $filter
        - une chaîne courte ('YYYY-MM-DD') pour Graph KQL $search
        """
        raw = str(date_str or "").strip()
        if not raw:
            return "", ""
        try:
            if len(raw) == 10 and raw.count("-") == 2:
                short_date = raw
                iso_time = "23:59:59Z" if is_end_of_day else "00:00:00Z"
                return f"{raw}T{iso_time}", short_date
            cleaned = raw.replace("Z", "+00:00")
            dt_obj = datetime.fromisoformat(cleaned)
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            else:
                dt_obj = dt_obj.astimezone(timezone.utc)
            short_date = dt_obj.strftime("%Y-%m-%d")
            iso_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
            return iso_str, short_date
        except Exception:
            return raw, raw

    @staticmethod
    def _clean_html_to_text(html_text: str) -> str:
        """Délègue la conversion HTML -> texte brut dense avec préservation des liens à MCPServiceProvider."""
        return MCPServiceProvider.clean_html_to_text(html_text)

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        detail = str(exc).strip()
        return f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__

    def _handle_tool_exception(self, action_name: str, exc: Exception) -> dict:
        if isinstance(exc, AuthRequiredError):
            return self._make_tool_response(
                {"status": "auth_required", "message": str(exc)},
                is_error=True,
            )
        return self._make_tool_response(
            {
                "status": "error",
                "message": f"Exception lors de {action_name}: {self._format_exception(exc)}",
            },
            is_error=True,
        )

    @staticmethod
    def _parse_bool(val: Any, default: bool = False) -> bool:
        if val is None:
            return default
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        s = str(val).strip().lower()
        if s in ("true", "1", "yes", "y", "on"):
            return True
        if s in ("false", "0", "no", "n", "off", ""):
            return False
        return default

    @staticmethod
    def _quote_graph_id(raw_id: str) -> str:
        return urllib.parse.quote(str(raw_id or "").strip(), safe="")

    @staticmethod
    def _create_graph_client(
        timeout_sec: float = 15.0, follow_redirects: bool = False
    ) -> httpx.AsyncClient:
        transport = httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=0)
        timeout = httpx.Timeout(timeout_sec, connect=6.0)
        return httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=follow_redirects,
        )

    async def _graph_request(
        self,
        method: str,
        url: str,
        headers: dict,
        params: Optional[dict] = None,
        json_data: Optional[dict] = None,
        json_body: Any = None,
        timeout_sec: float = 14.0,
        max_retries: int = 2,
        mailbox_key: Optional[str] = None,
        follow_redirects: bool = False,
    ) -> httpx.Response:
        """
        Exécute une requête Microsoft Graph v1.0 avec client HTTP frais par tentative,
        sémaphore par boîte mail et politique de retry sur timeouts, erreurs réseau, 429 et 5xx.
        """
        base_headers = dict(headers)
        payload_json = json_body if json_body is not None else json_data
        sem = self._get_mailbox_semaphore(mailbox_key) if mailbox_key else None

        for attempt in range(max_retries + 1):
            req_headers = dict(base_headers)
            req_params = dict(params) if params is not None else None
            if attempt >= 1 and method.upper() == "GET":
                if req_params and req_params.get("$skip") == "0":
                    req_params.pop("$skip", None)
                if req_params and "$select" in req_params:
                    cur_tokens = [
                        f.strip()
                        for f in str(req_params["$select"]).split(",")
                        if f.strip()
                    ]
                    if "from" in cur_tokens and "bodyPreview" in cur_tokens:
                        if "body" in cur_tokens:
                            req_params["$select"] = (
                                "id,subject,from,toRecipients,ccRecipients,"
                                "receivedDateTime,hasAttachments,body,conversationId,isRead"
                            )
                        else:
                            req_params["$select"] = (
                                "id,subject,from,toRecipients,receivedDateTime,"
                                "hasAttachments,conversationId,isRead"
                            )

            try:
                async with self._create_graph_client(
                    timeout_sec, follow_redirects=follow_redirects
                ) as client:
                    if sem is not None:
                        async with sem:
                            res = await client.request(
                                method=method,
                                url=url,
                                headers=req_headers,
                                params=req_params,
                                json=payload_json,
                            )
                    else:
                        res = await client.request(
                            method=method,
                            url=url,
                            headers=req_headers,
                            params=req_params,
                            json=payload_json,
                        )
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt < max_retries:
                    await asyncio.sleep((2 ** attempt) * 0.8)
                    continue
                raise

            if res.status_code == 429 and attempt < max_retries:
                retry_after = min(int(res.headers.get("Retry-After", "2") or 2), 10)
                await asyncio.sleep(retry_after)
                continue
            if res.status_code >= 500 and attempt < max_retries:
                await asyncio.sleep((2 ** attempt) * 0.8)
                continue
            return res
        return res

    @staticmethod
    def _get_email_select_fields(include_body: bool) -> str:
        if include_body:
            return (
                "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                "sentDateTime,hasAttachments,body,bodyPreview,conversationId,isRead"
            )
        return (
            "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
            "sentDateTime,hasAttachments,bodyPreview,conversationId,isRead"
        )

    def _normalize_email_message(
        self,
        msg: dict,
        include_body: bool,
        body_max_length: Optional[int] = None,
    ) -> dict:
        if not isinstance(msg, dict):
            return msg
        if include_body and "body" in msg and isinstance(msg["body"], dict):
            raw_content = msg["body"].get("content", "")
            clean_content = self._clean_html_to_text(raw_content)
            if body_max_length is not None and len(clean_content) > body_max_length:
                trunc_msg = f"\n\n[Content truncated - {len(clean_content)} total characters]"
                msg["body"]["content"] = clean_content[:body_max_length] + trunc_msg
                msg["body"]["truncated"] = True
                msg["body"]["total_length"] = len(clean_content)
            else:
                msg["body"]["content"] = clean_content
        elif not include_body and "body" in msg:
            del msg["body"]

        if "attachments" in msg and isinstance(msg["attachments"], list):
            for att in msg["attachments"]:
                if isinstance(att, dict) and "contentBytes" in att:
                    del att["contentBytes"]
        return msg

    @staticmethod
    def _compute_ms_sig(credentials: Optional[dict]) -> Optional[str]:
        if isinstance(credentials, dict) and credentials.get("MICROSOFT_MCP_CLIENT_ID"):
            cid = str(credentials.get("MICROSOFT_MCP_CLIENT_ID") or "").strip()
            tid = str(credentials.get("MICROSOFT_MCP_TENANT_ID") or "").strip()
            return hashlib.md5(f"{cid}:{tid}".encode()).hexdigest()[:12]
        return None

    @classmethod
    def _user_cache_dir(
        cls,
        user_id: str,
        credentials: Optional[dict] = None,
    ) -> str:
        safe_uid = cls._sanitize_segment(user_id, "anonymous")
        ms_sig = cls._compute_ms_sig(credentials)
        if ms_sig:
            return os.path.join(MCP_CACHE_ROOT, "microsoft_365", safe_uid, ms_sig)
        return os.path.join(MCP_CACHE_ROOT, "microsoft_365", safe_uid)

    @classmethod
    def _token_cache_path(
        cls,
        user_id: str,
        credentials: Optional[dict] = None,
    ) -> str:
        return os.path.join(
            cls._user_cache_dir(user_id, credentials),
            ".microsoft_mcp_token_cache.json",
        )

    @classmethod
    def _flow_cache_path(
        cls,
        user_id: str,
        credentials: Optional[dict] = None,
    ) -> str:
        return os.path.join(
            cls._user_cache_dir(user_id, credentials),
            ".microsoft_mcp_device_flow.json",
        )

    @classmethod
    def _load_cached_accounts(
        cls,
        user_id: str,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """Lit les comptes MSAL persistés dans `.microsoft_mcp_token_cache.json`."""
        cache_path = cls._token_cache_path(user_id, credentials)
        if not os.path.exists(cache_path):
            return []
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts_map = data.get("Account", {})
            if not isinstance(accounts_map, dict):
                return []
            result = []
            for acc in accounts_map.values():
                if isinstance(acc, dict) and acc.get("home_account_id"):
                    result.append(
                        {
                            "account_id": str(acc.get("home_account_id")),
                            "username": str(acc.get("username", "")),
                        }
                    )
            return result
        except Exception as e:
            logger.warning("Lecture du cache MSAL impossible (%s): %s", cache_path, e)
            return []

    async def sync_credentials(self, user_id: str, credentials: dict) -> str:
        cache_dir = self._user_cache_dir(user_id, credentials)
        os.makedirs(cache_dir, exist_ok=True)
        ms_sig = self._compute_ms_sig(credentials)
        if ms_sig:
            cid = str(credentials.get("MICROSOFT_MCP_CLIENT_ID") or "").strip()
            tid = str(credentials.get("MICROSOFT_MCP_TENANT_ID") or "").strip()
            full_md5 = hashlib.md5(f"{cid}:{tid}".encode()).hexdigest()

            scoped_token_cache = self._token_cache_path(user_id, credentials)
            scoped_flow_cache = self._flow_cache_path(user_id, credentials)
            if not os.path.exists(scoped_token_cache):
                legacy_dir = self._user_cache_dir(user_id, None)
                legacy_token_cache = os.path.join(
                    legacy_dir, ".microsoft_mcp_token_cache.json"
                )
                legacy_flow_cache = os.path.join(
                    legacy_dir, ".microsoft_mcp_device_flow.json"
                )
                legacy_sig_file = os.path.join(legacy_dir, ".cred_hash")
                should_migrate = os.path.exists(legacy_token_cache)
                if should_migrate and os.path.exists(legacy_sig_file):
                    try:
                        with open(legacy_sig_file, "r", encoding="utf-8") as sf_in:
                            legacy_sig = sf_in.read().strip()
                        if legacy_sig and not (
                            legacy_sig == full_md5 or legacy_sig.startswith(ms_sig)
                        ):
                            should_migrate = False
                    except Exception:
                        should_migrate = False
                if should_migrate:
                    try:
                        tmp_tok = f"{scoped_token_cache}.tmp"
                        shutil.copy2(legacy_token_cache, tmp_tok)
                        os.replace(tmp_tok, scoped_token_cache)
                        if (
                            os.path.exists(legacy_flow_cache)
                            and not os.path.exists(scoped_flow_cache)
                        ):
                            tmp_flw = f"{scoped_flow_cache}.tmp"
                            shutil.copy2(legacy_flow_cache, tmp_flw)
                            os.replace(tmp_flw, scoped_flow_cache)
                    except Exception as mig_err:
                        logger.warning(
                            "MS365: échec migration cache MSAL vers %s: %s",
                            cache_dir,
                            mig_err,
                        )

            cfg_file = os.path.join(cache_dir, ".cred_config.json")
            try:
                with open(cfg_file, "w", encoding="utf-8") as cf_out:
                    json.dump(
                        {
                            "MICROSOFT_MCP_CLIENT_ID": cid,
                            "MICROSOFT_MCP_TENANT_ID": tid or "common",
                        },
                        cf_out,
                    )
            except Exception:
                pass

        return cache_dir

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
        cache_key: Optional[str] = None,
    ) -> None:
        cache_dir = await self.sync_credentials(user_id, credentials)
        env["HOME"] = cache_dir

    @classmethod
    def _get_access_token(
        cls,
        user_id: str,
        account_id: Optional[str] = None,
        credentials: Optional[dict] = None,
    ) -> str:
        """
        Extrait un jeton d'accès Graph valide depuis le cache MSAL persistant.
        Tente d'abord une lecture directe du jeton actif, sinon rafraîchit
        silencieusement via MSAL SerializableTokenCache ou renvoie une exception.
        """
        cache_path = cls._token_cache_path(user_id, credentials)
        if not os.path.exists(cache_path):
            raise AuthRequiredError(f"Cache MSAL introuvable dans {cache_path}")

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                raw_cache = f.read()
            data = json.loads(raw_cache)
        except Exception as e:
            raise AuthRequiredError(f"Lecture du cache MSAL impossible: {e}")

        now = int(datetime.now(timezone.utc).timestamp())

        # 1. Recherche d'un AccessToken non expiré dans le cache sérialisé MSAL
        access_tokens = data.get("AccessToken", {})
        accounts_map = data.get("Account", {})
        if isinstance(access_tokens, dict):
            for item in access_tokens.values():
                if not isinstance(item, dict):
                    continue
                secret = item.get("secret")
                expires_on = int(item.get("expires_on", 0))
                if not (secret and expires_on > (now + 60)):
                    continue
                if account_id:
                    item_hid = str(item.get("home_account_id") or "").casefold()
                    if item_hid == account_id.casefold():
                        return secret
                else:
                    return secret

        # 2. Rafraîchissement silencieux via MSAL
        try:
            import msal
        except ImportError:
            raise AuthRequiredError("Module 'msal' indisponible pour rafraîchir le jeton.")

        cache_dir = cls._user_cache_dir(user_id, credentials)
        saved_cfg: dict = {}
        cfg_path = os.path.join(cache_dir, ".cred_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as cf:
                    loaded_cfg = json.load(cf)
                if isinstance(loaded_cfg, dict):
                    saved_cfg = loaded_cfg
            except Exception:
                pass

        creds_dict = credentials if isinstance(credentials, dict) else {}
        client_id = (
            creds_dict.get("MICROSOFT_MCP_CLIENT_ID")
            or os.environ.get("MICROSOFT_MCP_CLIENT_ID")
            or saved_cfg.get("MICROSOFT_MCP_CLIENT_ID")
        )
        if not client_id:
            for section in ("RefreshToken", "AccessToken", "AppMetadata"):
                sec_map = data.get(section, {})
                if isinstance(sec_map, dict):
                    for entry in sec_map.values():
                        if isinstance(entry, dict) and entry.get("client_id"):
                            client_id = str(entry["client_id"]).strip()
                            break
                if client_id:
                    break

        if not client_id:
            raise AuthRequiredError("MICROSOFT_MCP_CLIENT_ID manquant pour rafraîchir le jeton.")

        raw_tenants = [
            creds_dict.get("MICROSOFT_MCP_TENANT_ID"),
            os.environ.get("MICROSOFT_MCP_TENANT_ID"),
            saved_cfg.get("MICROSOFT_MCP_TENANT_ID"),
        ]
        if isinstance(accounts_map, dict):
            for acc in accounts_map.values():
                if isinstance(acc, dict) and acc.get("realm"):
                    if not account_id or str(acc.get("home_account_id") or "").casefold() == account_id.casefold():
                        raw_tenants.append(acc["realm"])
        raw_tenants.extend(["consumers", "common"])
        tenant_candidates: list[str] = []
        for t in raw_tenants:
            t_clean = str(t or "").strip()
            if t_clean and t_clean not in tenant_candidates:
                tenant_candidates.append(t_clean)

        rt_secret = None
        rt_map = data.get("RefreshToken", {})
        if isinstance(rt_map, dict):
            if account_id:
                for rt in rt_map.values():
                    if (
                        isinstance(rt, dict)
                        and str(rt.get("home_account_id") or "").casefold() == account_id.casefold()
                        and rt.get("secret")
                    ):
                        rt_secret = str(rt["secret"])
                        break
            elif len(rt_map) == 1:
                only_rt = next(iter(rt_map.values()))
                if isinstance(only_rt, dict) and only_rt.get("secret"):
                    rt_secret = str(only_rt["secret"])

        cache = msal.SerializableTokenCache()
        cache.deserialize(raw_cache)

        for t_id in tenant_candidates:
            authority = f"https://login.microsoftonline.com/{t_id}"
            app = msal.PublicClientApplication(
                client_id, authority=authority, token_cache=cache
            )
            accounts = app.get_accounts()
            account = None
            if account_id and accounts:
                acc_cf = account_id.casefold()
                account = next(
                    (
                        a for a in accounts
                        if str(a.get("home_account_id") or "").casefold() == acc_cf
                        or str(a.get("username") or "").casefold() == acc_cf
                    ),
                    None,
                )
            elif not account_id and accounts:
                account = accounts[0]

            if account_id and account is None and not rt_secret:
                continue

            res = (
                app.acquire_token_silent(
                    ["https://graph.microsoft.com/.default"], account=account
                )
                if (account is not None or not account_id)
                else None
            )
            if (not res or "access_token" not in res) and rt_secret:
                res = app.acquire_token_by_refresh_token(
                    rt_secret, scopes=["https://graph.microsoft.com/.default"]
                )
            if res and "access_token" in res:
                if cache.has_state_changed:
                    try:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            f.write(cache.serialize())
                    except Exception as e:
                        logger.warning("Échec sauvegarde cache MSAL après rafraîchissement: %s", e)
                return res["access_token"]

        raise AuthRequiredError("Impossible d'obtenir un jeton Graph valide silencieusement.")

    async def _get_access_token_async(
        self,
        user_id: str,
        account_id: Optional[str] = None,
        credentials: Optional[dict] = None,
    ) -> str:
        async with self._get_token_lock(user_id):
            return await asyncio.to_thread(
                self._get_access_token, user_id, account_id, credentials
            )

    async def _fetch_all_mail_folders(
        self,
        user_id: str,
        account_id: str,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """
        Explore récursivement tous les dossiers et sous-dossiers de messagerie
        via l'API Microsoft Graph v1.0.
        """
        token = await self._get_access_token_async(user_id, account_id, credentials)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        all_folders: list[dict] = []
        fields = (
            "id,displayName,parentFolderId,childFolderCount,"
            "unreadItemCount,totalItemCount"
        )
        mailbox_key = f"{user_id}:{account_id}"

        async def _fetch_paged_folders(
            url: str,
            params: Optional[dict],
            raise_on_4xx: bool = True,
        ) -> list[dict]:
            collected: list[dict] = []
            next_url: Optional[str] = url
            next_params: Optional[dict] = params
            for _ in range(10):
                if not next_url:
                    break
                resp = await self._graph_request(
                    "GET",
                    next_url,
                    headers=headers,
                    params=next_params,
                    timeout_sec=14.0,
                    max_retries=2,
                    mailbox_key=mailbox_key,
                )
                if resp.status_code >= 500 or (raise_on_4xx and resp.status_code != 200):
                    raise RuntimeError(
                        f"Échec Graph folders ({resp.status_code}): {resp.text[:300]}"
                    )
                if resp.status_code != 200:
                    break
                data = resp.json()
                collected.extend(data.get("value", []))
                next_url = data.get("@odata.nextLink")
                next_params = None
            return collected

        root_folders = await _fetch_paged_folders(
            "https://graph.microsoft.com/v1.0/me/mailFolders",
            params={"$top": "100", "$select": fields},
            raise_on_4xx=True,
        )

        async def _fetch_children(
            parent_id: str, parent_path: str, depth: int = 1
        ) -> None:
            if depth > 5:
                return
            q_pid = self._quote_graph_id(parent_id)
            child_url_base = (
                f"https://graph.microsoft.com/v1.0/me/mailFolders/{q_pid}/childFolders"
            )
            children = await _fetch_paged_folders(
                child_url_base,
                params={"$top": "100", "$select": fields},
                raise_on_4xx=False,
            )
            sub_tasks = []
            for child in children:
                c_name = child.get("displayName", "")
                c_path = f"{parent_path}/{c_name}" if parent_path else c_name
                child_entry = dict(child)
                child_entry["path"] = c_path
                all_folders.append(child_entry)
                if child_entry.get("childFolderCount", 0) > 0:
                    sub_tasks.append(
                        _fetch_children(child_entry["id"], c_path, depth + 1)
                    )
            if sub_tasks:
                await asyncio.gather(*sub_tasks)

        root_tasks = []
        for folder in root_folders:
            f_name = folder.get("displayName", "")
            folder_entry = dict(folder)
            folder_entry["path"] = f_name
            all_folders.append(folder_entry)
            if folder_entry.get("childFolderCount", 0) > 0:
                root_tasks.append(
                    _fetch_children(folder_entry["id"], f_name, 1)
                )
        if root_tasks:
            await asyncio.gather(*root_tasks)

        return all_folders

    async def _get_cached_folders(
        self,
        user_id: str,
        account_id: str,
        force_refresh: bool = False,
        raise_on_error: bool = False,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """Retourne la liste des dossiers depuis le cache mémoire (TTL 300s) ou rafraîchit."""
        now = datetime.now(timezone.utc).timestamp()
        cache_key = f"{user_id}:{account_id}"
        cached = self._FOLDERS_CACHE.get(cache_key)
        if not force_refresh and cached and (now - cached.get("timestamp", 0) < 300):
            return cached.get("folders", [])

        inflight_key = f"{id(asyncio.get_running_loop())}:{cache_key}"
        task = self._FOLDERS_INFLIGHT.get(inflight_key)
        created = False
        if task is None or task.done():
            task = asyncio.create_task(
                self._fetch_all_mail_folders(user_id, account_id, credentials)
            )
            self._FOLDERS_INFLIGHT[inflight_key] = task
            created = True

        try:
            folders = await asyncio.shield(task)
            if folders:
                self._FOLDERS_CACHE[cache_key] = {
                    "timestamp": datetime.now(timezone.utc).timestamp(),
                    "folders": folders,
                }
            return folders
        except Exception as e:
            logger.warning("Échec de récupération des dossiers M365: %s", e)
            if cached and cached.get("folders") and not force_refresh:
                return cached.get("folders", [])
            if raise_on_error:
                raise
            return []
        finally:
            if created and self._FOLDERS_INFLIGHT.get(inflight_key) is task:
                self._FOLDERS_INFLIGHT.pop(inflight_key, None)

    async def _resolve_folder_id(
        self,
        user_id: str,
        account_id: str,
        folder_name: str,
        credentials: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Résout un libellé ou chemin de dossier en Graph ID immuable.
        Laisse passer tels quels les alias réservés (inbox, etc.) et les FolderId déjà formatés.
        """
        if not folder_name:
            return None
        cleaned = folder_name.strip()
        if cleaned.casefold() in self.WELL_KNOWN_PATHS:
            return self.WELL_KNOWN_PATHS[cleaned.casefold()]

        # Détection d'un ID Graph immuable (Base64-like préfixé ou long)
        if cleaned.startswith(("AQMk", "AAMk")) or (len(cleaned) >= 50 and "=" in cleaned):
            return folder_name

        folders = await self._get_cached_folders(
            user_id, account_id, credentials=credentials
        )
        target = cleaned.lower()

        # 1. Correspondance exacte sur displayName ou chemin complet
        for f in folders:
            d_name = (f.get("displayName") or "").lower()
            f_path = (f.get("path") or "").lower()
            if target in (d_name, f_path):
                return f.get("id")

        # 2. Correspondance sur le segment final (ex: "Entretiens" pour ".../Entretiens")
        for f in folders:
            f_path = (f.get("path") or "").lower()
            if f_path.endswith(f"/{target}"):
                return f.get("id")

        # 3. Forcer un rafraîchissement au cas où le dossier vient d'être créé
        refreshed = await self._get_cached_folders(
            user_id, account_id, force_refresh=True, credentials=credentials
        )
        for f in refreshed:
            d_name = (f.get("displayName") or "").lower()
            f_path = (f.get("path") or "").lower()
            if target in (d_name, f_path) or f_path.endswith(f"/{target}"):
                return f.get("id")

        return None

    async def _resolve_immutable_folder_id(
        self,
        user_id: str,
        account_id: str,
        folder_endpoint_id: str,
        credentials: Optional[dict] = None,
    ) -> Optional[str]:
        """Résout un alias well-known ou un nom de dossier racine vers son Graph ID immuable."""
        if not folder_endpoint_id:
            return None
        aliases = self.WELL_KNOWN_DISPLAY_NAMES.get(folder_endpoint_id.casefold())
        if not aliases:
            return folder_endpoint_id
        try:
            folders = await self._get_cached_folders(
                user_id, account_id, credentials=credentials
            )
        except Exception:
            return None
        for f in folders:
            f_path = str(f.get("path") or "")
            if "/" in f_path:
                continue
            d_name = str(f.get("displayName") or "").strip().casefold()
            if d_name in aliases and f.get("id"):
                return f.get("id")
        return None

    @staticmethod
    def _sort_email_messages(messages: list[dict], order_by: str) -> list[dict]:
        """Trie une liste de messages normalisés selon `receivedDateTime`."""
        reverse = "asc" not in str(order_by or "").lower()
        return sorted(
            messages,
            key=lambda m: str(m.get("receivedDateTime") or ""),
            reverse=reverse,
        )

    async def _search_onedrive_files(
        self,
        user_id: str,
        account_id: str,
        query: str,
        limit: int = 25,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """Recherche universelle OneDrive (MSA & Entra ID) via /me/drive/root/search."""
        token = await self._get_access_token_async(user_id, account_id, credentials)
        clean_q = str(query or "").replace("'", "''").strip()
        q_encoded = urllib.parse.quote(clean_q, safe="")
        url = f"https://graph.microsoft.com/v1.0/me/drive/root/search(q='{q_encoded}')"
        res = await self._graph_request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params={"$top": str(min(max(int(limit or 25), 1), 100))},
            timeout_sec=15.0,
            max_retries=2,
            mailbox_key=f"{user_id}:{account_id}",
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"Échec Graph search_files ({res.status_code}): {res.text[:300]}"
            )
        return res.json().get("value", [])

    async def _search_calendar_events(
        self,
        user_id: str,
        account_id: str,
        query: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 25,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """Recherche universelle d'événements calendrier (MSA & Entra ID) via /me/calendarView ou /me/events."""
        token = await self._get_access_token_async(user_id, account_id, credentials)
        top_n = min(max(int(limit or 25), 1), 100)
        fetch_top = min(top_n * 3, 100) if query else top_n
        select_fields = "id,subject,start,end,location,organizer,isAllDay,webLink,bodyPreview"

        iso_start, _ = self._normalize_iso_date(start_date, is_end_of_day=False)
        iso_end, _ = self._normalize_iso_date(end_date, is_end_of_day=True)

        if iso_start and iso_end:
            url = "https://graph.microsoft.com/v1.0/me/calendarView"
            params = {
                "startDateTime": iso_start,
                "endDateTime": iso_end,
                "$top": str(fetch_top),
                "$select": select_fields,
                "$orderby": "start/dateTime asc",
            }
        elif iso_start:
            url = "https://graph.microsoft.com/v1.0/me/events"
            params = {
                "$top": str(fetch_top),
                "$select": select_fields,
                "$filter": f"start/dateTime ge '{iso_start}'",
                "$orderby": "start/dateTime asc",
            }
        elif iso_end:
            url = "https://graph.microsoft.com/v1.0/me/events"
            params = {
                "$top": str(fetch_top),
                "$select": select_fields,
                "$filter": f"end/dateTime le '{iso_end}'",
                "$orderby": "start/dateTime desc",
            }
        else:
            url = "https://graph.microsoft.com/v1.0/me/events"
            params = {
                "$top": str(fetch_top),
                "$select": select_fields,
                "$orderby": "start/dateTime desc",
            }

        res = await self._graph_request(
            "GET",
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
            params=params,
            timeout_sec=15.0,
            max_retries=2,
            mailbox_key=f"{user_id}:{account_id}",
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"Échec Graph search_events ({res.status_code}): {res.text[:300]}"
            )
        events = res.json().get("value", [])
        clean_q = str(query or "").strip().casefold()
        if clean_q:
            filtered = []
            for ev in events:
                subj = str(ev.get("subject") or "").casefold()
                prev = str(ev.get("bodyPreview") or "").casefold()
                loc = str((ev.get("location") or {}).get("displayName") or "").casefold()
                if clean_q in subj or clean_q in prev or clean_q in loc:
                    filtered.append(ev)
            events = filtered
        return events[:top_n]

    async def _search_messages_kql(
        self,
        user_id: str,
        account_id: str,
        query: str,
        limit: int = 25,
        credentials: Optional[dict] = None,
    ) -> list[dict]:
        """Recherche KQL rapide de messages pour unified_search (MSA & Entra ID)."""
        token = await self._get_access_token_async(user_id, account_id, credentials)
        clean_q = str(query or "").replace('"', "").strip()
        params = {
            "$top": str(min(max(int(limit or 25), 1), 100)),
            "$select": self._get_email_select_fields(False),
        }
        if clean_q:
            params["$search"] = f'"{clean_q}"'
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        if "$search" in params:
            headers["ConsistencyLevel"] = "eventual"

        res = await self._graph_request(
            "GET",
            "https://graph.microsoft.com/v1.0/me/messages",
            headers=headers,
            params=params,
            timeout_sec=16.0,
            max_retries=2,
            mailbox_key=f"{user_id}:{account_id}",
        )
        if res.status_code != 200:
            raise RuntimeError(
                f"Échec Graph search_messages ({res.status_code}): {res.text[:300]}"
            )
        messages = [
            self._normalize_email_message(m, False, body_max_length=5000)
            for m in res.json().get("value", [])
        ]
        return self._sort_email_messages(messages, "receivedDateTime desc")

    async def after_list_tools(
        self,
        user_id: str,
        result: dict,
        credentials: Optional[dict] = None,
    ) -> dict:
        """Retire `flow_cache` du schéma exposé au LLM pour `complete_authentication`."""
        tools = result.get("tools")
        if not isinstance(tools, list):
            return result

        result = dict(result)
        patched_tools = []
        has_list_mail_folders = False
        has_trash_email = False
        has_batch_trash = False
        has_batch_get = False

        for tool in tools:
            if not isinstance(tool, dict):
                patched_tools.append(tool)
                continue

            t_name = tool.get("name")
            if t_name == "complete_authentication":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Complete the authentication process after the user has entered the device code. "
                    "No arguments required (the Device Flow state is persisted automatically by the MCP Broker)."
                )
                t_copy["inputSchema"] = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }
                patched_tools.append(t_copy)
            elif t_name == "list_emails":
                t_copy = dict(tool)
                props = dict(t_copy.get("inputSchema", {}).get("properties", {}))
                props["skip"] = {
                    "type": "integer",
                    "description": "Number of emails to skip for pagination (default: 0).",
                    "default": 0,
                }
                props["order_by"] = {
                    "type": "string",
                    "description": (
                        "Sort order: 'receivedDateTime desc' (newest first) or "
                        "'receivedDateTime asc' (oldest first)."
                    ),
                    "default": "receivedDateTime desc",
                }
                props["body_max_length"] = {
                    "type": "integer",
                    "description": (
                        "Maximum characters for body content per email when include_body is true (default: 5000)."
                    ),
                    "default": 5000,
                }
                props["include_body"] = {
                    "type": "boolean",
                    "description": (
                        "Whether to include full email body (default: false; "
                        "bodyPreview is always returned)."
                    ),
                    "default": False,
                }
                t_copy["inputSchema"] = dict(t_copy.get("inputSchema", {}))
                t_copy["inputSchema"]["properties"] = props
                patched_tools.append(t_copy)
            elif t_name == "search_emails":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Search emails across the mailbox or in a specific folder by keywords, date range, "
                    "sender, and status. Universal MSA & Entra ID support (KQL $search or OData $filter "
                    "with automatic sorting)."
                )
                props = {
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional text keywords to search for. Can be omitted when filtering by dates, "
                            "sender, or folder alone."
                        ),
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Optional folder name, path (e.g. 'Boîte de réception/LinkedIn', 'inbox'), "
                            "or Graph ID. If omitted, searches across the entire mailbox."
                        ),
                    },
                    "received_after": {
                        "type": "string",
                        "description": (
                            "Optional start date/time (format 'YYYY-MM-DD' or ISO 'YYYY-MM-DDTHH:MM:SSZ'). "
                            "Matches emails received on or after this timestamp."
                        ),
                    },
                    "received_before": {
                        "type": "string",
                        "description": (
                            "Optional end date/time (format 'YYYY-MM-DD' or ISO 'YYYY-MM-DDTHH:MM:SSZ'). "
                            "Matches emails received on or before this timestamp."
                        ),
                    },
                    "sender": {
                        "type": "string",
                        "description": "Optional sender name or email address filter (e.g. 'linkedin').",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Optional boolean: if true, returns only unread messages.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "Optional boolean: filter by presence of attachments.",
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["receivedDateTime desc", "receivedDateTime asc"],
                        "default": "receivedDateTime desc",
                        "description": (
                            "Sort order: 'receivedDateTime desc' (newest first) or "
                            "'receivedDateTime asc' (oldest first). "
                            "Note: applies when query keywords are omitted (OData mode)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Maximum number of messages to return (default 50, max 100).",
                    },
                    "include_body": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Whether to include email body in search results (default false for fast scanning; "
                            "use batch_get_emails for deep reading)."
                        ),
                    },
                    "body_max_length": {
                        "type": "integer",
                        "default": 5000,
                        "description": (
                            "Maximum characters for body content per email when include_body is true (default: 5000)."
                        ),
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional account ID or email address. Auto-resolved if omitted.",
                    },
                }
                t_copy["inputSchema"] = {
                    "type": "object",
                    "properties": props,
                    "required": [],
                }
                patched_tools.append(t_copy)
            elif t_name == "get_email":
                t_copy = dict(tool)
                schema = dict(t_copy.get("inputSchema", {}))
                props = dict(schema.get("properties", {}))
                props["body_max_length"] = {
                    "type": "integer",
                    "default": 50000,
                    "description": (
                        "Maximum characters for body content when include_body is true (default: 50000)."
                    ),
                }
                schema["properties"] = props
                t_copy["inputSchema"] = schema
                patched_tools.append(t_copy)
            elif t_name == "delete_email":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "PERMANENTLY PURGE an email (Hard Delete). CAUTION: This deletes the message permanently "
                    "without moving it to the trash folder. For safe reversible deletion, use 'trash_email' instead."
                )
                patched_tools.append(t_copy)
            elif t_name == "get_file":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Download a file from OneDrive directly into the ECHO Download Broker for automatic "
                    "ingestion into the Unified Registry. 'download_path' is optional (only a custom "
                    "filename may be suggested; directory routing to the Download Broker is enforced)."
                )
                schema = dict(t_copy.get("inputSchema", {}))
                props = dict(schema.get("properties", {}))
                if "download_path" in props:
                    dp_prop = dict(props["download_path"])
                    dp_prop["description"] = (
                        "Optional desired filename (e.g. 'report.pdf'). Directory path is automatically "
                        "overridden to the ECHO Download Broker drop-zone."
                    )
                    props["download_path"] = dp_prop
                schema["properties"] = props
                schema["required"] = ["file_id"]
                t_copy["inputSchema"] = schema
                patched_tools.append(t_copy)
            elif t_name == "get_attachment":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Download an email attachment directly into the ECHO Download Broker for automatic "
                    "ingestion into the Unified Registry. 'save_path' is optional (only a custom "
                    "filename may be suggested; directory routing to the Download Broker is enforced)."
                )
                schema = dict(t_copy.get("inputSchema", {}))
                props = dict(schema.get("properties", {}))
                if "save_path" in props:
                    sp_prop = dict(props["save_path"])
                    sp_prop["description"] = (
                        "Optional desired filename (e.g. 'invoice.pdf'). Directory path is automatically "
                        "overridden to the ECHO Download Broker drop-zone."
                    )
                    props["save_path"] = sp_prop
                schema["properties"] = props
                schema["required"] = ["email_id", "attachment_id"]
                t_copy["inputSchema"] = schema
                patched_tools.append(t_copy)
            elif t_name == "list_mail_folders":
                has_list_mail_folders = True
                patched_tools.append(tool)
            elif t_name == "trash_email":
                has_trash_email = True
                patched_tools.append(tool)
            elif t_name == "batch_trash_emails":
                has_batch_trash = True
                patched_tools.append(tool)
            elif t_name == "batch_get_emails":
                has_batch_get = True
                patched_tools.append(tool)
            elif t_name == "search_files":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Search files and folders in OneDrive by keywords. "
                    "Universal MSA & Entra ID support via Graph /me/drive/root/search."
                )
                patched_tools.append(t_copy)
            elif t_name == "search_events":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Search calendar events by keywords and/or date range. "
                    "Universal MSA & Entra ID support via Graph /me/calendarView and /me/events."
                )
                patched_tools.append(t_copy)
            elif t_name == "unified_search":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Search across Microsoft 365 emails ('message'), OneDrive files ('driveItem'/'drive'), "
                    "and calendar events ('event'). SharePoint/Lists ('site', 'list', 'listItem') are not "
                    "supported on personal MSA accounts."
                )
                patched_tools.append(t_copy)
            else:
                patched_tools.append(tool)

        if not has_list_mail_folders:
            patched_tools.append(
                {
                    "name": "list_mail_folders",
                    "description": (
                        "List all mail folders and subfolders in the user's Microsoft 365 mailbox with their "
                        "display names, full hierarchical paths, Graph IDs, and unread/total message counts."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            }
                        },
                        "required": [],
                    },
                }
            )

        if not has_trash_email:
            patched_tools.append(
                {
                    "name": "trash_email",
                    "description": (
                        "Safely move an email to Deleted Items (Trash / Corbeille). Reversible soft delete."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_id": {
                                "type": "string",
                                "description": "The ID of the email to move to trash.",
                            },
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            },
                        },
                        "required": ["email_id"],
                    },
                }
            )

        if not has_batch_trash:
            patched_tools.append(
                {
                    "name": "batch_trash_emails",
                    "description": (
                        "Move multiple emails to Trash in batch using Graph $batch API (up to 100 emails at a time). "
                        "Highly recommended for bulk cleanup operations."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of email IDs to move to trash.",
                            },
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            },
                        },
                        "required": ["email_ids"],
                    },
                }
            )

        if not has_batch_get:
            patched_tools.append(
                {
                    "name": "batch_get_emails",
                    "description": (
                        "Retrieve full details and content for multiple emails in batch using Graph $batch API "
                        "(up to 100 emails at a time). Significantly faster and more token-efficient than calling "
                        "get_email repeatedly."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of email IDs to retrieve.",
                            },
                            "include_body": {
                                "type": "boolean",
                                "default": True,
                                "description": "Whether to include body content in the response.",
                            },
                            "body_max_length": {
                                "type": "integer",
                                "default": 5000,
                                "description": (
                                    "Maximum characters for body content per email to preserve LLM context."
                                ),
                            },
                            "include_attachments": {
                                "type": "boolean",
                                "default": False,
                                "description": "Whether to include attachment metadata (name, size, type).",
                            },
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            },
                        },
                        "required": ["email_ids"],
                    },
                }
            )

        result["tools"] = patched_tools
        return self._compact_tools_catalog(
            result,
            optional_props={"account_id"},
            max_desc_len=280,
        )

    async def before_tool_call(
        self,
        user_id: str,
        tool_name: str,
        args: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        account_id: Optional[str] = None,
    ) -> Tuple[dict, Optional[dict]]:
        args = dict(args or {})
        if credentials:
            await self.sync_credentials(user_id, credentials)

        # Cas 1 : Finalisation Device Code Flow -> restauration du flow exact + single-poll
        if tool_name == "complete_authentication":
            flow_path = self._flow_cache_path(user_id, credentials)
            raw_flow = None
            if os.path.exists(flow_path):
                try:
                    with open(flow_path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    raw_flow = saved.get("_flow_cache")
                except Exception as e:
                    logger.warning("Impossible de relire le device flow MS365: %s", e)

            flow_str = raw_flow or args.get("flow_cache", "")
            if not flow_str:
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": (
                            "Aucun flux d'authentification en cours trouvé dans le cache. "
                            "Veuillez d'abord appeler authenticate_account."
                        ),
                    },
                    is_error=True,
                )

            try:
                flow_dict = (
                    ast.literal_eval(flow_str) if isinstance(flow_str, str) else dict(flow_str)
                )
                if isinstance(flow_dict, dict):
                    # Force MSAL obtain_token_by_device_flow à exécuter 1 seule requête HTTP
                    # immédiate au lieu de bloquer 900s sur authorization_pending.
                    flow_dict["expires_at"] = 0
                    args["flow_cache"] = repr(flow_dict)
            except Exception as e:
                logger.warning("Échec de normalisation du flow_cache MS365: %s", e)
                if raw_flow:
                    args["flow_cache"] = raw_flow

            return args, None

        # Cas 2 : Outils d'authentification/listing de comptes -> passage direct
        if tool_name in self.AUTH_EXEMPT_TOOLS:
            return args, None

        # Cas 3 : Outils métiers -> vérification et résolution déterministe de account_id
        accounts = self._load_cached_accounts(user_id, credentials)
        if not accounts:
            return args, self._make_tool_response(
                {
                    "status": "auth_required",
                    "message": (
                        "Aucun compte Microsoft 365 authentifié dans le cache MSAL persistant. "
                        "Appelez d'abord `authenticate_account`, présentez le code et l'URL à "
                        "l'utilisateur, puis appelez `complete_authentication` après validation."
                    ),
                },
                is_error=True,
            )

        explicit_arg_id = str(args.get("account_id") or "").strip()
        vault_acc_id = str(account_id or "").strip()
        by_id = {a["account_id"]: a["account_id"] for a in accounts}
        by_username = {
            a["username"].lower(): a["account_id"]
            for a in accounts
            if a.get("username")
        }

        resolved_account = None
        if explicit_arg_id in by_id:
            resolved_account = explicit_arg_id
        elif explicit_arg_id and explicit_arg_id.lower() in by_username:
            resolved_account = by_username[explicit_arg_id.lower()]
            logger.info("MS365: account_id '%s' résolu vers '%s'", explicit_arg_id, resolved_account)
            args["account_id"] = resolved_account
        elif not explicit_arg_id and len(accounts) == 1:
            resolved_account = accounts[0]["account_id"]
            logger.info(
                "MS365: account_id absent, auto-résolu vers le compte unique '%s'",
                resolved_account,
            )
            args["account_id"] = resolved_account
        elif explicit_arg_id and explicit_arg_id == vault_acc_id and len(accounts) == 1:
            resolved_account = accounts[0]["account_id"]
            logger.info(
                "MS365: alias Vault '%s' auto-résolu vers le compte unique '%s'",
                explicit_arg_id,
                resolved_account,
            )
            args["account_id"] = resolved_account
        else:
            return args, self._make_tool_response(
                {
                    "status": "invalid_account_id",
                    "message": (
                        f"Le paramètre account_id='{explicit_arg_id}' ne correspond à aucun compte MSAL "
                        "en cache. Utilisez l'un des identifiants `account_id` ci-dessous."
                    ),
                    "available_accounts": accounts,
                },
                is_error=True,
            )

        mailbox_key = f"{user_id}:{resolved_account}"

        # Cas 4 : Outil virtuel list_mail_folders -> court-circuit direct
        if tool_name == "list_mail_folders":
            try:
                folders = await self._get_cached_folders(
                    user_id,
                    resolved_account,
                    raise_on_error=True,
                    credentials=credentials,
                )
                formatted = [
                    {
                        "displayName": f.get("displayName"),
                        "path": f.get("path"),
                        "id": f.get("id"),
                        "parentFolderId": f.get("parentFolderId"),
                        "unreadItemCount": f.get("unreadItemCount", 0),
                        "totalItemCount": f.get("totalItemCount", 0),
                        "childFolderCount": f.get("childFolderCount", 0),
                    }
                    for f in folders
                ]
                return args, self._make_tool_response(formatted)
            except Exception as e:
                return args, self._handle_tool_exception("list_mail_folders", e)

        # Cas 5 : Outil virtuel trash_email -> mise à la corbeille réversible (Soft Delete)
        if tool_name == "trash_email":
            email_id = args.get("email_id")
            if not email_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id obligatoire."}, is_error=True
                )
            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                q_eid = self._quote_graph_id(email_id)
                res = await self._graph_request(
                    "POST",
                    f"https://graph.microsoft.com/v1.0/me/messages/{q_eid}/move",
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    json_body={"destinationId": "deleteditems"},
                    timeout_sec=15.0,
                    mailbox_key=mailbox_key,
                )
                if res.status_code in (200, 201):
                    res_json = res.json() if res.content else {}
                    new_id = res_json.get("id", email_id)
                    return args, self._make_tool_response(
                        {
                            "status": "trashed",
                            "message_id": email_id,
                            "old_id": email_id,
                            "new_id": new_id,
                            "folder": "deleteditems",
                        }
                    )
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Échec de mise à la corbeille Graph: {res.text}",
                    },
                    is_error=True,
                )
            except Exception as e:
                return args, self._handle_tool_exception("trash_email", e)

        # Cas 6 : Outil virtuel batch_trash_emails -> suppression par lot via Graph $batch
        if tool_name == "batch_trash_emails":
            email_ids = args.get("email_ids", [])
            if not email_ids or not isinstance(email_ids, list):
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": "email_ids DOIT être une liste non vide d'identifiants.",
                    },
                    is_error=True,
                )
            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                trashed_count = 0
                failed_ids = []
                id_mapping = {}

                chunks = [
                    email_ids[i:i + 20] for i in range(0, len(email_ids), 20)
                ]
                for chunk in chunks:
                    sub_requests = [
                        {
                            "id": str(idx),
                            "method": "POST",
                            "url": f"/me/messages/{self._quote_graph_id(eid)}/move",
                            "headers": {"Content-Type": "application/json"},
                            "body": {"destinationId": "deleteditems"},
                        }
                        for idx, eid in enumerate(chunk)
                    ]
                    batch_res = await self._graph_request(
                        "POST",
                        "https://graph.microsoft.com/v1.0/$batch",
                        headers=headers,
                        json_body={"requests": sub_requests},
                        timeout_sec=30.0,
                        mailbox_key=mailbox_key,
                    )
                    if batch_res.status_code == 200:
                        b_data = batch_res.json()
                        for r in b_data.get("responses", []):
                            idx_req = int(r.get("id", -1))
                            if r.get("status") in (200, 201):
                                trashed_count += 1
                                if 0 <= idx_req < len(chunk):
                                    orig_id = chunk[idx_req]
                                    res_b = r.get("body", {})
                                    new_id = res_b.get("id", orig_id) if isinstance(res_b, dict) else orig_id
                                    id_mapping[orig_id] = new_id
                            else:
                                if 0 <= idx_req < len(chunk):
                                    failed_ids.append(chunk[idx_req])
                    else:
                        failed_ids.extend(chunk)

                return args, self._make_tool_response(
                    {
                        "status": "success" if not failed_ids else "partial_success",
                        "total_requested": len(email_ids),
                        "trashed_count": trashed_count,
                        "failed_count": len(failed_ids),
                        "id_mapping": id_mapping,
                        "failed_ids": failed_ids,
                    }
                )
            except Exception as e:
                return args, self._handle_tool_exception("batch_trash_emails", e)

        # Cas 7 : get_email -> interception Graph directe avec nettoyage HTML et préservation des liens
        if tool_name == "get_email":
            email_id = args.get("email_id")
            if not email_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id obligatoire."}, is_error=True
                )
            include_body = self._parse_bool(args.get("include_body"), True)
            body_max_length = max(int(args.get("body_max_length", 50000) or 50000), 100)
            include_attachments = self._parse_bool(args.get("include_attachments"), True)

            select_fields = self._get_email_select_fields(include_body)

            params = {"$select": select_fields}
            if include_attachments:
                params["$expand"] = "attachments($select=id,name,size,contentType,isInline)"

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                q_eid = self._quote_graph_id(email_id)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                res = await self._graph_request(
                    "GET",
                    f"https://graph.microsoft.com/v1.0/me/messages/{q_eid}",
                    headers=headers,
                    params=params,
                    timeout_sec=20.0,
                    mailbox_key=mailbox_key,
                )
                if res.status_code == 200:
                    msg = self._normalize_email_message(
                        res.json(), include_body, body_max_length
                    )
                    return args, self._make_tool_response(msg)
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Échec Graph get_email ({res.status_code}): {res.text}",
                    },
                    is_error=True,
                )
            except Exception as e:
                return args, self._handle_tool_exception("get_email", e)

        # Cas 8 : Outil virtuel batch_get_emails -> lecture par lot via Graph $batch
        if tool_name == "batch_get_emails":
            email_ids = args.get("email_ids", [])
            if not email_ids or not isinstance(email_ids, list):
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": "email_ids DOIT être une liste non vide d'identifiants.",
                    },
                    is_error=True,
                )

            email_ids = [str(e).strip() for e in email_ids if str(e).strip()][:100]
            include_body = self._parse_bool(args.get("include_body"), True)
            body_max_length = max(int(args.get("body_max_length", 5000) or 5000), 100)
            include_attachments = self._parse_bool(args.get("include_attachments"), False)

            select_fields = self._get_email_select_fields(include_body)

            expand_clause = (
                "&$expand=attachments($select=id,name,size,contentType,isInline)"
                if include_attachments
                else ""
            )

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                retrieved_emails = []
                failed_ids = []
                failed_details: list[dict] = []

                chunks = [
                    email_ids[i:i + 20] for i in range(0, len(email_ids), 20)
                ]
                sub_headers: dict[str, str] = {}

                async def _fetch_chunk(chunk: list[str]):
                    c_emails = []
                    c_failed_ids = []
                    c_failed_details: list[dict] = []
                    sub_requests = [
                        {
                            "id": str(idx),
                            "method": "GET",
                            "url": (
                                f"/me/messages/{self._quote_graph_id(eid)}"
                                f"?$select={select_fields}{expand_clause}"
                            ),
                            "headers": sub_headers,
                        }
                        for idx, eid in enumerate(chunk)
                    ]
                    batch_res = await self._graph_request(
                        "POST",
                        "https://graph.microsoft.com/v1.0/$batch",
                        headers=headers,
                        json_body={"requests": sub_requests},
                        timeout_sec=30.0,
                        mailbox_key=mailbox_key,
                    )
                    if batch_res.status_code == 200:
                        b_data = batch_res.json()
                        for r in b_data.get("responses", []):
                            idx_req = int(r.get("id", -1))
                            if r.get("status") == 200:
                                msg = self._normalize_email_message(
                                    r.get("body", {}), include_body, body_max_length
                                )
                                c_emails.append(msg)
                            else:
                                if 0 <= idx_req < len(chunk):
                                    eid = chunk[idx_req]
                                    c_failed_ids.append(eid)
                                    err_body = r.get("body")
                                    err_msg = ""
                                    if isinstance(err_body, dict):
                                        err_obj = err_body.get("error")
                                        if isinstance(err_obj, dict) and err_obj.get("message"):
                                            err_msg = str(err_obj["message"])
                                        elif err_body:
                                            err_msg = str(err_body)[:300]
                                    elif err_body:
                                        err_msg = str(err_body)[:300]
                                    c_failed_details.append(
                                        {
                                            "id": eid,
                                            "status": r.get("status"),
                                            "error": err_msg,
                                        }
                                    )
                    else:
                        err_text = batch_res.text[:300]
                        c_failed_ids.extend(chunk)
                        for eid in chunk:
                            c_failed_details.append(
                                {
                                    "id": eid,
                                    "status": batch_res.status_code,
                                    "error": err_text,
                                }
                            )
                    return c_emails, c_failed_ids, c_failed_details

                chunk_results = await asyncio.gather(*[_fetch_chunk(c) for c in chunks])
                for c_emails, c_failed_ids, c_failed_details in chunk_results:
                    retrieved_emails.extend(c_emails)
                    failed_ids.extend(c_failed_ids)
                    failed_details.extend(c_failed_details)

                return args, self._make_tool_response(
                    {
                        "status": "success" if not failed_ids else "partial_success",
                        "total_requested": len(email_ids),
                        "retrieved_count": len(retrieved_emails),
                        "failed_count": len(failed_ids),
                        "emails": retrieved_emails,
                        "failed_ids": failed_ids,
                        "failed_details": failed_details,
                    }
                )
            except Exception as e:
                return args, self._handle_tool_exception("batch_get_emails", e)

        # Cas 9 : list_emails -> exécution Graph directe (supporte skip, order_by, auto-résolution et repli)
        if tool_name == "list_emails":
            folder_arg = args.get("folder", "inbox")
            limit = min(int(args.get("limit", 10) or 10), 100)
            skip = max(int(args.get("skip", 0) or 0), 0)
            order_by = str(args.get("order_by") or "receivedDateTime desc").strip()
            include_body = self._parse_bool(args.get("include_body"), False)
            body_max_length = max(int(args.get("body_max_length", 5000) or 5000), 100)

            resolved_fid = await self._resolve_folder_id(
                user_id, resolved_account, folder_arg, credentials=credentials
            )
            if not resolved_fid:
                folders = await self._get_cached_folders(
                    user_id, resolved_account, credentials=credentials
                )
                available = [
                    f.get("path") or f.get("displayName") for f in folders
                ]
                return args, self._make_tool_response(
                    {
                        "status": "folder_not_found",
                        "message": (
                            f"Le dossier '{folder_arg}' est introuvable dans la messagerie Microsoft 365. "
                            f"Dossiers disponibles : {', '.join(str(x) for x in available[:30])}. "
                            "Appelez `list_mail_folders` pour examiner l'arborescence complète."
                        ),
                    },
                    is_error=True,
                )

            folder_endpoint_id = self.WELL_KNOWN_PATHS.get(
                resolved_fid.casefold(), resolved_fid
            )
            select_fields = self._get_email_select_fields(include_body)

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                q_fid = self._quote_graph_id(folder_endpoint_id)
                url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{q_fid}/messages"
                params = {
                    "$top": str(limit),
                    "$orderby": order_by,
                    "$select": select_fields,
                }
                if skip > 0:
                    params["$skip"] = str(skip)

                res = None
                primary_exc: Optional[Exception] = None
                try:
                    res = await self._graph_request(
                        "GET",
                        url,
                        headers=headers,
                        params=params,
                        timeout_sec=15.0,
                        max_retries=2,
                        mailbox_key=mailbox_key,
                    )
                except (httpx.TimeoutException, httpx.NetworkError) as exc:
                    primary_exc = exc

                if res is not None and res.status_code == 200:
                    messages = [
                        self._normalize_email_message(m, include_body, body_max_length)
                        for m in res.json().get("value", [])
                    ]
                    return args, self._make_tool_response(messages)

                if primary_exc is not None or (res is not None and res.status_code >= 500):
                    imm_id = await self._resolve_immutable_folder_id(
                        user_id, resolved_account, folder_endpoint_id, credentials
                    )
                    if imm_id:
                        fb_params = {
                            "$top": str(limit),
                            "$orderby": order_by,
                            "$select": select_fields,
                            "$filter": (
                                f"receivedDateTime ge 1900-01-01T00:00:00Z "
                                f"and parentFolderId eq '{imm_id}'"
                            ),
                        }
                        if skip > 0:
                            fb_params["$skip"] = str(skip)
                        fb_res = await self._graph_request(
                            "GET",
                            "https://graph.microsoft.com/v1.0/me/messages",
                            headers=headers,
                            params=fb_params,
                            timeout_sec=15.0,
                            max_retries=2,
                            mailbox_key=mailbox_key,
                        )
                        if (
                            fb_res.status_code == 400
                            and "InefficientFilter" in fb_res.text
                            and "$orderby" in fb_params
                        ):
                            fb_params.pop("$orderby", None)
                            fb_res = await self._graph_request(
                                "GET",
                                "https://graph.microsoft.com/v1.0/me/messages",
                                headers=headers,
                                params=fb_params,
                                timeout_sec=15.0,
                                max_retries=2,
                                mailbox_key=mailbox_key,
                            )
                        if fb_res.status_code == 200:
                            messages = [
                                self._normalize_email_message(m, include_body, body_max_length)
                                for m in fb_res.json().get("value", [])
                            ]
                            return args, self._make_tool_response(
                                self._sort_email_messages(messages, order_by)
                            )
                        res = fb_res

                if primary_exc is not None:
                    raise primary_exc

                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Échec Graph list_emails ({res.status_code}): {res.text}",
                    },
                    is_error=True,
                )
            except Exception as e:
                return args, self._handle_tool_exception("list_emails", e)

        # Cas 10 : search_emails -> recherche multi-axes (mots-clés, temporalité, expéditeur, dossier, statut)
        if tool_name == "search_emails":
            query = str(args.get("query") or "").strip()
            folder_arg = args.get("folder")
            received_after = str(args.get("received_after") or "").strip()
            received_before = str(args.get("received_before") or "").strip()
            sender = str(args.get("sender") or "").strip()
            unread_only = (
                self._parse_bool(args.get("unread_only"), False)
                if args.get("unread_only") is not None
                else None
            )
            has_attachments = (
                self._parse_bool(args.get("has_attachments"), False)
                if args.get("has_attachments") is not None
                else None
            )
            order_by = str(args.get("order_by") or "receivedDateTime desc").strip()
            limit = min(max(int(args.get("limit", 50) or 50), 1), 100)
            include_body = self._parse_bool(args.get("include_body"), False)
            body_max_length = max(int(args.get("body_max_length", 5000) or 5000), 100)

            if folder_arg:
                resolved_fid = await self._resolve_folder_id(
                    user_id, resolved_account, folder_arg, credentials=credentials
                )
                if not resolved_fid:
                    folders = await self._get_cached_folders(
                        user_id, resolved_account, credentials=credentials
                    )
                    available = [
                        f.get("path") or f.get("displayName") for f in folders
                    ]
                    return args, self._make_tool_response(
                        {
                            "status": "folder_not_found",
                            "message": (
                                f"Le dossier '{folder_arg}' est introuvable dans Microsoft 365. "
                                f"Dossiers disponibles : {', '.join(str(x) for x in available[:30])}."
                            ),
                        },
                        is_error=True,
                    )
                folder_endpoint_id = self.WELL_KNOWN_PATHS.get(
                    resolved_fid.casefold(), resolved_fid
                )
                q_fid = self._quote_graph_id(folder_endpoint_id)
                base_url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{q_fid}/messages"
            else:
                base_url = "https://graph.microsoft.com/v1.0/me/messages"

            select_fields = self._get_email_select_fields(include_body)

            iso_after, short_after = self._normalize_iso_date(received_after, is_end_of_day=False)
            iso_before, short_before = self._normalize_iso_date(received_before, is_end_of_day=True)

            use_kql = bool(query) or (bool(sender) and "@" not in sender)
            fetch_top = min(limit * 2, 100) if use_kql else limit
            params = {
                "$top": str(fetch_top),
                "$select": select_fields,
            }

            if use_kql:
                kql_terms = []
                clean_query = query.replace('"', "").strip()
                clean_sender = sender.replace('"', "").strip()
                if clean_query:
                    kql_terms.append(clean_query)
                if clean_sender:
                    kql_terms.append(f"from:{clean_sender}")
                if short_after:
                    kql_terms.append(f"received>={short_after}")
                if short_before:
                    kql_terms.append(f"received<={short_before}")
                if has_attachments is not None:
                    kql_terms.append(
                        f"hasattachment:{'true' if has_attachments else 'false'}"
                    )
                if unread_only is True:
                    kql_terms.append("isread:false")

                params["$search"] = f'"{" AND ".join(kql_terms)}"'
            else:
                filters = []
                if iso_after:
                    filters.append(f"receivedDateTime ge {iso_after}")
                if iso_before:
                    filters.append(f"receivedDateTime le {iso_before}")
                if sender:
                    clean_sender = sender.replace("'", "''")
                    filters.append(f"from/emailAddress/address eq '{clean_sender}'")
                if unread_only is True:
                    filters.append("isRead eq false")
                if has_attachments is not None:
                    filters.append(f"hasAttachments eq {'true' if has_attachments else 'false'}")

                if filters and not iso_after:
                    filters.insert(0, "receivedDateTime ge 1900-01-01T00:00:00Z")
                if filters:
                    params["$filter"] = " and ".join(filters)
                params["$orderby"] = order_by

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                }
                if use_kql:
                    headers["ConsistencyLevel"] = "eventual"
                res = await self._graph_request(
                    "GET",
                    base_url,
                    headers=headers,
                    params=params,
                    timeout_sec=16.0,
                    max_retries=2,
                    mailbox_key=mailbox_key,
                )
                needs_python_sort = use_kql
                if (
                    not use_kql
                    and res.status_code == 400
                    and "InefficientFilter" in res.text
                    and "$orderby" in params
                ):
                    params.pop("$orderby", None)
                    needs_python_sort = True
                    res = await self._graph_request(
                        "GET",
                        base_url,
                        headers=headers,
                        params=params,
                        timeout_sec=16.0,
                        max_retries=2,
                        mailbox_key=mailbox_key,
                    )

                if res.status_code == 200:
                    messages = [
                        self._normalize_email_message(
                            m, include_body, body_max_length=body_max_length
                        )
                        for m in res.json().get("value", [])
                    ]
                    if use_kql and (
                        "T" in received_after
                        or ":" in received_after
                        or "T" in received_before
                        or ":" in received_before
                    ):
                        filtered_msgs = []
                        for m in messages:
                            m_iso, _ = self._normalize_iso_date(
                                str(m.get("receivedDateTime") or "")
                            )
                            if iso_after and m_iso and m_iso < iso_after:
                                continue
                            if iso_before and m_iso and m_iso > iso_before:
                                continue
                            filtered_msgs.append(m)
                        messages = filtered_msgs
                    if needs_python_sort:
                        messages = self._sort_email_messages(messages, order_by)
                    return args, self._make_tool_response(messages[:limit])
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Échec Graph search_emails ({res.status_code}): {res.text}",
                    },
                    is_error=True,
                )
            except Exception as e:
                return args, self._handle_tool_exception("search_emails", e)

        # Cas 10b : search_files -> recherche universelle OneDrive (MSA & Entra ID)
        if tool_name == "search_files":
            query = str(args.get("query") or "").strip()
            limit = min(max(int(args.get("limit", 25) or 25), 1), 100)
            if not query:
                return args, self._make_tool_response(
                    {"status": "error", "message": "query obligatoire pour search_files."},
                    is_error=True,
                )
            try:
                files = await self._search_onedrive_files(
                    user_id, resolved_account, query, limit=limit, credentials=credentials
                )
                return args, self._make_tool_response(files)
            except Exception as e:
                return args, self._handle_tool_exception("search_files", e)

        # Cas 10c : search_events -> recherche universelle calendrier (MSA & Entra ID)
        if tool_name == "search_events":
            query = str(args.get("query") or "").strip()
            start_date = str(args.get("start_date") or args.get("start_datetime") or "").strip()
            end_date = str(args.get("end_date") or args.get("end_datetime") or "").strip()
            limit = min(max(int(args.get("limit", 25) or 25), 1), 100)
            try:
                events = await self._search_calendar_events(
                    user_id,
                    resolved_account,
                    query=query,
                    start_date=start_date,
                    end_date=end_date,
                    limit=limit,
                    credentials=credentials,
                )
                return args, self._make_tool_response(events)
            except Exception as e:
                return args, self._handle_tool_exception("search_events", e)

        # Cas 10d : unified_search -> recherche multi-ressources universelle (MSA & Entra ID)
        if tool_name == "unified_search":
            query = str(args.get("query") or "").strip()
            raw_types = args.get("entity_types") or args.get("types") or ["message", "driveItem", "event"]
            if isinstance(raw_types, str):
                raw_types = [t.strip() for t in raw_types.split(",") if t.strip()]
            limit = min(max(int(args.get("limit", 25) or 25), 1), 100)
            if not query:
                return args, self._make_tool_response(
                    {"status": "error", "message": "query obligatoire pour unified_search."},
                    is_error=True,
                )

            results: dict[str, Any] = {}
            errors: dict[str, str] = {}
            has_unsupported_type = False

            try:
                for raw_t in raw_types:
                    t_norm = "driveItem" if raw_t == "drive" else str(raw_t)
                    if t_norm in ("site", "list", "listItem"):
                        has_unsupported_type = True
                        errors[t_norm] = (
                            f"Le type '{t_norm}' n'est pas supporté sur les comptes Microsoft personnels (MSA)."
                        )
                        continue
                    try:
                        if t_norm == "message":
                            results["message"] = await self._search_messages_kql(
                                user_id, resolved_account, query, limit=limit, credentials=credentials
                            )
                        elif t_norm == "driveItem":
                            results["driveItem"] = await self._search_onedrive_files(
                                user_id, resolved_account, query, limit=limit, credentials=credentials
                            )
                        elif t_norm == "event":
                            results["event"] = await self._search_calendar_events(
                                user_id, resolved_account, query=query, limit=limit, credentials=credentials
                            )
                        else:
                            has_unsupported_type = True
                            errors[t_norm] = f"Type d'entité non supporté: '{t_norm}'."
                    except AuthRequiredError:
                        raise
                    except Exception as e:
                        errors[t_norm] = self._format_exception(e)
            except Exception as e:
                return args, self._handle_tool_exception("unified_search", e)

            if has_unsupported_type or (errors and not results):
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "results": results,
                        "errors": errors,
                    },
                    is_error=True,
                )
            payload: dict[str, Any] = {"results": results}
            if errors:
                payload["errors"] = errors
            return args, self._make_tool_response(payload)

        # Cas 11 : move_email -> résolution et exécution directe Graph (support de tous sous-dossiers)
        if tool_name == "move_email":
            dest_arg = args.get("destination_folder")
            email_id = args.get("email_id")
            if dest_arg and email_id:
                resolved_fid = await self._resolve_folder_id(
                    user_id, resolved_account, dest_arg, credentials=credentials
                )
                if not resolved_fid:
                    folders = await self._get_cached_folders(
                        user_id, resolved_account, credentials=credentials
                    )
                    available = [
                        f.get("path") or f.get("displayName") for f in folders
                    ]
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": (
                                f"Dossier de destination '{dest_arg}' introuvable. "
                                f"Dossiers disponibles : {', '.join(str(x) for x in available[:25])}."
                            ),
                        },
                        is_error=True,
                    )
                try:
                    token = await self._get_access_token_async(user_id, resolved_account, credentials)
                    q_eid = self._quote_graph_id(email_id)
                    move_res = await self._graph_request(
                        "POST",
                        f"https://graph.microsoft.com/v1.0/me/messages/{q_eid}/move",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json_body={"destinationId": resolved_fid},
                        timeout_sec=15.0,
                        mailbox_key=mailbox_key,
                    )
                    if move_res.status_code in (200, 201):
                        res_json = move_res.json() if move_res.content else {}
                        return args, self._make_tool_response(
                            {
                                "status": "moved",
                                "old_id": email_id,
                                "new_id": res_json.get("id"),
                                "destination_folder": dest_arg,
                            }
                        )
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph move_email: {move_res.text}",
                        },
                        is_error=True,
                    )
                except Exception as e:
                    return args, self._handle_tool_exception("move_email", e)

        # Cas 12 : get_file (OneDrive) -> téléchargement et routage forcé vers le Download Broker
        if tool_name == "get_file":
            file_id = str(args.get("file_id") or "").strip()
            if not file_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "file_id obligatoire."}, is_error=True
                )
            fid = uuid.uuid4().hex[:8]
            req_path = str(args.get("download_path") or "").strip()
            req_name = self._sanitize_filename(req_path, "") if req_path else ""
            safe_fid_seg = self._sanitize_segment(file_id, "file")

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                q_fid = self._quote_graph_id(file_id)
                meta_res = await self._graph_request(
                    "GET",
                    f"https://graph.microsoft.com/v1.0/me/drive/items/{q_fid}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout_sec=60.0,
                    mailbox_key=mailbox_key,
                    follow_redirects=True,
                )
                if meta_res.status_code != 200:
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph get_file metadata ({meta_res.status_code}): {meta_res.text}",
                        },
                        is_error=True,
                    )
                metadata = meta_res.json()
                download_url = metadata.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    return args, self._make_tool_response(
                        {"status": "error", "message": "Aucune downloadUrl disponible pour ce fichier OneDrive."},
                        is_error=True,
                    )
                remote_name = self._sanitize_filename(metadata.get("name"), "")
                chosen_name = remote_name or req_name or f"onedrive_{safe_fid_seg}"
                drop_dir = self._get_mcp_drop_dir(user_id, chat_id)
                safe_name = f"{fid}_{chosen_name}"
                final_path = os.path.join(drop_dir, safe_name)
                part_path = f"{final_path}.part"

                async with self._create_graph_client(60.0, follow_redirects=True) as client:
                    async with client.stream("GET", download_url) as dl_stream:
                        dl_stream.raise_for_status()
                        with open(part_path, "wb") as f_out:
                            async for chunk in dl_stream.aiter_bytes(chunk_size=65536):
                                f_out.write(chunk)

                _, safe_name, final_path = self._publish_file_to_broker(
                    part_path, user_id, chat_id, chosen_name, fid=fid
                )
                size_bytes = int(metadata.get("size") or os.path.getsize(final_path))
                mime_type = str(
                    (metadata.get("file") or {}).get("mimeType") or "application/octet-stream"
                )
                return args, self._make_tool_response(
                    self._build_broker_download_payload(
                        fid=fid,
                        safe_name=safe_name,
                        original_name=metadata.get("name", chosen_name),
                        final_path=final_path,
                        size_bytes=size_bytes,
                        content_type=mime_type,
                    )
                )
            except Exception as e:
                return args, self._handle_tool_exception("get_file", e)

        # Cas 13 : get_attachment (Outlook) -> téléchargement et routage forcé vers le Download Broker
        if tool_name == "get_attachment":
            email_id = str(args.get("email_id") or "").strip()
            attachment_id = str(args.get("attachment_id") or "").strip()
            if not email_id or not attachment_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id et attachment_id obligatoires."},
                    is_error=True,
                )
            fid = uuid.uuid4().hex[:8]
            req_path = str(args.get("save_path") or "").strip()
            req_name = self._sanitize_filename(req_path, "") if req_path else ""
            safe_att_seg = self._sanitize_segment(attachment_id[:8], "att")

            try:
                token = await self._get_access_token_async(user_id, resolved_account, credentials)
                q_eid = self._quote_graph_id(email_id)
                q_aid = self._quote_graph_id(attachment_id)
                att_res = await self._graph_request(
                    "GET",
                    f"https://graph.microsoft.com/v1.0/me/messages/{q_eid}/attachments/{q_aid}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    timeout_sec=60.0,
                    mailbox_key=mailbox_key,
                )
                if att_res.status_code != 200:
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph get_attachment ({att_res.status_code}): {att_res.text}",
                        },
                        is_error=True,
                    )
                att_data = att_res.json()
                remote_name = self._sanitize_filename(att_data.get("name"), "")
                chosen_name = remote_name or req_name or f"attachment_{safe_att_seg}"
                content_b64 = att_data.get("contentBytes")
                if content_b64:
                    content_bytes = base64.b64decode(content_b64)
                else:
                    odata_type = str(att_data.get("@odata.type") or "")
                    if odata_type.endswith("itemAttachment"):
                        val_res = await self._graph_request(
                            "GET",
                            f"https://graph.microsoft.com/v1.0/me/messages/{q_eid}/attachments/{q_aid}/$value",
                            headers={"Authorization": f"Bearer {token}"},
                            timeout_sec=60.0,
                            mailbox_key=mailbox_key,
                        )
                        if val_res.status_code != 200:
                            return args, self._make_tool_response(
                                {
                                    "status": "error",
                                    "message": (
                                        f"Échec Graph get_attachment/$value ({val_res.status_code}): "
                                        f"{val_res.text}"
                                    ),
                                },
                                is_error=True,
                            )
                        content_bytes = val_res.content
                        if not os.path.splitext(chosen_name)[1]:
                            chosen_name = f"{chosen_name}.eml"
                    elif odata_type.endswith("referenceAttachment"):
                        src_url = att_data.get("sourceUrl")
                        return args, self._make_tool_response(
                            {
                                "status": "error",
                                "message": (
                                    "Pièce jointe referenceAttachment (lien externe sans contenu binaire) : "
                                    f"{src_url or 'sourceUrl indisponible'}"
                                ),
                                "sourceUrl": src_url,
                            },
                            is_error=True,
                        )
                    else:
                        return args, self._make_tool_response(
                            {"status": "error", "message": "Contenu binaire (contentBytes) indisponible."},
                            is_error=True,
                        )

                _, safe_name, final_path = self._publish_file_to_broker(
                    content_bytes, user_id, chat_id, chosen_name, fid=fid
                )
                size_bytes = int(att_data.get("size") or len(content_bytes))
                ctype = str(att_data.get("contentType") or "application/octet-stream")
                return args, self._make_tool_response(
                    self._build_broker_download_payload(
                        fid=fid,
                        safe_name=safe_name,
                        original_name=att_data.get("name", chosen_name),
                        final_path=final_path,
                        size_bytes=size_bytes,
                        content_type=ctype,
                    )
                )
            except Exception as e:
                return args, self._handle_tool_exception("get_attachment", e)

        return args, None

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
        chat_id: Optional[str] = None,
        credentials: Optional[dict] = None,
        cache_key: Optional[str] = None,
    ) -> dict:
        if tool_name not in (
            "authenticate_account",
            "complete_authentication",
            "get_file",
            "get_attachment",
        ):
            return result

        payload = None
        if isinstance(result.get("structuredContent"), dict):
            payload = result["structuredContent"]
        else:
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        parsed = json.loads(item.get("text", ""))
                        if isinstance(parsed, dict):
                            payload = parsed
                            break
                    except Exception:
                        pass

        if not isinstance(payload, dict):
            return result

        flow_path = self._flow_cache_path(user_id, credentials)
        if tool_name == "authenticate_account" and payload.get("_flow_cache"):
            try:
                os.makedirs(os.path.dirname(flow_path), exist_ok=True)
                with open(flow_path, "w", encoding="utf-8") as f:
                    json.dump({"_flow_cache": str(payload["_flow_cache"])}, f)
                logger.info("MS365: _flow_cache sauvegardé dans %s", flow_path)

                # Suppression complète de _flow_cache de la réponse exposée au LLM
                sanitized = dict(payload)
                sanitized.pop("_flow_cache", None)
                sanitized["step4"] = (
                    "After the user confirms authentication, call 'complete_authentication' "
                    "with empty arguments {}."
                )
                result = dict(result)
                if isinstance(result.get("structuredContent"), dict):
                    result["structuredContent"] = sanitized
                if isinstance(result.get("content"), list):
                    new_content = []
                    for item in result["content"]:
                        if isinstance(item, dict) and item.get("type") == "text":
                            new_item = dict(item)
                            new_item["text"] = json.dumps(sanitized, ensure_ascii=False)
                            new_content.append(new_item)
                        else:
                            new_content.append(item)
                    result["content"] = new_content
            except Exception as e:
                logger.warning("MS365: échec de sauvegarde de _flow_cache: %s", e)

        elif tool_name == "complete_authentication" and payload.get("status") == "success":
            if os.path.exists(flow_path):
                try:
                    os.remove(flow_path)
                except Exception:
                    pass

        elif tool_name in ("get_file", "get_attachment"):
            raw_path = payload.get("path") or payload.get("saved_to")
            if raw_path and os.path.isfile(raw_path):
                base_fname = os.path.basename(raw_path)
                fid = self._extract_hex_fid(base_fname)
                orig_name = payload.get("name")
                target_name = orig_name if orig_name and orig_name != "unknown" else base_fname
                try:
                    fid, safe_name, final_path = self._publish_file_to_broker(
                        raw_path, user_id, chat_id, target_name, fid=fid
                    )
                    size_bytes = int(payload.get("size") or os.path.getsize(final_path))
                    ctype = str(
                        payload.get("content_type")
                        or payload.get("mime_type")
                        or "application/octet-stream"
                    )
                    return self._make_tool_response(
                        self._build_broker_download_payload(
                            fid=fid,
                            safe_name=safe_name,
                            original_name=orig_name or safe_name,
                            final_path=final_path,
                            size_bytes=size_bytes,
                            content_type=ctype,
                        ),
                        is_error=bool(result.get("isError")),
                    )
                except Exception as e:
                    logger.warning("MS365: échec finalisation fichier Download Broker: %s", e)

        return result

    async def upload_file_stream(
        self,
        user_id: str,
        chat_id: str,
        credentials: dict,
        byte_stream: AsyncIterator[bytes],
        filename: str,
        total_size: int,
        mime_type: str = "application/octet-stream",
        folder_id: Optional[str] = None,
        overwrite_file_id: Optional[str] = None,
        convert_to_native: bool = False,
        conflict_behavior: str = "replace",
        account_id: Optional[str] = None,
    ) -> dict:
        if total_size < 0 or total_size > MAX_DRIVE_UPLOAD_BYTES:
            raise ValueError(f"Taille de fichier invalide pour upload OneDrive : {total_size}")
        safe_filename = self._sanitize_filename(filename)
        await self.sync_credentials(user_id, credentials)
        access_token = await self._get_access_token_async(
            user_id, account_id=account_id, credentials=credentials
        )
        account_sig = self._compute_ms_sig(credentials) or self._sanitize_segment(account_id or "default")

        if overwrite_file_id:
            base_item = f"https://graph.microsoft.com/v1.0/me/drive/items/{self._quote_graph_id(overwrite_file_id)}"
        elif folder_id and str(folder_id).strip().lower() != "root":
            raw_f = str(folder_id).strip()
            if "/" in raw_f:
                clean_path = "/".join(urllib.parse.quote(seg, safe="") for seg in raw_f.strip("/").split("/") if seg)
                base_item = (
                    f"https://graph.microsoft.com/v1.0/me/drive/root:/{clean_path}/"
                    f"{urllib.parse.quote(safe_filename, safe='')}:"
                )
            else:
                base_item = (
                    f"https://graph.microsoft.com/v1.0/me/drive/items/{self._quote_graph_id(raw_f)}:/"
                    f"{urllib.parse.quote(safe_filename, safe='')}:"
                )
        else:
            base_item = f"https://graph.microsoft.com/v1.0/me/drive/root:/{urllib.parse.quote(safe_filename, safe='')}:"

        target_key = overwrite_file_id or f"{folder_id or 'root'}:{safe_filename}"
        async with self._get_upload_target_lock("microsoft_365", user_id, account_sig, target_key):
            async with self._get_upload_semaphore("microsoft_365", user_id, account_sig):
                if total_size == 0:
                    async for raw_chunk in byte_stream:
                        if raw_chunk:
                            raise ValueError("Flux non vide reçu alors que total_size == 0.")
                    async with self._create_upload_http_client(timeout_sec=30.0) as client:
                        resp = await client.put(
                            f"{base_item}/content",
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": mime_type or "application/octet-stream",
                            },
                            content=b"",
                        )
                    if resp.status_code == 401:
                        raise AuthRequiredError(f"Jeton Microsoft Graph invalide (401): {resp.text[:300]}")
                    if resp.status_code >= 400:
                        raise RuntimeError(f"Échec upload fichier vide OneDrive ({resp.status_code}): {resp.text[:400]}")
                    data = resp.json()
                else:
                    item_payload: dict[str, Any] = {"@microsoft.graph.conflictBehavior": conflict_behavior}
                    if not overwrite_file_id:
                        item_payload["name"] = safe_filename
                    async with self._create_upload_http_client(timeout_sec=30.0) as client:
                        sess_resp = await client.post(
                            f"{base_item}/createUploadSession",
                            headers={
                                "Authorization": f"Bearer {access_token}",
                                "Content-Type": "application/json",
                            },
                            json={"item": item_payload},
                        )
                    if sess_resp.status_code >= 400:
                        err_txt = sess_resp.text[:500]
                        low = err_txt.lower()
                        if sess_resp.status_code == 401 or (
                            sess_resp.status_code == 403
                            and any(m in low for m in ("invalid", "expired", "unauthenticated", "scope", "permission"))
                        ):
                            raise AuthRequiredError(f"Authentification Microsoft Graph invalide : {err_txt}")
                        raise RuntimeError(f"Échec createUploadSession OneDrive ({sess_resp.status_code}): {err_txt}")
                    sess_data = sess_resp.json()
                    upload_url = sess_data.get("uploadUrl")
                    if not upload_url:
                        raise RuntimeError("uploadUrl manquant dans la réponse createUploadSession OneDrive.")
                    data = await self._stream_resumable_put(
                        upload_url, byte_stream, total_size, mime_type or "application/octet-stream"
                    )

                return self._build_drive_upload_payload(
                    "microsoft_365",
                    file_id=data.get("id", ""),
                    name=data.get("name", safe_filename),
                    size=int(data.get("size") if data.get("size") is not None else total_size),
                    mime_type=(data.get("file") or {}).get("mimeType") or mime_type,
                    folder_id=(data.get("parentReference") or {}).get("id") or folder_id,
                    web_url=data.get("webUrl"),
                )


_DEFAULT_PROVIDER = MCPServiceProvider()
_PROVIDERS: dict[str, MCPServiceProvider] = {
    "microsoft_365": Microsoft365Provider(),
    "google_workspace": GoogleWorkspaceProvider(),
}


def get_service_provider(service: str) -> MCPServiceProvider:
    """Retourne l'adaptateur spécifique au service ou l'adaptateur No-Op par défaut."""
    return _PROVIDERS.get(service, _DEFAULT_PROVIDER)
