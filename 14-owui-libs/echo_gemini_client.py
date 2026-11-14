# -*- coding: utf-8 -*-
"""
title: ECHO Echo Gemini Client
author: Wilfried BARNAVON
version: 5.239.17.1
description: Client API LLM principal.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.17.1: Timeouts HTTP/2 granulaires via build_httpx_timeout (600s Non-Stream / 300s Stream) et extraction du délai serveur Retry-After / google.rpc.RetryInfo (_extract_server_retry_delay) sur congestion 429/503.
# 5.239.16.1: Garde anti-ping-pong tried_urls sur Fast-Failover OAuth2 dans call et stream, résolution de _get_global_client intra-boucle et alignement sur ECHO_LLM_CALL_TIMEOUT (240s).
# 5.237.11.1: Prise en charge du paramètre override_thinking dans call_distillation.
# 5.236.0.2: Activation inconditionnelle et fallback SSOT des crédits Google One AI en OAuth2.
# 5.235.5.1: Indexation Session RAG factorisée via build_session_rag_point_id et injection systématique de message_id pour source conversation.
import copy
import re
import time
import random
import hashlib
import uuid
import orjson as json
import orjson as std_json
import asyncio
from datetime import datetime, timedelta, timezone
import httpx
import pybase64 as base64
from typing import Any, AsyncGenerator, Dict, List, Literal, Optional, Type, Union
from pydantic import BaseModel
from echo_auth import EchoAuth
from echo_events import EchoEvents
from echo_state_manager import EchoStateManager
from echo_http import _get_global_client, FatalAPIError
from echo_core import clamp_model, split_thought_process
from echo_protocol import get_ca_model_id, build_ca_generation_config, normalize_generation_config, normalize_response_schema
from echo_constants import AUTH_DATA_PROJECT_ID, AUTH_METHOD_OAUTH2, ECHO_AGY_USER_AGENT, ECHO_API_KEY_RETRIES, ECHO_API_MAX_RETRIES, ECHO_ENDPOINT_LOCK_TIMEOUT_MIN, ECHO_GLOBAL_TENANT_PROJECT_ID, ECHO_LLM_CALL_TIMEOUT, ECHO_LLM_STREAM_TIMEOUT, ECHO_RETRY_BASE_DELAY, ECHO_RETRY_JITTER_MAX, ECHO_RETRY_JITTER_MIN, ECHO_RETRY_MULTIPLIER, ECHO_RETRY_SERVER_MAX_WAIT_SEC, ECHO_SAFETY_SETTINGS, ECHO_USER_AGENT, GOOGLE_API_BASE_URL, GOOGLE_OAUTH_TOKEN_LIFETIME, build_httpx_timeout, get_descending_cascade, get_generation_config

class EchoGeminiClient:
    """Moteur factorisé pour les appels API Gemini avec Architecture Symétrique (AI Studio & Code Assist)."""

    _MEM_STREAM_BUFFERS: Dict[str, bytes] = {}

    @staticmethod
    def _extract_server_retry_delay(headers: Any = None, error_text: str = "") -> Optional[float]:
        """Extrait le délai d'attente demandé par le serveur (Retry-After ou google.rpc.RetryInfo), borné à ECHO_RETRY_SERVER_MAX_WAIT_SEC."""
        delay_sec: Optional[float] = None
        if headers:
            ra = headers.get("retry-after") or headers.get("Retry-After")
            if ra:
                try:
                    delay_sec = float(str(ra).strip())
                except ValueError:
                    pass
        if delay_sec is None and error_text:
            m = re.search(r'"retryDelay"\s*:\s*"([0-9.]+)(ms|s)"', error_text)
            if m:
                val = float(m.group(1))
                delay_sec = (val / 1000.0) if m.group(2) == "ms" else val
        if delay_sec is not None and delay_sec > 0:
            return min(delay_sec, float(ECHO_RETRY_SERVER_MAX_WAIT_SEC))
        return None

    @classmethod
    def register_memory_stream(cls, raw_bytes: bytes) -> str:
        """Enregistre un segment binaire en mémoire vive et retourne son marqueur léger (zéro copie lors de copy.deepcopy)."""
        mem_id = uuid.uuid4().hex
        cls._MEM_STREAM_BUFFERS[mem_id] = raw_bytes
        return f"___ECHO_STREAM_MEM___{mem_id}___"

    @classmethod
    def release_memory_stream(cls, marker_or_id: str) -> None:
        """Libère immédiatement le buffer mémoire associé au marqueur."""
        if not marker_or_id:
            return
        clean_id = marker_or_id.replace("___ECHO_STREAM_MEM___", "").replace("___", "")
        cls._MEM_STREAM_BUFFERS.pop(clean_id, None)

    @staticmethod
    async def _zero_ram_memory_generator(raw_bytes: bytes, json_prefix: bytes, json_suffix: bytes):
        yield json_prefix
        chunk_size = 3 * 1024 * 1024  # Multiple strict de 3 requis pour B64 sans padding interne
        mv = memoryview(raw_bytes)
        total_len = len(raw_bytes)
        for offset in range(0, total_len, chunk_size):
            chunk = bytes(mv[offset:offset + chunk_size])
            b64_chunk = await asyncio.to_thread(base64.b64encode, chunk)
            yield b64_chunk
        yield json_suffix

    @staticmethod
    async def _zero_ram_stream_generator(filepath: str, json_prefix: bytes, json_suffix: bytes):
        yield json_prefix
        chunk_size = 3 * 1024 * 1024  # Multiple strict de 3 requis pour B64 sans padding interne
        import asyncio
        
        has_aiofiles = False
        try:
            import aiofiles
            has_aiofiles = True
        except ImportError:
            pass

        if has_aiofiles:
            async with aiofiles.open(filepath, "rb") as f:
                while True:
                    chunk = await f.read(chunk_size)
                    if not chunk: break
                    b64_chunk = await asyncio.to_thread(base64.b64encode, chunk)
                    yield b64_chunk
        else:
            with open(filepath, "rb") as f:
                while True:
                    # Empêcher le blocage de l'Event Loop principal (crucial pour SQLite)
                    chunk = await asyncio.to_thread(f.read, chunk_size)
                    if not chunk: break
                    # L'encodage B64 est CPU-bound, on peut aussi l'offload si nécessaire
                    b64_chunk = await asyncio.to_thread(base64.b64encode, chunk)
                    yield b64_chunk
        yield json_suffix

    @staticmethod
    def _prepare_zero_ram_content(payload: dict):
        """Détecte le Hook Porteur (Fichier ou Mémoire) dans le JSON et prépare le Stream HTTPX. Retourne (generator, content_length)."""
        import json, re, os
        payload_str = json.dumps(payload)
        match = re.search(r'("data":\s*)"___ECHO_STREAM_(FILE|MEM)___(.*?)___"', payload_str)
        if match:
            mode = match.group(2)
            target = json.loads('"' + match.group(3) + '"')
            parts = payload_str.split(match.group(0), 1)
            if len(parts) != 2:
                raise ValueError("Erreur inattendue de découpage du Hook Zéro-RAM.")
            prefix = (parts[0] + match.group(1) + '"').encode('utf-8')
            suffix = ('"' + parts[1]).encode('utf-8')
            
            if mode == "MEM":
                raw_bytes = EchoGeminiClient._MEM_STREAM_BUFFERS.get(target)
                if raw_bytes is None:
                    raise ValueError(f"Buffer mémoire introuvable ou expiré : {target}")
                raw_size = len(raw_bytes)
                b64_size = ((raw_size + 2) // 3) * 4
                content_length = len(prefix) + b64_size + len(suffix)
                return EchoGeminiClient._zero_ram_memory_generator(raw_bytes, prefix, suffix), str(content_length)
            else:
                # Calcul mathématique exact du Content-Length sur fichier
                file_size = os.path.getsize(target)
                b64_size = ((file_size + 2) // 3) * 4
                content_length = len(prefix) + b64_size + len(suffix)
                return EchoGeminiClient._zero_ram_stream_generator(target, prefix, suffix), str(content_length)
        return None, None

    @staticmethod
    async def _get_auth_headers(provider: Dict, is_agy: bool = False, is_generation: bool = False) -> Dict[str, str]:
        """Génère les en-têtes d'authentification selon le type de fournisseur (Agnostique)."""
        if is_agy:
            ua = ECHO_AGY_USER_AGENT
        else:
            ua = ECHO_USER_AGENT

        headers = {
            "Content-Type":       "application/json",
            "User-Agent":         ua,
        }
        p_type = provider.get("type")

        if p_type == AUTH_METHOD_OAUTH2:
            token = provider.get("token") # Priorité au token direct fourni
            if not token:
                uid = provider.get("user_id")
                auth = EchoAuth(user_id=uid)
                token = auth.get_auth_data("google_oauth2_access_token", uid)
                last_refresh = float(auth.get_auth_data("google_oauth2_last_refresh", uid) or 0)

                # Rafraîchissement proactif si le jeton est expiré et qu'un refresh_token est présent
                refresh_token = provider.get("refresh_token")
                if refresh_token and (not token or (time.time() - last_refresh) > GOOGLE_OAUTH_TOKEN_LIFETIME):
                    token = await auth.refresh_google_oauth_token(refresh_token, uid)

            if token:
                headers["Authorization"] = f"Bearer {token}"
                # Pour Code Assist Generation, le project est dans le payload JSON.
                # L'ajout du header x-goog-user-project peut provoquer une erreur 403 Forbidden.
                if not is_agy or not is_generation:
                    project_id = provider.get("project_id")
                    if not project_id:
                         project_id = (EchoAuth(user_id=provider.get("user_id")).get_auth_data(AUTH_DATA_PROJECT_ID) if provider.get("user_id") else None)
                    
                    if project_id:
                        headers["x-goog-user-project"] = ECHO_GLOBAL_TENANT_PROJECT_ID
            else:
                raise Exception("Échec de récupération du jeton d'accès (OAuth2).")
        else:
            headers["x-goog-api-key"] = provider.get("key")

        return headers

    @staticmethod
    def _normalize_contents_for_api(raw_contents: list) -> list:
        """Normalise en camelCase (inlineData / mimeType) sans dupliquer les buffers Base64 en RAM."""
        if not raw_contents:
            return []
        normalized = []
        for msg in raw_contents:
            if not isinstance(msg, dict):
                normalized.append(msg)
                continue
            new_parts = []
            for part in msg.get("parts", []):
                if not isinstance(part, dict):
                    new_parts.append(part)
                    continue
                if "inline_data" in part:
                    new_part = {k: v for k, v in part.items() if k != "inline_data"}
                    d = part["inline_data"]
                    new_part["inlineData"] = {
                        "mimeType": d.get("mime_type") or d.get("mimeType", "application/octet-stream"),
                        "data": d.get("data", ""),
                    }
                    new_parts.append(new_part)
                elif "inlineData" in part and isinstance(part["inlineData"], dict) and "mime_type" in part["inlineData"]:
                    new_part = dict(part)
                    d = part["inlineData"]
                    new_part["inlineData"] = {
                        "mimeType": d.get("mime_type") or d.get("mimeType", "application/octet-stream"),
                        "data": d.get("data", ""),
                    }
                    new_parts.append(new_part)
                else:
                    new_parts.append(part)
            normalized.append({**msg, "parts": new_parts})
        return normalized

    @staticmethod
    async def _prepare_request_context(
        provider: Dict,
        target_model: str,
        payload: Dict,
        method: str = "generateContent",
        chat_id: str = None,
        enable_paid_credits: bool = False,
        base_url: str = None,
        gen_config_override: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """
        Sélecteur de Protocole Symétrique : Prépare URL, Headers et Payload selon le backend.
        Retourne un dictionnaire de configuration ou None si le fournisseur est invalide.
        """
        # --- CAS 0 : BOUCLIER UNIVERSEL DES OUTILS (ALLOWLIST VIA CONSTANTES) ---
        from echo_constants import GEMINI_ALLOWED_SCHEMA_KEYS
        
        def clean_gemini_schema(schema: dict):
            if not isinstance(schema, dict): return
            keys_to_remove = [k for k in schema.keys() if k not in GEMINI_ALLOWED_SCHEMA_KEYS]
            for k in keys_to_remove: schema.pop(k, None)
            if "properties" in schema and isinstance(schema["properties"], dict):
                for v in schema["properties"].values(): clean_gemini_schema(v)
            if "items" in schema and isinstance(schema["items"], dict):
                clean_gemini_schema(schema["items"])

        if "tools" in payload and isinstance(payload["tools"], list):
            for t in payload["tools"]:
                for fn in (t.get("functionDeclarations") or t.get("function_declarations") or []):
                    if "parameters" in fn:
                        clean_gemini_schema(fn["parameters"])

        p_type = provider.get("type")
        is_agy = (p_type == AUTH_METHOD_OAUTH2)
        is_generation = method in ["generateContent", "streamGenerateContent", "embedContent"]
        headers = await EchoGeminiClient._get_auth_headers(provider, is_agy=is_agy, is_generation=is_generation)

        # --- CAS 1 & 2 : RÉSOLUTION DU MODÈLE TECHNIQUE VIA LE SSOT ---
        from echo_constants import ECHO_MODELS_REGISTRY
        import copy as _copy
        model_config = ECHO_MODELS_REGISTRY.get(target_model)
        if model_config:
            target_model = model_config["ca_model_id"] if is_agy else model_config["ai_studio_id"]

        effective_gen_conf = _copy.deepcopy(
            payload.get("generationConfig")
            or (model_config or {}).get("generationConfig", {})
        )
        if gen_config_override:
            effective_gen_conf.update(_copy.deepcopy(gen_config_override))

        # --- CAS 1 : PROTOCOLE API ANTIGRAVITY (OAuth2) ---
        if is_agy:
            if not model_config:
                target_model = get_ca_model_id(target_model)

            project_id = provider.get("project_id")

            if not project_id:
                return None

            api_url = f"{base_url}:{method}"
            if method == "streamGenerateContent":
                api_url += "?alt=sse"

            prompt_id = "agy-echo"
            try:
                # Extraction intelligente du texte pour l'ID de prompt
                if "contents" in payload:
                    first_msg = payload.get("contents", [{}])[0].get("parts", [{}])[0].get("text", "")
                elif "content" in payload:
                    first_msg = payload.get("content", {}).get("parts", [{}])[0].get("text", "")
                else: first_msg = ""

                if first_msg:
                    prompt_id = f"agy-{hashlib.sha256(first_msg.encode()).hexdigest()[:16]}"
            except: pass

            # ENCAPSULATION DU PAYLOAD (Code Assist Strict)
            if method == "embedContent":
                request_body = {
                    "content": payload.get("content", {}),
                    "sessionId": chat_id
                }
            else:
                # Harmonisation tool_config (owui) -> toolConfig (API)
                t_conf = payload.get("toolConfig") or payload.get("tool_config")

                # Adaptation generationConfig → protocole Code Assist (echo_protocol.py).
                # Depuis echo_protocol v1.1 : thinkingConfig passe à travers (CA l'accepte).
                # Cap maxOutputTokens à MAX_TOKENS_DEFAULT (65535 — corrige le 400 prod Gemini 3.1).
                gen_conf = build_ca_generation_config(effective_gen_conf)

                request_body = {
                    "contents": EchoGeminiClient._normalize_contents_for_api(payload.get("contents", [])),
                    "systemInstruction": payload.get("systemInstruction"),
                    "generationConfig": gen_conf,
                    "tools": payload.get("tools"),
                    "toolConfig": t_conf,
                    "safetySettings": payload.get("safetySettings", ECHO_SAFETY_SETTINGS),
                    "sessionId": chat_id
                }
                
                # Suppression des valeurs None (L'API Code Assist rejette les nulls explicites)
                request_body = {k: v for k, v in request_body.items() if v is not None}

            wrapped_payload = {
                "model": target_model,
                "project": ECHO_GLOBAL_TENANT_PROJECT_ID,
                "user_prompt_id": prompt_id, # Correction : snake_case requis ici
                "request": request_body,
                "userAgent": "antigravity",
                "requestType": (model_config or {}).get("request_type", "agent")
            }

            # CRÉDITS AI — opt-in via UserValve pipe ou préférence persistée identity.db
            is_credits_allowed = enable_paid_credits
            if not is_credits_allowed and provider.get("user_id"):
                try:
                    _state = EchoStateManager(user_id=provider["user_id"])
                    raw_val = _state.get_setting("enable_paid_credits")
                    is_credits_allowed = str(raw_val).strip().lower() in ("true", "1", "yes")
                except Exception:
                    pass

            if is_credits_allowed:
                # Le forfait régulier est toujours débité en premier par Google.
                # enabledCreditTypes n'est consommé qu'en cas d'épuisement du quota.
                wrapped_payload["enabledCreditTypes"] = ["GOOGLE_ONE_AI"]

            return {"url": api_url, "headers": headers, "payload": wrapped_payload}

        # --- CAS 2 : PROTOCOLE AI STUDIO (API Key) ---
        else:
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:{method}"
            if method == "streamGenerateContent":
                api_url += "?alt=sse"

            # Harmonisation stricte pour l'API publique AI Studio
            t_conf = payload.get("toolConfig") or payload.get("tool_config")
            
            contents = EchoGeminiClient._normalize_contents_for_api(payload.get("contents", []))
            
            request_body = {
                "contents": contents,
                "systemInstruction": payload.get("systemInstruction"),
                "generationConfig": normalize_generation_config(effective_gen_conf),
                "tools": payload.get("tools"),
                "toolConfig": t_conf,
                "safetySettings": payload.get("safetySettings", ECHO_SAFETY_SETTINGS),
            }

            request_body = {k: v for k, v in request_body.items() if v is not None}

            return {"url": api_url, "headers": headers, "payload": request_body}

    @staticmethod
    async def generate_embedding(
        text: str, 
        task_type: Literal["query", "document"], 
        __user__: dict, 
        __metadata__: dict, 
        title: str = None,
        max_retries: int = 1
    ) -> List[float]:
        """
        Génère un vecteur d'embedding via Harrier-OSS (Local).
        Zéro donnée sortante (infrastructure auto-hébergée).
        Retry intégré (1 retry, 2s backoff) pour absorber les indisponibilités transitoires.
        """
        from echo_constants import MODEL_EMBEDDING, ECHO_EMBEDDING_URL
        
        payload = {
            "model": MODEL_EMBEDDING,
            "input": text
        }
        
        headers = {}
        if __user__ and "id" in __user__:
            headers["X-OpenWebUI-User-Id"] = str(__user__["id"])
        
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                client = await _get_global_client()
                resp = await client.post(f"{ECHO_EMBEDDING_URL}/embeddings", headers=headers, json=payload, timeout=300.0)
                
                if resp.status_code == 200:
                    data = resp.json()
                    embeddings = data.get("data", [])
                    if embeddings:
                        return embeddings[0].get("embedding", [])
                    # Réponse 200 mais pas d'embeddings → pas de retry (réponse valide)
                    return []
                else:
                    last_error = f"HTTP {resp.status_code} - {resp.text[:100]}"
                    print(f"[EchoGemini] ⚠️ Échec Embedding Local : {last_error}")
            except Exception as e:
                last_error = str(e)
                print(f"[EchoGemini] ⚠️ Embedding Local tentative {attempt + 1}/{max_retries + 1} : {last_error}")
            
            # Retry avec backoff (sauf dernière tentative)
            if attempt < max_retries:
                await asyncio.sleep(2 * (attempt + 1))
        
        print(f"[EchoGemini] ❌ Embedding Local échoué après {max_retries + 1} tentatives : {last_error}")
        return []

    @staticmethod
    async def call_distillation(
        prompt: str, 
        __user__: dict, 
        __metadata__: dict, 
        is_json: bool = True,
        parts: Optional[List[Dict]] = None,
        max_tokens: int = 65535,  # 65535. Surchargeable : ex. 8192 (RAG), 2048 (brief).
        target_model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        timeout: int = 120,
        response_schema: Optional[Union[Type[BaseModel], dict]] = None,
        override_thinking: Optional[str] = None,
    ) -> Union[Dict, str]:
        """
        Exécute une tâche de distillation (extraction sémantique).
        Route exclusivement vers l'API Gemini (MODEL_DISTILLATION) suite à la rationalisation (suppression Gemma).
        """
        from echo_constants import MODEL_DISTILLATION, ECHO_MODELS_REGISTRY
        import copy
        
        user_id = (__user__ or {}).get("id", "system")
        chat_id = (__metadata__ or {}).get("chat_id")
        
        # Résolution du modèle
        actual_model = target_model if target_model else MODEL_DISTILLATION

        # 1. Préparation du contenu
        contents = parts if parts else [{"role": "user", "parts": [{"text": prompt}]}]

        # --- ROUTAGE API ---
        base_gen = copy.deepcopy(ECHO_MODELS_REGISTRY.get(actual_model, ECHO_MODELS_REGISTRY.get("MODEL_ZEPHYR", {})).get("generationConfig", {}))
        base_gen["maxOutputTokens"] = max_tokens
        if override_thinking:
            if "thinkingConfig" not in base_gen:
                base_gen["thinkingConfig"] = {}
            base_gen["thinkingConfig"]["thinkingLevel"] = override_thinking
        
        payload = {
            "contents": contents,
            "generationConfig": base_gen
        }
        
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
            
        if response_schema is not None:
            is_json = True
            payload["generationConfig"]["responseSchema"] = normalize_response_schema(response_schema)
        if is_json:
            payload["generationConfig"]["responseMimeType"] = "application/json"

        try:
            data = await EchoGeminiClient.call(
                actual_model, payload, user_id, timeout=timeout, chat_id=chat_id
            )

            # Extraction et Nettoyage (exclusion stricte des parts de pensée native Gemini)
            target = data.get("response", {}) if "response" in data else data
            candidates = target.get("candidates", [])
            if not candidates:
                return {} if is_json else ""

            parts_list = candidates[0].get("content", {}).get("parts", [])
            full_text = "".join([p.get("text", "") for p in parts_list if not p.get("thought")])
            clean_text, _ = split_thought_process(full_text)

            if is_json:
                if isinstance(response_schema, type) and issubclass(response_schema, BaseModel):
                    return response_schema.model_validate_json(clean_text).model_dump()
                return std_json.loads(clean_text)
            return clean_text
        except Exception as e:
            print(f"[EchoGeminiClient.call_distillation] Error ({actual_model}): {e}", flush=True)
            return {} if is_json else ""

    @staticmethod
    async def index_text_in_ephemeral_rag(
        distillate: str,
        source_id: str,
        uid: str,
        chat_id: str,
        __user__: dict,
        __metadata__: dict,
        max_chunk: Optional[int] = None,
        timeout: int = 180,
        unique_seed: Optional[str] = None
    ) -> tuple:
        """
        Factorise le pipeline chunk → embed → upsert Qdrant pour la Mémoire Vectorisée de Session.

        Découpe `distillate` (markdown structuré) en chunks sémantiques (~400 tokens Harrier-OSS)
        basés sur les séparateurs de paragraphes \\n\\n, avec recouvrement entre chunks contigus
        pour éviter la perte de contexte aux jointures.

        Retourne (nb_points_indexés: int, message_erreur: str).
        0 points indique un échec total (embedding worker indisponible ou Qdrant KO).
        """
        from echo_constants import (
            COLLECTION_SESSION_RAG,
            EMBEDDING_DIM,
            ECHO_QDRANT_URL,
            ECHO_SESSION_RAG_CHUNK_SIZE,
            SESSION_RAG_CONVERSATION_SOURCE_ID,
        )
        from echo_session_gc import build_session_rag_point_id
        qdrant_base = ECHO_QDRANT_URL
        
        if max_chunk is None:
            max_chunk = ECHO_SESSION_RAG_CHUNK_SIZE

        # --- 1. CHUNKING PAR PARAGRAPHES SÉMANTIQUES ---
        raw_paragraphs = [p.strip() for p in distillate.split("\n\n") if p.strip()]

        merged = []
        current = ""
        for para in raw_paragraphs:
            if len(current) + len(para) + 2 <= max_chunk:
                current = (current + "\n\n" + para).strip() if current else para
            else:
                if current:
                    merged.append(current)
                # Paragraphe supra-limite : découpe sur frontière de phrase
                if len(para) > max_chunk:
                    for sentence in para.replace(". ", ".\n").replace("! ", "!\n").replace("? ", "?\n").split("\n"):
                        sentence = sentence.strip()
                        if not sentence:
                            continue
                        if len(current) + len(sentence) + 1 <= max_chunk:
                            current = (current + " " + sentence).strip() if current else sentence
                        else:
                            if current:
                                merged.append(current)
                            if len(sentence) > max_chunk:
                                merged.append(sentence[:max_chunk])
                                current = sentence[max_chunk:]
                            else:
                                current = sentence
                else:
                    current = para
        if current:
            merged.append(current)

        # --- 2. RECOUVREMENT (évite la perte de contexte aux jointures) ---
        if len(merged) > 1:
            overlapped = [merged[0]]
            for i in range(1, len(merged)):
                prev_last = merged[i - 1][-150:]
                overlap_text = prev_last + "\n\n" + merged[i]
                if len(overlap_text) > max_chunk * 1.5:
                    overlap_text = overlap_text[:int(max_chunk * 1.5)]
                overlapped.append(overlap_text)
            chunks = overlapped
        else:
            chunks = merged if merged else [distillate[:max_chunk]]

        # --- 3. CRÉATION CONDITIONNELLE DE LA COLLECTION ---
        points = []
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                coll_check = await client.get(f"{qdrant_base}/collections/{COLLECTION_SESSION_RAG}")
                if coll_check.status_code == 404:
                    cr = await client.put(
                        f"{qdrant_base}/collections/{COLLECTION_SESSION_RAG}",
                        json={"vectors": {"size": EMBEDDING_DIM, "distance": "Cosine"}}
                    )
                    if cr.status_code not in (200, 201):
                        return 0, f"Échec création collection Qdrant : HTTP {cr.status_code}"

                # --- 4. GÉNÉRATION DES EMBEDDINGS ET CONSTRUCTION DES POINTS ---
                from echo_constants import ECHO_SUBAGENT_CONTEXT
                sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
                active_sub_sid = sub_ctx.get("sub_sid") if (sub_ctx and sub_ctx.get("is_subagent")) else None
                for i, chunk in enumerate(chunks):
                    vector = await EchoGeminiClient.generate_embedding(
                        chunk, "document", __user__, __metadata__, title=source_id
                    )
                    if vector:
                        point_id = build_session_rag_point_id(
                            uid,
                            chat_id,
                            source_id,
                            i,
                            unique_seed=unique_seed,
                            sub_sid=active_sub_sid,
                        )
                        
                        payload_data = {
                            "user_id": uid,
                            "chat_id": chat_id,
                            "source_id": source_id,
                            "text": chunk,
                            "timestamp": int(time.time())
                        }
                        if active_sub_sid:
                            payload_data["sub_sid"] = active_sub_sid
                        if unique_seed and source_id == SESSION_RAG_CONVERSATION_SOURCE_ID:
                            payload_data["message_id"] = str(unique_seed)

                        points.append({
                            "id": point_id,
                            "vector": vector,
                            "payload": payload_data
                        })

                if not points:
                    return 0, "Aucun embedding généré (worker d'embedding indisponible ?)"

                # --- 5. UPSERT ADDITIF (non destructif) ---
                upsert_resp = await client.put(
                    f"{qdrant_base}/collections/{COLLECTION_SESSION_RAG}/points",
                    json={"points": points}
                )
                if upsert_resp.status_code not in (200, 206):
                    return 0, f"Erreur Qdrant upsert : HTTP {upsert_resp.status_code} — {upsert_resp.text[:100]}"

        except Exception as e:
            return 0, str(e)

        return len(points), ""

    @staticmethod
    async def call_raw(target_model: str, payload: dict, user_id: str, method: str = "generateContent", chat_id: str = None) -> dict:
        """Méthode bas niveau pour les appels non-content (Embed, CountTokens)."""
        auth = EchoAuth(user_id=user_id)
        auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)
        client = await _get_global_client()
        
        for provider in auth_providers:
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, method, chat_id=chat_id)
                if not req_ctx: continue

                resp = await client.post(req_ctx["url"], json=req_ctx["payload"], headers=req_ctx["headers"], timeout=60)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("response", data)
                
                print(f"[EchoGemini] ⚠️ Échec provider {provider.get('type')} : HTTP {resp.status_code} - {resp.text[:200]}")
                if resp.status_code in [403, 404]: continue # Failover
                resp.raise_for_status()
            except Exception as e: 
                print(f"[EchoGemini] ⚠️ Exception sur provider {provider.get('type')} : {str(e)}")
                continue
        raise Exception(f"Aucune identité d'accès fonctionnelle après {len(auth_providers)} tentatives ({method}).")

    @staticmethod
    async def call(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = ECHO_LLM_CALL_TIMEOUT,
        chat_id: str = None,
        enable_paid_credits: bool = False,
        gen_config_override: Optional[Dict] = None,
    ) -> dict:
        # Résolution des identités d'accès aux modèles pour cet utilisateur
        if not auth_providers:
            auth = EchoAuth(user_id=user_id)
            auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)

        if not auth_providers: raise ValueError(f"Aucune identité d'accès aux modèles configurée pour l'utilisateur {user_id}.")
        active_idx = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        state_mgr = EchoStateManager(user_id=user_id)
        current_url_idx, base_url = state_mgr.get_agy_endpoint()
        tried_urls = {current_url_idx}

        attempt = 0
        while attempt <= max_retries:
            client = await _get_global_client()
            provider = auth_providers[active_idx]

            # Préparation symétrique de la requête
            # MESSAGE_SHADOWS: Injection de contexte persistant (RAG-lite)
            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(
                    provider,
                    target_model,
                    payload,
                    "generateContent",
                    chat_id=chat_id,
                    enable_paid_credits=enable_paid_credits,
                    base_url=base_url,
                    gen_config_override=gen_config_override,
                )
                if not req_ctx:
                    # Fail-over immédiat si configuration incomplète (ex: Project ID manquant)
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        continue
                    else: raise Exception(f"Configuration d'authentification {provider['type']} invalide (Project ID manquant).")

                stream_content, content_length = EchoGeminiClient._prepare_zero_ram_content(req_ctx["payload"])
                if stream_content:
                    if content_length:
                        req_ctx["headers"]["Content-Length"] = content_length
                    request = client.build_request(
                        "POST", req_ctx["url"], headers=req_ctx["headers"],
                        content=stream_content, timeout=build_httpx_timeout(timeout)
                    )
                    resp = await client.send(request)
                else:
                    resp = await client.post(req_ctx["url"], json=req_ctx["payload"], headers=req_ctx["headers"], timeout=build_httpx_timeout(timeout))

                if resp.status_code == 200:
                    # Déverrouillage proactif sur succès (Auto-Heal)
                    if provider.get("type") == AUTH_METHOD_OAUTH2:
                        state_mgr.unlock_agy_endpoint(current_url_idx)
                    json_data = resp.json()
                    # NORMALISATION : Déballage automatique de l'enveloppe Code Assist (OAuth2)
                    return json_data.get("response", json_data)

                # --- FAIL-FAST SUR ERREUR SYNTAXE ---
                if resp.status_code == 400:
                    raise FatalAPIError(f"Erreur 400 (Bad Request) - Payload rejeté par l'API: {resp.text}")

                # --- BASCULEMENT IMMÉDIAT (DROITS/DISPO) ---
                if resp.status_code in [401, 403, 404]:
                    if active_idx < len(auth_providers) - 1:
                        print(f"[ECHO-RETRY] (Non-Stream) Modèle indisponible/401 sur {provider['type']}. Bascule provider...", flush=True)      
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        current_delay = ECHO_RETRY_BASE_DELAY
                        continue

                if resp.status_code in [420, 429, 500, 503]:
                    # 1. --- FAST-FAILOVER INTRA-RETRY (OAuth2 uniquement) ---
                    if provider.get("type") == AUTH_METHOD_OAUTH2:
                        # Stratégie Agnostique : Verrou court fixe pour toutes les erreurs 429/50x
                        reset_time = (datetime.now(timezone.utc) + timedelta(minutes=ECHO_ENDPOINT_LOCK_TIMEOUT_MIN)).isoformat()
                        
                        # Verrouillage de l'URL actuelle
                        state_mgr.lock_agy_endpoint(current_url_idx, reset_time)
                        new_idx, new_url = state_mgr.get_agy_endpoint()
                        
                        # Si on obtient une nouvelle URL non encore essayée, on bascule immédiatement
                        if new_idx != current_url_idx and new_idx not in tried_urls:
                            print(f"[ECHO-RETRY] (Non-Stream) Surcharge ({resp.status_code}). Verrouillage endpoint et bascule immédiate...", flush=True)
                            current_url_idx = new_idx
                            base_url = new_url
                            tried_urls.add(new_idx)
                            continue

                    # Détermination de la limite de tentatives (Backoff max vs Switch rapide)
                    current_limit = max_retries if provider.get("type") == AUTH_METHOD_OAUTH2 else threshold

                    # 2. --- BACKOFF CLASSIQUE ---
                    if attempt < current_limit:
                        wait_msg = f"⚠️ Surcharge API ({resp.status_code})."
                        if resp.status_code in [420, 429]:
                            wait_msg = f"⏳ Limite de débit API ({resp.status_code})."

                        wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                        server_delay = EchoGeminiClient._extract_server_retry_delay(resp.headers, resp.text)
                        if server_delay:
                            wait_time = max(wait_time, server_delay)
                        print(f"[ECHO-RETRY] (Non-Stream) {wait_msg} Essai {attempt + 1}/{current_limit} dans {wait_time:.1f}s...", flush=True)
                        await asyncio.sleep(wait_time)
                        current_delay *= ECHO_RETRY_MULTIPLIER
                        attempt += 1
                        continue

                    # 3. --- CHANGEMENT DE PROVIDER (Si Backoff épuisé) ---
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        current_delay = ECHO_RETRY_BASE_DELAY
                        if events: await events.status(f"🔄 Surcharge source {provider['type']}. Bascule sur la suivante...", done=False)
                        continue

                resp.raise_for_status()
            except FatalAPIError as fe:
                raise fe
            except Exception as e:
                if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)) and provider.get("type") == AUTH_METHOD_OAUTH2:
                    reset_time = (datetime.now(timezone.utc) + timedelta(minutes=ECHO_ENDPOINT_LOCK_TIMEOUT_MIN)).isoformat()
                    state_mgr.lock_agy_endpoint(current_url_idx, reset_time)
                    new_idx, new_url = state_mgr.get_agy_endpoint()
                    if new_idx != current_url_idx and new_idx not in tried_urls:
                        print(f"[ECHO-RETRY] (Non-Stream) Timeout ({e.__class__.__name__}) sur {target_model}. Verrouillage endpoint et bascule immédiate...", flush=True)
                        current_url_idx = new_idx
                        base_url = new_url
                        tried_urls.add(new_idx)
                        continue

                current_limit = max_retries if provider.get("type") == AUTH_METHOD_OAUTH2 else threshold
                if attempt < current_limit:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    print(f"[EchoGemini] ⚠️ Tentative {attempt + 1}/{current_limit} échouée pour {target_model} : {e.__class__.__name__} - {str(e)}")
                    if events: await events.status(f"⛓️💥 Reconnexion au modèle, tentative {attempt + 1}/{current_limit} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    attempt += 1
                    continue
                else:
                    # 3. --- CHANGEMENT DE PROVIDER AUSSI EN CAS D'ERREUR RÉSEAU FATALE ---
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        current_delay = ECHO_RETRY_BASE_DELAY
                        if events: await events.status(f"🔄 Erreur fatale sur source {provider['type']}. Bascule sur la suivante...", done=False)
                        continue
                raise e
        raise Exception("Échec après épuisement total.")

    @staticmethod
    async def call_cascade(
        target_model_key: str,
        payload: dict,
        user_id: str,
        metadata: dict = None,
        events: Optional[EchoEvents] = None,
        threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        timeout: int = ECHO_LLM_CALL_TIMEOUT,
        chat_id: str = None,
        include_thoughts: bool = False,
        enable_paid_credits: bool = False,
        response_schema: Optional[Union[Type[BaseModel], dict]] = None,
    ) -> tuple:
        """
        Appel LLM avec cascade descendante centralisée.
        Gère : clamping (politique Pipe), thinkingConfig auto, cascade sur 429/503, toast, crédits.
        Retourne (response_data, model_key_used, reason).
        reason est None si nominal, sinon chaîne expliquant la divergence modèle.
        """
        # 1. Clamping via politique Pipe (lecture __metadata__ → fallback identity.db)
        clamped_key = clamp_model(target_model_key, metadata, user_id=user_id)

        # Résolution de l'autorisation des crédits : paramètre > metadata > identity.db
        resolved_paid_credits = enable_paid_credits
        if not resolved_paid_credits and metadata and isinstance(metadata, dict):
            resolved_paid_credits = bool(metadata.get("_echo_enable_paid_credits", False))
        if not resolved_paid_credits and user_id:
            try:
                _state = EchoStateManager(user_id=user_id)
                raw_val = _state.get_setting("enable_paid_credits")
                resolved_paid_credits = str(raw_val).strip().lower() in ("true", "1", "yes")
            except Exception:
                pass

        reason = None  # Tracker de raison pour le warning

        # Notification si le modèle a été ajusté par la politique
        if clamped_key != target_model_key:
            reason = f"{target_model_key} unavailable (policy)"
            if events:
                await events.status(
                    f"🔒 Politique Pipe : {target_model_key} → {clamped_key}", done=False
                )

        # 2. Construction cascade descendante depuis le modèle clampé
        cascade = get_descending_cascade(clamped_key, include_start=True)

        # Préservation des surcharges explicites passées par l'appelant dans payload["generationConfig"]
        caller_gen_cfg = copy.deepcopy(payload.get("generationConfig")) if isinstance(payload.get("generationConfig"), dict) else {}
        caller_thinking = (caller_gen_cfg.get("thinkingConfig") or {}).get("thinkingLevel")
        extra_gen_keys = {
            k: v for k, v in caller_gen_cfg.items()
            if k not in ("temperature", "topP", "topK", "thinkingConfig")
        }
        if response_schema is not None:
            extra_gen_keys["responseSchema"] = normalize_response_schema(response_schema)
            extra_gen_keys["responseMimeType"] = "application/json"

        # 3. Tentatives en cascade
        for model_key in cascade:
            actual_model = model_key
            gen_cfg = get_generation_config(
                model_key,
                override_thinking=caller_thinking,
                is_fallback_cascade=(model_key != clamped_key),
                include_thoughts=include_thoughts,
            )
            if extra_gen_keys:
                gen_cfg.update(copy.deepcopy(extra_gen_keys))
            payload["generationConfig"] = gen_cfg

            try:
                data = await EchoGeminiClient.call(
                    target_model=actual_model,
                    payload=payload,
                    user_id=user_id,
                    threshold=threshold,
                    max_retries=max_retries,
                    events=events,
                    timeout=timeout,
                    chat_id=chat_id,
                    enable_paid_credits=resolved_paid_credits,
                )
                # Confirmation du modèle effectif (visible dans le status)
                if model_key != clamped_key:
                    # Cascade descendante technique (429/503)
                    reason = f"{clamped_key} unavailable (API error)"
                    if events:
                        await events.status(
                            f"⚡ Cascade : {clamped_key} → {model_key} (fallback)", done=False
                        )
                return data, model_key, reason

            except Exception as e:
                err_msg = str(e)[:60]
                if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)):
                    err_msg = "Allocated time elapsed (Timeout)"
                elif not err_msg:
                    err_msg = e.__class__.__name__

                # Toast warning sur erreur technique (tous modes)
                if events:
                    await events.toast(
                        f"⚠️ {model_key} indisponible — repli automatique", "warning"
                    )
                    await events.status(
                        f"⚠️ {model_key} ({err_msg}). Repli...", done=False
                    )
                continue

        # Tous les modèles ont échoué — signal final
        if events:
            await events.toast(
                "❌ Cascade épuisée — aucun modèle disponible", "error"
            )
            await events.status(
                f"❌ Cascade épuisée : {clamped_key} → aucun modèle disponible.", done=True
            )
        return None, None, f"{clamped_key} unavailable (cascade exhausted)"

    @staticmethod
    async def stream(
        target_model: str,
        payload: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = ECHO_LLM_STREAM_TIMEOUT,
        chat_id: str = None,
        enable_paid_credits: bool = False,
    ) -> AsyncGenerator[Union[str, Dict], None]:
        # Résolution des identités d'accès aux modèles (si non fourni par le pipe)
        if not auth_providers:
            auth = EchoAuth(user_id=user_id)
            auth_providers = await auth.get_ordered_auth_providers(user_id=user_id)

        if not auth_providers:
            raise ValueError(f"🚫 Aucune identité d'accès aux modèles disponible pour l'utilisateur {user_id}.")

        active_idx = 0
        current_delay = ECHO_RETRY_BASE_DELAY

        state_mgr = EchoStateManager(user_id=user_id)
        current_url_idx, base_url = state_mgr.get_agy_endpoint()
        tried_urls = {current_url_idx}

        attempt = 0
        while attempt <= max_retries:
            client = await _get_global_client()
            provider = auth_providers[active_idx]

            try:
                req_ctx = await EchoGeminiClient._prepare_request_context(provider, target_model, payload, "streamGenerateContent", chat_id=chat_id, enable_paid_credits=enable_paid_credits, base_url=base_url)
                if not req_ctx:
                    if events: await events.status(f"⚠️ Config incomplète pour {provider['type']}. Bascule...", done=False)
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        continue
                    else: yield f"🚫 Erreur : Configuration d'authentification {provider['type']} invalide (Project ID manquant)."; return

                async with client.stream("POST", req_ctx["url"], content=json.dumps(req_ctx["payload"]), headers=req_ctx["headers"], timeout=build_httpx_timeout(timeout)) as r:     
                    # --- FAIL-FAST SUR ERREUR SYNTAXE ---
                    if r.status_code == 400:
                        body = await r.aread()
                        raise FatalAPIError(f"Erreur 400 (Bad Request) - Payload rejeté par l'API: {body.decode('utf-8')}")

                    # --- BASCULEMENT IMMÉDIAT (DROITS/DISPO) ---
                    if r.status_code in [401, 403, 404]:
                        if active_idx < len(auth_providers) - 1:
                            print(f"[ECHO-RETRY] (Stream) Modèle indisponible/401 sur {provider['type']}. Bascule provider...", flush=True)  
                            active_idx += 1
                            tried_urls = {current_url_idx}
                            attempt = 0
                            current_delay = ECHO_RETRY_BASE_DELAY
                            continue

                    if r.status_code in [420, 429, 500, 503]:
                        # 1. --- FAST-FAILOVER INTRA-RETRY (OAuth2 uniquement) ---
                        if provider.get("type") == AUTH_METHOD_OAUTH2:
                            # Stratégie Agnostique : Verrou court fixe pour toutes les erreurs 429/50x
                            reset_time = (datetime.now(timezone.utc) + timedelta(minutes=ECHO_ENDPOINT_LOCK_TIMEOUT_MIN)).isoformat()
                            
                            # Verrouillage de l'URL actuelle
                            state_mgr.lock_agy_endpoint(current_url_idx, reset_time)
                            new_idx, new_url = state_mgr.get_agy_endpoint()
                            
                            # Si on obtient une nouvelle URL non encore essayée, on bascule immédiatement
                            if new_idx != current_url_idx and new_idx not in tried_urls:
                                print(f"[ECHO-RETRY] (Stream) Surcharge ({r.status_code}). Verrouillage endpoint et bascule immédiate...", flush=True)
                                current_url_idx = new_idx
                                base_url = new_url
                                tried_urls.add(new_idx)
                                continue

                        # Détermination de la limite de tentatives (Backoff max vs Switch rapide)
                        current_limit = max_retries if provider.get("type") == AUTH_METHOD_OAUTH2 else threshold

                        # 2. --- BACKOFF CLASSIQUE ---
                        if attempt < current_limit:
                            wait_msg = f"⚠️ Surcharge API ({r.status_code})."
                            if r.status_code in [420, 429]:
                                wait_msg = f"⏳ Limite de débit API ({r.status_code})."

                            wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                            err_text = ""
                            try:
                                err_bytes = await r.aread()
                                err_text = err_bytes.decode("utf-8", errors="ignore")
                            except Exception:
                                pass
                            server_delay = EchoGeminiClient._extract_server_retry_delay(r.headers, err_text)
                            if server_delay:
                                wait_time = max(wait_time, server_delay)
                            print(f"[ECHO-RETRY] (Stream) {wait_msg} Essai {attempt + 1}/{current_limit} dans {wait_time:.1f}s...", flush=True)
                            await asyncio.sleep(wait_time)
                            current_delay *= ECHO_RETRY_MULTIPLIER
                            attempt += 1
                            continue

                        # 3. --- CHANGEMENT DE PROVIDER (Si Backoff épuisé) ---
                        if active_idx < len(auth_providers) - 1:
                            active_idx += 1
                            tried_urls = {current_url_idx}
                            attempt = 0
                            current_delay = ECHO_RETRY_BASE_DELAY
                            if events: await events.status(f"🔄 Surcharge source {provider['type']}. Bascule sur la suivante...", done=False)
                            continue

                    r.raise_for_status()

                    # Déverrouillage proactif sur succès (Auto-Heal)
                    if provider.get("type") == AUTH_METHOD_OAUTH2:
                        state_mgr.unlock_agy_endpoint(current_url_idx)

                    if process_callback:
                        try:
                            async for chunk in process_callback(r): yield chunk
                        except asyncio.CancelledError:
                            # Annulation uvicorn (shutdown container) — doit se propager.
                            print("[EchoGemini] ⚠️ Tâche SSE annulée (shutdown). Re-propagation.")
                            raise
                    else:
                        # Processeur par défaut pour les outils (Extraction Texte et Objets JSON)
                        buffer = ""
                        import codecs
                        decoder = codecs.getincrementaldecoder("utf-8")(errors="ignore")
                        buffered_lines = []
                        try:
                            async for chunk in r.aiter_bytes():
                                buffer += decoder.decode(chunk, final=False)
                                while "\n" in buffer:
                                    line, buffer = buffer.split("\n", 1)
                                    line = line.strip()

                                    if line.startswith("data: "):
                                        buffered_lines.append(line[6:].strip())
                                    elif line == "" and buffered_lines:
                                        # Fin d'un bloc SSE : accumulation et parsing du JSON complet
                                        full_json_str = "\n".join(buffered_lines)
                                        try:
                                            data = json.loads(full_json_str)
                                            yield data
                                        except: pass
                                        buffered_lines = []
                        except asyncio.CancelledError:
                            print("[EchoGemini] ⚠️ Tâche SSE annulée (shutdown). Re-propagation.")
                            raise
                    break
            except FatalAPIError as fe:
                raise fe
            except Exception as e:
                if isinstance(e, (httpx.TimeoutException, asyncio.TimeoutError)) and provider.get("type") == AUTH_METHOD_OAUTH2:
                    reset_time = (datetime.now(timezone.utc) + timedelta(minutes=ECHO_ENDPOINT_LOCK_TIMEOUT_MIN)).isoformat()
                    state_mgr.lock_agy_endpoint(current_url_idx, reset_time)
                    new_idx, new_url = state_mgr.get_agy_endpoint()
                    if new_idx != current_url_idx and new_idx not in tried_urls:
                        print(f"[ECHO-RETRY] (Stream) Timeout ({e.__class__.__name__}) sur {target_model}. Verrouillage endpoint et bascule immédiate...", flush=True)
                        current_url_idx = new_idx
                        base_url = new_url
                        tried_urls.add(new_idx)
                        continue

                current_limit = max_retries if provider.get("type") == AUTH_METHOD_OAUTH2 else threshold
                if attempt < current_limit:
                    wait_time = current_delay * random.uniform(ECHO_RETRY_JITTER_MIN, ECHO_RETRY_JITTER_MAX)
                    if events: await events.status(f"⛓️💥 Reconnexion au modèle, tentative {attempt + 1}/{current_limit} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= ECHO_RETRY_MULTIPLIER
                    attempt += 1
                    continue
                else: 
                    # 3. --- CHANGEMENT DE PROVIDER AUSSI EN CAS D'ERREUR RÉSEAU FATALE ---
                    if active_idx < len(auth_providers) - 1:
                        active_idx += 1
                        tried_urls = {current_url_idx}
                        attempt = 0
                        current_delay = ECHO_RETRY_BASE_DELAY
                        if events: await events.status(f"🔄 Erreur fatale sur source {provider['type']}. Bascule sur la suivante...", done=False)
                        continue
                    
                    # Remplacement du 'yield' par un 'raise' pour déclencher la cascade dans le Pipe Engine
                    raise e

    @staticmethod
    async def embed(
        model: str,
        content: dict,
        user_id: str,
        auth_providers: Optional[List[Dict]] = None,
        threshold: int = ECHO_API_KEY_RETRIES,
        max_retries: int = ECHO_API_MAX_RETRIES,
        events: Optional[EchoEvents] = None,
        timeout: int = 30,
        chat_id: str = None
    ) -> dict:
        """Legacy : Reste ici pour compatibilité, mais redirige vers call_raw."""
        return await EchoGeminiClient.call_raw(model, {"content": content}, user_id, method="embedContent", chat_id=chat_id)

