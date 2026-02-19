"""
title: ECHO Cognitive Delegate
author: ECHO Framework
version: 1.3
description: Délégation cognitive via Gemini 3 (Flash/Pro) et librairie partagée.
"""

import sys
import json
import requests
from typing import Optional

# Import Lib Partagée (Volume Docker)
sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoAuth
except ImportError:
    # Fallback critique uniquement pour éviter crash au chargement si volume absent
    class EchoAuth:
        def get_google_token(self, uid): return None

class Tools:
    def __init__(self):
        self.auth = EchoAuth()

    async def consult_expert(self, query: str, context: str, expert_persona: str, thinking_level: str = "low", __user__: dict = {}) -> str:
        if not __user__ or "id" not in __user__: return "❌ Erreur User."
        
        token = self.auth.get_google_token(__user__["id"])
        if not token: return "❌ Erreur Auth Google."

        # Modèle
        model = "gemini-3-pro-preview" if thinking_level == "high" else "gemini-3-flash-preview"
        
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        payload = {
            "contents": [{"parts": [{"text": f"CONTEXT:\n{context}\n\nREQUEST:\n{query}"}]}],
            "systemInstruction": {"parts": [{"text": f"You are {expert_persona}. Answer strictly based on context."}]},
            "generationConfig": {
                "thinkingConfig": {
                    "includeThoughts": True,
                    "thinkingLevel": thinking_level.upper()
                }
            }
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=120)
            if resp.status_code != 200: return f"❌ API Error: {resp.text}"
            return resp.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            return f"❌ Exception: {str(e)}"
