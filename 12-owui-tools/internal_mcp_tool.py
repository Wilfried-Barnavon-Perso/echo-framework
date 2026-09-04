"""
title: ECHO Internal MCP Tool
author: ECHO
version: 1.1
description: Outil natif d'interrogation et d'exécution sur le serveur MCP interne d'ECHO (Broker local).
"""
from pydantic import BaseModel
from typing import Any
import httpx
import json
import sys

sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents

class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self._CACHE = {}
        # L'URL est hardcodée vers le broker interne FastMCP d'ECHO (endpoint /mcp par défaut)
        self.internal_url = "http://echo-mcp-broker:8000/mcp"

    async def list_internal_mcp_tools(self, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Interroge le serveur MCP interne d'ECHO pour obtenir la liste de ses capacités et schémas d'outils (Corporate, Academic, Jobs, etc).
        """
        if not __user__: return wrap_tool_output(text="Erreur : Contexte manquant.", status={"status": "error"})
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Interrogation du broker MCP interne...")
            
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                rpc = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                resp = await client.post(self.internal_url, json=rpc)
                resp.raise_for_status()
                result = {"status": "success", "tools": resp.json()}
                await events.status("Schémas internes récupérés.", done=True)
                return wrap_tool_output(text=json.dumps(result), status={"status": "success"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=json.dumps({"status": "error", "message": str(e)}), status={"status": "error"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def call_internal_mcp_tool(self, tool_name: str, arguments: dict = None, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Exécute une fonction précise sur le serveur MCP interne d'ECHO.
        """
        if not __user__: return wrap_tool_output(text="Erreur : Contexte manquant.", status={"status": "error"})
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Appel interne de '{tool_name}'...")
            
        arguments = arguments or {}
        cache_key = f"internal_{tool_name}_{json.dumps(arguments, sort_keys=True)}"
        if cache_key in self._CACHE:
            await events.status(f"'{tool_name}' récupéré depuis le cache.", done=True)
            return wrap_tool_output(text=json.dumps({"status": "success", "source": "cache", "data": self._CACHE[cache_key]}), status={"status": "success"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments}
                }
                resp = await client.post(self.internal_url, json=payload)
                resp.raise_for_status()
                result = resp.json()

                self._CACHE[cache_key] = result
                await events.status(f"Exécution de '{tool_name}' terminée.", done=True)
                return wrap_tool_output(text=json.dumps({"status": "success", "source": "internal", "data": result}), status={"status": "success"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=json.dumps({"status": "error", "message": str(e)}), status={"status": "error"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
