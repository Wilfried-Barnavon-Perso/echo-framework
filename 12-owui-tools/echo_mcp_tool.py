"""
title: ECHO Universal MCP Tool
author: ECHO
version: 2.5
description: Outil universel permettant au Modèle d'interroger et d'exécuter des requêtes sur l'infrastructure MCP (serveurs natifs, résidents ou distants) via un routage dynamique.
"""
import sys
import json
import httpx
from typing import Any
from pydantic import BaseModel

sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output  # noqa: E402
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

    async def list_mcp_functions(
        self,
        service: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Permet au Modèle de découvrir dynamiquement les capacités et les schémas d'outils exposés par un serveur MCP.
        L'argument 'service' doit strictement correspondre à une clé valide issue du registre de l'Identity Vault.
        """
        if not __user__:
            return wrap_tool_output("Erreur: Contexte manquant.", {"status": "error"}, user_id="", chat_id="", metadata={})

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🌐 Routage dynamique pour l'interrogation de '{service}'...")

        category = await self._get_category(service)

        try:
            from echo_state_manager import EchoStateManager
            state = EchoStateManager(user_id=__user__["id"])
            with state._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT credentials FROM identity_vault WHERE user_id = ? AND service = ? LIMIT 1",
                    (__user__["id"], service)
                )
                row = cursor.fetchone()
                credentials = row[0] if row else ""
        except Exception:
            credentials = ""

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if category == "mcp_native":
                    rpc = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                    resp = await client.post(self.mcp_native_url, json=rpc)
                    resp.raise_for_status()
                    data = resp.json()
                    status_val = "success"
                else:
                    payload = {
                        "user_id": __user__["id"],
                        "service": service,
                        "credentials": credentials,
                        "rpc_request": {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                    }
                    resp = await client.post(self.proxy_url, json=payload)
                    data = resp.json()
                    is_err = data.get("status") == "error" or resp.status_code >= 400
                    status_val = "error" if is_err else "success"

                label = f"✅ Schémas récupérés pour '{service}'." if status_val == "success" else f"⚠️ Accès à '{service}' : configuration requise."
                await events.status(label, done=True)
                return wrap_tool_output(
                    json.dumps(data),
                    status={"status": status_val},
                    user_id=__user__["id"],
                    chat_id=__metadata__.get("chat_id"),
                    metadata=__metadata__
                )
        except Exception as e:
            return wrap_tool_output(
                json.dumps({"status": "error", "message": str(e)}),
                status={"status": "error"},
                user_id=__user__["id"],
                chat_id=__metadata__.get("chat_id"),
                metadata=__metadata__
            )

    async def call_mcp_function(
        self,
        service: str,
        function_name: str,
        arguments: dict = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Permet au Modèle d'exécuter une fonction précise sur un serveur MCP ciblé via le Broker.
        L'argument 'service' et 'function_name' sont strictement obligatoires.
        """
        if not __user__:
            return wrap_tool_output("Erreur: Contexte manquant.", {"status": "error"}, user_id="", chat_id="", metadata={})

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"⚙️ Exécution de la fonction '{function_name}' sur '{service}'...")

        arguments = arguments or {}
        category = await self._get_category(service)

        try:
            from echo_state_manager import EchoStateManager
            state = EchoStateManager(user_id=__user__["id"])
            with state._get_connection() as conn:
                cursor = conn.execute(
                    "SELECT credentials FROM identity_vault WHERE user_id = ? AND service = ? LIMIT 1",
                    (__user__["id"], service)
                )
                row = cursor.fetchone()
                credentials = row[0] if row else ""
        except Exception:
            credentials = ""

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if category == "mcp_native":
                    rpc = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": function_name, "arguments": arguments}, "id": 1}
                    resp = await client.post(self.mcp_native_url, json=rpc)
                    resp.raise_for_status()
                    data = resp.json()
                    status_val = "success"
                else:
                    payload = {
                        "user_id": __user__["id"],
                        "service": service,
                        "credentials": credentials,
                        "rpc_request": {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": function_name, "arguments": arguments}, "id": 1}
                    }
                    resp = await client.post(self.proxy_url, json=payload)
                    data = resp.json()
                    is_err = data.get("status") == "error" or resp.status_code >= 400
                    status_val = "error" if is_err else "success"

                label = "✅ Exécution terminée." if status_val == "success" else f"⚠️ Erreur lors de l'exécution sur '{service}'."
                await events.status(label, done=True)
                return wrap_tool_output(
                    json.dumps(data),
                    status={"status": status_val},
                    user_id=__user__["id"],
                    chat_id=__metadata__.get("chat_id"),
                    metadata=__metadata__
                )
        except Exception as e:
            return wrap_tool_output(
                json.dumps({"status": "error", "message": str(e)}),
                status={"status": "error"},
                user_id=__user__["id"],
                chat_id=__metadata__.get("chat_id"),
                metadata=__metadata__
            )
