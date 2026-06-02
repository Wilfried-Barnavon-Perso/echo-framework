from fastapi import FastAPI
from fastapi.responses import Response
from pydantic import BaseModel
from kokoro_onnx import Kokoro
import soundfile as sf
import io

app = FastAPI(title="ECHO TTS Worker", description="Kokoro ONNX CPU API")

print("🧠 Loading Kokoro ONNX model on CPU...")
# Chargement optimisé CPU
kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
print("✅ Model loaded successfully.")

class TTSRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str = "ff_siwis" # Voix française par défaut
    response_format: str = "wav"
    speed: float = 1.0

@app.get("/health")
async def health():
    return {"status": "ok", "model": "kokoro"}

# OpenAI Compatible Endpoint
@app.post("/v1/audio/speech")
async def create_speech(req: TTSRequest):
    try:
        # Fallback pour les requêtes de l'interface compatibles OpenAI (qui utilisent souvent "alloy")
        voice_id = req.voice
        if not voice_id or voice_id in ["alloy", "echo", "fable", "onyx", "nova", "shimmer"]:
            voice_id = "ff_siwis"
            
        # Protection contre les entrées vides (test de l'interface)
        if not req.input or not req.input.strip():
            print("[TTS] ⚠️ Entrée texte vide, renvoi d'un audio vide.")
            wav_io = io.BytesIO()
            sf.write(wav_io, [0]*16000, 24000, format='WAV') # 1s de silence
            wav_io.seek(0)
            return Response(content=wav_io.read(), media_type="audio/wav")
            
        print(f"[TTS] 🗣️ Génération pour '{req.input[:30]}...' avec la voix {voice_id}")
        
        # Génération audio
        samples, sample_rate = kokoro.create(
            req.input, voice=voice_id, speed=req.speed, lang="fr-fr"
        )
        
        # Conversion WAV en mémoire
        wav_io = io.BytesIO()
        sf.write(wav_io, samples, sample_rate, format='WAV')
        wav_io.seek(0)
        
        print("[TTS] ✅ Génération réussie.")
        return Response(content=wav_io.read(), media_type="audio/wav")
    
    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"[TTS] ❌ Erreur interne : {err_msg}")
        traceback.print_exc()
        return Response(content=f'{{"error": "{err_msg}"}}', status_code=500, media_type="application/json")
