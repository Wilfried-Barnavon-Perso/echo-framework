# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO In-Process Audio Bridge
author: ECHO Framework
version: 6.21.7.1
description: Pont audio in-process (STT Gemini MODEL_ZEPHYR & TTS Edge Vivienne).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.7.1: Factorisation de sniff_audio_mime_type et ECHO_AUDIO_STT_THINKING_LEVEL depuis echo_constants.
# 6.6.14.1: Prise en compte du paramètre optionnel language (ISO-639-1) dans la consigne STT de /transcriptions.
# 6.6.9.1: Durcissement de resolve_user_id (JWT valide ou loopback strict, sinon HTTP 401) sur /transcriptions, /speech, /models et /voices.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.4: Intégration du paramètre pitch (-2Hz) et rétablissement de la vitesse nominale 1.0.

import os
import sys
from typing import Optional, Dict, Any, AsyncGenerator

from fastapi import APIRouter, Request, Response, UploadFile, File, Form, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse

try:
    import pybase64 as base64
except ImportError:
    import base64

try:
    import edge_tts
except ImportError:
    edge_tts = None

from echo_constants import (
    ECHO_AUDIO_PREFIX,
    ECHO_AUDIO_DEFAULT_TTS_VOICE,
    ECHO_AUDIO_DEFAULT_TTS_SPEED,
    ECHO_AUDIO_DEFAULT_TTS_PITCH,
    ECHO_AUDIO_DEFAULT_STT_MODEL,
    ECHO_AUDIO_STT_THINKING_LEVEL,
    ECHO_AUDIO_MAX_UPLOAD_SIZE,
    ECHO_USERS_ROOT,
    sniff_audio_mime_type,
)
from echo_prompts import USR_AUDIO_TRANSCRIBE
from echo_gemini_client import EchoGeminiClient

# Routeur dédié au pont audio ECHO
audio_router = APIRouter(prefix=f"{ECHO_AUDIO_PREFIX}/audio", tags=["echo_audio"])


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _is_loopback_request(request: Optional[Request]) -> bool:
    """Vérifie si la requête provient explicitement de l'hôte loopback local."""
    if request is None or request.client is None:
        return False
    host = (request.client.host or "").strip().lower()
    return host in _LOOPBACK_HOSTS


async def resolve_user_id(request: Optional[Request] = None) -> str:
    """Résout et authentifie l'identifiant utilisateur via JWT ou loopback interne OWUI."""
    if request is not None:
        token = ""
        auth_header = request.headers.get("authorization") or ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip().strip('"')
        if not token:
            token = (request.cookies.get("token") or "").strip().strip('"')

        if token and not token.startswith("sk-"):
            try:
                from open_webui.utils.auth import decode_token, is_valid_token
                decoded = decode_token(token)
                redis_client = getattr(getattr(request.app, "state", None), "redis", None)
                if isinstance(decoded, dict) and decoded.get("id") and await is_valid_token(decoded, redis_client):
                    return str(decoded["id"])
            except ImportError:
                pass
            except Exception:
                pass

    if not _is_loopback_request(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    if request is not None:
        for header_name in ["x-openwebui-user-id", "x-webui-user", "x-user-id", "user-id"]:
            val = request.headers.get(header_name)
            if val and val.strip():
                return val.strip()

    try:
        if os.path.exists(ECHO_USERS_ROOT):
            candidates = [
                d for d in os.listdir(ECHO_USERS_ROOT)
                if os.path.isdir(os.path.join(ECHO_USERS_ROOT, d)) and not d.startswith(".")
            ]
            for candidate in candidates:
                if os.path.exists(os.path.join(ECHO_USERS_ROOT, candidate, "identity.db")):
                    return candidate
            if candidates:
                return candidates[0]
    except Exception:
        pass

    return "system"


def speed_to_rate(speed: float) -> str:
    """Convertit un multiplicateur numérique (ex: 1.25) en pourcentage Edge TTS (ex: '+25%')."""
    try:
        pct = int(round((float(speed) - 1.0) * 100))
        return f"{pct:+d}%"
    except Exception:
        return "+0%"


def extract_text_from_gemini_response(response: Dict[str, Any]) -> str:
    """Extrait le texte transcrit de la réponse multimodale Gemini en filtrant le thinking."""
    try:
        candidates = response.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = []
        for part in parts:
            if "text" in part and not part.get("thought", False):
                text_parts.append(part["text"])
        return "".join(text_parts).strip()
    except Exception:
        return ""


@audio_router.post("/transcriptions")
async def transcribe_audio(
    request: Request,
    file: UploadFile = File(...),
    model: Optional[str] = Form(ECHO_AUDIO_DEFAULT_STT_MODEL),
    language: Optional[str] = Form(None),
) -> JSONResponse:
    """Endpoint STT compatible OpenAI relayé vers Gemini MODEL_ZEPHYR."""
    audio_bytes = await file.read()
    if not audio_bytes or len(audio_bytes) < 100:
        return JSONResponse(content={"text": ""})

    if len(audio_bytes) > ECHO_AUDIO_MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=413,
            detail=f"Fichier audio trop volumineux ({len(audio_bytes)} octets > max {ECHO_AUDIO_MAX_UPLOAD_SIZE})."
        )

    mime_type = sniff_audio_mime_type(audio_bytes)
    b64_audio = base64.b64encode(audio_bytes).decode("utf-8")
    user_id = await resolve_user_id(request)
    target_model = model or ECHO_AUDIO_DEFAULT_STT_MODEL
    lang_hint = (language or "").strip()
    stt_prompt = (
        f"{USR_AUDIO_TRANSCRIBE}\nLangue attendue de l'audio (code ISO) : {lang_hint}."
        if lang_hint else USR_AUDIO_TRANSCRIBE
    )

    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {
                        "inlineData": {
                            "mimeType": mime_type,
                            "data": b64_audio,
                        }
                    },
                    {
                        "text": stt_prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 8192,
            "thinkingConfig": {
                "thinkingLevel": ECHO_AUDIO_STT_THINKING_LEVEL
            }
        }
    }

    try:
        gemini_resp = await EchoGeminiClient.call(
            target_model=target_model,
            payload=payload,
            user_id=user_id,
            timeout=60,
        )
        transcribed_text = extract_text_from_gemini_response(gemini_resp)
        return JSONResponse(content={"text": transcribed_text})
    except Exception as e:
        print(f"[ECHO Audio] Erreur transcription STT Gemini : {e}")
        # Tolérance de panne : renvoie une chaîne vide plutôt qu'un crash de l'interface vocale
        return JSONResponse(content={"text": ""})


