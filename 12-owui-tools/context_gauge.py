"""
title: ECHO Context Gauge
author: Wilfried BARNAVON
version: 2.3
description: 2.3: Deterministic JSON output to prevent model completion derailment.
"""

from pydantic import BaseModel, Field
import os
import json
import sqlite3
import sys
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output

class Tools:
    class Valves(BaseModel):
        context_limit: int = Field(
            default=1048576, 
            description="Limite théorique du contexte (Tokens). Défaut: 1M (Gemini Pro)."
        )

    def __init__(self):
        self.valves = self.Valves()
        # Le chemin racine des bases de données unifiées
        self.db_root_dir = "/app/backend/data/user_dbs"

    def get_context_load(
        self, 
        __messages__: list, 
        __user__: dict, 
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Lit l'historique de consommation de tokens directement depuis la base de données de l'utilisateur.
        Ne fait AUCUNE estimation : si la base ou les données ne sont pas trouvées, renvoie une erreur.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire.
        """
        limit = self.valves.context_limit
        real_stats = None
        source = "MISSING_DATA"
        user_id = __user__.get("id")

        if not user_id:
            return wrap_tool_output(text="❌ Erreur : Impossible d'identifier l'utilisateur.", status={"status": "error"})

        try:
            safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
            db_path = os.path.join(self.db_root_dir, f"user-{safe_uid}.db")

            if not os.path.exists(db_path):
                return wrap_tool_output(text=f"⚠️ Métrique indisponible : Pas de DB pour `{user_id}`.", status={"status": "missing_db"})

            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM context_stats WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    real_stats = json.loads(row[0])
                    source = "DB_REAL"
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur DB : {str(e)}", status={"status": "error", "error": str(e)})

        if not real_stats:
             return wrap_tool_output(text=f"⚠️ Aucune donnée de session pour `{user_id}`.", status={"status": "no_data"})

        est_tokens = real_stats.get("totalTokenCount", 0)
        p_tok = real_stats.get("promptTokenCount", 0)
        c_tok = real_stats.get("candidatesTokenCount", 0)

        percent = round((est_tokens / limit) * 100, 2)
        
        status_load = "SAFE"
        if percent > 80: status_load = "WARNING"
        if percent > 95: status_load = "CRITICAL"

        payload = {
            "context_load_percent": percent,
            "used_tokens": est_tokens,
            "total_limit": limit,
            "status": status_load,
            "data_source": source,
            "details": {
                "prompt": p_tok,
                "candidates": c_tok
            }
        }
            
        return wrap_tool_output(text=json.dumps(payload, indent=2), status={"status": "success", "load_percent": percent, "tokens": est_tokens})
