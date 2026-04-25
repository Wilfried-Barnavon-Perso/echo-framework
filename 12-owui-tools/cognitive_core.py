"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.8
description: 3.8: Correctif de stabilité (Fix auth_mesh et nettoyage syntaxique).
"""

import sys
import orjson as json
import asyncio
import re
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoAuth, EchoEvents, split_thought_process, EchoGeminiClient
from echo_constants import (
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, MODEL_LITE, MODEL_FLASH, MODEL_PRO,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

async def _call_gemini_direct(
    user_id: str, 
    model_id: str, 
    prompt: str, 
    system_instruction: Optional[str] = None,
    thinking_level: str = "HIGH", 
    events: Optional[EchoEvents] = None, 
    threshold: int = ECHO_API_KEY_THRESHOLD, 
    max_retries: int = ECHO_API_MAX_RETRIES,
    timeout: int = 120
) -> str:
    """Appel direct à l'API Gemini AI Studio via EchoGeminiClient pour délégation cognitive."""
    auth = EchoAuth(user_id=user_id)
    
    # Récupération du mesh d'authentification (Nouveau système)
    auth_mesh = await auth.get_ordered_auth_mesh(user_id)
    if not auth_mesh: 
        return "❌ Erreur: Non authentifié. Aucun moyen d'accès trouvé pour cet utilisateur."

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

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    try:
        data = await EchoGeminiClient.call(
            auth_mesh=auth_mesh,
            target_model=model_id,
            payload=payload,
            threshold=threshold,
            max_retries=max_retries,
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
        FLASH_THINKING: str = Field(default="HIGH", description="Niveau de réflexion pour le modèle FLASH (LOW, MEDIUM, HIGH)")
        PRO_THINKING: str = Field(default="HIGH", description="Niveau de réflexion pour le modèle PRO (LOW, MEDIUM, HIGH)")
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        COGNITIVE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")

    def __init__(self):
        self.valves = self.Valves()

    async def delegate_reasoning(
        self,
        context: str,
        prompt: str,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"],
        system_instruction: Optional[str] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Délégation cognitive sans état (stateless). Chaque appel est indépendant et ne conserve aucune mémoire des échanges précédents.
        Le paramètre 'context' est obligatoire pour injecter sémantiquement les faits, la mémoire ou les données nécessaires à la réflexion.
        
        Utilisez MODEL_LITE pour la distillation rapide et l'extraction de données.
        Utilisez MODEL_FLASH pour les tâches intermédiaires, le formatage ou la logique standard.
        Utilisez MODEL_PRO pour l'architecture complexe, le debug profond ou la planification stratégique.
        
        :param context: Contexte sémantique (Markdown) de référence pour la tâche.
        :param prompt: L'instruction ou la tâche spécifique à exécuter.
        :param target_model: Le modèle à utiliser (MODEL_LITE, MODEL_FLASH, MODEL_PRO).
        :param system_instruction: (Optionnel) Comportement strict ou format de sortie attendu.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        # Résolution du modèle
        model_map = {
            "MODEL_LITE": MODEL_LITE,
            "MODEL_FLASH": MODEL_FLASH,
            "MODEL_PRO": MODEL_PRO
        }
        actual_model = model_map.get(target_model, MODEL_PRO)
        
        # Résolution du niveau de réflexion
        thinking_level = self.valves.PRO_THINKING
        if target_model == "MODEL_FLASH":
            thinking_level = self.valves.FLASH_THINKING
        elif target_model == "MODEL_LITE":
            thinking_level = "LOW" # Lite ne supporte généralement pas de hauts niveaux de pensée

        await events.status(f"🧠 Délégation Cognitive ({target_model}) pour {user_id}...")
        
        # Construction du prompt sémantique
        combined_prompt = f"### CONTEXTE\n{context}\n\n### TÂCHE\n{prompt}"

        res = await _call_gemini_direct(
            user_id=user_id,
            model_id=actual_model,
            prompt=combined_prompt,
            system_instruction=system_instruction,
            thinking_level=thinking_level,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            timeout=self.valves.COGNITIVE_TIMEOUT
        )
        
        await events.status(f"Délégation terminée ({target_model}).", done=True)
        return wrap_tool_output(text=res)
