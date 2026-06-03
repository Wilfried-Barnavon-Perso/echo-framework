from fastapi import FastAPI
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from kokoro_onnx import Kokoro
import soundfile as sf
import io
import langid
langid.set_languages(['fr', 'en', 'es', 'it', 'pt', 'ja', 'zh', 'hi']) # Restriction des langues pour éviter les faux positifs

from pydub import AudioSegment
import numpy as np
import re
from spellchecker import SpellChecker

print("📚 Loading SpellCheckers for Hybrid G2P...")
# Chargement en mémoire RAM des dictionnaires disponibles nativement
dictionaries = {
    "fr": SpellChecker(language='fr'),
    "en": SpellChecker(language='en'),
    "es": SpellChecker(language='es'),
    "pt": SpellChecker(language='pt'),
}
print("✅ Dictionaries loaded.")

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
    Découpe le texte en blocs de mots de la même langue via dictionnaire.
    Préserve la ponctuation et les espaces.
    """
    tokens = re.findall(r"[\w']+|[^\w\s]+|\s+", text)
    chunks = []
    current_lang = main_lang
    current_text = ""
    
    for token in tokens:
        # Si ce n'est pas un mot avec des lettres (ponctuation, espace, chiffres) -> on ajoute au chunk actuel
        if not any(c.isalpha() for c in token):
            current_text += token
            continue
            
        clean_word = token.lower()
        word_lang = None
        
        # 1. Test Dico Langue Principale
        if main_lang in dictionaries and clean_word in dictionaries[main_lang]:
            word_lang = main_lang
        else:
            # 2. Test autres Dicos
            for l_code, checker in dictionaries.items():
                if l_code != main_lang and clean_word in checker:
                    word_lang = l_code
                    break
            
            # 3. Fallbacks (Sigles et Noms propres)
            if not word_lang:
                if token.isupper() and len(token) > 1:
                    # Épellation (séparation par des espaces)
                    token = " ".join(list(token))
                    word_lang = main_lang
                else:
                    word_lang = main_lang
                    
        # Logique de Chunking (création de blocs)
        if word_lang != current_lang:
            if current_text:
                chunks.append((current_text, current_lang))
            current_text = token
            current_lang = word_lang
        else:
            current_text += token
            
    if current_text:
        chunks.append((current_text, current_lang))
        
    return chunks

def encode_numpy_to_mp3(samples, sample_rate):
    # Convertir le float32 numpy array en PCM 16-bit
    audio_int16 = (samples * 32767).astype(np.int16)
    # Créer le segment audio avec pydub
    audio_segment = AudioSegment(
        audio_int16.tobytes(),
        frame_rate=sample_rate,
        sample_width=2, # 16-bit
        channels=1      # mono
    )
    # Exporter en buffer MP3
    mp3_io = io.BytesIO()
    audio_segment.export(mp3_io, format="mp3", bitrate="128k")
    mp3_io.seek(0)
    return mp3_io.read()

# OpenAI Compatible Endpoint (Streaming Chunked Transfer MP3)
@app.post("/v1/audio/speech")
async def create_speech(req: TTSRequest):
    try:
        # Protection contre les entrées vides (test de l'interface)
        if not req.input or not req.input.strip():
            print("[TTS] ⚠️ Entrée texte vide, renvoi d'un audio silencieux MP3.")
            silence = AudioSegment.silent(duration=1000, frame_rate=24000)
            mp3_io = io.BytesIO()
            silence.export(mp3_io, format="mp3", bitrate="128k")
            mp3_io.seek(0)
            return Response(content=mp3_io.read(), media_type="audio/mpeg")
            
        print(f"[TTS] 🗣️ Génération streamée MP3 demandée pour '{req.input[:30]}...'")

        async def audio_stream_generator():
            # Découpage du texte en phrases pour préserver la prosodie
            sentences = split_text_into_sentences(req.input)
            
            for sentence in sentences:
                # 1. Détection automatique de la langue
                lang_code, _ = langid.classify(sentence)
                
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
                        # Encodage en MP3 à la volée et envoi du chunk HTTP
                        mp3_bytes = encode_numpy_to_mp3(samples, sample_rate)
                        yield mp3_bytes
                except Exception as chunk_err:
                    print(f"[TTS] ❌ Erreur sur la génération du chunk : {chunk_err}")

        # Retourner une réponse streamée avec le bon type MIME
        return StreamingResponse(audio_stream_generator(), media_type="audio/mpeg")
    
    except Exception as e:
        import traceback
        err_msg = str(e)
        print(f"[TTS] ❌ Erreur interne : {err_msg}")
        traceback.print_exc()
        return Response(content=f'{{"error": "{err_msg}"}}', status_code=500, media_type="application/json")
