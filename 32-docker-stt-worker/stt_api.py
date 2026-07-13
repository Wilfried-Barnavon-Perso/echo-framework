"""
================================================================================
MODULE : ECHO STT WORKER API
VERSION : 1.0 (Initialisation)
AUTEUR : Wilfried BARNAVON & ECHO Team
DATE MAJ : 2026-07-05
================================================================================
"""
from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse
from faster_whisper import WhisperModel
import os
import tempfile

app = FastAPI(title="ECHO STT Worker", description="Faster-Whisper CPU optimized API")

# Chargement du modèle "small" (idéal compromis vitesse/qualité sur CPU, multilingue)
# compute_type="int8" permet de diviser la conso RAM par 2 et d'accélérer l'inférence CPU
print("🧠 Loading Faster-Whisper 'small' model on CPU (INT8)...")
model = WhisperModel("small", device="cpu", compute_type="int8")
print("✅ Model loaded successfully.")

@app.get("/health")
async def health():
    return {"status": "ok", "model": "small"}

# OpenAI Compatible Endpoint
@app.post("/v1/audio/transcriptions")
async def create_transcription(
    file: UploadFile = File(...),
    model_name: str = Form("whisper-1", alias="model"),
    language: str = Form(None)
):
    try:
        # Save uploaded file to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
            content = await file.read()
            temp_audio.write(content)
            temp_audio_path = temp_audio.name

        # Transcribe
        # beam_size=5 is default, gives better accuracy.
        segments, info = model.transcribe(temp_audio_path, language=language, beam_size=5)
        
        text = "".join([segment.text for segment in segments])
        
        # Cleanup
        os.remove(temp_audio_path)
        
        return JSONResponse(content={"text": text.strip()})
    
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})
