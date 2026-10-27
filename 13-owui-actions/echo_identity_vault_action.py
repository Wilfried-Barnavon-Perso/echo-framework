"""
title: ECHO Identity Vault
author: ECHO
version: 5.231.21.3
description: Coffre-fort universel pour l'authentification des agents (MCP et N8N).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xMiAyMnM4LTQgOC0xMFY1bC04LTMtOCAzdjdjMCA2IDggMTAgOCAxMHoiLz48L3N2Zz4=
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.21.3: Délégation CRUD chiffré à EchoStateManager, correction de l'auto-provisionnement remote_mcp et échappement JSON sûr pour echoVaultUpdate.
# 5.228.12.1: Résilience RPC post-standby et anti-timeout 60s via EchoEvents (resolve_live_session, wait_hud_action, support ping et reconnect_only).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 2.1: Suppression totale de la notion d'accès RO/RW (access_level).
# 1.4: Suppression de la notion d'Alias (multicompte) pour simplifier en accès unique.

import sys
import uuid
import asyncio
import orjson as json
from pydantic import BaseModel, Field
from typing import Any, Optional, List, Dict
import httpx

sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents  # noqa: E402
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_ui import EchoUI  # noqa: E402

_ACTIVE_VAULT_LOOPS: dict[str, str] = {}


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=40, description="Priorité d'affichage dans le menu Actions.")

    def __init__(self):
        self.valves = self.Valves()

    def _init_vault(self, user_id: str) -> EchoStateManager:
        return EchoStateManager(user_id=user_id)

    def _get_accounts(self, state: EchoStateManager) -> List[Dict]:
        return state.list_vault_accounts()

    def _save_account(self, state: EchoStateManager, service: str, account_id: str, credentials: str):
        state.save_vault_credential(service, account_id, credentials)

    def _delete_account(self, state: EchoStateManager, service: str, account_id: str):
        state.delete_vault_credential(service, account_id)

    async def action(self, body: dict, __user__: Optional[dict] = None, __event_emitter__: Any = None, __event_call__: Any = None, __metadata__: Optional[dict] = None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)

        if not __event_call__:
            return None

        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]
        chat_id = body.get("chat_id") or (__metadata__ or {}).get("chat_id") or ""
        mid = body.get("id") or (__metadata__ or {}).get("message_id") or ""
        model_id = body.get("model") or (__metadata__ or {}).get("model_id") or "echo_orchestrator"
        reconnect_only = bool(body.get("reconnect_only"))

        if reconnect_only or not body.get("session_id"):
            resolved = await EchoEvents.resolve_live_session(
                user_id, chat_id, mid, hud_id="echo-vault-identity"
            )
            if resolved:
                events, _ = resolved
            elif reconnect_only:
                return {"status": "no_active_hud"}

        state = self._init_vault(user_id)

        if not reconnect_only:
            # 1. Récupération des schémas MCP (dynamique)
            schemas = {}
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get("http://echo-mcp-broker:8000/schemas", timeout=2.0)
                    if resp.status_code == 200:
                        schemas = resp.json()
            except Exception:
                pass

            # Injection du schéma générique N8N
            schemas["n8n_workflows"] = {
                "category": "orchestration",
                "name": "N8N Orchestration",
                "fields": [
                    {"id": "credentials", "label": "N8N Secret", "type": "text", "help": "Valeur du secret."}
                ]
            }

            if not schemas:
                schemas = {
                    "default": {
                        "name": "Service Défaut",
                        "fields": [{"id": "credentials", "label": "Credentials", "type": "text", "help": "Saisissez vos identifiants."}]
                    }
                }
            schemas_json = json.dumps(schemas).decode("utf-8")

            # 2. Récupération des comptes et génération du HUD JS
            accounts = self._get_accounts(state)
            accounts_json = json.dumps(accounts).decode("utf-8")

            hud_js = EchoUI._generate_identity_vault_js(accounts_json, schemas_json, chat_id, mid, model_id)
            await events.call_execute(hud_js)
            await events.status("🔐 ECHO Identity Vault actif.", True)

        loop_id = uuid.uuid4().hex
        _ACTIVE_VAULT_LOOPS[user_id] = loop_id

        # 2. Boucle d'événements asynchrones résiliente
        while _ACTIVE_VAULT_LOOPS.get(user_id) == loop_id:
            response = await events.wait_hud_action("echoVaultResolve", "_echoVaultOnReady")

            if _ACTIVE_VAULT_LOOPS.get(user_id) != loop_id:
                break

            if not response or not isinstance(response, dict):
                if _ACTIVE_VAULT_LOOPS.get(user_id) == loop_id:
                    await asyncio.sleep(1.5)
                    resolved = await EchoEvents.resolve_live_session(
                        user_id, chat_id, mid, hud_id="echo-vault-identity", retries=3, delay=2.0
                    )
                    if resolved and _ACTIVE_VAULT_LOOPS.get(user_id) == loop_id:
                        events, _ = resolved
                        continue
                break

            action_type = response.get("action")

            if action_type == "close":
                await events.toast("Fermeture du Vault.", "info")
                break
            elif action_type == "ping":
                continue

            elif action_type == "add_account":
                service = response.get("service")
                account_id = response.get("account_id")

                # Encodage JSON des champs dynamiques générés par le Schéma
                dynamic_data = {}
                if "credentials" in response:
                    credentials = response["credentials"]
                    try:
                        parsed = json.loads(credentials)
                        if isinstance(parsed, dict):
                            dynamic_data = parsed
                    except Exception:
                        pass
                else:
                    reserved = {"action", "service", "account_id"}
                    dynamic_data = {k: v for k, v in response.items() if k not in reserved}
                    credentials = json.dumps(dynamic_data).decode("utf-8") if dynamic_data else ""

                # Auto-provisionnement Sémantique si la description est vide
                if service == "remote_mcp" and isinstance(dynamic_data, dict) and not dynamic_data.get("description"):
                    try:
                        url = dynamic_data.get("url", "")
                        async with httpx.AsyncClient() as client:
                            rpc = {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
                            resp = await client.post(url, json=rpc, timeout=5.0)
                            tools_schema = resp.text

                        from echo_gemini_client import EchoGeminiClient
                        prompt = (
                            "Objectif : Rédiger une description sémantique d'UNE SEULE PHRASE (maximum 15 mots) "
                            "définissant le cas d'usage d'un serveur MCP distant, à partir de ses capacités brutes.\n"
                            f"Capacités extraites : {tools_schema}\n"
                            "Contrainte : Style impersonnel, direct et technique (ex: 'Fournit la météo et les prévisions mondiales.'). "
                            "Aucun texte additionnel n'est autorisé."
                        )
                        dynamic_data["description"] = await EchoGeminiClient.call_distillation(
                            prompt=prompt, __user__=__user__, __metadata__={}, is_json=False
                        )
                    except Exception:
                        dynamic_data["description"] = "Serveur MCP distant (Auto-provisionnement échoué)."
                    credentials = json.dumps(dynamic_data).decode("utf-8")

                if service and account_id and credentials:
                    self._save_account(state, service, account_id, credentials)
                    await events.toast(f"✅ Compte {account_id} enregistré pour {service}.", "success")

                # Refresh list
                accounts = self._get_accounts(state)
                accounts_json = json.dumps(accounts).decode("utf-8")
                accounts_arg = json.dumps(accounts_json).decode("utf-8")
                update_js = f"if(window.echoVaultUpdate) window.echoVaultUpdate({accounts_arg});"
                await events.call_execute(update_js)

            elif action_type == "delete_account":
                service = response.get("service")
                account_id = response.get("account_id") or "default"

                if service:
                    self._delete_account(state, service, account_id)
                    await events.toast(f"🗑️ Compte supprimé pour {service}.", "success")

                # Refresh list
                accounts = self._get_accounts(state)
                accounts_json = json.dumps(accounts).decode("utf-8")
                accounts_arg = json.dumps(accounts_json).decode("utf-8")
                update_js = f"if(window.echoVaultUpdate) window.echoVaultUpdate({accounts_arg});"
                await events.call_execute(update_js)

            else:
                break

        return {"status": "success"}

