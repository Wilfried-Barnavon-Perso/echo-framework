"""
title: ECHO Memory Search
author: ECHO Framework
version: 1.5
description: 1.5: Standardized output with wrap_tool_output.
"""

import sys
import requests
from typing import Optional, Any

sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoAuth, EchoEvents, wrap_tool_output
except ImportError:
    class EchoAuth:
        def get_google_token(self, uid): return None
    class EchoEvents:
        def __init__(self, e=None, c=None): pass
        async def status(self, d, done=False): pass
    def wrap_tool_output(text, status=None, echo_tool_multiparts=None): return {"text": text}

QDRANT_URL = "http://echo-qdrant:6333"
COLLECTION = "echo_knowledge"

class Tools:
    def __init__(self):
        self.auth = EchoAuth()

    async def search_knowledge_base(
        self, 
        query: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__: 
            return wrap_tool_output(text="❌ Erreur User.", status={"status": "error"})

        await events.status(f"📚 ECHO Memory : {query}...")
        token = self.auth.get_google_token(__user__["id"])
        if not token: 
            return wrap_tool_output(text="❌ Erreur Auth.", status={"status": "error"})

        # Embedding
        url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json={
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": query}]}
            }, timeout=10)
            vector = resp.json()["embedding"]["values"]
        except: 
            return wrap_tool_output(text="❌ Erreur Vectorisation.", status={"status": "error"})

        # Qdrant
        try:
            resp = requests.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json={
                "vector": vector, "limit": 5, "with_payload": True, "score_threshold": 0.4
            }, timeout=5)
            results = resp.json().get("result", [])
        except Exception as e: 
            return wrap_tool_output(text=f"❌ Erreur Qdrant: {str(e)}", status={"status": "error", "error": str(e)})

        if not results: 
            return wrap_tool_output(text="Aucun résultat.", status={"status": "empty"})
        
        md = "📚 **Mémoire :**\n"
        for hit in results:
            md += f"- **{hit['payload'].get('source','?')}** ({hit['score']:.2f})\n"
        
        return wrap_tool_output(text=md, status={"status": "success", "count": len(results)})
