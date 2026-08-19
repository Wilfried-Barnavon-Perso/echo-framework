"""
================================================================================
MODULE : ECHO EMBEDDING WORKER (Llama.cpp / GGUF)
VERSION : 2.1 (Rate-Limit Healthcheck)
AUTEUR : Wilfried BARNAVON
DATE : 2026-08-19

ROLE : Serveur d'embedding texte compatible OpenAI (Harrier-OSS GGUF)
       Multilingue, 1024 dimensions, 8192 tokens max.
       Support de l'Edge Embedding (WebGPU) via WebSocket Proxy.

CHANGELOG :
  1.4 : Stabilisation threadpool.
  1.5 : Support Edge Embedding WebGPU (WebSocket proxy & Fallback).
  1.6 : Correction critique : passage au CLS Token Pooling (index 0) au lieu du Last-Token.
  2.0 : Migration majeure vers llama.cpp (GGUF) pour réduire l'empreinte RAM. Suppression de PyTorch.
  2.1 : Ajout d'un filtre de logs limitant l'affichage des requêtes /health (1/5min).
================================================================================
"""

import os
import logging
import asyncio
import math
from llama_cpp import Llama
from huggingface_hub import hf_hub_download
import pybase64 as base64
import orjson as json
import uuid
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import List, Union, Optional
import time

# Configuration du logging (Format ECHO)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("echo-embedding")

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

app = FastAPI(
    title="ECHO bge-m3 Embedding Worker",
    description="Worker souverain pour embeddings texte multilingues (BAAI/bge-m3) & Edge WebGPU",
    version="1.5"
)

# Verrou asynchrone global pour sérialiser l'inférence
embedding_lock = asyncio.Lock()

# Configuration via variables d'environnement
MODEL_ID = os.getenv("MODEL_ID", "microsoft/Harrier-OSS-v1-0.6B")
GGUF_REPO = os.getenv("GGUF_REPO", "mradermacher/harrier-oss-v1-0.6b-GGUF")
GGUF_FILE = os.getenv("GGUF_FILE", "harrier-oss-v1-0.6b.Q8_0.gguf")
# Llama.cpp CPU
DEVICE = "cpu"

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

def download_gguf_model():
    """Télécharge le modèle GGUF depuis HuggingFace Hub si non présent localement."""
    logger.info(f"⏳ Téléchargement ou vérification de {GGUF_FILE} depuis {GGUF_REPO}...")
    return hf_hub_download(repo_id=GGUF_REPO, filename=GGUF_FILE)

@app.on_event("startup")
async def load_model():
    """Chargement du modèle au démarrage pour éviter la latence sur la première requête."""
    logger.info(f"🚀 Initialisation du worker d'embedding (GGUF) : {MODEL_ID}")
    logger.info(f"💻 Device : {DEVICE} | Max length : {MAX_LENGTH}")

    try:
        # Téléchargement bloquant au démarrage
        model_path = download_gguf_model()
        logger.info(f"📦 Modèle localisé à : {model_path}")
        
        # Initialisation Llama.cpp avec pooling CLS natif (embedding=True)
        state["model"] = Llama(
            model_path=model_path,
            embedding=True,
            verbose=False,
            n_ctx=MAX_LENGTH
        )
        state["ready"] = True
        logger.info(f"✅ {MODEL_ID} (GGUF) est prêt à recevoir des requêtes.")
    except Exception as e:
        logger.error(f"❌ Échec critique du chargement du modèle : {str(e)}")
        # On ne lève pas d'exception ici pour permettre au conteneur de rester vivant
        # et de logger l'erreur. Les requêtes retourneront HTTP 503 via /health.

class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: Optional[str] = None
    encoding_format: Optional[str] = "float"

def l2_normalize(vector: List[float]) -> List[float]:
    """Normalisation L2 manuelle pour remplacer torch.norm"""
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm > 0 else vector

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
                
                # Validation stricte du retour Edge
                if not embeddings or not isinstance(embeddings, list) or any(e is None or e == [None] or not isinstance(e, list) for e in embeddings):
                    raise ValueError("Edge client returned null or invalid embeddings")
                
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
            except Exception as e:
                logger.warning(f"⚠️ Edge Inference Error for {user_id}: {e}. Fallback -> CPU.")
            finally:
                pending_edge_requests.pop(req_id, None)

        # 2. FALLBACK CPU SYNCHRONE
        def process_batch_sync(inputs_list: List[str]):
            if not state["ready"]:
                raise RuntimeError("Le modèle n'est pas encore chargé.")
                
            # Llama.cpp gère nativement le batching de requêtes avec `create_embedding`
            response = state["model"].create_embedding(inputs_list)
            
            batch_data = []
            for i, data_point in enumerate(response["data"]):
                raw_vector = data_point["embedding"]
                normalized_vector = l2_normalize(raw_vector)
                batch_data.append({
                    "object": "embedding",
                    "index": i,
                    "embedding": normalized_vector
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
