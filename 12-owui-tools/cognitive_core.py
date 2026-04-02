"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.20
description: 3.20: Renamed flash_distillation to lite_reasoning for better alignment.
"""

import sys
import orjson as json
import asyncio
import re
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoAuth, EchoEvents, split_thought_process, EchoGeminiClient
from echo_constants import GOOGLE_API_BASE_URL, ECHO_USER_AGENT, MODEL_LITE, MODEL_PRO

async def _call_gemini_direct(user_id: str, model_id: str, prompt: str, thinking_level: str = "MEDIUM", events: Optional[EchoEvents] = None, threshold: int = 3, timeout: int = 120) -> str:
    """Appel direct à l'API Gemini AI Studio via EchoGeminiClient pour délégation cognitive."""
    auth = EchoAuth(user_id=user_id)
    api_keys = auth.get_api_keys(user_id)
    if not api_keys: 
        return "❌ Erreur: Non authentifié. Aucune clé API Google AI Studio trouvée pour cet utilisateur."

    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "maxOutputTokens": 16000,
            "thinkingConfig": {
                "includeThoughts": True, 
                "thinkingLevel": thinking_level.lower()
            }
        }
    }

    try:
        data = await EchoGeminiClient.call(
            keys=api_keys,
            target_model=model_id,
            payload=payload,
            threshold=threshold,
            max_retries=3,
            events=events,
            timeout=timeout
        )
        
        target = data.get("response", {}) if "response" in data else data
        candidates = target.get("candidates", [])
        if candidates and candidates[0].get("content"):
            full_text = ""
            for p in candidates[0]["content"].get("parts", []):
                if "text" in p: 
                    full_text += p["text"]
            return full_text
            
    except Exception as e: 
        return f"❌ Erreur système ou API : {str(e)}"
    
    return "❌ Erreur: Réponse Gemini vide ou invalide."

class Tools:
    class Valves(BaseModel):
        FLASH_MODEL: str = Field(default=MODEL_LITE)
        FLASH_THINKING: str = Field(default="MEDIUM")
        PRO_MODEL: str = Field(default=MODEL_PRO)
        PRO_THINKING: str = Field(default="HIGH")
        KEY_SWITCH_THRESHOLD: int = Field(default=3, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        COGNITIVE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")

    def __init__(self):
        self.valves = self.Valves()

    async def deep_reasoning(
        self,
        question: str,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Unité de raisonnement profond pour problèmes textuels complexes, architecture, debug ou planification.
        Utilise le modèle Gemini Pro avec un niveau de réflexion élevé.
        :param question: La question complexe ou la tâche nécessitant une réflexion approfondie.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        await events.status(f"🧠 Délégation Expert (Gemini Pro) pour {user_id}...")
        
        res = await _call_gemini_direct(
            user_id,
            self.valves.PRO_MODEL,
            question,
            thinking_level=self.valves.PRO_THINKING,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            timeout=self.valves.COGNITIVE_TIMEOUT
        )
        
        await events.status("Raisonnement terminé.", done=True)
        return res

    async def lite_reasoning(
        self,
        text_to_distill: str,
        instruction: str = "Distille ce texte pour n'en extraire que l'essentiel (points clés, faits, résumé).",
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Raisonnement léger et distillation rapide de textes longs ou de données brutes via un modèle Flash Lite.
        Libère le contexte principal en déléguant l'extraction d'information essentielle.
        :param text_to_distill: Le texte ou les données à traiter (distiller).
        :param instruction: L'instruction spécifique pour la distillation.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        await events.status(f"⚡ Raisonnement Lite (Flash) pour {user_id}...")
        
        prompt = f"INSTRUCTION: {instruction}\n\nTEXTE À DISTILLER:\n{text_to_distill}"
        
        res = await _call_gemini_direct(
            user_id,
            self.valves.FLASH_MODEL,
            prompt,
            thinking_level=self.valves.FLASH_THINKING,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            timeout=self.valves.COGNITIVE_TIMEOUT
        )
        
        await events.status("Distillation terminée.", done=True)
        return res
