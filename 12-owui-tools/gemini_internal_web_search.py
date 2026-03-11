"""
title: ECHO Gemini Web Search
author: Wilfried BARNAVON
version: 12.7
description: 12.7: Mutualized thought splitting using echo_utils (Standard <think>).
"""

import json
import os
import httpx
import uuid
import sys
import re
from typing import Optional, Any, Tuple
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, wrap_tool_output, split_thought_process
from echo_constants import ECHO_USER_AGENT

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def search_web(
        self, 
        query: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Recherche en temps réel sur le web via Gemini Search.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"🌐 Recherche Web : {query}...")

        token, project_id = self.auth.get_credentials(__user__.get("id"))
        if not token: return wrap_tool_output(text="❌ Erreur Auth.", status={"status": "error"})

        url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
        
        payload = {
            "model": self.valves.GEMINI_FLASH_MODEL,
            "project": project_id,
            "request": {
                "contents": [{"role": "user", "parts": [{"text": query}]}],
                "tools": [{"googleSearch": {}}],
                "generationConfig": {"thinkingConfig": {"includeThoughts": True}}
            }
        }

        try:
            full_text = ""
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                chunk = json.loads(line[5:].strip())
                                cand = chunk.get("response", {}).get("candidates", [])[0]
                                if "content" in cand:
                                    for p in cand["content"].get("parts", []):
                                        if "text" in p: full_text += p["text"]
                            except: pass

            clean_text, thoughts = split_thought_process(full_text)
            await events.status("Recherche terminée.", done=True)
            multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
            return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Search: {str(e)}", status={"status": "error"})
