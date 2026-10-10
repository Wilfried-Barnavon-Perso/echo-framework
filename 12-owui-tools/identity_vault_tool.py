"""
title: ECHO Identity Vault Tool
author: ECHO
version: 1.6
description: Outil permettant à l'Agent de gérer le Identity Vault (ajout/suppression de serveurs distants ou N8N).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.6: Remplacement de la vérification __event_call__ par ECHO_SUBAGENT_CONTEXT pour l'interdiction Headless.
# 1.4: Refonte list_identities (tolérance aux fautes, import json global).
# 1.2: Suppression totale de la notion d'accès RO/RW (access_level).
# 1.1: Refonte du Lazy-Loading JS des modales ECHO (get_custom_modals_js) pour éviter les fallbacks moches hors-Codex.
# 1.0: Outil initial.
import sys
import json
from typing import Optional, Any, List, Dict, Literal
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager
from echo_ui import EchoUI
from echo_events import EchoEvents

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

    async def list_identities_services(self, category_filter: Literal["all", "mcp_native", "mcp_resident", "mcp_ephemeral", "mcp_remote", "orchestration", "uncategorized"] = "all", __user__: dict = None) -> str:
        """
        Interroge l'API pour extraire le catalogue des services configurables de l'Identity Vault, classés par catégorie.
        
        Args:
            category_filter: Filtre sémantique permettant de restreindre la recherche. Valeur 'all' par défaut.
        """
        if not __user__: return "Erreur: Auth requise."
        
        import httpx
        schemas = {}
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get("http://echo-mcp-broker:8000/schemas", timeout=2.0)
                if resp.status_code == 200:
                    schemas = resp.json()
        except Exception:
            pass
            
        taxonomie = (
            "CATÉGORIES D'ARCHITECTURE ECHO :\n"
            "- mcp_native : Modules internes intégrés au Broker MCP (Zéro latence).\n"
            "- mcp_resident : Serveurs locaux préinstallés garantissant stabilité et résilience.\n"
            "- mcp_ephemeral : Serveurs exécutés à la volée (via uvx/npx) pour le prototypage.\n"
            "- mcp_remote : Connexions distantes (HTTP/SSE) vers des composants externes.\n"
            "- orchestration : Interfaces d'automatisation (ex: N8N).\n"
            "- uncategorized : Entrées historiques ou personnalisées ne relevant d'aucune taxonomie stricte.\n\n"
            f"SERVICES CONFIGURABLES DANS LE REGISTRE DE L'IDENTITY VAULT (Filtre: {category_filter}) :\n"
        )
        for svc_id, data in schemas.items():
            cat = data.get("category", "uncategorized")
            if category_filter == "all" or cat == category_filter:
                name = data.get("name", svc_id)
                taxonomie += f"- [{cat}] {svc_id} : {name}\n"
            
        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT DISTINCT service FROM identity_vault WHERE user_id = ?", (__user__["id"],))
            rows = cursor.fetchall()
            
        taxonomie += "\nCOMPTES ACTUELLEMENT INSTANCIÉS EN BASE :\n"
        if not rows:
            taxonomie += "Aucun compte existant."
        else:
            taxonomie += ", ".join([r[0] for r in rows])
            
        return taxonomie

    async def list_identities(self, service: str, __user__: dict = None) -> str:
        """
        Interroge l'Identity Vault pour restituer les comptes tiers associés à un service spécifique.
        L'argument 'service' est strictement requis. Invoquer 'list_identities_services' en cas d'ambiguïté.
        """
        if not __user__: return "Erreur: Utilisateur inconnu."
        if not service: return "Erreur: L'argument 'service' est strictement requis. Invoquez 'list_available_services' en cas de doute."
        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT service, account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ?", (__user__["id"], service))
            rows = cursor.fetchall()
            
        result = []
        for r in rows:
            svc = r[0]
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

    async def manage_identity(self, action: str, service: str, account_id: str, credentials_json: str = "", __user__: dict = None, __event_emitter__: Any = None, __event_call__: Any = None) -> str:
        """
        Ajoute, modifie ou purge une identité applicative ou un serveur au sein de l'Identity Vault. Action autorisée : 'add', 'update' ou 'delete'.
        
        RÈGLES POUR 'credentials_json' :
        - Pour tous les services : Le JSON DOIT impérativement inclure une clé "description" explicitant la finalité.
        - Spécifique à 'mcp_remote' : Le JSON DOIT contenir les clés "url" et "transport" ("sse" ou "streamable_http").
        """
        if not __user__: return "Erreur: Contexte OWUI manquant."
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
                fallback_msg = f"Autoriser l'ajout de {account_id} ({service}) ?"
            else:
                action_fr = "mettre à jour"
                fallback_msg = f"Autoriser la mise à jour de {account_id} ({service}) ?"

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
            js_fallback_escaped = json.dumps(fallback_msg)
            
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
            fallback_msg = f"Confirmer la suppression définitive de {account_id} ({service}) ?"
            
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
            js_fallback_escaped = json.dumps(fallback_msg)
            
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
