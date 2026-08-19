"""
title: ECHO Identity Vault
author: ECHO
version: 2.0
description: Coffre-fort universel pour l'authentification des agents (MCP et N8N).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0xMiAyMnM4LTQgOC0xMFY1bC04LTMtOCAzdjdjMCA2IDggMTAgOCAxMHoiLz48L3N2Zz4=
"""
# Historique des versions :
# 1.4: Suppression de la notion d'Alias (multicompte) pour simplifier en accès unique.
# 1.3: Remplacement de la saisie manuelle des services par une découverte dynamique (API /schemas du MCP Broker).
# 1.2: Mise à jour de la priorité d'affichage à 40.

import sys
import orjson as json
from pydantic import BaseModel, Field
from typing import Any, Optional, List, Dict
import httpx

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoStateManager
from echo_ui import EchoUI

class Action:
    class Valves(BaseModel):
        priority: int = Field(default=40, description="Priorité d'affichage dans le menu Actions.")

    def __init__(self):
        self.valves = self.Valves()

    def _init_vault(self, user_id: str) -> EchoStateManager:
        state = EchoStateManager(user_id=user_id)
        with state._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS identity_vault (
                    user_id TEXT, 
                    service TEXT, 
                    account_id TEXT, 
                    credentials TEXT, 
                    access_level TEXT, 
                    PRIMARY KEY (user_id, service, account_id)
                )
            """)
            conn.commit()
        return state

    def _get_accounts(self, state: EchoStateManager) -> List[Dict]:
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT service, account_id, access_level FROM identity_vault WHERE user_id = ?", (state.user_id,))
            rows = cursor.fetchall()
            return [{"service": r[0], "account_id": r[1], "access_level": r[2]} for r in rows]

    def _save_account(self, state: EchoStateManager, service: str, account_id: str, credentials: str, access_level: str):
        with state._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO identity_vault (user_id, service, account_id, credentials, access_level) VALUES (?, ?, ?, ?, ?)",
                (state.user_id, service, account_id, credentials, access_level)
            )
            conn.commit()

    def _delete_account(self, state: EchoStateManager, service: str, account_id: str):
        with state._get_connection() as conn:
            conn.execute(
                "DELETE FROM identity_vault WHERE user_id = ? AND service = ? AND account_id = ?",
                (state.user_id, service, account_id)
            )
            conn.commit()

    async def action(self, body: dict, __user__: Optional[dict] = None, __event_emitter__: Any = None, __event_call__: Any = None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)
        
        if not __event_call__:
            return None

        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]
        state = self._init_vault(user_id)

        # 1. Récupération des schémas MCP (dynamique)
        schemas = {}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://echo-mcp-broker:8000/schemas", timeout=2.0)
                if resp.status_code == 200:
                    schemas = resp.json()
        except Exception as e:
            pass
        
        # Injection du schéma générique N8N
        schemas["n8n_workflows"] = {
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
        
        hud_js = EchoUI._generate_identity_vault_js(accounts_json, schemas_json)
        await __event_call__({"type": "execute", "data": {"code": hud_js}})
        await events.status("🔐 ECHO Identity Vault actif.", True)

        # 2. Boucle d'événements asynchrones
        while True:
            wait_code = "return new Promise(r => window.echoVaultResolve = r);"
            response = await __event_call__({"type": "execute", "data": {"code": wait_code}})

            if not response or not isinstance(response, dict):
                break

            action_type = response.get("action")

            if action_type == "close":
                await events.toast("Fermeture du Vault.", "info")
                break

            elif action_type == "add_account":
                service = response.get("service")
                account_id = response.get("account_id")
                credentials = response.get("credentials")
                access_level = response.get("access_level")
                
                if service and account_id and credentials and access_level:
                    self._save_account(state, service, account_id, credentials, access_level)
                    await events.toast(f"✅ Compte {account_id} enregistré pour {service}.", "success")
                
                # Refresh list
                accounts = self._get_accounts(state)
                accounts_json = json.dumps(accounts).decode("utf-8")
                update_js = f"if(window.echoVaultUpdate) window.echoVaultUpdate('{accounts_json}');"
                await __event_call__({"type": "execute", "data": {"code": update_js}})

            elif action_type == "delete_account":
                service = response.get("service")
                account_id = response.get("account_id") or "default"
                
                if service:
                    self._delete_account(state, service, account_id)
                    await events.toast(f"🗑️ Compte supprimé pour {service}.", "success")
                
                # Refresh list
                accounts = self._get_accounts(state)
                accounts_json = json.dumps(accounts).decode("utf-8")
                update_js = f"if(window.echoVaultUpdate) window.echoVaultUpdate('{accounts_json}');"
                await __event_call__({"type": "execute", "data": {"code": update_js}})

            else:
                break

        return {"status": "success"}
