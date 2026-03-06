"""
title: ECHO File Content Explorer
author: ECHO Framework
version: 1.12
description: 1.12: Universal Probe Protocol (Support for application/octet-stream).
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
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field

# Importations ECHO Strictes
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, resolve_upload_file_path
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
    ) -> str:
        """
        Sonde universelle du stockage physique. À utiliser impérativement pour lire le contenu brut de tout fichier 
        dont le type MIME est 'application/octet-stream' ou si l'IA nécessite une vérification factuelle 
        approfondie non couverte par le résumé initial (Smart Context).
        
        Cet outil est votre accès direct aux données brutes du système de fichiers ECHO.

        :param file_id: L'UUID du fichier (provenant du Registre Technique).
        :param start_line: Ligne de début (index 1).
        :param end_line: Ligne de fin.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        fpath = resolve_upload_file_path(file_id, self.uploads_dir)
        if not fpath: return f"❌ Fichier {file_id} introuvable."

        try:
            await events.status(f"📖 Lecture brute : {os.path.basename(fpath)}...")
            with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                lines = f.readlines()
                total = len(lines)
                subset = lines[start_line-1:end_line]
                content = "".join(subset)
            
            await events.status(f"Lecture terminée ({len(subset)} lignes).", done=True)
            return f"--- CONTENU BRUT (Lignes {start_line}-{min(end_line, total)} sur {total}) ---\n\n{content}\n\n--- FIN DU BLOC ---"
        except Exception as e:
            return f"❌ Erreur de lecture : {str(e)}"

    async def semantic_probe(
        self, 
        file_id: str, 
        query: str, 
        thinking_level: str = "MEDIUM",
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Sonde sémantiquement un fichier volumineux (image, PDF, long texte) via Gemini Flash.
        Idéal pour : 'Trouve le passage qui parle de X' ou 'Décris ce schéma'.
        
        :param file_id: L'UUID du fichier.
        :param query: Votre question précise sur le contenu.
        :param thinking_level: Niveau d'intensité de la réflexion (MINIMAL, LOW, MEDIUM, HIGH).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        fpath = resolve_upload_file_path(file_id, self.uploads_dir)
        if not fpath: return f"❌ Fichier {file_id} introuvable."

        token, project_id = self.auth.get_credentials(__user__.get("id"))
        if not token or not project_id: return "❌ Erreur Auth."

        mime, supported = get_gemini_mime(fpath)
        if not supported: return f"❌ Type {mime} non supporté pour le sondage."

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
                        "thinkingConfig": {"thinkingLevel": thinking_level.upper()},
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
                                    if parts and "text" in parts[0]: full_text += parts[0]["text"]
                            except: pass

            await events.status(f"🤖 Analyse terminée.", done=True)
            return f"🤖 **Sondage Sémantique ({thinking_level}) :**\n\n{full_text}"
        except Exception as e: return f"❌ Exception: {str(e)}"
