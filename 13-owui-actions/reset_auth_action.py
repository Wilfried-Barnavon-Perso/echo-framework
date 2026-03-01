"""
title: ECHO Auth Manager
author: Wilfried BARNAVON
version: 3.4
description: 3.4: Restored detailed UX messages (Strict Architecture).
"""

import os
import sqlite3
import sys
from pydantic import BaseModel, Field
from typing import Any

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents
from echo_constants import ECHO_USER_DBS_DIR

class Action:
    class Valves(BaseModel):
        pass

    def __init__(self):
        self.valves = self.Valves()
        self.user_dbs_dir = ECHO_USER_DBS_DIR

    async def action(self, body: dict, __user__=None, __event_emitter__=None, __event_call__=None, **kwargs):
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or "id" not in __user__:
            await events.toast("❌ Erreur : Utilisateur non identifié.", "error")
            return None

        user_id = __user__["id"]
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        db_path = os.path.join(self.user_dbs_dir, f"user-{safe_uid}.db")

        if not await events.confirm("🔴 Réinitialiser votre Auth Google ?", "Cela supprimera vos accès Google Cloud de la base ECHO. Vous devrez vous reconnecter."):
            return None

        if not os.path.exists(db_path):
            await events.toast("Aucune session d'authentification trouvée.", "info")
            return None

        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute("DELETE FROM auth_data WHERE key = 'google_token' OR key = 'google_project_id'")
                rows = cursor.rowcount
                conn.commit()
            await events.toast(f"✅ Succès ! {rows} entrées supprimées. Vous pouvez vous ré-authentifier.", "success")
        except Exception as e:
            await events.toast(f"❌ Erreur SQLite : {str(e)}", "error")

        return None
