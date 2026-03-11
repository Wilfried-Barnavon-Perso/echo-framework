"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.4
description: 3.4: Mutualized thought splitting using echo_utils (Standard <think>).
"""

import sys
import json
import httpx
import asyncio
import re
from typing import Optional, List, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, split_thought_process
from echo_constants import ECHO_USER_AGENT

# ==============================================================================
# FONCTIONS PRIVÉES COGNITIVES (Restauration v3.3)
# ==============================================================================

async def _execute_intel_request(valves, auth, query, context, persona, model, thinking, schema, user, events):
    token, project_id = auth.get_credentials(user.get("id"))
    if not token or not project_id: return "❌ Erreur Auth."

    url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
    
    prompt = f"CONTEXTE :\n{context}\n\nREQUÊTE :\n{query}" if context else query
    
    payload = {
        "model": model,
        "project": project_id,
        "request": {
            "systemInstruction": {"parts": [{"text": persona}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking.upper()},
                "responseMimeType": "application/json" if schema else "text/plain"
            }
        }
    }
    if schema: payload["request"]["generationConfig"]["responseSchema"] = schema

    full_text = ""
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                for p in cand["content"].get("parts", []):
                                    if "text" in p: full_text += p["text"]
                        except: pass
        except Exception as e: return f"❌ Erreur API : {str(e)}"
    return full_text

# ==============================================================================
# CLASSE DES OUTILS
# ==============================================================================

class Tools:
    class Valves(BaseModel):
        FAST_MODEL: str = Field(default="gemini-3-flash-preview")
        FAST_THINKING: str = Field(default="MEDIUM")
        PRO_MODEL: str = Field(default="gemini-2.0-pro-exp-02-05")
        PRO_THINKING: str = Field(default="HIGH")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def deep_reasoning(
        self, 
        query: str, 
        context: Optional[str] = None,
        intensity: str = "HIGH",
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> dict:
        """Unité de raisonnement profond pour problèmes complexes."""
        events = EchoEvents(__event_emitter__)
        await events.status(f"🧠 Réflexion Profonde ({intensity})...")
        
        persona = "Tu es l'unité de raisonnement profond d'ECHO. Ton but est de résoudre ce problème étape par étape."
        res_text = await _execute_intel_request(self.valves, self.auth, query, context, persona, self.valves.PRO_MODEL, intensity, None, __user__, events)
        
        clean_text, thoughts = split_thought_process(res_text)
        await events.status("Raisonnement terminé.", done=True)
        multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
        return wrap_tool_output(text=clean_text, status={"status": "success", "intensity": intensity}, echo_tool_multiparts=multiparts)

    async def quick_intel(
        self, 
        query: str, 
        context: Optional[str] = None,
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> dict:
        """Unité d'analyse rapide pour les tâches immédiates."""
        events = EchoEvents(__event_emitter__)
        await events.status("⚡ Analyse Rapide...")
        
        persona = "Tu es l'unité d'analyse rapide d'ECHO. Sois concis et direct."
        res_text = await _execute_intel_request(self.valves, self.auth, query, context, persona, self.valves.FAST_MODEL, self.valves.FAST_THINKING, None, __user__, events)
        
        clean_text, thoughts = split_thought_process(res_text)
        await events.status("Analyse terminée.", done=True)
        multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
        return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)
