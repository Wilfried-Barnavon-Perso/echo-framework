"""
title: ECHO Session RAG Conversation Filter
author: ECHO Framework
version: 5.239.13.1
description: Composant système interne : ECHO Session RAG Conversation Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.13.1: Marquage rag_index_state différé au succès effectif de l'upsert Qdrant (_on_rag_indexed) et garde anti-double indexation _RAG_INFLIGHT.
# 5.235.5.1: Clôture d'un tour de parole conditionnée à la présence d'au moins un message assistant lors de messages user consécutifs.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.13: Résolution Fork Assistant : Utilisation de l'ID du message assistant pour garantir l'indexation lors d'une régénération.
# 1.11: Résolution Fork Bomb (Branching) : Itération à rebours O(1) via SQLite is_embedded.

from typing import Optional, Any
from pydantic import BaseModel, Field
import asyncio
import functools
import logging

# Importations ECHO Standard
import sys
sys.path.append("/app/backend/echo_libs")
from echo_gemini_client import EchoGeminiClient
from echo_state_manager import EchoStateManager
from echo_constants import SESSION_RAG_CONVERSATION_SOURCE_ID

logger = logging.getLogger("ECHO-RAG-FILTER")

# Graines de tours en cours d'indexation (clé "chat_id:seed") : évite la double vectorisation
# lorsqu'un outlet suivant survient avant l'aboutissement de l'upsert Qdrant (best-effort par process).
_RAG_INFLIGHT: set = set()

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=100, hidden=True, description="Priorité d'exécution (0 = premier).")
        ENABLE_CONVERSATION_RAG: bool = Field(
            default=True,
            description="Active l'indexation automatique de la conversation dans le Session RAG."
        )

    def __init__(self):
        self.valves = self.Valves()

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

    @staticmethod
    def _on_rag_indexed(task: asyncio.Task, state_mgr: Any, seed: Optional[str], inflight_key: Optional[str]) -> None:
        """Marque le tour comme vectorisé uniquement si l'upsert Qdrant a produit au moins un point."""
        if inflight_key:
            _RAG_INFLIGHT.discard(inflight_key)
        try:
            count, err = task.result()
        except asyncio.CancelledError:
            return
        except Exception as e:
            logger.error(f"Erreur RAG asynchrone (tour {seed}) : {e}")
            return
        if count and count > 0 and seed:
            try:
                state_mgr.mark_message_embedded(seed)
            except Exception as e:
                logger.error(f"Échec marquage rag_index_state (tour {seed}) : {e}")
        else:
            logger.warning(f"Tour {seed} non vectorisé, ré-indexation au prochain outlet : {err}")

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None
    ) -> dict:
        """Déclenchement asynchrone Stateless sur Fenêtre de Tour Dynamique."""
        is_enabled = self.valves.ENABLE_CONVERSATION_RAG
        
        if not is_enabled or not __user__:
            return body

        messages = body.get("messages", [])
        chat_id = (__metadata__ or {}).get("chat_id") or body.get("chat_id")
        user_id = __user__.get("id")
        
        if not chat_id or not user_id or not messages:
            return body
            
        # --- DEBOUNCE SÉMANTIQUE (Économie CPU) ---
        # Si le dernier message n'est pas l'assistant final (ex: c'est un outil en cours, 
        # ou un assistant qui appelle un outil), on ignore l'indexation pour ne pas saturer le CPU.
        last_msg = messages[-1]
        is_turn_finished = (
            last_msg.get("role") == "assistant" and 
            not last_msg.get("tool_calls")
        )
        
        if not is_turn_finished:
            return body
            
        # --- SELF-HEALING RAG ARCHITECTURE ---
        # Instanciation de l'état SQLite pour ce chat_id
        state_mgr = EchoStateManager(user_id=user_id, chat_id=chat_id)
        
        # Découpage en tours de parole (chaque tour commence par un 'user')
        turns = []
        current_turn = []
        for msg in messages:
            if msg.get("role") == "user" and current_turn and any(m.get("role") == "assistant" for m in current_turn):
                turns.append(current_turn)
                current_turn = []
            current_turn.append(msg)
        if current_turn:
            turns.append(current_turn)
            
        # Itération à rebours pour s'arrêter au premier ancêtre vectorisé
        turns_to_process = []
        for turn in reversed(turns):
            if not turn: continue
            # Utilisation de l'ID du message de l'assistant (dernier du tour) pour détecter correctement
            # les forks de régénération (où l'ID du message utilisateur turn[0] ne change pas).
            unique_seed = turn[-1].get("id") or str(turn[-1].get("timestamp"))
            
            # Dès qu'on trouve un message déjà marqué, l'historique antérieur est valide
            if state_mgr.is_message_embedded(str(unique_seed)):
                break
                
            # Sinon, on empile le tour (par le haut pour conserver la chronologie)
            turns_to_process.insert(0, turn)
            
        if not turns_to_process:
            return body
            
        # Marquage SQLite différé au succès effectif de l'upsert Qdrant.
        # Ré-indexation idempotente (ID de point déterministe uuid5(seed, index)).
        for turn in turns_to_process:
            unique_seed = turn[-1].get("id") or str(turn[-1].get("timestamp"))
            seed_str = str(unique_seed) if unique_seed else None
            inflight_key = f"{chat_id}:{seed_str}" if seed_str else None
            if inflight_key and inflight_key in _RAG_INFLIGHT:
                continue
            formatted_text = self._format_messages(turn)
            task = asyncio.create_task(
                EchoGeminiClient.index_text_in_ephemeral_rag(
                    distillate=formatted_text,
                    source_id=SESSION_RAG_CONVERSATION_SOURCE_ID,
                    uid=user_id,
                    chat_id=chat_id,
                    __user__=__user__,
                    __metadata__=__metadata__,
                    unique_seed=seed_str
                )
            )
            if inflight_key:
                _RAG_INFLIGHT.add(inflight_key)
            task.add_done_callback(
                functools.partial(self._on_rag_indexed, state_mgr=state_mgr, seed=seed_str, inflight_key=inflight_key)
            )

        return body
