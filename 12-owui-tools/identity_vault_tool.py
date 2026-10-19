"""
title: ECHO Identity Vault Tool
author: ECHO
version: 5.226.18.0
description: Outil permettant à l'Agent de gérer le Identity Vault (ajout/suppression de serveurs distants ou N8N).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.9: Remplacement de auth_failure_instructions par user_required_instructions et correction Flake8.
# 1.8: Filtrage croisé de configured_services et forçage sémantique du paramètre 'all'.
# 1.7: Refonte JSON de list_identities_services.
# 1.6: Remplacement de la vérification __event_call__ par ECHO_SUBAGENT_CONTEXT pour l'interdiction Headless.
import sys
import json
from typing import Any, Literal
from pydantic import BaseModel

sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager  # noqa: E402
from echo_ui import EchoUI  # noqa: E402
from echo_events import EchoEvents  # noqa: E402


class Tools:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()

    def _init_vault(self, user_id: str) -> EchoStateManager:
        state = EchoStateManager(user_id=user_id)
        with state._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS identity_vault (
                    user_id TEXT, service TEXT, account_id TEXT,
                    credentials TEXT,
                    PRIMARY KEY (user_id, service, account_id)
                )
            """)
            conn.commit()
        return state

    async def list_identities_services(
        self,
        category_filter: Literal["all", "mcp_native", "mcp_resident", "mcp_ephemeral", "mcp_remote", "orchestration", "uncategorized"] = "all",
        __user__: dict = None
    ) -> str:
        """
        Interroge l'API pour extraire le catalogue des services de l'Identity Vault.

        Args:
            category_filter: Filtre sémantique. Sélectionner 'all' pour une découverte exhaustive.
        """
        if not __user__:
            return "Erreur: Auth requise."

        import httpx
        schemas = {}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://echo-mcp-broker:8000/schemas", timeout=2.0)
                if resp.status_code == 200:
                    schemas = resp.json()
        except Exception:
            pass

        available_services_list = []
        for svc_id, data in schemas.items():
            cat = data.get("category", "uncategorized")
            if category_filter == "all" or cat == category_filter:
                svc_data = {
                    "service": svc_id,
                    "category": cat,
                    "name": data.get("name", svc_id)
                }
                if "user_required_instructions" in data:
                    svc_data["user_required_instructions"] = data["user_required_instructions"]
                available_services_list.append(svc_data)

        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT DISTINCT service FROM identity_vault WHERE user_id = ?", (__user__["id"],))
            rows = cursor.fetchall()

        configured_services = []
        if rows:
            for r in rows:
                svc = r[0]
                if category_filter == "all":
                    configured_services.append(svc)
                else:
                    cat = schemas.get(svc, {}).get("category", "uncategorized")
                    if cat == category_filter:
                        configured_services.append(svc)

        result_json = {
            "categories_services": {
                "mcp_native": "Services natifs vers des serveurs MCP distants",
                "mcp_resident": "Services résidents de serveurs MCP préinstallés",
                "mcp_ephemeral": "Services de serveurs MCP installés à la volée (npx/uvx)",
                "mcp_remote": "Services MCP via connexions distantes (HTTP/SSE)",
                "orchestration": "Services et leurs identifiants orchestrés par N8N",
                "uncategorized": "Autres services et leurs identifiants, non catégorisés"
            },
            "services_disponibles_via_mcp_broker": available_services_list,
            "services_avec_identites_configurees": configured_services
        }

        return json.dumps(result_json, ensure_ascii=False, indent=2)

    async def list_identities(self, service: str, __user__: dict = None) -> str:
        """
        Interroge l'Identity Vault pour restituer les comptes tiers associés à un service spécifique.
        L'argument 'service' est strictement requis. Invoquer 'list_identities_services' en cas d'ambiguïté.
        """
        if not __user__:
            return "Erreur: Utilisateur inconnu."
        if not service:
            return "Erreur: L'argument 'service' est strictement requis. Invoquez 'list_identities_services' en cas de doute."
        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute(
                "SELECT service, account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ?",
                (__user__["id"], service)
            )
            rows = cursor.fetchall()

        result = []
        for r in rows:
            account = r[1]
            creds = r[2]
            try:
                data = json.loads(creds)
                desc = data.get("description", creds)
            except Exception:
                desc = creds
            result.append(f"[Compte: {account} : {desc}]")

        if not result:
            return f"Aucun compte trouvé pour '{service}'."
        return f"[{service}] " + ", ".join(result)

    async def manage_identity(
        self,
        action: str,
        service: str,
        account_id: str,
        credentials_json: str = "",
        __user__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Ajoute, modifie ou purge une identité applicative ou un serveur au sein de l'Identity Vault. Action autorisée : 'add', 'update' ou 'delete'.

        RÈGLES POUR 'credentials_json' :
        - Pour tous les services : Le JSON DOIT impérativement inclure une clé "description" explicitant la finalité.
        - Spécifique à 'mcp_remote' : Le JSON DOIT contenir les clés "url" et "transport" ("sse" ou "streamable_http").
        """
        if not __user__:
            return "Erreur: Contexte OWUI manquant."
        from echo_constants import ECHO_SUBAGENT_CONTEXT
        if ECHO_SUBAGENT_CONTEXT.get().get("is_subagent"):
            return "Erreur : L'environnement d'exécution (Headless/Sous-agent) ne permet pas de gérer les identités."

        events = EchoEvents(__event_emitter__, __event_call__)

        if action in ["add", "update"]:
            try:
                payload_dict = json.loads(credentials_json)
                payload_html = "<ul>" + "".join([f"<li><i>{k}</i> : {str(v)}</li>" for k, v in payload_dict.items()]) + "</ul>"
            except Exception:
                payload_html = f"<code>{credentials_json}</code>"

            if action == "add":
                action_fr = "ajouter"
            else:
                action_fr = "mettre à jour"

            if service == "mcp":
                action_desc = f"L'Agent souhaite <b>{action_fr}</b> le Serveur MCP distant nommé <b>{account_id}</b> :"
            else:
                action_desc = f"L'Agent souhaite <b>{action_fr}</b> les identifiants d'API pour <b>{account_id}</b> ({service}) :"

            js_msg = (
                f"🛡️ <b>Demande d'autorisation système</b><br><br>"
                f"{action_desc}<br><br>"
                f"<b>Détails de la configuration :</b><br>"
                f"{payload_html}<br>"
                f"Autoriser cette modification sur votre environnement ?"
            )
            js_msg_escaped = json.dumps(js_msg)

            confirm_js = f"""
            {EchoUI.get_custom_modals_js()}
            return await new Promise((resolve) => {{
                window.echoCustomConfirm({js_msg_escaped}, (agreed) => resolve(agreed));
            }});
            """
            user_consent = await events.call_execute(confirm_js)

            if not user_consent:
                return "Opération annulée : L'utilisateur a refusé la modification."

            state = self._init_vault(__user__["id"])
            with state._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO identity_vault (user_id, service, account_id, credentials) VALUES (?, ?, ?, ?)",
                    (__user__["id"], service, account_id, credentials_json)
                )
                conn.commit()
            return f"Succès: Source '{account_id}' ({service}) configurée avec succès."

        elif action == "delete":
            if service == "mcp":
                action_desc = f"L'Agent demande la <b>suppression définitive</b> du Serveur MCP distant nommé <b>{account_id}</b>."
            else:
                action_desc = f"L'Agent demande la <b>suppression définitive</b> des identifiants pour <b>{account_id}</b> ({service})."

            js_msg = (
                f"⚠️ <b>Action Destructive Requise</b><br><br>"
                f"{action_desc}<br><br>"
                f"Cette action est irréversible. Confirmer la suppression ?"
            )
            js_msg_escaped = json.dumps(js_msg)

            confirm_js = f"""
            {EchoUI.get_custom_modals_js()}
            return await new Promise((resolve) => {{
                window.echoCustomConfirm({js_msg_escaped}, (agreed) => resolve(agreed));
            }});
            """
            user_consent = await events.call_execute(confirm_js)

            if not user_consent:
                return "Opération annulée : L'utilisateur a refusé la suppression."

            state = self._init_vault(__user__["id"])
            with state._get_connection() as conn:
                conn.execute(
                    "DELETE FROM identity_vault WHERE user_id = ? AND service = ? AND account_id = ?",
                    (__user__["id"], service, account_id)
                )
                conn.commit()
            return f"Succès: Source '{account_id}' supprimée."

        return "Erreur: action invalide (utiliser add, update, ou delete)."
