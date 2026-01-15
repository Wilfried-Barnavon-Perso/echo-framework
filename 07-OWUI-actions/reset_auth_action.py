"""
title: ECHO Auth Manager
author: ECHO Team
version: v1.0.0
description: Ajoute un bouton d'action pour réinitialiser l'authentification Google (Tokens & Cache) directement depuis l'interface, avec confirmation.
"""

import os
from pydantic import BaseModel, Field

class Action:
    def __init__(self):
        self.valves = self.Valves()
        # Doit pointer vers le même dossier que le pipe_engine
        self.data_dir = "/app/backend/data"

    class Valves(BaseModel):
        # Pas de configuration complexe nécessaire pour l'instant
        pass

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None, **kwargs):
        """
        Exécuté quand l'utilisateur clique sur le bouton d'action.
        """
        
        # 1. Sécurité : Seuls les admins peuvent reset l'auth globale
        if __user__ and __user__.get("role") != "admin":
            if __event_emitter__:
                await __event_emitter__({
                    "type": "notification",
                    "data": {"type": "error", "content": "⛔ Accès refusé : Seul un administrateur peut réinitialiser l'authentification système."}
                })
            return None

        # 2. Confirmation Interactive
        if __event_call__:
            confirm = await __event_call__({
                "type": "confirmation",
                "data": {
                    "title": "🔴 Réinitialiser ECHO Auth ?",
                    "message": "Cette action va supprimer tous les tokens Google et le cache du projet. Vous devrez vous ré-authentifier au prochain message. Continuer ?"
                }
            })
            if not confirm:
                return None

        # 3. Exécution du Reset
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Nettoyage des fichiers d'authentification..."}
            })

        files_to_remove = [
            "gemini_official_token.json",
            "gemini_pkce_verifier.txt",
            "gemini_internal_project.txt"
        ]
        
        deleted_count = 0
        errors = []

        for filename in files_to_remove:
            path = os.path.join(self.data_dir, filename)
            if os.path.exists(path):
                try:
                    os.remove(path)
                    deleted_count += 1
                except Exception as e:
                    errors.append(f"{filename}: {str(e)}")

        # 4. Notification de résultat
        if errors:
            msg_type = "warning"
            content = f"Reset partiel ({deleted_count} supprimés). Erreurs: {', '.join(errors)}"
        elif deleted_count == 0:
            msg_type = "info"
            content = "Aucun fichier d'auth trouvé. Le système est déjà propre."
        else:
            msg_type = "success"
            content = f"✅ Succès ! {deleted_count} fichiers d'authentification supprimés."

        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Terminé", "done": True}
            })
            await __event_emitter__({
                "type": "notification",
                "data": {"type": msg_type, "content": content}
            })

        # On ne modifie pas le message d'origine
        return None