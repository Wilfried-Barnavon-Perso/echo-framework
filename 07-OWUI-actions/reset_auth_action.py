"""
title: ECHO Auth Manager (User Isolation Compatible)
author: ECHO Team
version: 2.0
description: 2.0: Réinitialise l'authentification Google (Tokens & Cache) de l'utilisateur courant. Compatible avec l'architecture multi-utilisateurs v137.0+.
"""

import os
import re
from pydantic import BaseModel, Field

class Action:
    def __init__(self):
        self.valves = self.Valves()
        # Doit pointer vers le même dossier racine que le pipe_engine
        self.data_dir = "/app/backend/data"
        self.tokens_dir = os.path.join(self.data_dir, "tokens")

    class Valves(BaseModel):
        # Pas de configuration complexe nécessaire pour l'instant
        pass

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None, **kwargs):
        """
        Exécuté quand l'utilisateur clique sur le bouton d'action.
        """
        
        # 0. Vérification Utilisateur
        if not __user__ or "id" not in __user__:
            if __event_emitter__:
                await __event_emitter__({
                    "type": "notification",
                    "data": {"type": "error", "content": "❌ Erreur critique : Impossible d'identifier l'utilisateur."}
                })
            return None

        # Récupération et sécurisation de l'ID (comme dans le Pipe v137.0)
        user_id = __user__["id"]
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")

        # 1. Confirmation Interactive (Modifiée pour refléter l'action personnelle)
        if __event_call__:
            confirm = await __event_call__({
                "type": "confirmation",
                "data": {
                    "title": "🔴 Réinitialiser VOTRE Auth ?",
                    "message": "Cette action va supprimer vos tokens Google personnels et votre cache de projet. Vous devrez vous ré-authentifier au prochain message. Continuer ?"
                }
            })
            if not confirm:
                return None

        # 2. Exécution du Reset
        if __event_emitter__:
            await __event_emitter__({
                "type": "status",
                "data": {"description": "Nettoyage de vos fichiers d'authentification..."}
            })

        # Noms de fichiers alignés avec pipe_engine.py v137.0
        files_to_remove = [
            f"gemini_official_token_{safe_uid}.json",
            f"gemini_pkce_{safe_uid}.txt",
            f"gemini_project_{safe_uid}.txt"
        ]
        
        deleted_count = 0
        errors = []

        # S'assurer que le dossier tokens existe
        if os.path.exists(self.tokens_dir):
            for filename in files_to_remove:
                path = os.path.join(self.tokens_dir, filename)
                if os.path.exists(path):
                    try:
                        os.remove(path)
                        deleted_count += 1
                    except Exception as e:
                        errors.append(f"{filename}: {str(e)}")
        
        # 3. Notification de résultat
        if errors:
            msg_type = "warning"
            content = f"Reset partiel ({deleted_count} supprimés). Erreurs: {', '.join(errors)}"
        elif deleted_count == 0:
            msg_type = "info"
            content = "Aucun fichier d'auth trouvé pour votre compte. Le système est déjà propre."
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