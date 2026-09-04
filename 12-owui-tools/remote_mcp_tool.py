"""
title: ECHO Remote MCP Tool
author: ECHO
version: 1.9
description: Outil natif permettant d'interroger et d'exécuter des requêtes sur un serveur MCP (distant SSE ou local Stdio) enregistré dans l'Identity Vault via l'ECHO MCP Broker.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.8: Amélioration : Rendu impersonnel du prompt d'Action Requise pour l'authentification et incitation à utiliser ask_user_input.
# 1.7: (Non documenté précédemment)
# 1.3: Faille critique (Data Leak/Stale Data) résolue : suppression du cache en mémoire pour les appels d'outils.
# 1.2: Ajout du routage réseau et du relais HTTPX via l'ECHO MCP Broker.
from echo_state_manager import EchoStateManager
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from pydantic import BaseModel
from typing import Any, Optional
import httpx
import json
import sys
import asyncio

sys.path.append("/app/backend/echo_libs")


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    async def _get_service_config(self, user_id: str, server_alias: str) -> Optional[dict]:
        def _fetch():
            state = EchoStateManager(user_id=user_id)
            with state._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT service, credentials FROM identity_vault WHERE user_id = ? AND account_id = ?",
                    (user_id, server_alias)
                )
                row = cursor.fetchone()
                if row:
                    try:
                        service_type = row[0]
                        data = json.loads(row[1])
                        data["type"] = service_type
                        return data
                    except Exception:
                        pass
            return None
        return await asyncio.to_thread(_fetch)

    async def list_remote_mcp_tools(self, server_alias: str, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Interroge un serveur MCP (enregistré dans le Vault) pour obtenir la liste de ses capacités et schémas d'outils.
        """
        if not __user__:
            return wrap_tool_output(text="Erreur : Contexte manquant.", status={"status": "error"})
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Connexion MCP '{server_alias}' via Broker...")

        config = await self._get_service_config(__user__["id"], server_alias)
        if not config:
            return wrap_tool_output(text=f"Erreur : Serveur '{server_alias}' introuvable dans le Vault.", status={"status": "error"})

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                payload = {
                    "service_config": config,
                    "method": "tools/list",
                    "params": {}
                }
                resp = await client.post("http://echo-mcp-broker:8000/proxy_mcp", json=payload)
                resp.raise_for_status()
                result = {"status": "success", "tools": resp.json()}
                await events.status("Schémas récupérés.", done=True)
                return wrap_tool_output(text=json.dumps(result), status={"status": "success"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            error_dict = {
                "status": "error",
                "source": "broker_network_error",
                "message": str(e)
            }
            if hasattr(e, "response"):
                try:
                    err_data = e.response.json()
                    if "message" in err_data:
                        error_dict["message"] = err_data["message"]
                    if "source" in err_data:
                        error_dict["source"] = err_data["source"]
                    if "http_code" in err_data:
                        error_dict["http_code"] = err_data["http_code"]
                        if error_dict["http_code"] in (401, 403):
                            error_dict["suggestion"] = (
                                "Le serveur distant exige une authentification ou les droits sont insuffisants. "
                                "Le Modèle doit résoudre ce problème de manière autonome : "
                                "1. Identifier l'URL officielle permettant de générer les variables d'authentification requises. "
                                "2. Utiliser l'outil `ask_user_input` pour fournir cette URL à l'Utilisateur et lui demander interactivement de saisir les informations. "
                                "3. Utiliser l'outil `manage_identity` (service='mcp') pour enregistrer ou mettre à jour la configuration dans l'Identity Vault."
                            )
                except Exception:
                    pass
            return wrap_tool_output(text=json.dumps(error_dict), status={"status": "error"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def call_remote_mcp_tool(self, server_alias: str, tool_name: str, arguments: dict = None, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None, __event_call__: Any = None) -> dict:
        """
        Exécute une fonction précise sur un serveur MCP distant ou local via le Broker.
        """
        if not __user__:
            return wrap_tool_output(text="Erreur : Contexte manquant.", status={"status": "error"})
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"Exécution '{tool_name}' sur '{server_alias}'...")

        config = await self._get_service_config(__user__["id"], server_alias)
        if not config:
            return wrap_tool_output(text=f"Erreur : Serveur '{server_alias}' introuvable.", status={"status": "error"})

        arguments = arguments or {}

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                payload = {
                    "service_config": config,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments}
                }
                resp = await client.post("http://echo-mcp-broker:8000/proxy_mcp", json=payload)
                resp.raise_for_status()
                result = resp.json()

                await events.status(f"Exécution '{tool_name}' terminée.", done=True)
                return wrap_tool_output(text=json.dumps({"status": "success", "source": "broker", "data": result}), status={"status": "success"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            error_dict = {
                "status": "error",
                "source": "broker_network_error",
                "message": str(e)
            }
            if hasattr(e, "response"):
                try:
                    err_data = e.response.json()
                    if "message" in err_data:
                        error_dict["message"] = err_data["message"]
                    if "source" in err_data:
                        error_dict["source"] = err_data["source"]
                    if "http_code" in err_data:
                        error_dict["http_code"] = err_data["http_code"]
                        if error_dict["http_code"] in (401, 403):
                            error_dict["suggestion"] = (
                                "Le serveur distant exige une authentification ou les droits sont insuffisants. "
                                "Le Modèle doit résoudre ce problème de manière autonome : "
                                "1. Identifier l'URL officielle permettant de générer les variables d'authentification requises. "
                                "2. Utiliser l'outil `ask_user_input` pour fournir cette URL à l'Utilisateur et lui demander interactivement de saisir les informations. "
                                "3. Utiliser l'outil `manage_identity` (service='mcp') pour enregistrer ou mettre à jour la configuration dans l'Identity Vault."
                            )
                except Exception:
                    pass
            return wrap_tool_output(text=json.dumps(error_dict), status={"status": "error"}, user_id=__user__["id"], chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
