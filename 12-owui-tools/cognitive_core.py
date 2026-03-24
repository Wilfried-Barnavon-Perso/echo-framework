"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.9
description: 3.9: Fixed authentication by using AuthService and dynamic user context. Preserved thought cleaning.
"""

import sys
import json
import httpx
import asyncio
import re
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoStateManager, EchoEvents, split_thought_process
from echo_constants import GOOGLE_API_BASE_URL, ECHO_USER_AGENT
from echo_auth import AuthService

async def _call_gemini_direct(auth: AuthService, model: str, prompt: str, thinking_level: str = "MEDIUM", events: Optional[EchoEvents] = None) -> str:
    creds = auth.get_valid_credentials()
    if not creds: return "❌ Erreur: Non authentifié. L'utilisateur doit s'authentifier via le Pipe ECHO d'abord."
    
    # Récupération sécurisée du PID (synchronisation si cache vide)
    cached_pid, _ = auth.get_project_id(creds)
    if not cached_pid: return "❌ Erreur: Project ID manquant. Utilisez le Pipe ECHO pour initialiser la session."

    url = f"{GOOGLE_API_BASE_URL}:streamGenerateContent?alt=sse"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
    
    payload = {
        "model": model,
        "project": cached_pid,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.lower()}
            }
        }
    }

    full_text = ""
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                if response.status_code != 200:
                    err_body = await response.aread()
                    return f"❌ Erreur API Gemini ({response.status_code}): {err_body.decode()}"
                
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            target = data.get("response", {}) if "response" in data else data
                            candidates = target.get("candidates", [])
                            
                            if candidates and candidates[0].get("content"):
                                parts = candidates[0]["content"].get("parts", [])
                                for p in parts:
                                    # On accumule tout le texte (y compris les pensées balisées)
                                    if "text" in p: full_text += p["text"]
                        except: pass
    except Exception as e: return f"❌ Erreur API : {str(e)}"
    return full_text

class Tools:
    class Valves(BaseModel):
        FAST_MODEL: str = Field(default="gemini-3-flash-preview")
        FAST_THINKING: str = Field(default="MEDIUM")
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
        
        await events.status(f"Lancement du raisonnement profond (Gemini Pro) pour {user_id}...")
        
        # Contexte d'auth dynamique
        state_manager = EchoStateManager(user_id=user_id)
        auth_service = AuthService(user_data_manager=state_manager)
        
        res = await _call_gemini_direct(
            auth_service, 
            self.valves.PRO_MODEL, 
            question, 
            self.valves.PRO_THINKING,
            events
        )
        
        # NETTOYAGE DES PENSÉES (Sanctuarisé)
        clean_text, thoughts = split_thought_process(res)
        return clean_text

    async def deep_analysis(
        self,
        text_to_analyze: str,
        instruction: Optional[str] = "Analyse ce contenu de manière critique.",
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Analyse rapide et intelligente d'un texte ou d'un snippet de code via Gemini Flash.
        :param text_to_analyze: Le contenu brut à analyser.
        :param instruction: Instruction spécifique pour l'analyse.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        await events.status("Analyse Flash en cours...")
        
        # Contexte d'auth dynamique
        state_manager = EchoStateManager(user_id=user_id)
        auth_service = AuthService(user_data_manager=state_manager)
        
        prompt = f"{instruction}\n\nCONTENU :\n{text_to_analyze}"
        
        res = await _call_gemini_direct(
            auth_service, 
            self.valves.FAST_MODEL, 
            prompt, 
            self.valves.FAST_THINKING,
            events
        )
        
        # NETTOYAGE DES PENSÉES (Sanctuarisé)
        clean_text, thoughts = split_thought_process(res)
        return clean_text
