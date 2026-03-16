"""
title: ECHO Context Gauge
author: Wilfried BARNAVON
version: 2.7
description: 2.7: Added user billing information (plan, AI overage credits, and base quota) to the tool's JSON payload.
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
        Renvoie l'utilisation exacte du contexte (en nombre de tokens et pourcentage) pour la session actuelle.
        Lit l'historique de consommation de tokens directement depuis la base de données persistante de l'utilisateur.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire de travail (Context Window).
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

        # --- [Nouveau] Ajout des informations de facturation ---
        plan_name = "Inconnu"
        credits = "0"
        q_rem = None
        q_lim = None
        q_reset = None
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM auth_data WHERE key = 'google_plan_name'")
                p_row = cursor.fetchone()
                if p_row: plan_name = p_row[0]
                
                cursor.execute("SELECT value FROM auth_data WHERE key = 'google_credits'")
                c_row = cursor.fetchone()
                if c_row: credits = c_row[0]
                
                cursor.execute("SELECT value FROM auth_data WHERE key = 'google_quota_remaining'")
                r_row = cursor.fetchone()
                if r_row: q_rem = r_row[0]
                
                cursor.execute("SELECT value FROM auth_data WHERE key = 'google_quota_limit'")
                l_row = cursor.fetchone()
                if l_row: q_lim = l_row[0]
                
                cursor.execute("SELECT value FROM auth_data WHERE key = 'google_quota_reset_time'")
                rt_row = cursor.fetchone()
                if rt_row: q_reset = rt_row[0]
        except: pass
        
        try: credits_int = int(credits)
        except: credits_int = 0
        # -------------------------------------------------------

        payload = {
            "billing": {
                "plan_name": plan_name,
                "ai_overage_credits": credits_int
            },
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
        
        if q_rem and q_lim:
            payload["billing"]["base_quota"] = {
                "remaining": int(q_rem) if q_rem.isdigit() else q_rem,
                "limit": int(q_lim) if q_lim.isdigit() else q_lim,
                "reset_time": q_reset
            }
            
        return wrap_tool_output(text=json.dumps(payload, indent=2), status={"status": "success", "load_percent": percent, "tokens": est_tokens})
