"""
title: Context Gauge (Strict)
author: Wilfried BARNAVON
version: v1.3
description: Outil d'introspection permettant au modèle de vérifier son niveau d'occupation de la fenêtre de contexte (Données réelles uniquement).
"""

from pydantic import BaseModel, Field
import os
import json

class Tools:
    class Valves(BaseModel):
        context_limit: int = Field(
            default=1048576, 
            description="Limite théorique du contexte (Tokens). Défaut: 1M (Gemini Pro)."
        )

    def __init__(self):
        self.valves = self.Valves()

    def get_context_load(self, __messages__: list, __metadata__: dict = None) -> str:
        """
        Lit l'historique de consommation de tokens directement depuis les fichiers système du serveur.
        Ne fait AUCUNE estimation : si le fichier n'est pas trouvé, renvoie une erreur.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire.
        """
        limit = self.valves.context_limit
        real_stats = None
        source = "MISSING_DATA"
        chat_id = None
        
        if __metadata__:
            chat_id = __metadata__.get("chat_id") or __metadata__.get("session_id")
            if chat_id:
                try:
                    safe_id = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
                    stats_path = f"/app/backend/data/stats/{safe_id}.json"
                    if os.path.exists(stats_path):
                        with open(stats_path, "r") as f:
                            real_stats = json.load(f)
                            source = "API_REAL"
                except Exception as e:
                    return f"Erreur de lecture système : {str(e)}"

        if not real_stats:
             return f"Métrique indisponible : Aucune donnée de session trouvée pour l'ID `{chat_id}`."

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