"""
title: ECHO Universal MCP Tool
author: ECHO
version: 5.239.11.1
description: Outil universel d'interrogation et d'exécution sur l'infrastructure MCP via routage dynamique.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.11.1: Retour dict normalisé (status="error", error=...) sur erreur MCP/HTTP pour ❌ Open WebUI et statut LLM.
# 5.239.10.1: Factorisation RPC/erreurs, timeouts 90s/65s alignés Broker et clôture events.status sur erreur.
# 5.233.0.1: Décoration @echo_tool sur les 2 méthodes publiques et simplification des retours wrap_tool_output.
# 5.231.37.1: Propagation de chat_id (en-tête x-openwebui-chat-id et payload proxy_mcp) pour le Download Broker.
# 5.231.21.1: Délégation de _fetch_credentials au CRUD chiffré AES-256-GCM de EchoStateManager (get_vault_credential).
import sys
import json
import base64
import httpx
from typing import Any, Optional
from pydantic import BaseModel

sys.path.append("/app/backend/echo_libs")
from echo_core import echo_tool  # noqa: E402
from echo_events import EchoEvents  # noqa: E402


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.schemas_url = "http://echo-mcp-broker:8000/schemas"
        self.mcp_native_url = "http://echo-mcp-broker:8000/mcp"
        self.proxy_url = "http://echo-mcp-broker:8000/proxy_mcp"

    async def _get_category(self, service: str) -> str:
        """Récupère dynamiquement la taxonomie d'un serveur pour déterminer son routage."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.schemas_url, timeout=2.0)
                schemas = resp.json()
                return schemas.get(service, {}).get("category", "uncategorized")
        except Exception:
            return "uncategorized"

    def _fetch_credentials(self, user_id: str, service: str, account_id: Optional[str] = None) -> str:
        """Extrait les credentials de l'Identity Vault pour l'utilisateur et le compte spécifiés."""
        try:
            from echo_state_manager import EchoStateManager
            return EchoStateManager(user_id=user_id).get_vault_credential(service, account_id)
        except Exception:
            return ""

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
        return {
            "status": "error",
            "error": msg,
            "message": msg,
            "service": service,
            "action": action_label,
            "details": data,
        }

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
        credentials = self._fetch_credentials(__user__["id"], service, account_id)
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
            return json.dumps(data)
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
        credentials = self._fetch_credentials(__user__["id"], service, account_id)
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
            return json.dumps(data)
        except Exception as e:
            err = self._format_transport_error(service, function_name, e)
            await events.status(f"❌ Erreur sur '{service}' : {err['message']}", done=True)
            return err
