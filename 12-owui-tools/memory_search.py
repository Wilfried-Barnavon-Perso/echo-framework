"""
title: ECHO Memory Search
author: ECHO Framework
version: 1.4
description: 1.4: Using restored get_google_token for RAG embedding.
"""

import sys
import requests
from typing import Optional, Any

sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoAuth, EchoEvents
except ImportError:
    class EchoAuth:
        def get_google_token(self, uid): return None
    class EchoEvents:
        def __init__(self, e=None, c=None): pass
        async def status(self, d, done=False): pass

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
    ) -> str:
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__: return "❌ Erreur User."

        await events.status(f"📚 ECHO Memory : {query}...")
        token = self.auth.get_google_token(__user__["id"])
        if not token: return "❌ Erreur Auth."

        # Embedding
        url = "https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        try:
            resp = requests.post(url, headers=headers, json={
                "model": "models/text-embedding-004",
                "content": {"parts": [{"text": query}]}
            }, timeout=10)
            vector = resp.json()["embedding"]["values"]
        except: return "❌ Erreur Vectorisation."

        # Qdrant
        try:
            resp = requests.post(f"{QDRANT_URL}/collections/{COLLECTION}/points/search", json={
                "vector": vector, "limit": 5, "with_payload": True, "score_threshold": 0.4
            }, timeout=5)
            results = resp.json().get("result", [])
        except Exception as e: return f"❌ Erreur Qdrant: {str(e)}"

        if not results: return "Aucun résultat."
        
        md = "📚 **Mémoire :**\n"
        for hit in results:
            md += f"- **{hit['payload'].get('source','?')}** ({hit['score']:.2f})\n"
        return md
