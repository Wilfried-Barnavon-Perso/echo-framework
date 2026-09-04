"""
title: ECHO Identity Vault Tool
author: ECHO
version: 1.3
description: Outil permettant à l'Agent de gérer le Identity Vault (ajout/suppression de serveurs distants ou N8N).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.2: Suppression totale de la notion d'accès RO/RW (access_level).
# 1.1: Refonte du Lazy-Loading JS des modales ECHO (get_custom_modals_js) pour éviter les fallbacks moches hors-Codex.
# 1.0: Outil initial.
import sys
from typing import Optional, Any, List, Dict
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_state_manager import EchoStateManager
from echo_ui import EchoUI

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

    async def list_identities(self, service: str, __user__: dict = None) -> str:
        """
        Récupère la liste des serveurs ou services tiers enregistrés pour le compte actif, accompagnée de leur description sémantique et rôle. Permet d'identifier si un serveur pertinent est déjà disponible pour accomplir une tâche.
        """
        if not __user__: return "Erreur: Utilisateur inconnu."
        state = self._init_vault(__user__["id"])
        with state._get_connection() as conn:
            cursor = conn.execute("SELECT account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ?", (__user__["id"], service))
            rows = cursor.fetchall()
            
        import json
        result = []
        for r in rows:
            try:
                data = json.loads(r[1])
                desc = data.get("description", r[1])
            except:
                desc = r[1]
            result.append(f"[{r[0]} : {desc}]")
        return f"Serveurs pour {service}: " + ", ".join(result) if result else f"Aucun serveur trouvé pour {service}."

    async def manage_identity(self, action: str, service: str, account_id: str, credentials_json: str = "", __user__: dict = None, __event_call__: Any = None) -> str:
        """
        Ajoute, modifie ou supprime un serveur public distant (MCP) dans le registre sécurisé du système. Permet au modèle d'étendre dynamiquement ses propres capacités cognitives. Si la résolution d'une tâche exige un outil inexistant localement, permet au modèle d'effectuer une recherche web pour identifier un serveur MCP pertinent, puis d'invoquer cette fonction pour l'installer à la volée. Action = 'add', 'update' ou 'delete'. Une demande d'autorisation explicite est envoyée à l'utilisateur avant toute modification.
        """
        if not __user__ or not __event_call__: return "Erreur: Contexte OWUI manquant."
        
        if action in ["add", "update"]:
            import json
            
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
            user_consent = await __event_call__({"type": "execute", "data": {"code": confirm_js}})
            
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
            import json
            
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
            user_consent = await __event_call__({"type": "execute", "data": {"code": confirm_js}})
            
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
