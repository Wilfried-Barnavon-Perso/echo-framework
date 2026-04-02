"""
title: ECHO File Content Explorer
author: Wilfried BARNAVON
version: 5.97
description: 5.97: Refonte et factorisation de l'injection HUD via echo_utils.EchoUI.
"""

import os
import sys
import base64
import httpx
import orjson as json
import mimetypes
import hashlib
import zlib
from urllib.parse import urlparse, quote
from typing import Optional, List, Dict, Any, Union, Tuple, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    EchoAuth, EchoEvents, wrap_tool_output, 
    resolve_upload_file_path, get_echo_version, split_thought_process,
    EchoGeminiClient, EchoStateManager, generate_echo_file_id, EchoUI
)
from echo_constants import ECHO_UPLOADS_DIR, ECHO_USER_AGENT, GOOGLE_API_BASE_URL, get_gemini_mime, MODEL_FLASH

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default=MODEL_FLASH)
        UPLOADS_DIR: str = Field(default=ECHO_UPLOADS_DIR)
        KEY_SWITCH_THRESHOLD: int = Field(default=3, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        PROBE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour le sondage sémantique.")
        MAX_READ_SIZE_KB: int = Field(default=16, description="Taille maximale (en Ko) pour la lecture brute (RAW). Brider à 16 pour conformité API.")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.uploads_dir = self.valves.UPLOADS_DIR

    async def read_raw_file_content(
        self, 
        file_id: str, 
        start_line: int = 1, 
        end_line: int = 100, 
        output_mode: Literal["text", "base64", "hex"] = "text",
        byte_offset: int = 0,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Lit le contenu brut d'un fichier en mode Tell-Safe (évite le bug next() call).
        Supporte la pagination par octets (byte_offset) pour les fichiers massifs.
        Note : La sortie est strictement limitée à 16 Ko pour garantir la conformité avec l'API Gemini.
        :param file_id: L'identifiant du fichier.
        :param start_line: Ligne de début (ignoré si byte_offset > 0 ou mode != text).
        :param end_line: Ligne de fin (inclusive, ou limite relative si byte_offset > 0).
        :param output_mode: Mode d'affichage ('text', 'base64' ou 'hex').
        :param byte_offset: (Optionnel) Déplacement en octets depuis le début du fichier.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        
        if not fpath:
            return wrap_tool_output(text="❌ Fichier introuvable dans le Vault ECHO.", status={"status": "error"})

        size = os.path.getsize(fpath)
        if byte_offset > size:
            return wrap_tool_output(text=f"❌ L'offset ({byte_offset}) dépasse la taille du fichier ({size} octets).", status={"status": "error"})

        MAX_CHARS = self.valves.MAX_READ_SIZE_KB * 1024
        msg = f"📂 Lecture ({output_mode}) : {os.path.basename(fpath)} (Offset: {byte_offset})..."
        await events.status(msg)

        try:
            new_offset = byte_offset
            
            if output_mode == "text":
                lines_to_read = []
                # Utilisation de f.readline() au lieu de for line in f pour éviter le bug d'itération/tell()
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    if byte_offset > 0:
                        f.seek(byte_offset)
                        limit = max(1, end_line - start_line + 1)
                        for i in range(limit):
                            line = f.readline()
                            if not line: break
                            lines_to_read.append(f"+{i} | {line.rstrip()}")
                            if len("\n".join(lines_to_read)) >= MAX_CHARS: break
                    else:
                        # Recherche de la ligne de début
                        current_l = 1
                        while current_l < start_line:
                            if not f.readline(): break
                            current_l += 1
                        
                        while current_l <= end_line:
                            line = f.readline()
                            if not line: break
                            lines_to_read.append(f"{current_l:4d} | {line.rstrip()}")
                            current_l += 1
                            if len("\n".join(lines_to_read)) >= MAX_CHARS: break
                    
                    new_offset = f.tell()
                    content = "\n".join(lines_to_read)
                    suffix = "\n[... SORTIE TRONQUÉE À 16Ko ...]" if len(content) >= MAX_CHARS else ""
                    res_text = f"--- CONTENU TEXTE (Offset {byte_offset} à {new_offset}) ---\n\n{content}{suffix}\n\n--- FIN (Nouvel offset: {new_offset} / {size}) ---"
            
            elif output_mode == "base64":
                # Limite Base64 : 12Ko source -> 16Ko destination
                max_bytes = 12288
                with open(fpath, 'rb') as f:
                    f.seek(byte_offset)
                    raw_data = f.read(max_bytes)
                    new_offset = f.tell()
                    content = base64.b64encode(raw_data).decode('utf-8')
                
                suffix = " (TRONQUÉ À 12Ko SOURCE)" if (size - byte_offset) > max_bytes else ""
                res_text = f"--- CONTENU BASE64{suffix} (Offset {byte_offset} à {new_offset}) ---\n\n{content}\n\n--- FIN ---"

            else:
                # Mode HEX : 8Ko source -> 16Ko destination
                max_bytes = 8192
                with open(fpath, 'rb') as f:
                    f.seek(byte_offset)
                    raw_data = f.read(max_bytes)
                    new_offset = f.tell()
                
                import binascii
                content = binascii.hexlify(raw_data, sep=' ', bytes_per_sep=2).decode('utf-8')
                suffix = " (TRONQUÉ À 8Ko)" if (size - byte_offset) > max_bytes else ""
                res_text = f"--- CONTENU HEXADECIMAL{suffix} (Offset {byte_offset} à {new_offset}) ---\n\n{content}\n\n--- FIN ---"

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
        Supporte la résilience multi-clés.
        :param file_id: L'identifiant du fichier à analyser.
        :param query: Votre question ou instruction précise pour l'analyse.
        :param thinking_level: Niveau de réflexion du modèle (MINIMAL, LOW, MEDIUM, HIGH). Par défaut HIGH.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        api_keys = self.auth.get_api_keys(uid)
        if not api_keys: return wrap_tool_output(text="❌ Erreur Auth: Clé API Google AI Studio introuvable.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"🤖 Sondage Sémantique de {os.path.basename(fpath)}...")

        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": query}, {"inlineData": {"mimeType": mime, "data": b64}}]}],
                "generationConfig": {
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.lower()},
                    "responseMimeType": "text/plain"
                }
            }

            data = await EchoGeminiClient.call(
                keys=api_keys,
                target_model=self.valves.GEMINI_FLASH_MODEL,
                payload=payload,
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=3,
                events=events,
                timeout=self.valves.PROBE_TIMEOUT
            )
            
            target = data.get("response", {}) if "response" in data else data
            candidates = target.get("candidates", [])
            full_text = ""
            if candidates and candidates[0].get("content"):
                for p in candidates[0]["content"].get("parts", []):
                    if "text" in p: full_text += p["text"]

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
        Récupère les métadonnées techniques d'un fichier.
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
        Calcule les hashs SHA-256 de plusieurs fichiers.
        """
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

    async def show_image_to_user(
        self, 
        file_id: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Affiche une image stockée dans le Vault ECHO avec interface HUD interactive.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Image introuvable.", status={"status": "error"})
        
        mime, _ = mimetypes.guess_type(fpath)
        with open(fpath, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode('utf-8')
        
        if __event_call__:
            await EchoUI.monitor_ECHO(
                events=events,
                b64=b64,
                mime=mime or "image/png",
                hud_id=f"echo-viewer-{file_id}",
                title="📸 ECHO VIEWER",
                state_key=f"echo_viewer_state_{file_id}",
                timeout=0
            )

        return wrap_tool_output(
            text=f"✅ L'image '{os.path.basename(fpath)}' a été affichée physiquement à l'utilisateur.", 
            status={"status": "success"}
        )

    async def download_from_url(
        self, 
        url: str, 
        filename: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Télécharge un fichier depuis une URL et l'intègre au Registre ECHO.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        cid = __metadata__.get("chat_id", "unknown")
        
        user_vault = os.path.join(self.uploads_dir, f"U_{uid}")
        os.makedirs(user_vault, exist_ok=True)
        
        # Génération d'un ID ECHO unique
        file_id = generate_echo_file_id(uid, cid)
        
        parsed_url = urlparse(url)
        orig_name = filename or os.path.basename(parsed_url.path) or "downloaded_file"
        safe_name = quote(orig_name)
        
        final_filename = f"{file_id}_{safe_name}"
        fpath = os.path.join(self.uploads_dir, final_filename)
        
        await events.status(f"📥 Téléchargement de {url}...")
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("GET", url, headers={"User-Agent": ECHO_USER_AGENT}) as resp:
                    resp.raise_for_status()
                    mime_type = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                    with open(fpath, 'wb') as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
            
            # SCELLEMENT BDD (Pour Suture au prochain tour)
            state_manager = EchoStateManager(user_id=uid, chat_id=cid)
            state_manager.mark_processed(cid, file_id, orig_name, mime_type, "transmitted")
            state_manager.move_to_vault(file_id, orig_name)

            await events.status(f"✅ Téléchargé et Scellé : {orig_name}", done=True)
            
            nouveau_fichier = {
                "id": file_id,
                "name": orig_name,
                "meta": {"content_type": mime_type},
                "type": "file"
            }
            
            return wrap_tool_output(
                text=f"✅ Fichier '{orig_name}' téléchargé avec succès (ID: {file_id}).", 
                status={"status": "success"},
                nouveaux_fichiers=[nouveau_fichier]
            )
        except Exception as e:
            if os.path.exists(fpath): os.remove(fpath)
            return wrap_tool_output(text=f"❌ Échec du téléchargement : {str(e)}", status={"status": "error"})
