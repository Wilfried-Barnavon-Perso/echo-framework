"""
title: ECHO Cognitive Core
author: ECHO Framework
version: 3.1
description: 3.1: Robust Payload Protocol (STRING payloads for creative freedom).
"""

import sys
import json
import httpx
import asyncio
from typing import Optional, Literal, Any
from pydantic import BaseModel, Field

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents
from echo_constants import ECHO_USER_AGENT, GOOGLE_SSE_URL

async def _execute_intel_request(valves, auth, query: str, context: str, persona: str, model: str, thinking: str, schema: dict, __user__: dict, events: EchoEvents) -> str:
    """Moteur interne asynchrone vers l'API Gemini (Cloud Code Endpoint)."""
    if not __user__ or "id" not in __user__: return json.dumps({"error": "User ID missing"})
    
    token, project_id = auth.get_credentials(__user__["id"])
    if not token or not project_id: return json.dumps({"error": "Google Auth or Project ID missing"})

    await events.status(f"🧠 ECHO Intel ({model})...")

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
    payload = {
        "model": model,
        "project": project_id,
        "request": {
            "contents": [{"role": "user", "parts": [{"text": f"CONTEXT:\n{context}\n\nREQUEST:\n{query}"}]}],
            "systemInstruction": {"parts": [{"text": f"You are {persona}. Output STRICT JSON."}]},
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": schema,
                "thinkingConfig": {
                    "includeThoughts": False, 
                    "thinkingLevel": thinking
                }
            }
        }
    }

    try:
        async with httpx.AsyncClient(http2=valves.ENABLE_HTTP2, timeout=valves.HTTP_TIMEOUT) as client:
            full_text = ""
            async with client.stream("POST", GOOGLE_SSE_URL, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    error_body = await resp.aread()
                    return json.dumps({"error": f"API Error {resp.status_code}", "details": error_body.decode()})
                
                async for line in resp.aiter_lines():
                    if line.startswith("data:"):
                        try:
                            data = json.loads(line[5:].strip())
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: full_text += parts[0]["text"]
                        except: pass
            
            await events.status(f"🧠 ECHO Intel ({model}) terminée.", done=True)
            return full_text
        
    except httpx.TimeoutException:
        return json.dumps({"error": "Gateway Timeout", "details": f"L'IA n'a pas répondu dans le délai de {valves.HTTP_TIMEOUT}s."})
    except Exception as e:
        return json.dumps({"error": "Exception in Cognitive Core", "details": str(e)})

class Tools:
    class Valves(BaseModel):
        EXPERT_MODEL: str = Field(default="gemini-3.1-pro-preview", description="Modèle pour le raisonnement profond (Expert).")
        EXPERT_THINKING: Literal["LOW", "MEDIUM", "HIGH"] = Field(default="HIGH", description="Niveau de réflexion de l'expert.")
        FAST_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle pour les tâches rapides (Assistant).")
        FAST_THINKING: Literal["MINIMAL", "LOW", "MEDIUM", "HIGH"] = Field(default="MINIMAL", description="Niveau de réflexion de l'assistant.")
        HTTP_TIMEOUT: int = Field(default=180, description="Timeout maximum (secondes) pour la réflexion de l'IA.")
        ENABLE_HTTP2: bool = Field(default=True, description="Activer HTTP/2 pour plus d'efficience.")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()

    async def consult_expert(
        self, 
        query: str, 
        context: str, 
        expert_persona: str, 
        include_reasoning: bool = False,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Invoque un Expert de Niveau 1 (Raisonnement Profond). Réponse EXCLUSIVEMENT en JSON.
        À utiliser pour : Analyses complexes, stratégie, programmation avancée, audit.
        
        VOTRE MISSION (Modèle Mandant) :
        1. Délégation pure par défaut (Silencieux). 
        2. Activer 'include_reasoning' pour le raisonnement du sous-agent (consommation de tokens augmentée) sinon désactivé.
        3. Le champ 'expert_payload' est une chaîne de caractères (STRING) pouvant contenir du Markdown riche.

        :param query: Requête d'analyse riche. Le résultat sera placé dans 'expert_payload'.
        :param context: Contexte complet et structuré.
        :param expert_persona: Rôle précis de l'expert.
        :param include_reasoning: Activer pour le raisonnement du sous-agent (consommation de tokens augmentée) sinon désactivé.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        # Métadonnées Sanctuarisées
        properties = {
            "expert_metadata": {
                "type": "OBJECT",
                "properties": {
                    "persona_active": {"type": "STRING"},
                    "confidence_score": {"type": "NUMBER"},
                    "uncertainty_factors": {"type": "ARRAY", "items": {"type": "STRING"}}
                },
                "required": ["persona_active", "confidence_score", "uncertainty_factors"]
            }
        }
        required = ["expert_metadata", "expert_payload"]
        
        # Schéma Elastique pour le Raisonnement
        if include_reasoning:
            properties["expert_logic"] = {
                "type": "OBJECT",
                "properties": {
                    "thought_process_summary": {"type": "STRING"},
                    "key_assumptions": {"type": "ARRAY", "items": {"type": "STRING"}}
                },
                "required": ["thought_process_summary", "key_assumptions"]
            }
            required.append("expert_logic")
            
        # Option A : Payload en STRING pour une robustesse maximale
        properties["expert_payload"] = {"type": "STRING"}
        
        schema = {
            "type": "OBJECT",
            "properties": properties,
            "required": required
        }
        
        return await _execute_intel_request(self.valves, self.auth, query, context, expert_persona, self.valves.EXPERT_MODEL, self.valves.EXPERT_THINKING, schema, __user__, events)

    async def fast_check(
        self, 
        query: str, 
        context: str, 
        assistant_persona: str = "Assistant Rapide", 
        include_reasoning: bool = True,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Invoque un Assistant Rapide (Niveau Réflexe). Réponse EXCLUSIVEMENT en JSON.
        À utiliser pour : Vérifications simples, calculs, dates, extractions, reformulations.

        VOTRE MISSION (Modèle Mandant) :
        1. Transparence par défaut pour validation immédiate.
        2. Désactiver 'include_reasoning' pour une réponse chirurgicale ultra-rapide.
        3. Le champ 'fast_payload' est une chaîne de caractères (STRING).

        :param query: Requête de vérification simple. Le résultat sera placé dans 'fast_payload'.
        :param context: Contexte ou extrait minimal.
        :param assistant_persona: Rôle de l'assistant.
        :param include_reasoning: Activer pour le raisonnement du sous-agent (consommation de tokens augmentée) sinon désactivé.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        # Métadonnées Sanctuarisées
        properties = {
            "fast_metadata": {
                "type": "OBJECT",
                "properties": {
                    "status": {"type": "STRING"},
                    "persona_active": {"type": "STRING"}
                },
                "required": ["status", "persona_active"]
            }
        }
        required = ["fast_metadata", "fast_payload"]
        
        # Schéma Elastique pour le Raisonnement
        if include_reasoning:
            properties["fast_logic"] = {
                "type": "OBJECT",
                "properties": {
                    "brief_explanation": {"type": "STRING"}
                },
                "required": ["brief_explanation"]
            }
            required.append("fast_logic")
            
        # Option A : Payload en STRING pour une robustesse maximale
        properties["fast_payload"] = {"type": "STRING"}
        
        schema = {
            "type": "OBJECT",
            "properties": properties,
            "required": required
        }
        
        return await _execute_intel_request(self.valves, self.auth, query, context, assistant_persona, self.valves.FAST_MODEL, self.valves.FAST_THINKING, schema, __user__, events)
