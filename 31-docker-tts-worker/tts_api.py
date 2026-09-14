"""
================================================================================
MODULE : ECHO TTS WORKER API
VERSION : 1.5 (Multithreading & Anti-Zombie)
AUTEUR : ECHO Team
DATE MAJ : 2026-09-14

CHANGELOG 1.5 :
- FIX: Isolement de Kokoro dans to_thread pour éviter le blocage de l'Event Loop (Timeout serveur).
- FIX: Annulation explicite de la tâche de génération (Zombie) lors d'une déconnexion HTTP prématurée.
CHANGELOG 1.4 :
CHANGELOG 1.3 :
- FEAT: Intégration de lingua-language-detector pour le G2P franglais.
- FIX: Remplacement de pydub par ffmpeg subprocess pour un vrai streaming MP3.
CHANGELOG 1.2 :
- FEAT: Standardisation de l'en-tête du module.
- FIX: Ajout d'un filtre de logs limitant l'affichage des requêtes /health (1/5min).
================================================================================
"""
from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from kokoro_onnx import Kokoro
import numpy as np
import re
from lingua import Language, LanguageDetectorBuilder
import asyncio
import traceback
print("📚 Loading Lingua Language Detector...")
languages = [Language.FRENCH, Language.ENGLISH, Language.SPANISH, Language.PORTUGUESE, 
             Language.ITALIAN, Language.JAPANESE, Language.CHINESE, Language.HINDI]
detector = LanguageDetectorBuilder.from_languages(*languages).build()
print("✅ Lingua loaded.")

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

app = FastAPI(title="ECHO TTS Worker", description="Kokoro ONNX CPU API")

print("🧠 Loading Kokoro ONNX model on CPU...")
# Chargement optimisé CPU
kokoro = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
print("✅ Model loaded successfully.")

class TTSRequest(BaseModel):
    model: str = "kokoro"
    input: str
    voice: str = "ff_siwis" # Ignoré par la logique d'autodétection
    response_format: str = "mp3"
    speed: float = 1.0

# Dictionnaire de mappage (Langue -> Voix Féminine Kokoro)
VOICE_MAP = {
    "fr": "ff_siwis",
    "en": "af_bella",
    "es": "ef_dora",
    "it": "if_sara",
    "pt": "pf_dora",
    "ja": "jf_alpha",
    "zh": "zf_xiaoxiao",
    "hi": "hf_alpha"
}

@app.get("/health")
async def health():
    return {"status": "ok", "model": "kokoro"}

def split_text_into_sentences(text: str):
    # Regex simple pour découper le texte en phrases (sur ponctuations fortes et retours chariot)
    sentences = re.split(r'(?<=[.!?\n])\s+', text.strip())
    return [s for s in sentences if s.strip()]

def hybrid_g2p_parse(text: str, main_lang: str):
    """
    Découpe hybride via Lingua. Très résilient au code-switching (franglais).
    """
    words = re.findall(r"[\w']+|[^\w\s]+|\s+", text)
    chunks = []
    current_lang = main_lang
    current_text = ""

    for word in words:
        if not any(c.isalpha() for c in word):
            current_text += word
            continue

        # Détection au niveau du mot (Lingua est plus robuste sur les n-grammes)
        detected = detector.detect_language_of(word)
        word_lang = detected.iso_code_639_1.name.lower() if detected else main_lang
        
        # Logique de Chunking (création de blocs)
        if word_lang != current_lang:
            if current_text:
                chunks.append((current_text, current_lang))
            current_text = word
            current_lang = word_lang
        else:
            current_text += word
            
    if current_text:
        chunks.append((current_text, current_lang))
        
    return chunks

