"""
title: ECHO Universal MCP Tool
author: ECHO
version: 2.0
description: Outil universel permettant au Modèle d'interroger et d'exécuter des requêtes sur l'infrastructure MCP (serveurs natifs, résidents ou distants) via un routage dynamique.
"""
import sys
import json
import httpx
from typing import Any
from pydantic import BaseModel

sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.schemas_url = "http://echo-mcp-broker:8000/schemas"
        self.mcp_native_url = "http://echo-mcp-broker:8000/mcp"
        self.proxy_url = "http://echo-mcp-broker:8000/proxy_mcp"

    async def _get_category(self, server_alias: str) -> str:
        """Récupère dynamiquement la taxonomie d'un serveur pour déterminer son routage."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(self.schemas_url, timeout=2.0)
                schemas = resp.json()
                return schemas.get(server_alias, {}).get("category", "uncategorized")
        except Exception:
            return "uncategorized"

    async def list_mcp_tools(self, server_alias: str, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Permet au Modèle de découvrir dynamiquement les capacités et les schémas d'outils exposés par un serveur MCP.
        L'argument 'server_alias' doit strictement correspondre à une clé valide issue du registre de l'Identity Vault.
        """
        if not __user__:
            return wrap_tool_output("Erreur: Contexte manquant.", {"status": "error"}, user_id="", chat_id="", metadata={})
        
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Routage dynamique pour l'interrogation de '{server_alias}'...")

        category = await self._get_category(server_alias)
        
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                if category == "mcp_native":
                    rpc = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                    resp = await client.post(self.mcp_native_url, json=rpc)
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    payload = {
                        "user_id": __user__["id"],
                        "server_alias": server_alias,
                        "rpc_request": {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                    }
                    resp = await client.post(self.proxy_url, json=payload)
                    data = resp.json()
                    
                await events.status(f"Schémas récupérés pour '{server_alias}'.", done=True)
                return wrap_tool_output(json.dumps(data), {"status": "success"}, __user__["id"], __metadata__.get("chat_id"), __metadata__)
        except Exception as e:
            return wrap_tool_output(json.dumps({"status": "error", "message": str(e)}), {"status": "error"}, __user__["id"], __metadata__.get("chat_id"), __metadata__)

    async def call_mcp_tool(self, server_alias: str, tool_name: str, arguments: dict = None, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Permet au Modèle d'exécuter une fonction précise sur un serveur MCP ciblé via le Broker.
        L'argument 'server_alias' et 'tool_name' sont strictement obligatoires.
        """
        if not __user__:
            return wrap_tool_output("Erreur: Contexte manquant.", {"status": "error"}, user_id="", chat_id="", metadata={})
            
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Appel universel de '{tool_name}' sur '{server_alias}'...")

        arguments = arguments or {}
        category = await self._get_category(server_alias)
        
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                if category == "mcp_native":
                    rpc = {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}, "id": 1}
                    resp = await client.post(self.mcp_native_url, json=rpc)
                    resp.raise_for_status()
                    data = resp.json()
                else:
                    payload = {
                        "user_id": __user__["id"],
                        "server_alias": server_alias,
                        "rpc_request": {"jsonrpc": "2.0", "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}, "id": 1}
                    }
                    resp = await client.post(self.proxy_url, json=payload)
                    data = resp.json()
                    
                await events.status(f"Exécution terminée.", done=True)
                return wrap_tool_output(json.dumps(data), {"status": "success"}, __user__["id"], __metadata__.get("chat_id"), __metadata__)
        except Exception as e:
            return wrap_tool_output(json.dumps({"status": "error", "message": str(e)}), {"status": "error"}, __user__["id"], __metadata__.get("chat_id"), __metadata__)
