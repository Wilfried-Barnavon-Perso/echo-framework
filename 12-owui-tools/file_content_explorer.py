"""
title: ECHO File Content Explorer
author: ECHO Framework
version: 1.10
description: 1.10: Refined Low-Level reading instructions (Docstring).
"""

import os
import sys
import glob
import json
import base64
import requests
import uuid
import random
import time
from typing import Optional, Any

# Importations ECHO Strictes
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, resolve_upload_file_path
from echo_constants import get_gemini_mime, ECHO_UPLOADS_DIR, ECHO_USER_AGENT, GOOGLE_SSE_URL

# Configuration
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"

def robust_read(path: str) -> bytes:
    """Tentative de lecture robuste avec retry."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(path, 'rb') as f: return f.read()
        except (PermissionError, OSError):
            if attempt < max_retries - 1: time.sleep(0.5)
            else: raise
    return b""

class Tools:
    def __init__(self):
        self.auth = EchoAuth()

    async def read_raw_file_content(
        self, 
        file_id: str, 
        encoding: str, 
        start_chunk: int = 0, 
        end_chunk: Optional[int] = None, 
        chunk_size: int = 100000,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        [LECTURE BAS NIVEAU / CHUNKS / BINAIRE] 
        Lit le contenu brut, exact et non altéré d'un fichier (via FILE_ID).
        USAGE REQUIS POUR : 
        1. Fichiers texte volumineux nécessitant une pagination (start_chunk, end_chunk).
        2. Code source (.py, .js, .html) et données structurées exactes (.csv, .json, .log).
        3. Extraction de métadonnées de bas niveau, vérification d'en-têtes, de signatures ou de corruption de fichiers (via encodages 'hex' ou 'base64').
        CONTRE-INDICATION : Ne PAS utiliser pour comprendre le "sens", les concepts, ou analyser le contenu narratif/visuel des médias (vidéos, images, PDF scannés). Pour l'analyse sémantique et conceptuelle, utilisez impérativement 'semantic_probe'.

        :param file_id: L'UUID technique fourni dans le bloc Smart Context (FILE_ID).
        :param encoding: MODE DE LECTURE OBLIGATOIRE ('utf-8', 'hex', 'base64').
        :param start_chunk: Index du morceau de lecture (Pagination).
        :param chunk_size: Taille du morceau.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        path = resolve_upload_file_path(file_id, ECHO_UPLOADS_DIR)
        if not path: return f"❌ Fichier introuvable pour l'ID : {file_id}"

        await events.status(f"📂 Lecture brute : {os.path.basename(path)}...")
        try:
            content_bytes = robust_read(path)
            if encoding == "base64": text = base64.b64encode(content_bytes).decode('ascii')
            elif encoding == "hex": text = content_bytes.hex()
            elif encoding == "utf-8":
                try: text = content_bytes.decode('utf-8')
                except: text = content_bytes.decode('latin-1', errors='replace')
            else: return "❌ Erreur: Paramètre 'encoding' invalide."
            
            total_len = len(text)
            start_idx = start_chunk * chunk_size
            end_idx = end_chunk * chunk_size if end_chunk is not None else start_idx + chunk_size
            if start_idx >= total_len: return "⚠️ Fin du fichier atteinte."
            if end_idx > total_len: end_idx = total_len
            
            extracted = text[start_idx:end_idx]
            footer = f"\n\n--- FIN CHUNK ({encoding}) ---\n➡️ Reste : {total_len - end_idx}." if end_idx < total_len else "\n\n--- FIN DU FICHIER ---"
            await events.status(f"📂 Lecture terminée ({encoding}).", done=True)
            return f"--- CONTENU ({encoding}) : {os.path.basename(path)} ---\n{extracted}{footer}"
        except Exception as e: return f"❌ Erreur lecture: {str(e)}"

    async def semantic_probe(
        self, 
        file_id: str, 
        query: str, 
        thinking_level: str = "HIGH", 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Interroge un sous-agent multimodal sur un fichier.
        :param file_id: L'UUID technique fourni dans le bloc Smart Context (FILE_ID).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        if not __user__ or "id" not in __user__: return "❌ Erreur User."
        
        path = resolve_upload_file_path(file_id, ECHO_UPLOADS_DIR)
        if not path: return f"❌ Fichier introuvable pour l'ID : {file_id}"
        
        mime, supported = get_gemini_mime(path)
        if not supported: return f"❌ Type non supporté ({mime or 'inconnu'})."
        
        token, project_id = self.auth.get_credentials(__user__["id"])
        if not token or not project_id: return "❌ Erreur Auth Google."
        
        await events.status(f"🤖 Sondage Multimodal ({thinking_level}) : {os.path.basename(path)}...")
        
        headers = {
            "Authorization": f"Bearer {token}", 
            "Content-Type": "application/json",
            "User-Agent": ECHO_USER_AGENT,
            "x-goog-api-client": "gl-python/3.10"
        }
        
        try:
            content_bytes = robust_read(path)
            b64 = base64.b64encode(content_bytes).decode('utf-8')
            payload = {
                "model": GEMINI_FLASH_MODEL,
                "project": project_id,
                "user_prompt_id": hex(random.getrandbits(64))[2:],
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": query}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                    "session_id": str(uuid.uuid4()),
                    "generationConfig": {
                        "temperature": 0.4, "maxOutputTokens": 8192,
                        "thinkingConfig": {"thinkingLevel": thinking_level.upper()},
                        "responseMimeType": "text/plain"
                    }
                }
            }
            resp = requests.post(GOOGLE_SSE_URL, headers=headers, json=payload, stream=True, timeout=120)
            if resp.status_code != 200: return f"❌ API Error {resp.status_code}: {resp.text[:200]}"
            full_text = ""
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            data = json.loads(json_str)
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: full_text += parts[0]["text"]
                        except: pass
            await events.status(f"🤖 Analyse terminée.", done=True)
            return f"🤖 **Sondage Sémantique ({thinking_level}) :**\\n\\n{full_text}"
        except Exception as e: return f"❌ Exception: {str(e)}"
