"""
title: ECHO Memory Purge
author: Wilfried BARNAVON
version: 1.0
description: 1.0: Permet à l'utilisateur de purger l'intégralité de sa mémoire organique stockée dans Qdrant.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0zIDZoMThtLTIgMHYxNGEyIDIgMCAwIDEtMiAyaC0xMGEyIDIgMCAwIDEtMi0yVjZtMyAwVjRhMiAyIDAgMCAxIDItMmg0YTIgMiAwIDAgMSAyIDJ2Mk0xMCAxMXY2bTQtNnY2Ii8+PC9zdmc+
"""

import sys
import httpx
from pydantic import BaseModel, Field
from typing import Any, Optional

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents
from echo_constants import COLLECTION_MEMORY

class Action:
    class Valves(BaseModel):
        QDRANT_URL: str = Field(default="http://echo-qdrant:6333", description="URL interne de Qdrant.")
        priority: int = Field(default=2, description="Priorité d'affichage.")

    def __init__(self):
        self.valves = self.Valves()

    async def action(self, body: dict, __user__: Optional[dict] = None, __event_emitter__: Any = None, __event_call__: Any = None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)
        
        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]

        # 1. Demande de confirmation explicite
        confirmed = await events.confirm(
            "🗑️ Purger toute votre mémoire organique ?",
            "Cette action est irréversible. ECHO oubliera tous les faits, décisions et préférences mémorisés pour votre compte dans la base vectorielle."
        )

        if not confirmed:
            return None

        await events.status("🧠 Purge de la mémoire organique en cours...", False)

        try:
            # 2. Requête de suppression ciblée sur l'utilisateur
            delete_payload = {
                "filter": {
                    "must": [
                        {"key": "user_id", "match": {"value": user_id}}
                    ]
                }
            }

            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{self.valves.QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete",
                    json=delete_payload
                )

                if resp.status_code == 200:
                    await events.status("🧠 Mémoire organique purgée avec succès.", True)
                    await events.toast("✅ Votre mémoire organique a été intégralement effacée.", "success")
                else:
                    error_msg = resp.text
                    await events.status("❌ Échec de la purge.", True)
                    await events.toast(f"❌ Erreur Qdrant : {error_msg}", "error")

        except Exception as e:
            await events.status("❌ Erreur système lors de la purge.", True)
            await events.toast(f"❌ Erreur : {str(e)}", "error")

        return None
