"""
title: ECHO Context Gauge
author: Wilfried BARNAVON
version: 5.235.5.1
description: Composant système interne : ECHO Context Gauge.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.235.5.1: Suppression du repli identity.db et lecture exclusive des stats de contexte dans session.db.
# 5.233.0.1: Décoration @echo_tool sur get_context_load et simplification des retours wrap_tool_output.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 3.6: Alignement sur la nomenclature cognitive (MODEL_PROMETHEUS).

from pydantic import BaseModel, Field
import os
import orjson as json
import sys
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output, echo_tool
from echo_constants import CONTEXT_LOAD_WARNING_THRESHOLD, CONTEXT_LOAD_CRITICAL_THRESHOLD
from echo_state_manager import EchoStateManager
from echo_paths import get_echo_session_path

class Tools:
    class Valves(BaseModel):
        context_limit: int = Field(
            default=1048576, 
            description="Limite théorique du contexte (Tokens). Défaut: 1M (Gemini Pro)."
        )

    def __init__(self):
        self.valves = self.Valves()

    @echo_tool
    def get_context_load(
        self, 
        __messages__: list, 
        __user__: dict, 
        __metadata__: dict = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Mesure le taux d'occupation de la fenêtre de contexte (tokens utilisés et statut SAFE/WARNING/CRITICAL)."""
        limit = self.valves.context_limit
        real_stats = None
        source = "MISSING_DATA"
        user_id = __user__.get("id")

        if not user_id:
            return "❌ Erreur : Impossible d'identifier l'utilisateur."

        try:
            chat_id = (__metadata__ or {}).get("chat_id")
            if not chat_id:
                return wrap_tool_output(
                    text=f"⚠️ Métrique indisponible : Pas de session active pour `{user_id}`.",
                    status={"status": "missing_db"},
                )
            session_db = get_echo_session_path(user_id, chat_id, "db")
            if not os.path.exists(session_db):
                return wrap_tool_output(
                    text=f"⚠️ Métrique indisponible : Pas de session active pour `{user_id}`.",
                    status={"status": "missing_db"},
                )
            real_stats = EchoStateManager(user_id=user_id, chat_id=chat_id).get_last_context_stats()
            source = "DB_SESSION"
        except Exception as e:
            return wrap_tool_output(
                text=f"❌ Erreur DB : {str(e)}",
                status={"status": "error", "error": str(e)},
            )

        if not real_stats:
             return wrap_tool_output(text=f"⚠️ Aucune donnée de session pour `{user_id}`.", status={"status": "no_data"})

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
            
        return json.dumps(payload, option=json.OPT_INDENT_2).decode('utf-8')
