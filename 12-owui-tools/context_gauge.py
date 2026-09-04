"""
title: ECHO Context Gauge
author: Wilfried BARNAVON
version: 3.4
description: Composant système interne : ECHO Context Gauge.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 3.3: Bascule des seuils cognitifs (WARNING/CRITICAL) sur les constantes définies dans echo_constants.py.
# 3.2: Alignement sur le standard de retour minimaliste (wrap_tool_output).

from pydantic import BaseModel, Field
import os
import orjson as json
import sqlite3
import sys
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_constants import ECHO_BASE_DATA_DIR, CONTEXT_LOAD_WARNING_THRESHOLD, CONTEXT_LOAD_CRITICAL_THRESHOLD

class Tools:
    class Valves(BaseModel):
        context_limit: int = Field(
            default=1048576, 
            description="Limite théorique du contexte (Tokens). Défaut: 1M (Gemini Pro)."
        )

    def __init__(self):
        self.valves = self.Valves()
        # Le chemin racine des bases de données unifiées (v5.76.0+)
        self.db_root_dir = f"{ECHO_BASE_DATA_DIR}/users"

    def get_context_load(
        self, 
        __messages__: list, 
        __user__: dict, 
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Surveillance de la saturation du contexte (Tokens). Déclencheur d'escalade cognitive : si statut CRITICAL, RECOMMANDE l'usage de 'new_cognitive_level' (PRO). Retourne un JSON détaillé (context_load_percent, used_tokens, status).
        """
        limit = self.valves.context_limit
        real_stats = None
        source = "MISSING_DATA"
        user_id = __user__.get("id")

        if not user_id:
            return wrap_tool_output(text="❌ Erreur : Impossible d'identifier l'utilisateur.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        try:
            safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
            chat_id = (__metadata__ or {}).get("chat_id")
            
            # 1. Chemin vers l'identité (Facturation)
            identity_db = os.path.join(self.db_root_dir, safe_uid, "identity.db")
            
            # 2. Chemin vers la session (Tokens)
            if chat_id:
                from echo_paths import get_echo_session_path
                session_db = get_echo_session_path(user_id, chat_id, "db")
            else:
                session_db = identity_db # Fallback sur identity si pas de chat_id

            if not os.path.exists(session_db):
                return wrap_tool_output(text=f"⚠️ Métrique indisponible : Pas de session active pour `{user_id}`.", status={"status": "missing_db"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            # Lecture des stats de tokens (depuis session_db ou identity_db fallback)
            with sqlite3.connect(f"file://{session_db}?mode=ro", uri=True, timeout=5.0) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT data FROM context_stats WHERE id = 1")
                row = cursor.fetchone()
                if row:
                    real_stats = json.loads(row[0])
                    source = "DB_SESSION" if chat_id else "DB_IDENTITY_FALLBACK"
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur DB : {str(e)}", status={"status": "error", "error": str(e)}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if not real_stats:
             return wrap_tool_output(text=f"⚠️ Aucune donnée de session pour `{user_id}`.", status={"status": "no_data"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        est_tokens = real_stats.get("totalTokenCount", 0)
        p_tok = real_stats.get("promptTokenCount", 0)
        c_tok = real_stats.get("candidatesTokenCount", 0)

        percent = round((est_tokens / limit) * 100, 2)
        
        status_load = "SAFE"
        if percent > CONTEXT_LOAD_WARNING_THRESHOLD: status_load = "WARNING"
        if percent > CONTEXT_LOAD_CRITICAL_THRESHOLD: status_load = "CRITICAL"

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
            
        return wrap_tool_output(
            text=json.dumps(payload, option=json.OPT_INDENT_2).decode('utf-8'), 
            status={"status": "success"}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
