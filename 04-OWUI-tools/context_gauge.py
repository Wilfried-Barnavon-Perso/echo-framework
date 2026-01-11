"""
title: Context Gauge
author: Wilfried BARNAVON
version: 1.0
type: tool
description: Outil d'introspection permettant au modèle de vérifier son niveau d'occupation de la fenêtre de contexte (Estimation).
"""

from pydantic import BaseModel, Field
from typing import List, Dict, Any

class Tools:
    class Valves(BaseModel):
        context_limit: int = Field(
            default=1048576, 
            description="Limite théorique du contexte (Tokens). Défaut: 1M (Gemini Pro)."
        )
        token_ratio: float = Field(
            default=4.0,
            description="Ratio moyen caractères/token pour l'estimation."
        )

    def __init__(self):
        self.valves = self.Valves()

    def get_context_load(self, __messages__: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyse l'historique de la conversation pour estimer la charge de la fenêtre de contexte.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire.
        
        :param __messages__: La liste complète des messages de la conversation (automatiquement injecté par OWUI).
        :return: Un rapport sur l'utilisation du contexte.
        """
        total_chars = 0
        
        # Calcul de la charge (Estimation)
        # Note: __messages__ contient tout l'historique
        for msg in __messages__:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])
        
        est_tokens = int(total_chars / self.valves.token_ratio)
        limit = self.valves.context_limit
        percent = round((est_tokens / limit) * 100, 2)
        
        status = "SAFE"
        if percent > 80: status = "WARNING"
        if percent > 95: status = "CRITICAL"

        return {
            "estimated_tokens": est_tokens,
            "context_limit": limit,
            "load_percentage": f"{percent}%",
            "status": status,
            "recommendation": "Continue normal operation." if status == "SAFE" else "Consider summarizing conversation."
        }