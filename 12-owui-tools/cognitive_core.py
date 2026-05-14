"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 4.1
description: 4.1: Centralisation des paramètres de génération via echo_constants.
"""

import sys
import orjson as json
import asyncio
import re
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents, EchoGeminiClient
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO, MODEL_ROUTING,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES
)

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
        
        # Résolution du modèle via le Registre Souverain
        actual_model = MODEL_ROUTING.get(target_model, MODEL_PRO)
        
        # Résolution du niveau de réflexion
        thinking_level = self.valves.PRO_THINKING
        if target_model == "MODEL_FLASH":
            thinking_level = self.valves.FLASH_THINKING
        elif target_model == "MODEL_LITE":
            thinking_level = "LOW" # Lite ne supporte généralement pas de hauts niveaux de pensée

        await events.status(f"🧠 Délégation Cognitive ({target_model}) pour {user_id}...")
        
        # Construction du prompt sémantique
        combined_prompt = f"### CONTEXTE\n{context}\n\n### TÂCHE\n{prompt}"

        # Appel au client agnostique (Purifié)
        res_json = await EchoGeminiClient.call(
            target_model=actual_model,
            payload={
                "contents": [{"role": "user", "parts": [{"text": combined_prompt}]}],
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                    "maxOutputTokens": 16000,
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.lower()}
                },
                "systemInstruction": {"parts": [{"text": system_instruction}]} if system_instruction else None
            },
            user_id=user_id,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            timeout=self.valves.COGNITIVE_TIMEOUT
        )
        
        # Extraction normalisée (le client déballe déjà les enveloppes Code Assist)
        candidates = res_json.get("candidates", [])
        if candidates and candidates[0].get("content"):
            full_text = "".join([p.get("text", "") for p in candidates[0]["content"].get("parts", [])])
            await events.status(f"Délégation terminée ({target_model}).", done=True)
            return wrap_tool_output(text=full_text)
        
        return wrap_tool_output(text="❌ Erreur: Réponse Gemini vide.")
