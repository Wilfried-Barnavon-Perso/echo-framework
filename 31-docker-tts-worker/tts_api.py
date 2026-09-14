"""
================================================================================
MODULE : ECHO TTS WORKER API
VERSION : 1.3 (Lingua G2P & FFmpeg MP3 Streaming)
AUTEUR : ECHO Team
DATE MAJ : 2026-09-14

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
import subprocess
import asyncio

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
            # Initialisation du processus FFmpeg continu
            # On stream du PCM brut (s16le, 24000Hz mono) vers l'entrée (stdin) de ffmpeg, 
            # et on lit le flux MP3 continu sur sa sortie (stdout).
            process = subprocess.Popen(
                [
                    "ffmpeg", "-y",
                    "-f", "s16le", "-ar", "24000", "-ac", "1", "-i", "pipe:0",
                    "-f", "mp3", "-b:a", "128k", "pipe:1"
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL
            )
            
            # Fonction asynchrone pour lire stdout en continu et le renvoyer
            async def read_stdout():
                while True:
                    # Lecture asynchrone non-bloquante du stdout de ffmpeg
                    chunk = await asyncio.to_thread(process.stdout.read, 4096)
                    if not chunk:
                        break
                    yield chunk

            # On démarre le lecteur dans une tâche séparée
            reader_generator = read_stdout()
            
            try:
                # Découpage du texte en phrases pour préserver la prosodie
                sentences = split_text_into_sentences(req.input)
                
                for sentence in sentences:
                    # 1. Détection automatique de la langue via Lingua
                    detected_sentence = detector.detect_language_of(sentence)
                    lang_code = detected_sentence.iso_code_639_1.name.lower() if detected_sentence else "en"
                    
                    # 2. Assignation de la voix féminine correspondante
                    voice_id = VOICE_MAP.get(lang_code, "af_bella") # Fallback sur US english si langue non reconnue
                    
                    # 3. Mappage du code langue strict pour le phonémiseur Kokoro
                    kokoro_lang = lang_code
                    if lang_code == "en": kokoro_lang = "en-us"
                    elif lang_code == "fr": kokoro_lang = "fr-fr"
                    elif lang_code == "pt": kokoro_lang = "pt-br"
    
                    print(f"[TTS] Phrase détectée: '{lang_code}' -> Voix: '{voice_id}' | {sentence[:30]}")
                    
                    try:
                        # 4. Hybrid G2P Chunking
                        chunks = hybrid_g2p_parse(sentence, lang_code)
                        mixed_phonemes = ""
                        
                        for chunk_text, chunk_lang in chunks:
                            # Mappage des langues pour le phonémiseur interne
                            k_lang = chunk_lang
                            if chunk_lang == "en": k_lang = "en-us"
                            elif chunk_lang == "fr": k_lang = "fr-fr"
                            elif chunk_lang == "pt": k_lang = "pt-br"
                            
                            # Phonémisation spécifique au bloc
                            chunk_phonemes = kokoro.tokenizer.phonemize(chunk_text, k_lang)
                            mixed_phonemes += chunk_phonemes
                            
                        print(f"[TTS] Phonèmes hybrides générés : {mixed_phonemes[:60]}...")
                        
                        # 5. Inférence Native depuis les phonèmes hybrides
                        samples, sample_rate = kokoro.create(
                            mixed_phonemes, voice=voice_id, speed=req.speed, lang=kokoro_lang, is_phonemes=True
                        )
                        
                        if samples is not None and len(samples) > 0:
                            # Injection du PCM dans ffmpeg
                            audio_int16 = (samples * 32767).astype(np.int16)
                            process.stdin.write(audio_int16.tobytes())
                            process.stdin.flush()
                            
                            # Rapatriement immédiat des bytes MP3 encodés
                            chunk = await anext(reader_generator, None)
                            if chunk:
                                yield chunk
                                
                    except Exception as chunk_err:
                        print(f"[TTS] ❌ Erreur sur la génération du chunk : {chunk_err}")
            finally:
                # Fermeture du flux PCM, FFmpeg finira son encodage MP3
                if process.stdin:
                    process.stdin.close()
                # Yield des derniers bytes
                async for final_chunk in reader_generator:
                    yield final_chunk
                process.wait()

        # Retourner une réponse streamée avec le bon type MIME
        return StreamingResponse(audio_stream_generator(), media_type="audio/mpeg")
    
    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"[TTS] ❌ Erreur interne : {err_msg}")
        traceback.print_exc()
        return Response(content=f'{{"error": "{err_msg}"}}', status_code=500, media_type="application/json")
