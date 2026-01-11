"""
title: Context Gauge
author: Wilfried BARNAVON
version: 1.1
description: Outil d'introspection permettant au modèle de vérifier son niveau d'occupation de la fenêtre de contexte (Estimation).
"""

from pydantic import BaseModel, Field

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

    def get_context_load(self, __messages__: list) -> str:
        """
        Analyse l'historique de la conversation pour estimer la charge de la fenêtre de contexte.
        Utilisez cet outil lorsque vous devez vérifier si vous approchez de la limite de mémoire.
        """
        total_chars = 0
        
        try:
            # Calcul de la charge (Estimation)
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
            limit = self.valves.context_limit
            percent = round((est_tokens / limit) * 100, 2)
            
            status = "SAFE"
            if percent > 80: status = "WARNING"
            if percent > 95: status = "CRITICAL"

            return f"Context Load: {percent}% ({est_tokens}/{limit} est. tokens) - Status: {status}"
        except Exception as e:
            return f"Error calculating context load: {str(e)}"