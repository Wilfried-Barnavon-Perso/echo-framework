"""
================================================================================
MODULE : ECHO EMBEDDING WORKER (bge-m3)
VERSION : 1.5
AUTEUR : Wilfried BARNAVON
DATE : 2026-06-15

ROLE : Serveur d'embedding texte compatible OpenAI (BAAI/bge-m3)
       Multilingue, 1024 dimensions, 8192 tokens max.
       Support de l'Edge Embedding (WebGPU) via WebSocket Proxy.

CHANGELOG :
  1.1 : Correction truncation=True (SigLIP-2 max 64 tokens).
  1.2 : Truncation silencieuse (guard-rail).
  1.3 : Migration BAAI/bge-m3. Suppression SigLIP-2 et branche image.
        CLS token pooling + normalisation L2.
  1.4 : Stabilisation threadpool.
  1.5 : Support Edge Embedding WebGPU (WebSocket proxy & Fallback).
================================================================================
"""

import os
import logging
import asyncio
import torch
import numpy as np
import pybase64 as base64
import orjson as json
import uuid
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
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
    description="Worker souverain pour embeddings texte multilingues (BAAI/bge-m3) & Edge WebGPU",
    version="1.5"
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

# Registres pour le Edge Embedding WebGPU
active_edge_clients: dict[str, WebSocket] = {}
edge_client_status: dict[str, str] = {}
pending_edge_requests: dict[str, asyncio.Future] = {}

def get_user_id_from_jwt(token: str) -> str:
    """Extraction basique du user_id depuis le JWT d'Open WebUI."""
    try:
        payload_b64 = token.split('.')[1]
        payload_b64 += "=" * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
        return payload.get("id")
    except Exception:
        return None

@app.websocket("/ws/edge-embed")
async def websocket_edge_embed(websocket: WebSocket):
    # Le token peut être dans la query string (dev) ou dans les cookies (prod sécurisée par WAF)
    token = websocket.query_params.get("token") or websocket.cookies.get("token")
    if not token:
        await websocket.close(code=1008)
        return
        
    user_id = get_user_id_from_jwt(token)
    if not user_id:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    active_edge_clients[user_id] = websocket
    edge_client_status[user_id] = "connecting"
    logger.info(f"🔌 WebGPU Client Connected | User: {user_id}")

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")
            
            if msg_type == "ready":
                edge_client_status[user_id] = "ready"
                logger.info(f"✅ WebGPU Client Ready | User: {user_id}")
            
            elif msg_type == "incompatible":
                edge_client_status[user_id] = "incompatible"
                logger.warning(f"⚠️ WebGPU Client Incompatible | User: {user_id}")
                
            elif msg_type == "result":
                req_id = data.get("request_id")
                future = pending_edge_requests.get(req_id)
                if future and not future.done():
                    future.set_result(data.get("embedding"))
                
    except Exception as e:
        logger.warning(f"🔌 WebGPU Client Disconnected | User: {user_id} | Reason: {e}")
    finally:
        active_edge_clients.pop(user_id, None)
        edge_client_status.pop(user_id, None)

@app.get("/internal/edge-status")
async def edge_status(user_id: str):
    """Vérification rapide de l'état Edge pour le filtre ECHO."""
    return {"status": edge_client_status.get(user_id, "unknown")}

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

        # 1. TENTATIVE EDGE EMBEDDING (WebGPU)
        if user_id in active_edge_clients and edge_client_status.get(user_id) == "ready":
            ws = active_edge_clients[user_id]
            req_id = str(uuid.uuid4())
            future = asyncio.get_running_loop().create_future()
            pending_edge_requests[req_id] = future
            
            try:
                await ws.send_json({"type": "embed", "texts": inputs, "request_id": req_id})
                # Timeout strict (15s) pour basculer rapidement sur CPU si le navigateur bloque
                embeddings = await asyncio.wait_for(future, timeout=15.0)
                
                batch_data = []
                for i, emb in enumerate(embeddings):
                    batch_data.append({
                        "object": "embedding",
                        "index": i,
                        "embedding": emb
                    })
                    
                duration = time.time() - start_time
                logger.info(f"✨ Edge Vectors Generated | User: {user_id} | Count: {len(inputs)} | Time: {duration:.3f}s")
                
                return {
                    "object": "list",
                    "data": batch_data,
                    "model": MODEL_ID,
                    "usage": {"prompt_tokens": 0, "total_tokens": 0}
                }
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ Edge Inference Timeout for {user_id}. Fallback -> CPU.")
            finally:
                pending_edge_requests.pop(req_id, None)

        # 2. FALLBACK CPU SYNCHRONE
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
