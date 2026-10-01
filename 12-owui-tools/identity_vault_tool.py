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
from typing import Optional, Any, List, Dict
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

    async def list_available_services(self, __user__: dict = None) -> str:
        """
        Permet au modèle de lister de manière exhaustive les noms de services actuellement configurés dans le coffre-fort.
        DIRECTIVE : Le Modèle doit utiliser cet outil puis 'list_identities' pour vérifier si un serveur MCP local ou distant approprié est déjà à sa disposition et relatif à la tâche en cours.
        """
        if not __user__: return "Erreur: Auth requise."
        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT DISTINCT service FROM identity_vault WHERE user_id = ?", (__user__["id"],))
            rows = cursor.fetchall()
            
        if not rows:
            return "Aucun service configuré."
        
        return f"Services disponibles : {', '.join([r[0] for r in rows])}"

    async def list_identities(self, service: str, __user__: dict = None) -> str:
        """
        Permet au modèle de récupérer les comptes tiers rattachés à un service spécifique. L'argument 'service' est strictement obligatoire.
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
        Ajoute, modifie ou supprime une identité/serveur distant dans le Vault. Action = 'add', 'update' ou 'delete'.
        
        RÈGLES POUR 'credentials_json' :
        - Pour tous les services : Le JSON DOIT impérativement inclure une clé "description" expliquant clairement la finalité du compte ou serveur (ex: "Serveur MCP donnant accès à l'Open Data français").
        - Spécifique à 'remote_mcp' : Le JSON DOIT contenir la clé "url". Il DOIT également contenir la clé "transport" valant soit "sse" soit "streamable_http" (à déduire via recherche documentaire). La clé "headers" est optionnelle.
        Exemple : {"description": "...", "url": "https://api.com/mcp", "transport": "streamable_http", "headers": {"Authorization": "Bearer XXX"}}
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
