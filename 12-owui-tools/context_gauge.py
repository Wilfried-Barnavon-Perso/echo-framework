"""
title: ECHO Context Gauge
author: Wilfried BARNAVON
version: 2.0
description: Outil d'introspection permettant de vérifier l'occupation de la fenêtre de contexte depuis la base de données utilisateur.
"""

from pydantic import BaseModel, Field
import os
import json
import sqlite3

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

    def get_context_load(self, __messages__: list, __user__: dict, __metadata__: dict = None) -> str:
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
            return "Erreur critique : Impossible d'identifier l'utilisateur."

        try:
            safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
            db_path = os.path.join(self.db_root_dir, f"user-{safe_uid}.db")

            if not os.path.exists(db_path):
                return f"Métrique indisponible : Aucune base de données trouvée pour l'utilisateur ID `{user_id}`."

            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM context_stats WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    real_stats = json.loads(row[0])
                    source = "DB_REAL"
        except sqlite3.OperationalError as e:
            return f"Métrique indisponible : La table `context_stats` est probablement manquante dans la base de données. Erreur: {e}"
        except Exception as e:
            return f"Erreur de lecture système : {str(e)}"

        if not real_stats:
             return f"Métrique indisponible : Aucune donnée de session trouvée pour l'utilisateur ID `{user_id}`."

        # Usage des vraies stats
        est_tokens = real_stats.get("totalTokenCount", 0)
        p_tok = real_stats.get("promptTokenCount", 0)
        c_tok = real_stats.get("candidatesTokenCount", 0)

        percent = round((est_tokens / limit) * 100, 2)
        
        status = "SAFE"
        if percent > 80: status = "WARNING"
        if percent > 95: status = "CRITICAL"

        msg = (
            f"Context Load: {percent}% ({est_tokens}/{limit} tokens) - Status: {status} [{source}]\n"
            f"Details: Prompt={p_tok}, Candidates={c_tok}"
        )
            
        return msg