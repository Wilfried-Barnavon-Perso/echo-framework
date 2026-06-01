"""
MODULE : ECHO GEMMA DISTILLER
VERSION : 1.2
ROLE : Serveur de distillation locale (Gemma 4 E4B, GGUF Q5_K_M)
       API compatible OpenAI /v1/chat/completions
       Détection GPU automatique (CUDA → CPU fallback)
       Support natif multi-threading dynamique (N_THREADS)
"""

import os
import time
import logging
import asyncio
from fastapi import FastAPI
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn
import multiprocessing

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-GEMMA")

# ==============================================================================
# CONFIGURATION (Variables d'environnement Docker)
# ==============================================================================

MODEL_PATH = os.environ.get("MODEL_PATH", "/models/distiller.gguf")
N_CTX      = int(os.environ.get("N_CTX", 4096))
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", 2048))
PORT       = int(os.environ.get("PORT", 7998))

DEFAULT_THREADS = max(1, multiprocessing.cpu_count())
N_THREADS = int(os.environ.get("N_THREADS", DEFAULT_THREADS))

# ==============================================================================
# CHARGEMENT DU MODÈLE (une seule fois au démarrage)
# ==============================================================================

from llama_cpp import Llama

# Détection GPU dynamique
# WSL2 expose CUDA nativement via paravirtualisation, VM Hyper-V = CPU only
try:
    from llama_cpp import llama_supports_gpu_offload
    GPU_AVAILABLE = llama_supports_gpu_offload()
except ImportError:
    GPU_AVAILABLE = False

N_GPU_LAYERS = -1 if GPU_AVAILABLE else 0  # -1 = toutes les couches sur GPU
DEVICE = "cuda" if GPU_AVAILABLE else "cpu"

logger.info(f"🔧 Device détecté : {DEVICE} (n_gpu_layers={N_GPU_LAYERS}, n_threads={N_THREADS})")
logger.info(f"📦 Chargement du modèle : {MODEL_PATH}")

model = Llama(
    model_path=MODEL_PATH,
    n_ctx=N_CTX,
    n_gpu_layers=N_GPU_LAYERS,
    n_threads=N_THREADS,
    n_threads_batch=N_THREADS,
    verbose=False
)
logger.info(f"✅ Modèle chargé sur {DEVICE} avec {N_THREADS} threads")

# ==============================================================================
# GRAMMAR GBNF — Force la sortie JSON valide (équivalent response_mime_type)
# ==============================================================================

# Grammar GBNF standard pour contraindre la génération à du JSON valide.
# Utilisée quand response_format.type == "json_object".
# Équivalent local du `response_mime_type: "application/json"` de l'API Gemini.
JSON_GBNF = r"""
root   ::= object
object ::= "{" ws members ws "}"
members ::= pair ( "," ws pair )*
pair   ::= string ws ":" ws value
value  ::= string | number | object | array | "true" | "false" | "null"
string ::= "\"" chars "\""
chars  ::= char*
char   ::= [^"\\] | "\\" escape
escape ::= ["\\nrt/] | "u" [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F] [0-9a-fA-F]
number ::= "-"? [0-9]+ ( "." [0-9]+ )? ( [eE] [+-]? [0-9]+ )?
array  ::= "[" ws ( value ( "," ws value )* )? ws "]"
ws     ::= [ \t\n]*
""".strip()

# ==============================================================================
# API FastAPI
# ==============================================================================

app = FastAPI(title="ECHO Gemma Distiller", version="1.1")

# Verrou asynchrone global pour sérialiser l'inférence
inference_lock = asyncio.Lock()


class ChatMessage(BaseModel):
    role: str
    content: str


class ResponseFormat(BaseModel):
    type: str = "text"


class CompletionRequest(BaseModel):
    messages: list[ChatMessage]
    temperature: float = 0.0
    max_tokens: int = Field(default=MAX_TOKENS)
    response_format: Optional[ResponseFormat] = None


@app.post("/v1/chat/completions")
async def chat_completions(req: CompletionRequest):
    """
    Endpoint OpenAI-compatible pour la distillation.
    Accepte le format messages[] standard et retourne choices[].
    """
    # Construction du prompt au format Gemma 4 (support natif system, user, model)
    prompt_parts = []
    for msg in req.messages:
        if msg.role == "system":
            prompt_parts.append(f"<start_of_turn>system\n{msg.content}<end_of_turn>")
        elif msg.role == "user":
            prompt_parts.append(f"<start_of_turn>user\n{msg.content}<end_of_turn>")
        elif msg.role == "assistant":
            prompt_parts.append(f"<start_of_turn>model\n{msg.content}<end_of_turn>")
    prompt_parts.append("<start_of_turn>model\n")
    prompt = "\n".join(prompt_parts)

    # Activation de la grammar JSON si demandé
    grammar = None
    if req.response_format and req.response_format.type == "json_object":
        from llama_cpp import LlamaGrammar
        grammar = LlamaGrammar.from_string(JSON_GBNF)

    t0 = time.time()
    
    def run_inference():
        return model(
            prompt,
            max_tokens=req.max_tokens,
            temperature=max(req.temperature, 0.01),  # llama.cpp refuse temperature=0.0 strict
            grammar=grammar,
            stop=["<end_of_turn>", "<eos>"]
        )

    # Exécution protégée dans le threadpool pour ne pas bloquer l'Event Loop
    async with inference_lock:
        output = await asyncio.to_thread(run_inference)
        
    elapsed = time.time() - t0

    text = output["choices"][0]["text"].strip()
    tokens_used = output.get("usage", {})

    logger.info(
        f"📝 Distillation terminée en {elapsed:.2f}s "
        f"({tokens_used.get('completion_tokens', '?')} tokens)"
    )

    return {
        "id": f"echo-gemma-{int(t0)}",
        "object": "chat.completion",
        "created": int(t0),
        "model": "gemma-4-E4B-it",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": output["choices"][0].get("finish_reason", "stop")
        }],
        "usage": tokens_used,
        "_echo_meta": {"device": DEVICE, "elapsed_s": round(elapsed, 2)}
    }


@app.get("/health")
async def health():
    """Healthcheck pour Docker et monitoring ECHO."""
    return {
        "status": "ready",
        "model": "gemma-4-E4B-it",
        "quantization": "Q5_K_M",
        "device": DEVICE,
        "n_ctx": N_CTX,
        "gpu_available": GPU_AVAILABLE
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)
