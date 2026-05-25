"""
title: ECHO Explorateur de l'Espace Personnel
author: Wilfried BARNAVON
version: 5.109.8
description: 5.109.5: Refactorisation terminologique (Vault Explorer → Explorateur de l'Espace Personnel). 5.109.6: Correction show_image_to_user (injection JS via events). 5.109.7: Ajout UserValves ANALYSE_MODEL pour semantic_probe (MODEL_FLASH → niveau cognitif paramétrable). 5.109.8: Fix import manquant TEMP_DEFAULT/TOP_P_DEFAULT (NameError dans semantic_probe).
"""

import os
import sys
import base64
import httpx
import orjson as json
import mimetypes
import hashlib
from urllib.parse import urlparse, quote
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    EchoEvents, wrap_tool_output, 
    resolve_upload_file_path, split_thought_process,
    EchoGeminiClient, EchoStateManager, generate_echo_file_id,
    get_stealth_headers
)
from echo_ui import EchoUI
from echo_constants import (
    ECHO_UPLOADS_TRANSIT_DIR, get_gemini_mime, MODEL_FLASH,
    MODEL_ROUTING, ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT
)

class Tools:
    class Valves(BaseModel):
        UPLOADS_DIR: str = Field(default=ECHO_UPLOADS_TRANSIT_DIR)
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        PROBE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour le sondage sémantique.")
        MAX_READ_SIZE_KB: int = Field(default=16, description="Taille maximale (en Ko) pour la lecture brute (RAW).")
        MAX_MULTIMODAL_SIZE_KB: int = Field(default=102400, description="Taille maximale (en Ko) pour l'injection multimédia.")

    class UserValves(BaseModel):
        ANALYSE_MODEL: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = Field(
            default="MODEL_FLASH",
            description="Niveau cognitif utilisé pour le sondage sémantique (semantic_probe). MODEL_PRO pour les fichiers très complexes."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.uploads_dir = self.valves.UPLOADS_DIR
        self.user_valves = self.UserValves()

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
        Lit le contenu brut d'un fichier en mode pagination (offset).
        Note : La sortie est strictement limitée à 16 Ko pour conformité API Gemini.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        size = os.path.getsize(fpath)
        if byte_offset > size: return wrap_tool_output(text=f"❌ Offset invalide.", status={"status": "error"})

        MAX_CHARS = self.valves.MAX_READ_SIZE_KB * 1024
        await events.status(f"📂 Lecture : {os.path.basename(fpath)}...")

        try:
            new_offset = byte_offset
            if output_mode == "text":
                lines = []
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(byte_offset)
                    for i in range(end_line - start_line + 1):
                        line = f.readline()
                        if not line: break
                        lines.append(f"+{i} | {line.rstrip()}")
                        if len("\n".join(lines)) >= MAX_CHARS: break
                    new_offset = f.tell()
                res_text = f"--- CONTENU TEXTE (Offset {byte_offset} à {new_offset}) ---\n\n" + "\n".join(lines)
            elif output_mode == "base64":
                with open(fpath, 'rb') as f:
                    f.seek(byte_offset)
                    content = base64.b64encode(f.read(12288)).decode('utf-8')
                    new_offset = f.tell()
                res_text = f"--- CONTENU BASE64 ---\n\n{content}"
            else:
                with open(fpath, 'rb') as f:
                    f.seek(byte_offset)
                    import binascii
                    content = binascii.hexlify(f.read(8192), sep=' ', bytes_per_sep=2).decode('utf-8')
                    new_offset = f.tell()
                res_text = f"--- CONTENU HEXADECIMAL ---\n\n{content}"

            await events.status("Lecture terminée.", done=True)
            return wrap_tool_output(text=res_text, status={"status": "success", "new_offset": new_offset, "total_size": size})
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
        """Sonde sémantiquement un fichier volumineux ou complexe via le niveau cognitif configuré (ANALYSE_MODEL)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        u_valves = __user__.get("valves", self.user_valves) if __user__ else self.user_valves
        resolved_model = MODEL_ROUTING.get(u_valves.ANALYSE_MODEL, MODEL_FLASH)
        user_id = __user__.get("id", "system")
        fpath = resolve_upload_file_path(user_id, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"🤖 Analyse de {os.path.basename(fpath)}...")
        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            payload = {
                "contents": [{"role": "user", "parts": [{"text": query}, {"inlineData": {"mimeType": mime, "data": b64}}]}],
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                    "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.lower()}
                }
            }
            data = await EchoGeminiClient.call(
                target_model=resolved_model,
                payload=payload, 
                user_id=user_id,
                threshold=self.valves.KEY_SWITCH_THRESHOLD,
                max_retries=self.valves.MAX_RETRIES,
                events=events,
                timeout=self.valves.PROBE_TIMEOUT
            )
            target = data.get("response", {}) if "response" in data else data
            cand = target.get("candidates", [])[0]
            full_text = "".join([p["text"] for p in cand["content"]["parts"] if "text" in p])
            clean, thought = split_thought_process(full_text)
            return wrap_tool_output(text=clean, status={"status": "success"}, echo_tool_multiparts=[{"type": "thought", "content": thought}] if thought else [])
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Sonde : {str(e)}", status={"status": "error"})

    async def read_multimedia_file(
        self, 
        file_id: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Transmet un fichier multimédia au moteur Gemini."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"👁️ Injection {os.path.basename(fpath)}...")
        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            return wrap_tool_output(
                text=f"✅ Média chargé dans le cortex.", 
                status={"status": "success"},
                echo_tool_multiparts=[{"type": "media", "mime_type": mime, "data": b64}]
            )
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def show_image_to_user(
        self, 
        target: str, 
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Affiche une image (Locale ou Distante) dans un viewer flottant injecté dans le DOM.
        Supporte file_id ou URL http(s).
        Retourne un dict standard (wrap_tool_output) — jamais un Tuple ou HTMLResponse.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        is_url = target.lower().startswith(("http://", "https://"))
        
        # Sécurité anti-chemins absolus
        if not is_url and (target.startswith("/") or (len(target) > 1 and target[1] == ":")):
             return wrap_tool_output(text="❌ Sécurité : Accès restreint.", status={"status": "error"})

        try:
            if is_url:
                await events.status("🌐 Validation image distante...")
                # Vérification HEAD préalable (lève les 403/404 tôt)
                h = get_stealth_headers(target)
                async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as c:
                    r = await c.head(target, headers=h)
                    if r.status_code >= 400:
                        # Certains serveurs bloquent HEAD, on tente un GET partiel
                        r = await c.get(target, headers=h, timeout=10.0)
                        r.raise_for_status()
                
                title = f"Distant : {target[:50]}..."
                img_url = target
            else:
                fpath = resolve_upload_file_path(uid, target, self.uploads_dir)
                if not fpath:
                    return wrap_tool_output(text=f"❌ Image '{target}' introuvable.", status={"status": "error"})
                mime, _ = mimetypes.guess_type(fpath)
                with open(fpath, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                img_url = f"data:{mime or 'image/png'};base64,{b64}"
                title = f"Espace Personnel : {os.path.basename(fpath)}"

            # Injection JS pure dans le DOM Open WebUI — aucun retour HTMLResponse
            # Pattern identique à EchoUI.monitor_ECHO (events.call "execute")
            js_code = EchoUI.show_image_js(img_url, title)
            if events and (events.caller or events.emitter):
                await events.call("execute", {"code": js_code})
            
            await events.status("✅ Image affichée.", done=True)
            return wrap_tool_output(text=f"✅ Image affichée dans le viewer ECHO.\n\n**Titre :** {title}", status={"status": "success"})

        except Exception as e:
            return wrap_tool_output(text=f"❌ Échec affichage : {str(e)}", status={"status": "error"})

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
        Télécharge un fichier avec le Stealth Engine (Mimic Browser).
        Gère les redirections (302) et les protections anti-bot (403).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        cid = __metadata__.get("chat_id", "unknown")
        os.makedirs(os.path.join(self.uploads_dir, f"U_{uid}"), exist_ok=True)
        file_id = generate_echo_file_id(uid, cid)
        orig_name = filename or os.path.basename(urlparse(url).path.split('?')[0]) or "file"
        fpath = os.path.join(self.uploads_dir, f"{file_id}_{quote(orig_name)}")
        
        await events.status(f"📥 Récupération Stealth : {url[:50]}...")
        try:
            h = get_stealth_headers(url)
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                # Wikimedia/Wikipedia nécessitent souvent une session cohérente
                async with client.stream("GET", url, headers=h) as resp:
                    resp.raise_for_status()
                    furl = str(resp.url)
                    mime = resp.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                    with open(fpath, 'wb') as f:
                        async for chunk in resp.aiter_bytes(): f.write(chunk)
            
            EchoStateManager(user_id=uid, chat_id=cid).mark_processed(cid, file_id, orig_name, mime, "transmitted")
            EchoStateManager(user_id=uid, chat_id=cid).move_to_vault(file_id, orig_name) # move_to_vault : déplace dans l'Espace Personnel

            return wrap_tool_output(
                text=f"✅ Téléchargement réussi (ID: {file_id}).\nSource: {url}\nDestination finale: {furl}", 
                status={"status": "success", "file_id": file_id}
            )
        except Exception as e:
            if os.path.exists(fpath): os.remove(fpath)
            return wrap_tool_output(text=f"❌ Échec téléchargement : {str(e)}", status={"status": "error"})

    async def get_file_metadata(self, file_id: str, __user__: dict = {}, __event_emitter__: Any = None) -> str:
        """Récupère les métadonnées techniques d'un fichier."""
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})
        stat = os.stat(fpath)
        mime, _ = get_gemini_mime(fpath)
        return wrap_tool_output(text=f"📊 **{os.path.basename(fpath)}**\nTaille: {stat.st_size} octets\nType: {mime}", status={"status": "success"})

    async def calculate_file_hashes(self, file_ids: List[str], __user__: dict = {}, __event_emitter__: Any = None) -> str:
        """Calcule les hashs SHA-256 de fichiers."""
        uid = __user__.get("id", "anonymous")
        res = []
        for fid in file_ids:
            p = resolve_upload_file_path(uid, fid, self.uploads_dir)
            if p:
                with open(p, 'rb') as f: h = hashlib.sha256(f.read()).hexdigest()
                res.append(f"{os.path.basename(p)}: {h}")
        return wrap_tool_output(text="\n".join(res), status={"status": "success"})
