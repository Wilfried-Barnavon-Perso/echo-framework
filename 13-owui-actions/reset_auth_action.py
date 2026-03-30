"""
title: ECHO Auth Manager
author: Wilfried BARNAVON
version: 3.7
description: 3.7: Mise à jour des messages pour le mode Clé API Google AI Studio.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxyZWN0IHdpZHRoPSIxOCIgaGVpZ2h0PSIxMSIgeD0iMyIgeT0iMTEiIHJ4PSIyIiByeT0iMiIvPjxwYXRoIGQ9Ik03IDExVjdhNSA1IDAgMCAxIDEwIDB2NCIvPjwvc3ZnPg==
"""

import os
import sqlite3
import sys
from pydantic import BaseModel, Field
from typing import Any

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents
from echo_constants import ECHO_USERS_ROOT

class Action:
    class Valves(BaseModel):
        priority: int = Field(default=1, description="Priorité d'affichage (1 = Premier).")

    def __init__(self):
        self.valves = self.Valves()
        self.user_dbs_dir = ECHO_USERS_ROOT

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        db_path = os.path.join(self.user_dbs_dir, safe_uid, "identity.db")

        if not await events.confirm("🔴 Réinitialiser votre Clé API Google ?", "Cela supprimera votre clé Google AI Studio de la base ECHO. Vous devrez la fournir à nouveau."):
            return None

        if not os.path.exists(db_path):
            await events.toast("Aucune configuration d'authentification trouvée.", "info")
            return None

        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM auth_data WHERE key LIKE 'google_%'")
                rows = cursor.rowcount
                conn.commit()
            await events.toast(f"✅ Succès ! {rows} entrées supprimées. Vous pouvez fournir une nouvelle clé.", "success")
        except Exception as e:
            await events.toast(f"❌ Erreur SQLite : {str(e)}", "error")

        return None