# OpenAI Compatible Endpoint (Streaming Chunked Transfer MP3)
@app.post("/v1/audio/speech")
async def create_speech(req: TTSRequest):
    try:
        # Protection contre les entrées vides (test de l'interface)
        if not req.input or not req.input.strip():
            print("[TTS] ⚠️ Entrée texte vide, renvoi d'un 204.")
            return Response(status_code=204)
            
        print(f"[TTS] 🗣️ Génération streamée MP3 demandée pour '{req.input[:30]}...'")

        async def audio_stream_generator():
            # Initialisation asynchrone du processus FFmpeg
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-y",
                "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
                "-f", "mp3", "-b:a", "128k", "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Tâche de génération en arrière-plan (Publisher)
            async def generate_audio_task():
                try:
                    sentences = split_text_into_sentences(req.input)
                    for sentence in sentences:
                        detected_sentence = detector.detect_language_of(sentence)
                        lang_code = detected_sentence.iso_code_639_1.name.lower() if detected_sentence else "en"
                        voice_id = VOICE_MAP.get(lang_code, "af_bella")
                        
                        kokoro_lang = lang_code
                        if lang_code == "en": kokoro_lang = "en-us"
                        elif lang_code == "fr": kokoro_lang = "fr-fr"
                        elif lang_code == "pt": kokoro_lang = "pt-br"
                        
                        print(f"[TTS] Phrase détectée: '{lang_code}' -> Voix: '{voice_id}' | {sentence[:30]}")
                        
                        chunks = hybrid_g2p_parse(sentence, lang_code)
                        mixed_phonemes = ""
                        for chunk_text, chunk_lang in chunks:
                            k_lang = chunk_lang
                            if chunk_lang == "en": k_lang = "en-us"
                            elif chunk_lang == "fr": k_lang = "fr-fr"
                            elif chunk_lang == "pt": k_lang = "pt-br"
                            mixed_phonemes += kokoro.tokenizer.phonemize(chunk_text, k_lang)
                            
                        print(f"[TTS] Phonèmes hybrides générés : {mixed_phonemes[:60]}...")
                        
                        # Inférence Native depuis les phonèmes hybrides (offloaded to thread)
                        samples, sample_rate = await asyncio.to_thread(
                            kokoro.create, mixed_phonemes, voice=voice_id, speed=req.speed, lang=kokoro_lang, is_phonemes=True
                        )
                        
                        if samples is not None and len(samples) > 0:
                            # Injection asynchrone sans blocage
                            audio_int16 = (samples * 32767).astype(np.int16)
                            process.stdin.write(audio_int16.tobytes())
                            await process.stdin.drain()
                            
                except Exception as chunk_err:
                    print(f"[TTS] ❌ Erreur de génération : {chunk_err}")
                finally:
                    # Signal de fin incontestable pour FFmpeg
                    if process.stdin:
                        process.stdin.close()
                        try:
                            await process.stdin.wait_closed()
                        except Exception:
                            pass
                            
            # Lancement immédiat de la tâche d'arrière-plan avec référence pour annulation
            bg_task = asyncio.create_task(generate_audio_task())
            
            # Boucle principale (Subscriber) : pompage non-bloquant
            try:
                while True:
                    # Lecture asynchrone native de la STD
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        # Si FFmpeg s'arrête prématurément, lisons l'erreur
                        stderr_output = await process.stderr.read()
                        if stderr_output:
                            print(f"[TTS] ⚠️ FFmpeg stderr: {stderr_output.decode('utf-8', errors='ignore')}")
                        break
                    yield chunk
            finally:
                # Annulation de la tâche d'arrière-plan zombie en cas de déconnexion client
                if not bg_task.done():
                    bg_task.cancel()
                    
                # Sécurité : Tuer le processus orphelin si le client réseau coupe violemment
                if process.returncode is None:
                    try:
                        process.terminate()
                        await process.wait()
                    except ProcessLookupError:
                        pass

        # Retourner une réponse streamée avec le bon type MIME
        return StreamingResponse(audio_stream_generator(), media_type="audio/mpeg")
    
    except Exception as e:
        err_msg = str(e)
        print(f"[TTS] ❌ Erreur interne : {err_msg}")
        traceback.print_exc()
        return Response(content=f'{{"error": "{err_msg}"}}', status_code=500, media_type="application/json")
