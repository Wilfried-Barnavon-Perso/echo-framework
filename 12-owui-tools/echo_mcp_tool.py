"""
title: ECHO Universal MCP Tool
author: ECHO
version: 6.11.0.1
description: Outil universel d'interrogation et d'exécution sur l'infrastructure MCP via routage dynamique.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.11.0.1: Ajout de upload_file_to_drive (streaming 256 KiB vers /upload_drive, resolve_user_exportable_file_path) et user_required_instructions.
# 6.7.18.1: Filtre query et formatage 1-ligne/outil dans list_mcp_functions, et contrôle strict multi-comptes dans _fetch_credentials.
# 5.239.12.1: Déduplication payloads MCP (_compact_mcp_payload) sans double sérialisation content/structuredContent.
# 5.239.11.1: Retour dict normalisé (status="error", error=...) sur erreur MCP/HTTP pour ❌ Open WebUI et statut LLM.
# 5.239.10.1: Factorisation RPC/erreurs, timeouts 90s/65s alignés Broker et clôture events.status sur erreur.
import os
import sys
import json
import base64
import asyncio
import mimetypes
import httpx
from typing import Any, AsyncIterator, Literal, Optional
from pydantic import BaseModel

sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool  # noqa: E402
from echo_events import EchoEvents  # noqa: E402
from echo_paths import extract_clean_filename, resolve_user_exportable_file_path  # noqa: E402


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.schemas_url = "http://echo-mcp-broker:8000/schemas"
        self.mcp_native_url = "http://echo-mcp-broker:8000/mcp"
        self.proxy_url = "http://echo-mcp-broker:8000/proxy_mcp"
        self.upload_drive_url = "http://echo-mcp-broker:8000/upload_drive"

    async def _get_category(self, service: str) -> str:
        """Récupère dynamiquement la taxonomie d'un serveur pour déterminer son routage."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.schemas_url, timeout=2.0)
                schemas = resp.json()
                return schemas.get(service, {}).get("category", "uncategorized")
        except Exception:
            return "uncategorized"

    def _fetch_credentials(
        self,
        user_id: str,
        service: str,
        account_id: Optional[str] = None,
    ) -> tuple[str, Optional[dict]]:
        """Extrait les credentials de l'Identity Vault pour l'utilisateur et le compte spécifiés."""
        try:
            from echo_state_manager import EchoStateManager
            state = EchoStateManager(user_id=user_id)
            creds = state.get_vault_credential(
                service, account_id, allow_single_fallback=True
            )
            if creds or not account_id:
                return creds, None
            available = [acc for acc, _ in state.list_vault_credentials(service)]
            if available:
                msg = (
                    f"Compte '{account_id}' introuvable pour '{service}' "
                    f"(comptes disponibles : {', '.join(available)})."
                )
                return "", {
                    "status": "error",
                    "error": msg,
                    "message": msg,
                    "service": service,
                    "available_accounts": available,
                }
            return "", None
        except Exception:
            return "", None

    @staticmethod
    def _format_transport_error(service: str, action_label: str, exc: Exception) -> dict:
        if isinstance(exc, httpx.TimeoutException):
            msg = f"{type(exc).__name__}: délai dépassé lors de l'appel à '{service}' ({action_label})."
            return {
                "status": "error",
                "error": msg,
                "message": msg,
            }
        err_msg = str(exc).strip() or type(exc).__name__
        return {"status": "error", "error": err_msg, "message": err_msg}

    @staticmethod
    def _compact_mcp_payload(data: Any, unwrap_result: bool = True) -> Any:
        if (
            isinstance(data, dict)
            and "structuredContent" in data
            and data["structuredContent"] is not None
        ):
            content = data.get("content")
            has_non_text = isinstance(content, list) and any(
                isinstance(c, dict) and c.get("type") != "text" for c in content
            )
            if not has_non_text:
                sc = data["structuredContent"]
                if unwrap_result and not data.get("isError"):
                    if isinstance(sc, dict) and set(sc.keys()) == {"result"}:
                        return sc["result"]
                    if set(data.keys()) <= {
                        "content",
                        "structuredContent",
                        "isError",
                        "_meta",
                        "resultType",
                    }:
                        return sc
                compacted = dict(data)
                compacted.pop("content", None)
                compacted.pop("_meta", None)
                compacted.pop("resultType", None)
                return compacted
        return data

    @staticmethod
    def _format_tools_catalog(data: Any, query: Optional[str] = None) -> str:
        if not isinstance(data, dict) or not isinstance(data.get("tools"), list):
            return json.dumps(data, ensure_ascii=False)
        raw_tools = data["tools"]
        q_tokens = [t for t in str(query or "").casefold().split() if t]
        formatted_lines = []
        for tool in raw_tools:
            if not isinstance(tool, dict):
                continue
            t_name = str(tool.get("name") or "")
            t_desc = str(tool.get("description") or "")
            if q_tokens:
                haystack = f"{t_name} {t_desc}".casefold()
                if not all(tok in haystack for tok in q_tokens):
                    continue
            compact_tool: dict[str, Any] = {"name": t_name}
            if t_desc:
                compact_tool["description"] = t_desc
            schema = tool.get("inputSchema")
            if isinstance(schema, dict):
                clean_schema: dict[str, Any] = {}
                if "type" in schema:
                    clean_schema["type"] = schema["type"]
                props = schema.get("properties")
                if isinstance(props, dict):
                    clean_props = {}
                    for prop_name, prop_def in props.items():
                        if isinstance(prop_def, dict):
                            clean_props[prop_name] = {
                                k: v for k, v in prop_def.items() if k != "title"
                            }
                        else:
                            clean_props[prop_name] = prop_def
                    clean_schema["properties"] = clean_props
                if "required" in schema:
                    clean_schema["required"] = schema["required"]
                compact_tool["inputSchema"] = clean_schema
            formatted_lines.append(
                json.dumps(compact_tool, ensure_ascii=False, separators=(",", ":"))
            )
        inner = ",\n".join(formatted_lines)
        tools_block = f"[\n{inner}\n]" if inner else "[]"
        return f'{{"service_tools_count":{len(raw_tools)},"tools":{tools_block}}}'

    @staticmethod
    def _build_error_dict(service: str, action_label: str, data: Any) -> dict:
        msg = f"Erreur lors de l'appel '{action_label}' sur '{service}'."
        if isinstance(data, dict):
            sc = data.get("structuredContent")
            if isinstance(sc, dict) and (sc.get("message") or sc.get("error")):
                msg = str(sc.get("message") or sc.get("error"))
            elif data.get("message") or data.get("error"):
                raw_err = data.get("message") or data.get("error")
                msg = json.dumps(raw_err, ensure_ascii=False) if isinstance(raw_err, dict) else str(raw_err)
            elif isinstance(data.get("content"), list):
                for item in data["content"]:
                    if isinstance(item, dict) and item.get("text"):
                        txt = str(item["text"]).strip()
                        try:
                            parsed = json.loads(txt)
                            if isinstance(parsed, dict) and (parsed.get("message") or parsed.get("error")):
                                msg = str(parsed.get("message") or parsed.get("error"))
                            else:
                                msg = txt[:500]
                        except Exception:
                            msg = txt[:500]
                        break
        err_out = {
            "status": "error",
            "error": msg,
            "message": msg,
            "service": service,
            "action": action_label,
            "details": Tools._compact_mcp_payload(data, unwrap_result=False),
        }
        if isinstance(data, dict) and data.get("user_required_instructions"):
            err_out["user_required_instructions"] = data["user_required_instructions"]
        return err_out

    async def _execute_broker_rpc(
        self,
        service: str,
        user_id: str,
        chat_id: str,
        account_id: Optional[str],
        credentials: str,
        rpc: dict,
        timeout_sec: float,
    ) -> tuple[dict, str]:
        category = await self._get_category(service)
        timeout = httpx.Timeout(timeout_sec, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            if category == "mcp_native":
                b64_creds = (
                    base64.b64encode(credentials.encode("utf-8")).decode("ascii")
                    if credentials
                    else ""
                )
                headers = {
                    "x-openwebui-user-id": str(user_id),
                    "x-openwebui-chat-id": chat_id,
                    "x-echo-credentials": b64_creds,
                }
                resp = await client.post(self.mcp_native_url, json=rpc, headers=headers)
                try:
                    data = resp.json()
                except Exception:
                    data = {"status": "error", "message": resp.text or f"HTTP {resp.status_code}"}
                is_err = (
                    resp.status_code >= 400
                    or (isinstance(data, dict) and (
                        data.get("status") == "error"
                        or data.get("isError") is True
                        or "error" in data
                    ))
                )
                status_val = "error" if is_err else "success"
            else:
                payload = {
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "service": service,
                    "account_id": account_id,
                    "credentials": credentials,
                    "rpc_request": rpc,
                }
                resp = await client.post(self.proxy_url, json=payload)
                try:
                    data = resp.json()
                except Exception:
                    data = {"status": "error", "message": resp.text or f"HTTP {resp.status_code}"}
                is_err = (
                    resp.status_code >= 400
                    or (isinstance(data, dict) and (
                        data.get("status") == "error"
                        or data.get("isError") is True
                        or (
                            isinstance(data.get("structuredContent"), dict)
                            and data["structuredContent"].get("status") == "error"
                        )
                    ))
                )
                status_val = "error" if is_err else "success"
        return data, status_val

    @echo_tool
    async def list_mcp_functions(
        self,
        service: str,
        account_id: Optional[str] = None,
        query: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> Any:
        """Découvre les fonctions et schémas d'arguments exposés par un serveur MCP."""
        if not __user__:
            return "Erreur: Contexte manquant."

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🌐 Routage dynamique pour l'interrogation de '{service}'...")

        chat_id = str((__metadata__ or {}).get("chat_id") or "")
        credentials, cred_err = self._fetch_credentials(__user__["id"], service, account_id)
        if cred_err is not None:
            await events.status(f"⚠️ Accès à '{service}' : {cred_err['message'][:120]}", done=True)
            return cred_err
        rpc = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}

        try:
            data, status_val = await self._execute_broker_rpc(
                service=service,
                user_id=__user__["id"],
                chat_id=chat_id,
                account_id=account_id,
                credentials=credentials,
                rpc=rpc,
                timeout_sec=65.0,
            )
            if status_val == "error":
                err_dict = self._build_error_dict(service, "tools/list", data)
                await events.status(f"⚠️ Accès à '{service}' : {err_dict['message'][:120]}", done=True)
                return err_dict
            await events.status(f"✅ Schémas récupérés pour '{service}'.", done=True)
            return self._format_tools_catalog(data, query=query)
        except Exception as e:
            err = self._format_transport_error(service, "tools/list", e)
            await events.status(f"❌ Erreur sur '{service}' : {err['message']}", done=True)
            return err

    @echo_tool
    async def call_mcp_function(
        self,
        service: str,
        function_name: str,
        arguments: dict = None,
        account_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> Any:
        """Exécute une fonction spécifique sur un serveur MCP via le Broker."""
        if not __user__:
            return "Erreur: Contexte manquant."

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"⚙️ Exécution de la fonction '{function_name}' sur '{service}'...")

        arguments = arguments or {}
        chat_id = str((__metadata__ or {}).get("chat_id") or "")
        credentials, cred_err = self._fetch_credentials(__user__["id"], service, account_id)
        if cred_err is not None:
            await events.status(f"⚠️ Accès à '{service}' : {cred_err['message'][:120]}", done=True)
            return cred_err
        rpc = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {"name": function_name, "arguments": arguments},
            "id": 1,
        }

        try:
            data, status_val = await self._execute_broker_rpc(
                service=service,
                user_id=__user__["id"],
                chat_id=chat_id,
                account_id=account_id,
                credentials=credentials,
                rpc=rpc,
                timeout_sec=90.0,
            )
            if status_val == "error":
                err_dict = self._build_error_dict(service, function_name, data)
                await events.status(
                    f"⚠️ Erreur sur '{service}' ({function_name}) : {err_dict['message'][:120]}",
                    done=True,
                )
                return err_dict
            await events.status("✅ Exécution terminée.", done=True)
            return json.dumps(self._compact_mcp_payload(data), ensure_ascii=False)
        except Exception as e:
            err = self._format_transport_error(service, function_name, e)
            await events.status(f"❌ Erreur sur '{service}' : {err['message']}", done=True)
            return err

    @staticmethod
    async def _iter_local_file_chunks(file_path: str, chunk_size: int = 262144) -> AsyncIterator[bytes]:
        fh = await asyncio.to_thread(open, file_path, "rb")
        try:
            while True:
                chunk = await asyncio.to_thread(fh.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        finally:
            await asyncio.to_thread(fh.close)

    @echo_tool
    async def upload_file_to_drive(
        self,
        service: Literal["google_workspace", "microsoft_365"],
        file_ref: str,
        workspace: Optional[Literal["main", "plans", "generated", "sandbox", "files", "browserviews"]] = None,
        remote_folder_id: Optional[str] = None,
        remote_filename: Optional[str] = None,
        overwrite_file_id: Optional[str] = None,
        convert_to_native: bool = False,
        conflict_behavior: Literal["replace", "rename", "fail"] = "replace",
        account_id: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> Any:
        """Envoie en streaming un fichier local ECHO (Codex, /files, browserviews, registre) vers Google Drive ou OneDrive."""
        uid = str((__user__ or {}).get("id") or "").strip()
        if not __user__ or not uid or uid == "anonymous":
            msg = "Erreur: Contexte utilisateur authentifié manquant."
            return {
                "status": "error",
                "error": msg,
                "message": msg,
                "service": service,
                "action": "upload_file_to_drive",
            }

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"☁️ Préparation de l'envoi vers '{service}'...")
        chat_id = str((__metadata__ or {}).get("chat_id") or "")

        local_path = resolve_user_exportable_file_path(
            user_id=uid,
            chat_id=chat_id,
            target=file_ref,
            workspace=workspace,
        )
        if not local_path:
            msg = f"Fichier local introuvable ou non exportable : '{file_ref}'."
            await events.status(f"⚠️ {msg}", done=True)
            return {
                "status": "error",
                "error": msg,
                "message": msg,
                "service": service,
                "action": "upload_file_to_drive",
            }

        total_size = os.path.getsize(local_path)
        target_name = (remote_filename or "").strip() or extract_clean_filename(local_path) or os.path.basename(local_path)
        guessed_mime = mimetypes.guess_type(target_name)[0] or "application/octet-stream"

        credentials, cred_err = self._fetch_credentials(uid, service, account_id)
        if cred_err is not None:
            await events.status(f"⚠️ Accès à '{service}' : {cred_err['message'][:120]}", done=True)
            return cred_err

        meta_obj = {
            "service": service,
            "user_id": uid,
            "chat_id": chat_id,
            "filename": target_name,
            "total_size": total_size,
            "mime_type": guessed_mime,
            "folder_id": remote_folder_id,
            "overwrite_file_id": overwrite_file_id,
            "convert_to_native": bool(convert_to_native),
            "conflict_behavior": conflict_behavior,
            "account_id": account_id,
        }
        b64_meta = base64.b64encode(json.dumps(meta_obj, ensure_ascii=False).encode("utf-8")).decode("ascii")
        b64_creds = base64.b64encode(credentials.encode("utf-8")).decode("ascii") if credentials else ""

        headers = {
            "x-openwebui-user-id": uid,
            "x-openwebui-chat-id": chat_id,
            "x-echo-upload-meta": b64_meta,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(total_size),
        }
        if b64_creds:
            headers["x-echo-credentials"] = b64_creds

        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
                resp = await client.post(
                    self.upload_drive_url,
                    content=self._iter_local_file_chunks(local_path),
                    headers=headers,
                )
            try:
                data = resp.json()
            except Exception:
                data = {"status": "error", "message": resp.text or f"HTTP {resp.status_code}"}

            if resp.status_code >= 400 or (isinstance(data, dict) and data.get("status") == "error"):
                err_dict = self._build_error_dict(service, "upload_file_to_drive", data)
                await events.status(
                    f"⚠️ Erreur sur '{service}' (upload_file_to_drive) : {err_dict['message'][:120]}",
                    done=True,
                )
                return err_dict

            await events.status(f"✅ Fichier '{target_name}' envoyé sur '{service}'.", done=True)
            return json.dumps(data, ensure_ascii=False)
        except Exception as e:
            err = self._format_transport_error(service, "upload_file_to_drive", e)
            await events.status(f"❌ Erreur sur '{service}' : {err['message']}", done=True)
            return err
