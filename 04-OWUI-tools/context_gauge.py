"""
title: Context Gauge
author: Wilfried BARNAVON
version: 1.2
description: Outil d'introspection permettant au modèle de vérifier son niveau d'occupation de la fenêtre de contexte (Réel ou Estimation).
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
        token_ratio: float = Field(
            default=4.0,
            description="Ratio moyen caractères/token pour l'estimation (Fallback)."
        )

    def __init__(self):
        self.valves = self.Valves()

    def get_context_load(self, __messages__: list, __metadata__: dict = None) -> str:
        """
        Analyse l'historique de la conversation pour estimer la charge de la fenêtre de contexte.
        Tente d'abord de lire les stats réelles de l'API Gemini, sinon fait une estimation.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire.
        """
        # 1. Tentative lecture stats réelles
        real_stats = None
        source = "ESTIMATION"
        
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
                except: pass

        limit = self.valves.context_limit
        est_tokens = 0
        
        if real_stats:
            # Usage des vraies stats
            est_tokens = real_stats.get("totalTokenCount", 0)
        else:
            # 2. Fallback Estimation manuelle
            total_chars = 0
            try:
                if __messages__:
                    for msg in __messages__:
                        content = msg.get("content", "")
                        if isinstance(content, str):
                            total_chars += len(content)
                        elif isinstance(content, list):
                            for part in content:
                                if isinstance(part, dict) and "text" in part:
                                    total_chars += len(part["text"])
                est_tokens = int(total_chars / self.valves.token_ratio)
            except Exception as e:
                return f"Error calculating context load: {str(e)}"

        percent = round((est_tokens / limit) * 100, 2)
        
        status = "SAFE"
        if percent > 80: status = "WARNING"
        if percent > 95: status = "CRITICAL"

        msg = f"Context Load: {percent}% ({est_tokens}/{limit} tokens) - Status: {status} [{source}]"
        if source == "API_REAL":
            p_tok = real_stats.get("promptTokenCount", 0)
            c_tok = real_stats.get("candidatesTokenCount", 0)
            msg += f"\nDetails: Prompt={p_tok}, Candidates={c_tok}"
            
        return msg
