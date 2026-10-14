# -*- coding: utf-8 -*-
"""
title: ECHO In-Process Audio Bridge
author: ECHO Framework
version: 1.4
description: Pont audio in-process (STT Gemini MODEL_ZEPHYR & TTS Edge Vivienne).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.4: Intégration du paramètre pitch (-2Hz) et rétablissement de la vitesse nominale 1.0.
# 1.3: Ajustement de la vitesse d'élocution TTS par défaut à 1.06 (106%).
# 1.2: Ajustement de la vitesse d'élocution TTS par défaut à 1.1 (110%).
# 1.1: Restauration température STT par défaut, maxOutputTokens=8192, thinkingLevel=minimal et vitesse 1.25.
# 1.0: Implémentation initiale du pont in-process (STT Gemini MODEL_ZEPHYR & TTS Edge Vivienne).

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
    ECHO_AUDIO_MAX_UPLOAD_SIZE,
    ECHO_USERS_ROOT,
)
from echo_prompts import USR_AUDIO_TRANSCRIBE
from echo_gemini_client import EchoGeminiClient

# Routeur dédié au pont audio ECHO
audio_router = APIRouter(prefix=f"{ECHO_AUDIO_PREFIX}/audio", tags=["echo_audio"])


def sniff_audio_mime_type(data: bytes) -> str:
    """Détecte avec précision le conteneur audio via ses magic bytes."""
    if not data or len(data) < 4:
        return "audio/webm"

    # 1. WebM / Matroska (EBML ID: 1A 45 DF A3)
    if data.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"

    # 2. WAV / RIFF (RIFF .... WAVE)
    if data.startswith(b"RIFF") and len(data) >= 12 and data[8:12] == b"WAVE":
        return "audio/wav"

    # 3. OGG (OggS)
    if data.startswith(b"OggS"):
        return "audio/ogg"

    # 4. FLAC (fLaC)
    if data.startswith(b"fLaC"):
        return "audio/flac"

    # 5. MP3 (ID3v2 tag ou frame sync FF FB / FF F3 / FF F2)
    if data.startswith(b"ID3"):
        return "audio/mpeg"
    if len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0:
        return "audio/mpeg"

    # 6. MP4 / M4A (ftyp à l'offset 4)
    if len(data) >= 8 and data[4:8] == b"ftyp":
        return "audio/mp4"

    # Fallback par défaut adapté au flux audio WebRTC d'Open WebUI
    return "audio/webm"


def resolve_user_id(request: Optional[Request] = None) -> str:
    """Résout de manière sécurisée l'identifiant utilisateur actif."""
    if request:
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
    user_id = resolve_user_id(request)
    target_model = model or ECHO_AUDIO_DEFAULT_STT_MODEL

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
                        "text": USR_AUDIO_TRANSCRIBE
                    }
                ]
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 8192,
            "thinkingConfig": {
                "thinkingLevel": "minimal"
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
async def list_audio_models() -> JSONResponse:
    """Liste les modèles STT et TTS disponibles."""
    return JSONResponse(
        content={
            "models": [
                {"id": "edge-tts", "name": "Microsoft Edge TTS (Vivienne Multilingual)"},
                {"id": ECHO_AUDIO_DEFAULT_STT_MODEL, "name": "ECHO Gemini Transcriber (Zephyr)"},
            ]
        }
    )


@audio_router.get("/voices")
async def list_audio_voices() -> JSONResponse:
    """Liste les voix Edge TTS recommandées pour Open WebUI."""
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
