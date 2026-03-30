"""
title: ECHO File Content Explorer
author: Wilfried BARNAVON
version: 5.83
description: 5.83: Set Flash MODEL.
"""

import os
import sys
import base64
import httpx
import orjson as json
import mimetypes
from typing import Optional, List, Dict, Any, Union, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    EchoAuth, EchoEvents, wrap_tool_output, 
    resolve_upload_file_path, get_echo_version, split_thought_process
)
from echo_constants import ECHO_UPLOADS_DIR, ECHO_USER_AGENT, GOOGLE_API_BASE_URL, get_gemini_mime, MODEL_FLASH

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default=MODEL_FLASH)
        UPLOADS_DIR: str = Field(default=ECHO_UPLOADS_DIR)

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.uploads_dir = self.valves.UPLOADS_DIR

    async def read_raw_file_content(
        self, 
        file_id: str, 
        start_line: int = 1, 
        end_line: int = 100, 
        output_mode: Literal["text", "hex"] = "text",
        byte_offset: int = 0,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Lit le contenu brut d'un fichier. Supporte la lecture par lignes ou par déplacement direct (byte_offset) pour les fichiers massifs.
        :param file_id: L'identifiant du fichier.
        :param start_line: Ligne de début (ignoré si byte_offset > 0).
        :param end_line: Ligne de fin (inclusive, ou limite relative si byte_offset > 0).
        :param output_mode: Mode d'affichage ('text' ou 'hex').
        :param byte_offset: (Optionnel) Déplacement en octets depuis le début du fichier. Idéal pour reprendre une lecture.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        
        if not fpath:
            return wrap_tool_output(text="❌ Fichier introuvable dans le Vault ECHO.", status={"status": "error"})

        size = os.path.getsize(fpath)
        if byte_offset > size:
            return wrap_tool_output(text=f"❌ L'offset ({byte_offset}) dépasse la taille du fichier ({size} octets).", status={"status": "error"})

        msg = f"📂 Lecture de {os.path.basename(fpath)} (Offset: {byte_offset})..." if byte_offset > 0 else f"📂 Lecture de {os.path.basename(fpath)} (L{start_line}-L{end_line})..."
        await events.status(msg)

        try:
            new_offset = byte_offset
            
            if output_mode == "text":
                lines_to_read = []
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    if byte_offset > 0:
                        f.seek(byte_offset)
                        # On lit un nombre de lignes équivalent à (end_line - start_line) + 1
                        limit = max(1, end_line - start_line + 1)
                        for i in range(limit):
                            line = f.readline()
                            if not line: break
                            lines_to_read.append(f"+{i} | {line.rstrip()}")
                        new_offset = f.tell()
                        content = "\n".join(lines_to_read)
                        res_text = f"--- CONTENU TEXTE (Depuis offset {byte_offset}) ---\n\n{content}\n\n--- FIN (Nouvel offset: {new_offset} / {size}) ---"
                    else:
                        for i, line in enumerate(f, 1):
                            if i >= start_line and i <= end_line:
                                lines_to_read.append(f"{i:4d} | {line.rstrip()}")
                            if i > end_line: 
                                new_offset = f.tell()
                                break
                        content = "\n".join(lines_to_read)
                        res_text = f"--- CONTENU TEXTE (L{start_line} à L{end_line}) ---\n\n{content}\n\n--- FIN (Nouvel offset estimé: {new_offset} / {size}) ---"
            
            else:
                # Mode HEX
                max_bytes = 8192
                with open(fpath, 'rb') as f:
                    f.seek(byte_offset)
                    raw_data = f.read(max_bytes)
                    new_offset = f.tell()
                
                import binascii
                content = binascii.hexlify(raw_data, sep=' ', bytes_per_sep=2).decode('utf-8')
                suffix = " (TRONQUÉ À 8Ko)" if (size - byte_offset) > max_bytes else ""
                res_text = f"--- CONTENU HEXADECIMAL{suffix} (Offset {byte_offset} à {new_offset}) ---\n\n{content}\n\n--- FIN (Nouvel offset: {new_offset} / {size}) ---"

            await events.status(f"Lecture terminée.", done=True)
            return wrap_tool_output(text=res_text, status={"status": "success", "file": os.path.basename(fpath), "new_offset": new_offset})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

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
        Sonde sémantiquement un fichier volumineux ou complexe via Gemini Flash.
        Permet d'extraire le sens, de résumer ou de chercher des informations spécifiques sans lire tout le fichier brute.
        :param file_id: L'identifiant du fichier à analyser.
        :param query: Votre question ou instruction précise pour l'analyse.
        :param thinking_level: Niveau de réflexion du modèle (MINIMAL, LOW, MEDIUM, HIGH). Par défaut HIGH.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        api_key = self.auth.get_api_key(__user__.get("id"))
        if not api_key: return wrap_tool_output(text="❌ Erreur Auth: Clé API Google AI Studio introuvable.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"🤖 Sondage Sémantique de {os.path.basename(fpath)}...")

        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            
            # URL AI Studio standard (v1beta)
            url = f"{GOOGLE_API_BASE_URL}/models/{self.valves.GEMINI_FLASH_MODEL}:streamGenerateContent?key={api_key}&alt=sse"
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json", 
                "User-Agent": ECHO_USER_AGENT
            }
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": query}, {"inlineData": {"mimeType": mime, "data": b64}}]}],
                "generationConfig": {
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.lower()},
                    "responseMimeType": "text/plain"
                }
            }

            full_text = ""
            async with httpx.AsyncClient(timeout=120, http2=True) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        err = await resp.aread()
                        return wrap_tool_output(text=f"❌ Erreur API Gemini ({resp.status_code}): {err.decode()}", status={"status": "error"})
                        
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[5:].strip())
                                candidates = data.get("candidates", [])
                                if candidates and candidates[0].get("content"):
                                    for p in candidates[0]["content"].get("parts", []):
                                        if "text" in p: full_text += p["text"]
                            except: pass

            clean_text, thoughts = split_thought_process(full_text)
            await events.status(f"🤖 Analyse terminée.", done=True)
            multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
            return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Sonde: {str(e)}", status={"status": "error"})

    async def get_file_metadata(
        self, 
        file_id: str, 
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Récupère les métadonnées techniques d'un fichier (taille exacte en octets, type MIME, etc.).
        Indispensable avant de lire un gros fichier pour déterminer le `byte_offset` de fin (ex: lire un log à l'envers).
        :param file_id: L'identifiant du fichier.
        """
        import datetime
        events = EchoEvents(__event_emitter__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        
        if not fpath:
            return wrap_tool_output(text="❌ Fichier introuvable dans le Vault ECHO.", status={"status": "error"})

        try:
            stat_info = os.stat(fpath)
            size_bytes = stat_info.st_size
            
            # Formatage lisible de la taille
            for unit in ['octets', 'Ko', 'Mo', 'Go', 'To']:
                if size_bytes < 1024.0:
                    size_str = f"{size_bytes:3.1f} {unit}"
                    break
                size_bytes /= 1024.0
            else:
                size_str = f"{size_bytes:.1f} Po"
                
            mime, _ = get_gemini_mime(fpath)
            mod_time = datetime.datetime.fromtimestamp(stat_info.st_mtime).strftime('%Y-%m-%d %H:%M:%S')
            
            metadata = {
                "Nom": os.path.basename(fpath),
                "Taille (octets)": stat_info.st_size,
                "Taille (lisible)": size_str,
                "Type MIME": mime,
                "Dernière modification": mod_time
            }
            
            output = "📊 **Métadonnées du fichier**\n"
            for k, v in metadata.items():
                output += f"- **{k}** : {v}\n"
                
            return wrap_tool_output(text=output, status={"status": "success", "size_bytes": stat_info.st_size})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur lecture métadonnées : {str(e)}", status={"status": "error"})

    async def calculate_file_hashes(
        self, 
        file_ids: List[str], 
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Calcule les hashs SHA-256 de plusieurs fichiers pour vérification d'intégrité ou dédoublonnage.
        :param file_ids: Liste des identifiants de fichiers.
        """
        import hashlib
        events = EchoEvents(__event_emitter__)
        uid = __user__.get("id", "anonymous")
        results = []
        
        for fid in file_ids:
            fpath = resolve_upload_file_path(uid, fid, self.uploads_dir)
            if fpath:
                with open(fpath, 'rb') as f:
                    h = hashlib.sha256(f.read()).hexdigest()
                results.append(f"{os.path.basename(fpath)}: {h}")
            else:
                results.append(f"{fid}: INTROUVABLE")
        
        return "Hashs SHA-256 :\n" + "\n".join(results)

    async def show_image(
        self, 
        file_id: str, 
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> dict:
        """
        Affiche une image stockée dans le Vault ECHO directement dans l'interface de chat.
        :param file_id: L'identifiant de l'image.
        """
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Image introuvable.", status={"status": "error"})
        
        mime, _ = mimetypes.guess_type(fpath)
        with open(fpath, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        
        multiparts = [{"type": "media", "mime_type": mime or "image/png", "data": b64}]
        return wrap_tool_output(text=f"Affichage de l'image : {os.path.basename(fpath)}", status={"status": "success"}, echo_tool_multiparts=multiparts)

    async def download_from_url(
        self, 
        url: str, 
        filename: Optional[str] = None,
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Télécharge un fichier depuis une URL et l'ajoute au coffre-fort ECHO de l'utilisateur.
        :param url: L'URL directe du fichier à télécharger.
        :param filename: (Optionnel) Nom à donner au fichier.
        """
        events = EchoEvents(__event_emitter__)
        uid = __user__.get("id", "anonymous")
        user_vault = os.path.join(self.uploads_dir, f"U_{uid}")
        os.makedirs(user_vault, exist_ok=True)
        
        fname = filename or url.split('/')[-1] or "downloaded_file"
        fpath = os.path.join(user_vault, fname)
        
        await events.status(f"📥 Téléchargement de {url}...")
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                with open(fpath, 'wb') as f:
                    f.read(resp.content)
            
            await events.status(f"✅ Téléchargé : {fname}", done=True)
            return f"Fichier téléchargé avec succès dans le Vault : {fname}"
        except Exception as e:
            return f"❌ Échec du téléchargement : {str(e)}"
