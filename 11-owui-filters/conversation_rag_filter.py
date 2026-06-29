"""
title: ECHO Session RAG Conversation Filter
author: ECHO Framework
version: 1.4
description: 1.4: Extraction propre du texte dans les messages multipart pour empêcher l'embedding de Base64 massif.
             1.3: Correction de la sécurité d'importation (ajouts systèmes ECHO pour le filtre).
             1.1: Suppression de l'overlap de messages pour éviter la redondance dans le RAG.
             1.0: Filtre Outlet asynchrone pour l'injection sans latence de l'historique conversationnel dans le Session RAG.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field
import asyncio
import time

# Importations ECHO Standard
import sys
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoGeminiClient
from echo_constants import SESSION_RAG_CONVERSATION_SOURCE_ID

class Filter:
    priority: int = 100

    class Valves(BaseModel):
        WINDOW_SIZE: int = Field(
            default=1,
            description="Nombre de messages à attendre avant de déclencher l'indexation de l'historique dans le Session RAG."
        )

    class UserValves(BaseModel):
        ENABLE_CONVERSATION_RAG: bool = Field(
            default=True,
            description="Active l'indexation automatique de la conversation dans le Session RAG."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.message_count = {}
        self.toggle = True

    def _format_messages(self, messages: list) -> str:
        """Formate les messages en texte brut."""
        formatted = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text" and p.get("text")]
                content = "\n".join(text_parts)
            else:
                content = str(content)
            
            if content.strip():
                formatted.append(f"{role.upper()}:\n{content.strip()}")
        return "\n\n".join(formatted)

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Déclenchement déterministe par fenêtre glissante de l'indexation asynchrone."""
        u_v = __user__.get("valves") if __user__ else self.user_valves
        is_enabled = getattr(u_v, "ENABLE_CONVERSATION_RAG", True)
        
        if not is_enabled or not __user__:
            return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        user_id = __user__.get("id")
        
        if not chat_id or not user_id or not messages:
            return body
            
        count = self.message_count.get(chat_id, 0) + 1
        self.message_count[chat_id] = count

        window_size = self.valves.WINDOW_SIZE
        
        if count >= window_size and len(messages) >= window_size:
            self.message_count[chat_id] = 0
            window_msgs = messages[-window_size:]
            
            formatted_text = self._format_messages(window_msgs)
            
            # Injection asynchrone Zéro-Latence via echo-embedding (bge-m3)
            # Pas d'appel LLM, donc exécution quasi-instantanée
            asyncio.create_task(
                EchoGeminiClient.index_text_in_ephemeral_rag(
                    distillate=formatted_text,
                    source_id=SESSION_RAG_CONVERSATION_SOURCE_ID,
                    uid=user_id,
                    chat_id=chat_id,
                    __user__=__user__,
                    __metadata__=__metadata__
                )
            )

        return body