@audio_router.post("/speech")
async def text_to_speech(request: Request) -> Response:
    """Endpoint TTS compatible OpenAI relayé vers Microsoft Edge Read-Aloud."""
    await resolve_user_id(request)
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Payload JSON invalide.")

    text = str(body.get("input", "")).strip()
    if not text:
        # Buffer MP3 vide silencieux
        return Response(content=b"", media_type="audio/mpeg")

    voice = str(body.get("voice", "")).strip() or ECHO_AUDIO_DEFAULT_TTS_VOICE
    speed = float(body.get("speed", ECHO_AUDIO_DEFAULT_TTS_SPEED))
    pitch = str(body.get("pitch", ECHO_AUDIO_DEFAULT_TTS_PITCH)).strip() or ECHO_AUDIO_DEFAULT_TTS_PITCH
    rate = speed_to_rate(speed)

    if edge_tts is None:
        raise HTTPException(status_code=500, detail="Bibliothèque edge-tts non installée.")

    try:
        communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)

        async def stream_generator() -> AsyncGenerator[bytes, None]:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    yield chunk["data"]

        return StreamingResponse(stream_generator(), media_type="audio/mpeg")
    except Exception as e:
        print(f"[ECHO Audio] Erreur synthèse TTS Edge : {e}")
        raise HTTPException(status_code=502, detail=f"Erreur synthèse Edge TTS : {e}")


@audio_router.get("/models")
async def list_audio_models(request: Request) -> JSONResponse:
    """Liste les modèles STT et TTS disponibles."""
    await resolve_user_id(request)
    return JSONResponse(
        content={
            "models": [
                {"id": "edge-tts", "name": "Microsoft Edge TTS (Vivienne Multilingual)"},
                {"id": ECHO_AUDIO_DEFAULT_STT_MODEL, "name": "ECHO Gemini Transcriber (Zephyr)"},
            ]
        }
    )


@audio_router.get("/voices")
async def list_audio_voices(request: Request) -> JSONResponse:
    """Liste les voix Edge TTS recommandées pour Open WebUI."""
    await resolve_user_id(request)
    return JSONResponse(
        content={
            "voices": [
                {"id": "fr-FR-VivienneMultilingualNeural", "name": "Vivienne (Français - Naturelle)"},
                {"id": "fr-FR-RemyMultilingualNeural", "name": "Remy (Français - Naturel)"},
                {"id": "fr-FR-DeniseNeural", "name": "Denise (Français)"},
                {"id": "fr-FR-HenriNeural", "name": "Henri (Français)"},
                {"id": "en-US-AvaMultilingualNeural", "name": "Ava (English - Natural)"},
                {"id": "en-US-AndrewMultilingualNeural", "name": "Andrew (English - Natural)"},
            ]
        }
    )


def register_echo_audio_routes() -> None:
    """Injecte de manière idempotente les routes audio in-process en tête du routeur Open WebUI."""
    try:
        if "open_webui.main" not in sys.modules:
            try:
                import open_webui.main as owui_main
            except ImportError:
                return
        else:
            import open_webui.main as owui_main

        app = getattr(owui_main, "app", None)
        if not app or not hasattr(app, "router"):
            return

        # 1. Purge idempotente des anciennes routes /echo/v1/audio
        filtered_routes = [
            r for r in app.router.routes
            if not getattr(r, "path", "").startswith(f"{ECHO_AUDIO_PREFIX}/audio")
        ]
        app.router.routes = filtered_routes

        # 2. Insertion chirurgicale en position 0 pour devancer le catch-all SPAStaticFiles
        for route in reversed(audio_router.routes):
            app.router.routes.insert(0, route)

        print("[ECHO Audio] Pont In-Process audio (STT Gemini & TTS Edge) enregistré avec succès.")
    except Exception as e:
        print(f"[ECHO Audio] Avertissement: Impossible d'enregistrer les routes audio in-process : {e}")
