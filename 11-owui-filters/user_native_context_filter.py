"""
title: ECHO User Native Context Filter
author: ECHO Framework
version: 1.0
description: Composant système interne : Paramétrages personnels du contexte.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.0: Création du filtre pour déléguer les UserValves et invisibiliser le New Context Filter.

from pydantic import BaseModel, Field
from typing import Optional

class Filter:
    class Valves(BaseModel):
        priority: int = Field(
            default=10, 
            description="Priorité d'exécution (0 = premier)."
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
