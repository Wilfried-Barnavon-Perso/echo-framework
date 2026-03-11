"""
title: ECHO File Content Explorer
author: ECHO Framework
version: 1.15
description: 1.15: Mutualized thought splitting using echo_utils (Standard <think>).
"""

import os
import sys
import glob
import json
import base64
import random
import uuid
import asyncio
import httpx
import hashlib
import zlib
import re
from typing import Optional, List, Dict, Any, Union, Tuple
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, resolve_upload_file_path, wrap_tool_output, split_thought_process
from echo_constants import ECHO_USER_AGENT, ECHO_UPLOADS_DIR, get_gemini_mime

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle pour les sondages sémantiques.")
        MAX_READ_SIZE_KB: int = Field(default=512, description="Taille max par lecture brute.")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.uploads_dir = ECHO_UPLOADS_DIR

    async def read_raw_file_content(
        self, 
        file_id: str, 
        start_line: int = 1, 
        end_line: int = 500,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Sonde universelle du stockage physique."""
        events = EchoEvents(__event_emitter__, __event_call__)
        fpath = resolve_upload_file_path(file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text=f"❌ Fichier {file_id} introuvable.", status={"status": "error"})

        try:
            await events.status(f"📖 Lecture brute : {os.path.basename(fpath)}...")
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
                total = len(lines)
                subset = lines[start_line-1:end_line]
                content = "".join(subset)
            
            res_text = f"--- CONTENU BRUT (Lignes {start_line}-{min(end_line, total)} sur {total}) ---\n\n{content}\n\n--- FIN DU BLOC ---"
            await events.status(f"Lecture terminée.", done=True)
            return wrap_tool_output(text=res_text, status={"status": "success", "file": os.path.basename(fpath)})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def semantic_probe(
        self, 
        file_id: str, 
        query: str, 
        thinking_level: str = "MEDIUM",
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Sonde sémantiquement un fichier volumineux via Gemini Flash."""
        events = EchoEvents(__event_emitter__, __event_call__)
        fpath = resolve_upload_file_path(file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        token, project_id = self.auth.get_credentials(__user__.get("id"))
        if not token or not project_id: return wrap_tool_output(text="❌ Erreur Auth.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"🤖 Sondage Sémantique ({thinking_level})...")

        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            
            payload = {
                "model": self.valves.GEMINI_FLASH_MODEL,
                "project": project_id,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": query}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                    "generationConfig": {
                        "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.upper()},
                        "responseMimeType": "text/plain"
                    }
                }
            }

            full_text = ""
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                cand = data.get("response", {}).get("candidates", [])[0]
                                if "content" in cand:
                                    parts = cand["content"].get("parts", [])
                                    for p in parts:
                                        if "text" in p: full_text += p["text"]
                            except: pass

            clean_text, thoughts = split_thought_process(full_text)
            await events.status(f"🤖 Analyse terminée.", done=True)
            multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
            return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)
        except Exception as e: return wrap_tool_output(text=f"❌ Exception: {str(e)}", status={"status": "error"})

    async def calculate_file_hashes(
        self, 
        file_id: str, 
        algorithms: List[str] = ["sha256"],
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Calcule les empreintes numériques (Hash) d'un fichier."""
        events = EchoEvents(__event_emitter__, __event_call__)
        fpath = resolve_upload_file_path(file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        supported = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512, "sha3_256": hashlib.sha3_256, "sha3_512": hashlib.sha3_512}
        results = {}; active_hashes = {}; do_crc32 = False; crc32_val = 0

        for algo in algorithms:
            a_lower = algo.lower()
            if a_lower in supported: active_hashes[a_lower] = supported[a_lower]()
            elif a_lower == "crc32": do_crc32 = True
            else: results[algo] = "Unsupported"

        try:
            filename = os.path.basename(fpath)
            await events.status(f"🧮 Calcul des hashs pour {filename}...")
            with open(fpath, "rb") as f:
                while chunk := f.read(65536):
                    for h_obj in active_hashes.values(): h_obj.update(chunk)
                    if do_crc32: crc32_val = zlib.crc32(chunk, crc32_val)
            
            for name, h_obj in active_hashes.items(): results[name] = h_obj.hexdigest()
            if do_crc32: results["crc32"] = format(crc32_val & 0xFFFFFFFF, '08x')

            await events.status(f"Calculs terminés.", done=True)
            return wrap_tool_output(text=json.dumps(results, indent=2), status={"status": "success", "filename": filename})
        except Exception as e: return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"})
