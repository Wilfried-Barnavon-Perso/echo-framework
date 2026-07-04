"""
title: ECHO Session RAG Conversation Filter
author: ECHO Framework
version: 1.6
description: 1.6: Architecture Stateless Zéro RAM (Fenêtre de Tour Dynamique) & Bouclier Regex anti-Base64.
             1.5: Fix UUID collision en RAG éphémère (ajout unique_seed).
             1.4: Extraction propre du texte dans les messages multipart pour empêcher l'embedding de Base64 massif.
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
        self.toggle = True

    def _format_messages(self, messages: list) -> str:
        """Formate les messages en texte brut avec protection Base64."""
        import re
        import datetime
        formatted = []
        b64_pattern = re.compile(r'data:[a-zA-Z0-9/+-.]+;base64,[a-zA-Z0-9+/]+={0,2}')
        
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = [str(p.get("text", "")) for p in content if isinstance(p, dict) and p.get("type") == "text" and p.get("text")]
                content = "\n".join(text_parts)
            else:
                content = str(content)
            
            if content.strip():
                # Bouclier : Purge des payloads Base64 injectés brutalement dans le texte brut
                content = b64_pattern.sub("[CONTENU MÉDIA BASE64 MASQUÉ POUR LE RAG]", content)
                
                timestamp = msg.get("timestamp")
                if timestamp:
                    dt = datetime.datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')
                    formatted.append(f"[{dt}] {role.upper()}:\n{content.strip()}")
                else:
                    formatted.append(f"{role.upper()}:\n{content.strip()}")
        return "\n\n".join(formatted)

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Déclenchement asynchrone Stateless sur Fenêtre de Tour Dynamique."""
        u_v = __user__.get("valves") if __user__ else self.user_valves
        is_enabled = getattr(u_v, "ENABLE_CONVERSATION_RAG", True)
        
        if not is_enabled or not __user__:
            return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        user_id = __user__.get("id")
        
        if not chat_id or not user_id or not messages:
            return body
            
        # Extraction de la Fenêtre Dynamique du Tour Courant
        last_user_idx = len(messages) - 1
        while last_user_idx >= 0:
            if messages[last_user_idx].get("role") == "user":
                break
            last_user_idx -= 1
            
        if last_user_idx < 0:
            last_user_idx = 0
            
        current_turn_messages = messages[last_user_idx:]
        
        # --- DEBOUNCE SÉMANTIQUE (Économie CPU) ---
        # Si le dernier message n'est pas l'assistant final (ex: c'est un outil en cours, 
        # ou un assistant qui appelle un outil), on ignore l'indexation pour ne pas saturer le CPU.
        last_msg = current_turn_messages[-1]
        is_turn_finished = (
            last_msg.get("role") == "assistant" and 
            not last_msg.get("tool_calls")
        )
        
        if not is_turn_finished:
            return body
            
        # Le formatage agrège tout le tour dans une seule bulle de contexte pour le RAG
        formatted_text = self._format_messages(current_turn_messages)
        
        # Upsert Idempotent sur le message initial du tour
        # (L'écrasement écrase l'ancien passage du filtre s'il y a des outils asynchrones)
        unique_seed = current_turn_messages[0].get("id") or str(current_turn_messages[0].get("timestamp"))
        
        # Injection asynchrone Zéro-Latence via echo-embedding (bge-m3)
        asyncio.create_task(
            EchoGeminiClient.index_text_in_ephemeral_rag(
                distillate=formatted_text,
                source_id=SESSION_RAG_CONVERSATION_SOURCE_ID,
                uid=user_id,
                chat_id=chat_id,
                __user__=__user__,
                __metadata__=__metadata__,
                unique_seed=str(unique_seed) if unique_seed else None
            )
        )

        return body
