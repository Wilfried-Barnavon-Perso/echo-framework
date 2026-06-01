"""
================================================================================
MODULE : ECHO EMBEDDING WORKER (bge-m3)
VERSION : 1.4
AUTEUR : Wilfried BARNAVON
DATE : 2026-05-22

ROLE : Serveur d'embedding texte compatible OpenAI (BAAI/bge-m3)
       Multilingue, 1024 dimensions, 8192 tokens max.
       Remplace SigLIP-2 (vision-only, 64 tokens — inadapté au RAG texte).

CHANGELOG :
  1.1 : Correction truncation=True (SigLIP-2 max 64 tokens).
  1.2 : Truncation silencieuse (guard-rail).
  1.3 : Migration BAAI/bge-m3. Suppression SigLIP-2 et branche image.
        CLS token pooling + normalisation L2.
================================================================================
"""

import os
import logging
import asyncio
import torch
import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Union, Optional
from transformers import AutoTokenizer, AutoModel
import time

# Configuration du logging (Format ECHO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("echo-embedding")

app = FastAPI(
    title="ECHO bge-m3 Embedding Worker",
    description="Worker souverain pour embeddings texte multilingues (BAAI/bge-m3)",
    version="1.4"
)

# Verrou asynchrone global pour sérialiser l'inférence
embedding_lock = asyncio.Lock()

# Configuration via variables d'environnement
MODEL_ID = os.getenv("MODEL_ID", "BAAI/bge-m3")
# Détection GPU dynamique (WSL2 expose CUDA nativement, VM Hyper-V = CPU only)
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Sweet spot qualité/vitesse sur CPU pour bge-m3.
# Le modèle supporte 8192 tokens, mais les chunks ECHO font ~300-500 tokens.
MAX_LENGTH = int(os.getenv("MAX_LENGTH", "512"))

# État global pour le modèle
state = {
    "tokenizer": None,
    "model": None,
    "ready": False
}

@app.on_event("startup")
async def load_model():
    """Chargement du modèle au démarrage pour éviter la latence sur la première requête."""
    logger.info(f"🚀 Initialisation du worker d'embedding : {MODEL_ID}")
    logger.info(f"💻 Device : {DEVICE} | Max length : {MAX_LENGTH}")

    try:
        state["tokenizer"] = AutoTokenizer.from_pretrained(MODEL_ID)
        state["model"] = AutoModel.from_pretrained(MODEL_ID).to(DEVICE)
        state["model"].eval()
        state["ready"] = True
        logger.info(f"✅ {MODEL_ID} est prêt à recevoir des requêtes.")
    except Exception as e:
        logger.error(f"❌ Échec critique du chargement du modèle : {str(e)}")
        # On ne lève pas d'exception ici pour permettre au conteneur de rester vivant
        # et de logger l'erreur. Les requêtes retourneront HTTP 503 via /health.

class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"

def process_single_input(text: str) -> List[float]:
    """
    Génère un vecteur d'embedding pour un texte via bge-m3.
    Utilise CLS token pooling + normalisation L2 (standard sentence-transformers).
    """
    if not state["ready"]:
        raise RuntimeError("Le modèle n'est pas encore chargé.")

    with torch.inference_mode():
        inputs = state["tokenizer"](
            text,
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
            return_tensors="pt"
        ).to(DEVICE)

        outputs = state["model"](**inputs)

        # CLS token pooling : last_hidden_state[:, 0, :] est la représentation globale
        # C'est le standard bge-m3 / sentence-transformers pour la recherche sémantique.
        tensor = outputs.last_hidden_state[:, 0, :]

        # Normalisation L2 : rend le produit scalaire équivalent à la similarité cosinus
        embeddings = tensor / tensor.norm(p=2, dim=-1, keepdim=True)
        return embeddings.cpu().numpy().flatten().tolist()

@app.post("/v1/embeddings")
@app.post("/embeddings")
async def create_embeddings(request: EmbeddingRequest, req: Request):
    """Endpoint compatible OpenAI pour la génération d'embeddings."""
    user_id = req.headers.get("X-OpenWebUI-User-Id", "anonymous")
    start_time = time.time()

    try:
        inputs = request.input
        if isinstance(inputs, str):
            inputs = [inputs]

        def process_batch_sync(inputs_list):
            batch_data = []
            for i, item in enumerate(inputs_list):
                embedding_vector = process_single_input(item)
                batch_data.append({
                    "object": "embedding",
                    "index": i,
                    "embedding": embedding_vector
                })
            return batch_data

        # Exécution protégée dans le threadpool pour ne pas bloquer l'Event Loop
        async with embedding_lock:
            data = await asyncio.to_thread(process_batch_sync, inputs)

        duration = time.time() - start_time
        logger.info(f"✨ Vectors Generated | User: {user_id} | Count: {len(inputs)} | Time: {duration:.3f}s")

        return {
            "object": "list",
            "data": data,
            "model": MODEL_ID,
            "usage": {
                "prompt_tokens": 0,
                "total_tokens": 0
            }
        }
    except Exception as e:
        logger.error(f"💥 Error processing embedding: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health")
async def health():
    """Vérification de l'état du service."""
    if state["ready"]:
        return {
            "status": "ready",
            "model": MODEL_ID,
            "device": DEVICE,
            "max_length": MAX_LENGTH,
            "dim": 1024
        }
    else:
        return JSONResponse(status_code=503, content={"status": "initializing", "model": MODEL_ID})

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7997)
