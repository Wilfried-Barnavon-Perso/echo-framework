"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.8
description: 3.8: Added missing Pydantic imports.
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
from echo_utils import wrap_tool_output, EchoAuth, EchoEvents
from echo_constants import GOOGLE_API_BASE_URL, ECHO_USER_AGENT

async def _call_gemini_direct(auth: EchoAuth, model: str, prompt: str, thinking_level: str = "MEDIUM", events: Optional[EchoEvents] = None) -> str:
    creds = auth.get_valid_credentials()
    if not creds: return "❌ Erreur: Non authentifié. L'utilisateur doit s'authentifier via le Pipe ECHO d'abord."
    
    cached_pid = auth.get_project_id_from_cache()
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
    thoughts = ""
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
                            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                            for p in parts:
                                if "thought" in p and p["thought"]:
                                    thoughts += p["text"]
                                    if events: await events.status(f"🧠 Réflexion en cours... ({len(thoughts)} car.)")
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
        self.auth = EchoAuth()

    async def deep_reasoning(
        self,
        question: str,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Unité de raisonnement profond pour problèmes textuelles complexes, architecture, debug ou planification.
        Utilise le modèle Gemini Pro avec un niveau de réflexion élevé.
        :param question: La question complexe ou la tâche nécessitant une réflexion approfondie.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status("Lancement du raisonnement profond (Gemini Pro)...")
        
        res = await _call_gemini_direct(
            self.auth, 
            self.valves.PRO_MODEL, 
            question, 
            self.valves.PRO_THINKING,
            events
        )
        
        # Extraction des pensées si présentes
        thoughts = ""
        clean_text = res
        if "<think>" in res:
            match = re.search(r"<think>(.*?)</think>", res, re.DOTALL)
            if match:
                thoughts = match.group(1).strip()
                clean_text = res.replace(match.group(0), "").strip()

        await events.status("Analyse terminée.", done=True)
        multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
        return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)

    async def quick_intel(
        self,
        question: str,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Unité d'analyse rapide pour requêtes textuelles simples, résumés, ou vérifications de faits.
        Utilise le modèle Gemini Flash pour une réponse quasi-instantanée.
        :param question: La question simple ou la micro-tâche à traiter rapidement.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status("Analyse flash en cours (Gemini Flash)...")
        
        res = await _call_gemini_direct(
            self.auth, 
            self.valves.FAST_MODEL, 
            question, 
            self.valves.FAST_THINKING,
            events
        )

        thoughts = ""
        clean_text = res
        if "<think>" in res:
            match = re.search(r"<think>(.*?)</think>", res, re.DOTALL)
            if match:
                thoughts = match.group(1).strip()
                clean_text = res.replace(match.group(0), "").strip()

        await events.status("Analyse terminée.", done=True)
        multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
        return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)
