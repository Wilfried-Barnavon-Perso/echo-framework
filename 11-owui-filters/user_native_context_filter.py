"""
title: ECHO User Native Context Filter
author: ECHO Framework
version: 5.231.11.1
description: Composant système interne : Paramétrages personnels du contexte.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.11.1: Réétagement de la priorité à 20 (après context_gauge_filter à 10 et avant new_context_filter à 30).
# 5.227.1.1: Suppression de la UserValve ENABLE_AUTO_COMPACTION et de _echo_auto_compaction_enabled.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.1: Ajout de la UserValve ENABLE_AUTO_COMPACTION (False par défaut) pour le compactage contextuel automatique.
# 1.0: Création du filtre pour déléguer les UserValves et invisibiliser le New Context Filter.

from pydantic import BaseModel, Field
from typing import Optional


class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=20,
            description="Priorité d'exécution (20 = après context_gauge_filter à 10 et avant new_context_filter à 30)."
        )

    class UserValves(BaseModel):
        ENABLE_USER_NAME: bool = Field(default=False, description="🔒 Partager mon nom avec le modèle.")
        OVERRIDE_LOCATION: str = Field(default="", description="📍 Surcharger ma position géographique (Ex: Paris, France).")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

        # --- CONFIGURATION UI OPEN WEBUI ---
        self.toggle = True  # Affiche le switch et l'accès aux UserValves dans l'interface
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMjAgMjF2LTJhNCA0IDAgMCAwLTQtNEg4YTQgNCAwIDAgMC00IDR2MiIvPjxjaXJjbGUgY3g9IjEyIiBjeT0iNyIgcj0iNCIvPjwvc3ZnPg=="

    async def inlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        try:
            u_v = __user__.get("valves") if __user__ else self.user_valves
            enable_name = getattr(u_v, "ENABLE_USER_NAME", False)
            override_location = getattr(u_v, "OVERRIDE_LOCATION", "")

            body.setdefault("metadata", {})
            body["metadata"]["_echo_user_name_enabled"] = enable_name
            body["metadata"]["_echo_override_location"] = override_location

            return body
        except Exception as e:
            print(f"[ECHO-USER-FILTER] ❌ Erreur : {e}", flush=True)
            return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        return body
