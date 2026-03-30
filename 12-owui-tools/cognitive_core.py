"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.13
description: 3.13: Renamed deep_analysis to flash_distillation for better transparency. Updated valves naming.
"""

import sys
import orjson as json
import httpx
import asyncio
import re
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoAuth, EchoEvents, split_thought_process
from echo_constants import GOOGLE_API_BASE_URL, ECHO_USER_AGENT

async def _call_gemini_direct(user_id: str, model: str, prompt: str, thinking_level: str = "MEDIUM", events: Optional[EchoEvents] = None) -> str:
    """Appel direct à l'API Gemini AI Studio via API Key pour délégation cognitive."""
    auth = EchoAuth(user_id=user_id)
    api_key = auth.get_api_key(user_id)
    if not api_key: 
        return "❌ Erreur: Non authentifié. Aucune clé API Google AI Studio trouvée pour cet utilisateur."

    # Construction URL standard AI Studio (v1beta)
    url = f"{GOOGLE_API_BASE_URL}/models/{model}:streamGenerateContent?key={api_key}&alt=sse"
    headers = {
        "x-goog-api-key": api_key, 
        "Content-Type": "application/json", 
        "User-Agent": ECHO_USER_AGENT
    }
    
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "thinkingConfig": {
                "includeThoughts": True, 
                "thinkingLevel": thinking_level.lower()
            }
        }
    }

    full_text = ""
    try:
        async with httpx.AsyncClient(timeout=120, http2=True) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    err_body = await response.aread()
                    return f"❌ Erreur API Gemini ({response.status_code}): {err_body.decode()}"
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:].strip())
                            # Structure stream standard candidates[0].content.parts
                            candidates = data.get("candidates", [])
                            if candidates and candidates[0].get("content"):
                                for p in candidates[0]["content"].get("parts", []):
                                    if "text" in p: full_text += p["text"]
                        except: pass
    except Exception as e: 
        return f"❌ Erreur API : {str(e)}"
    
    return full_text

class Tools:
    class Valves(BaseModel):
        FLASH_MODEL: str = Field(default="gemini-3.1-flash-lite-preview")
        FLASH_THINKING: str = Field(default="MEDIUM")
        PRO_MODEL: str = Field(default="gemini-3.1-pro-preview")
        PRO_THINKING: str = Field(default="HIGH")

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
            events=events
        )
        
        await events.status("Raisonnement terminé.", done=True)
        return res

    async def flash_distillation(
        self,
        text_to_distill: str,
        instruction: str = "Distille ce texte pour n'en extraire que l'essentiel (points clés, faits, résumé).",
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Distillation rapide et efficace de textes longs ou de données brutes via un modèle Flash Lite.
        Libère le contexte principal en déléguant l'extraction d'information essentielle.
        :param text_to_distill: Le texte ou les données à traiter (distiller).
        :param instruction: L'instruction spécifique pour la distillation.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        await events.status(f"⚡ Distillation Flash (Lite) pour {user_id}...")
        
        prompt = f"INSTRUCTION: {instruction}\n\nTEXTE À DISTILLER:\n{text_to_distill}"
        
        res = await _call_gemini_direct(
            user_id,
            self.valves.FLASH_MODEL,
            prompt,
            thinking_level=self.valves.FLASH_THINKING,
            events=events
        )
        
        await events.status("Distillation terminée.", done=True)
        return res
