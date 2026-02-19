"""
title: ECHO File Content Explorer
author: ECHO Framework
version: 1.5
description: Outil d'exploration profonde. Supporte ID (FILE_ID) ou chemin. Utilise echo_constants pour la détection MIME. Robustesse I/O Windows.
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
from typing import Optional

# Import Partagé
sys.path.append("/app/backend/echo_libs")
try:
    from echo_utils import EchoAuth
    from echo_constants import get_gemini_mime
except ImportError:
    class EchoAuth:
        def get_credentials(self, uid): return None, None
    def get_gemini_mime(path): return "application/octet-stream", False

# Configuration
UPLOADS_DIR = "/app/backend/data/uploads"
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"

# --- HELPER INTERNE ---
def resolve_file_path(identifier: str) -> str:
    path = os.path.join(UPLOADS_DIR, identifier)
    if os.path.exists(path): return path
    try:
        matches = glob.glob(os.path.join(UPLOADS_DIR, f"{identifier}_*"))
        if matches: return matches[0]
        matches = glob.glob(os.path.join(UPLOADS_DIR, f"*_{identifier}"))
        if matches: 
            matches.sort(key=os.path.getmtime, reverse=True)
            return matches[0]
    except: pass
    return ""

def robust_read(path: str) -> bytes:
    """Tentative de lecture robuste avec retry pour éviter les verrous Windows/Docker."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(path, 'rb') as f:
                return f.read()
        except (PermissionError, OSError):
            if attempt < max_retries - 1:
                time.sleep(0.5)
            else:
                raise
    return b""

class Tools:
    def __init__(self):
        self.auth = EchoAuth()

    async def read_raw_file_content(self, file_id: str, encoding: str, start_chunk: int = 0, end_chunk: Optional[int] = None, chunk_size: int = 100000) -> str:
        """
        Lit le contenu brut de TOUT type de fichier (Texte, Binaire, Code, PDF, Image...).
        
        :param file_id: L'identifiant unique du fichier (Valeur de 'FILE_ID' dans le contexte) ou son nom.
        :param encoding: MODE DE LECTURE OBLIGATOIRE.
            - 'utf-8': Pour fichiers TEXTE pur uniquement (.py, .txt, .md, .json). Plante sur les binaires.
            - 'hex': Pour analyser la structure interne (Header, Magic Bytes) de tout fichier inconnu ou binaire.
            - 'base64': Pour l'extraction et le transfert de données brutes sans perte.
        :param start_chunk: Index du morceau de lecture (Pagination).
        :param chunk_size: Taille du morceau.
        """
        path = resolve_file_path(file_id)
        if not path: return f"❌ Fichier introuvable pour l'ID : {file_id}"

        try:
            content_bytes = robust_read(path)
            
            if encoding == "base64":
                text = base64.b64encode(content_bytes).decode('ascii')
            elif encoding == "hex":
                text = content_bytes.hex()
            elif encoding == "utf-8":
                try: text = content_bytes.decode('utf-8')
                except: text = content_bytes.decode('latin-1', errors='replace')
            else:
                return "❌ Erreur: Paramètre 'encoding' invalide. Utilisez 'utf-8', 'hex' ou 'base64'."
            
            total_len = len(text)
            start_idx = start_chunk * chunk_size
            end_idx = end_chunk * chunk_size if end_chunk is not None else start_idx + chunk_size
            
            if start_idx >= total_len: return "⚠️ Fin du fichier atteinte."
            if end_idx > total_len: end_idx = total_len
            
            extracted = text[start_idx:end_idx]
            footer = f"\n\n--- FIN CHUNK ({encoding}) ---\n➡️ Reste : {total_len - end_idx}. Suivant : start_chunk={end_chunk if end_chunk else start_chunk+1}" if end_idx < total_len else "\n\n--- FIN DU FICHIER ---"
                
            return f"--- CONTENU ({encoding}) : {os.path.basename(path)} ---\n{extracted}{footer}"

        except Exception as e:
            return f"❌ Erreur lecture: {str(e)}"

    async def semantic_probe(self, file_id: str, query: str, thinking_level: str = "HIGH", __user__: dict = {}) -> str:
        """
        Interroge un sous-agent multimodal sur un fichier (PDF, Image, Vidéo, Audio).
        """
        if not __user__ or "id" not in __user__: return "❌ Erreur User."
        
        path = resolve_file_path(file_id)
        if not path: return f"❌ Fichier introuvable pour l'ID : {file_id}"
        
        mime, supported = get_gemini_mime(path)
        if not supported:
            return f"❌ Type de fichier non supporté pour l'analyse sémantique (MIME: {mime or 'inconnu'})."
        
        token, project_id = self.auth.get_credentials(__user__["id"])
        if not token or not project_id: return "❌ Erreur Auth Google (Smart Context non configuré)."
        
        clean_pid = project_id.replace("projects/", "")
        url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
        
        headers = {
            "Authorization": f"Bearer {token}", 
            "Content-Type": "application/json",
            "User-Agent": "GeminiCLI/0.24.0",
            "x-goog-api-client": "gl-python/3.10"
        }
        
        try:
            content_bytes = robust_read(path)
            b64 = base64.b64encode(content_bytes).decode('utf-8')
            
            payload = {
                "model": GEMINI_FLASH_MODEL,
                "project": clean_pid,
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
            
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=120)
            if resp.status_code != 200:
                return f"❌ Flash API Error {resp.status_code}: {resp.text[:200]}"

            full_text = ""
            for line in resp.iter_lines():
                if line:
                    decoded = line.decode("utf-8")
                    if decoded.startswith("data:"):
                        try:
                            json_str = decoded[5:].strip()
                            if not json_str: continue
                            data = json.loads(json_str)
                            cand = data.get("response", {}).get("candidates", [])[0]
                            if "content" in cand:
                                parts = cand["content"].get("parts", [])
                                if parts and "text" in parts[0]: full_text += parts[0]["text"]
                        except: pass
            
            return f"🤖 **Sondage Sémantique ({thinking_level}) :**\n\n{full_text}"

        except Exception as e:
            return f"❌ Exception: {str(e)}"