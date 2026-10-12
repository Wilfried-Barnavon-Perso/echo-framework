"""
================================================================================
MODULE : ECHO STT WORKER API
VERSION : 1.7 (Dépendance Master GitHub)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-10-12

CHANGELOG 1.7 :
- ARCHITECTURE: Remplacement de l'installation PyPI de faster-whisper par la branche master GitHub pour résoudre nativement le crash PyAV (metadata_errors) sans bloquer les versions de dépendances.

CHANGELOG 1.6 :
- REVERT: Retrait du monkey-patch `av.open`. La compatibilité PyAV est désormais gérée proprement dans le Dockerfile (av<12.0.0).

CHANGELOG 1.4 :
- FIX: Ajout de logs explicites pour empêcher le masquage des erreurs internes 500.
- FIX: Sanitization du paramètre `language` (vide -> None) pour prévenir le crash de faster_whisper.
- OPTIM: Déport de l'inférence (générateur) vers `run_in_threadpool` pour libérer l'Event Loop.

CHANGELOG 1.3 :
- FIX: Remplacement de l'itération asynchrone (async for file.file) par une lecture while await file.read(1M) native pour résoudre l'erreur 500.
CHANGELOG 1.2 :
- OPTIM: Passage au modèle whisper 'base'.
- FIX: Streaming chunké pour l'upload (zéro surcharge RAM).
CHANGELOG 1.1 :
- FIX: Ajout d'un filtre de logs limitant l'affichage des requêtes /health (1/5min).
================================================================================
"""
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from fastapi.concurrency import run_in_threadpool
from faster_whisper import WhisperModel
import os
import tempfile
import logging
import time

class RateLimitHealthCheckFilter(logging.Filter):
    def __init__(self, rate_limit_seconds=300):
        super().__init__()
        self.rate_limit_seconds = rate_limit_seconds
        self.last_logged = 0

    def filter(self, record):
        if hasattr(record, 'args') and isinstance(record.args, tuple) and len(record.args) >= 3:
            if record.args[2] in ('/health', '/health/'):
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        try:
            msg = record.getMessage()
            if "GET /health" in msg:
                now = time.time()
                if now - self.last_logged >= self.rate_limit_seconds:
                    self.last_logged = now
                    return True
                return False
        except Exception:
            pass
        return True

logging.getLogger("uvicorn.access").addFilter(RateLimitHealthCheckFilter())

app = FastAPI(title="ECHO STT Worker", description="Faster-Whisper CPU optimized API")

# Chargement du modèle "small" (idéal compromis vitesse/qualité sur CPU, multilingue)
# compute_type="int8" permet de diviser la conso RAM par 2 et d'accélérer l'inférence CPU
print("🧠 Loading Faster-Whisper 'base' model on CPU (INT8)...")
model = WhisperModel("base", device="cpu", compute_type="int8")
print("✅ Model loaded successfully.")

@app.get("/health")
async def health():
    return {"status": "ok", "model": "base"}

# OpenAI Compatible Endpoint
@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model_name: str = Form("whisper-1", alias="model"),
    language: str = Form(None)
):
    try:
        # Récupération de l'extension d'origine proprement
        ext = os.path.splitext(file.filename)[1] if file.filename else ".tmp"
        
        # Save uploaded file to a temporary file via lecture par blocs
        with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp_audio:
            # On lit par chunks de 1 Mo (1024 * 1024 octets) pour ne pas saturer la RAM
            while chunk := await file.read(1024 * 1024):
                temp_audio.write(chunk)
            temp_audio_path = temp_audio.name

        # Transcribe
        # beam_size=5 is default, gives better accuracy.
        if not language:
            language = None
            
        def perform_transcription():
            segments, info = model.transcribe(temp_audio_path, language=language, beam_size=5)
            return "".join([segment.text for segment in segments])
            
        text = await run_in_threadpool(perform_transcription)
        
        # Cleanup
        os.remove(temp_audio_path)
        
        return JSONResponse(content={"text": text.strip()})
    
    except Exception as e:
        logging.exception(f"Erreur critique lors de la transcription STT: {str(e)}")
        return JSONResponse(status_code=500, content={"error": str(e)})
