"""
title: ECHO Explorateur de l'Espace Personnel
author: Wilfried BARNAVON
version: 5.231.32.1
description: Composant système interne : ECHO Explorateur de l'Espace Personnel.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.32.1: Surcharge d'instruction pour read_multimedia_file, correction d'assignation SYS/USR prompt et docstring descriptive étendue.
# 5.231.29.1: Alignement de _resolve_extended_file_path sur les constantes SSOT CODEX_DIR_NAME, CODEX_WORKSPACE_MAIN et CODEX_WORKSPACE_SANDBOX.
# 5.231.20.1: Isolation multi-chat du viewer d'image via transmission de chat_id à EchoUI.show_image_js.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.228.7.1: Factorisation de la détection textuelle via is_text_mime (echo_constants).

import os
import sys
import base64
import httpx
import mimetypes
import hashlib
import asyncio
from typing import List, Any, Literal, Optional
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_core import wrap_tool_output, wrap_cascade_output, split_thought_process
from echo_paths import resolve_upload_file_path, get_echo_session_path
from echo_state_manager import EchoStateManager
from echo_gemini_client import EchoGeminiClient
from echo_http import get_stealth_headers
from echo_ui import EchoUI
from echo_constants import (
    CODEX_DIR_NAME,
    CODEX_WORKSPACE_MAIN,
    CODEX_WORKSPACE_SANDBOX,
    ECHO_API_KEY_RETRIES,
    ECHO_API_MAX_RETRIES,
    ECHO_UPLOADS_TRANSIT_DIR,
    get_generation_config,
    get_gemini_mime,
    is_text_mime,
)
from echo_prompts import SYS_EXPLORE_SENSORY, SYS_EXPLORER_PROBE, USR_EXPLORE_SENSORY

class Tools:
    class Valves(BaseModel):
        PROBE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour le sondage sémantique.")
        MAX_READ_SIZE_KB: int = Field(default=16, description="Taille maximale (en Ko) pour la lecture brute (RAW).")
        MAX_MULTIMODAL_SIZE_KB: int = Field(default=30720, description="Taille maximale (en Ko) pour l'injection multimédia (défaut: 30 Mo).")

    def __init__(self):
        self.valves = self.Valves()
        self.uploads_dir = ECHO_UPLOADS_TRANSIT_DIR

    def _resolve_extended_file_path(self, uid: str, cid: str, target: str) -> str:
        """Résout le chemin d'un fichier (main, upload, ou sandbox)."""
        if not target: return None
        target = target.strip()
        safe_target = target.replace("\\", "/").strip("/")
        
        if cid:
            # 1. Résolution Registre (Codex 'main')
            state = EchoStateManager(user_id=uid, chat_id=cid)
            resource = state.get_resource(target)
            if resource and resource.get("resource_type") == "codex":
                return os.path.join(
                    get_echo_session_path(uid, cid, CODEX_DIR_NAME),
                    CODEX_WORKSPACE_MAIN,
                    resource.get("name", target),
                )
                
        # 2. Upload Standard (files)
        fpath = resolve_upload_file_path(uid, target, self.uploads_dir, chat_id=cid)
        if fpath: return fpath
            
        # 3. Fallback 'Sandbox' (fichier brut non indexé)
        if cid and ".." not in safe_target.split("/"):
            sandbox_path = os.path.join(
                get_echo_session_path(uid, cid, CODEX_DIR_NAME),
                CODEX_WORKSPACE_SANDBOX,
                safe_target,
            )
            if os.path.exists(sandbox_path) and os.path.isfile(sandbox_path):
                return sandbox_path
                
        return None

    async def read_raw_file_content(
        self, 
        file_id: str, 
        start_line: int = 1, 
        end_line: int = 100, 
        output_mode: Literal["text", "base64", "hex"] = "text",
        byte_offset: int = 0,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Lit un fichier de l'Espace Personnel (Uploads, Codex `main` ou `sandbox`) par lignes ou offset binaire."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = self._resolve_extended_file_path(uid, __metadata__.get("chat_id"), file_id)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        size = os.path.getsize(fpath)
        if byte_offset > size: return wrap_tool_output(text="❌ Offset invalide.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        MAX_CHARS = self.valves.MAX_READ_SIZE_KB * 1024
        safe_name = os.path.basename(fpath)
        if "_" in safe_name and len(safe_name.split("_")[0]) >= 32: safe_name = safe_name.split("_", 1)[1]
        await events.status(f"📂 Lecture : {safe_name}...")

        try:
            new_offset = byte_offset
            mime, _ = get_gemini_mime(fpath)
            ext = os.path.splitext(fpath)[1].lower()
            is_text = is_text_mime(mime, ext)

            warning_msg = ""
            if output_mode == "text" and not is_text:
                warning_msg = f"\n\n[ATTENTION SYSTÈME ECHO]\nCe fichier est un binaire ({mime}). Si le texte extrait ci-dessus est illisible, incomplet ou ne correspond pas aux attentes, le Modèle a la STRICTE INTERDICTION de boucler en rappelant l'outil avec d'autres lignes/offsets.\n=> Le Modèle DOIT utiliser output_mode='base64' pour une injection multimodale native (PDF/Images/Media), ou interroger le RAG via `semantic_probe`."

            if output_mode == "text":
                lines = []
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(byte_offset)
                    
                    if byte_offset == 0:
                        if start_line > 1:
                            for _ in range(start_line - 1):
                                if not f.readline(MAX_CHARS): break
                                
                        total_read = 0
                        for i in range(start_line, end_line + 1):
                            line = f.readline(MAX_CHARS)
                            if not line: break
                            
                            # Détection de la troncature d'une ligne trop longue
                            if len(line) == MAX_CHARS and not line.endswith('\n'):
                                f.readline()  # purge le reste de la ligne dans le flux
                                line += " [...tronqué]"
                                
                            total_read += len(line)
                            clean_line = line.replace('\x00', '').rstrip('\r\n')
                            lines.append(f"{i} | {clean_line}")
                            if total_read >= MAX_CHARS:
                                lines.append(f"... [Tronqué : limite de {self.valves.MAX_READ_SIZE_KB} Ko atteinte]")
                                break
                        new_offset = f.tell()
                        res_text = f"--- CONTENU TEXTE (Lignes {start_line} à {end_line}, Offset 0 à {new_offset}) ---\n\n" + "\n".join(lines) + warning_msg
                    else:
                        # Lecture brute par offset (évite l'hallucination des lignes virtuelles)
                        raw_chunk = f.read(MAX_CHARS).replace('\x00', '')
                        lines.append(raw_chunk)
                        new_offset = f.tell()
                        res_text = f"--- CONTENU TEXTE (Offset {byte_offset} à {new_offset}) ---\n\n" + raw_chunk + warning_msg
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
            return wrap_tool_output(text=res_text, status={"status": "success", "new_offset": str(new_offset), "total_size": str(size)}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def semantic_probe(
        self, 
        file_id: str, 
        query: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Analyse sémantiquement un fichier volumineux ou binaire (PDF, Média) sans saturer le contexte."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        fpath = self._resolve_extended_file_path(user_id, __metadata__.get("chat_id"), file_id)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        file_size_kb = os.path.getsize(fpath) / 1024
        if file_size_kb > self.valves.MAX_MULTIMODAL_SIZE_KB:
            return wrap_tool_output(
                text=f"❌ Max Size Exceeded : Le fichier ({file_size_kb / 1024:.1f} Mo) dépasse la limite autorisée de {self.valves.MAX_MULTIMODAL_SIZE_KB / 1024:.1f} Mo pour l'analyse multimédia.", 
                status={"status": "error"}
            )

        safe_name = os.path.basename(fpath)
        if "_" in safe_name and len(safe_name.split("_")[0]) >= 32: safe_name = safe_name.split("_", 1)[1]
        await events.status(f"🤖 Analyse de {safe_name}...")
        try:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": query}, {"inline_data": {"mime_type": mime, "data": f"___ECHO_STREAM_FILE___{fpath}___"}}]}],
                "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
                "systemInstruction": {"parts": [{"text": SYS_EXPLORER_PROBE}]}
            }
            cascade_task = asyncio.create_task(EchoGeminiClient.call_cascade(
                target_model_key="MODEL_APOLLO",
                payload=payload, 
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                threshold=ECHO_API_KEY_RETRIES,
                max_retries=ECHO_API_MAX_RETRIES,
                timeout=self.valves.PROBE_TIMEOUT,
                include_thoughts=True,
            ))

            step_idx = 0
            heartbeats = [f"🤖 Analyse sémantique de {safe_name}...", "🤖 Parcours des vecteurs sémantiques...", "🤖 Extraction des concepts complexes..."]
            while not cascade_task.done():
                await events.status(heartbeats[step_idx % len(heartbeats)], False)
                step_idx += 1
                try:
                    await asyncio.wait_for(asyncio.shield(cascade_task), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    break

            if cascade_task.exception():
                return wrap_tool_output(text=f"❌ Erreur Sonde : {str(cascade_task.exception())}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            data, model_used, reason = cascade_task.result()

            if not data:
                return wrap_tool_output(text="❌ Cascade épuisée : aucun modèle disponible pour le sondage sémantique.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            target = data.get("response", {}) if "response" in data else data
            cand = target.get("candidates", [])[0]
            full_text = "".join([p["text"] for p in cand["content"]["parts"] if "text" in p])
            clean, thought = split_thought_process(full_text)
            
            await events.status("✅ Analyse terminée.", True)
            return wrap_cascade_output(
                text=clean, model_requested="MODEL_APOLLO", model_used=model_used,
                status={"status": "success"},
                echo_tool_multiparts=[{"type": "thought", "content": thought}] if thought else [],
                reason=reason
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Sonde : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def read_multimedia_file(
        self, 
        file_id: str, 
        instruction: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Génère un rapport textuel descriptif complet et horodaté d'un fichier multimédia (image, audio, vidéo)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = self._resolve_extended_file_path(uid, __metadata__.get("chat_id"), file_id)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        file_size_kb = os.path.getsize(fpath) / 1024
        if file_size_kb > self.valves.MAX_MULTIMODAL_SIZE_KB:
            return wrap_tool_output(
                text=f"❌ Max Size Exceeded : Le fichier ({file_size_kb / 1024:.1f} Mo) dépasse la limite autorisée de {self.valves.MAX_MULTIMODAL_SIZE_KB / 1024:.1f} Mo pour l'analyse multimédia.", 
                status={"status": "error"}
            )

        safe_name = os.path.basename(fpath)
        if "_" in safe_name and len(safe_name.split("_")[0]) >= 32: safe_name = safe_name.split("_", 1)[1]
        
        user_prompt = instruction.strip() if instruction and instruction.strip() else USR_EXPLORE_SENSORY
        system_prompt = SYS_EXPLORE_SENSORY.format(filename=safe_name)
        
        try:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": user_prompt}, {"inline_data": {"mime_type": mime, "data": f"___ECHO_STREAM_FILE___{fpath}___"}}]}],
                "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
                "systemInstruction": {"parts": [{"text": system_prompt}]}
            }

            cascade_task = asyncio.create_task(EchoGeminiClient.call_cascade(
                target_model_key="MODEL_APOLLO",
                payload=payload,
                user_id=uid,
                metadata=__metadata__,
                events=events,
                include_thoughts=True
            ))

            step_idx = 0
            heartbeats = [f"👁️ L'agent sensoriel visionne {safe_name}...", "👁️ Analyse visuelle en cours...", "👁️ Extraction des détails..."]
            while not cascade_task.done():
                await events.status(heartbeats[step_idx % len(heartbeats)], False)
                step_idx += 1
                try:
                    await asyncio.wait_for(asyncio.shield(cascade_task), timeout=15.0)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    break

            if cascade_task.exception():
                return wrap_tool_output(text=f"❌ Erreur de l'agent sensoriel : {str(cascade_task.exception())}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            data, model_used, reason = cascade_task.result()
            
            if not data: return wrap_tool_output(text="❌ Échec de l'agent sensoriel.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            target = data.get("response", {}) if "response" in data else data
            cand = target.get("candidates", [])[0]
            full_text = "".join([p["text"] for p in cand["content"]["parts"] if "text" in p])
            clean, thought = split_thought_process(full_text)

            await events.status("✅ Distillation sensorielle terminée.", True)
            
            return wrap_cascade_output(
                text=f"### 👁️ Rapport de Distillation ({safe_name})\n{clean}",
                model_requested="MODEL_APOLLO", model_used=model_used,
                status={"status": "success"},
                echo_tool_multiparts=[{"type": "thought", "content": thought}] if thought else [],
                reason=reason
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def show_image_to_user(
        self, 
        target: str, 
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """Affiche une image locale ou valide une URL d'image distante pour présentation à l'utilisateur."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        is_url = target.lower().startswith(("http://", "https://"))
        
        # Sécurité anti-chemins absolus
        if not is_url and (target.startswith("/") or (len(target) > 1 and target[1] == ":")):
             return wrap_tool_output(text="❌ Sécurité : Accès restreint.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        try:
            if is_url:
                await events.status("🌐 Vérification du lien distant...")
                h = get_stealth_headers(target)
                
                try:
                    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as c:
                        r = await c.head(target, headers=h)
                        if r.status_code >= 400:
                            r = await c.get(target, headers={**h, "Range": "bytes=0-0"}, timeout=8.0)
                        
                        r.raise_for_status()
                        
                        content_type = r.headers.get("content-type", "").lower()
                        if content_type and not content_type.startswith("image/"):
                            return wrap_tool_output(
                                text=f"❌ Le lien pointe vers un contenu '{content_type}', pas une image. Le Modèle DOIT s'interdire d'utiliser la syntaxe MD.",
                                status={"status": "error"}
                            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                            
                        return wrap_tool_output(
                            text=f"✅ L'image est valide et accessible. Le Modèle DOIT utiliser ce code exact dans sa réponse : `![Description]({target})`",
                            status={"status": "success"}
                        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                        
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 404:
                        return wrap_tool_output(
                            text="❌ L'image n'existe pas (Erreur 404). Lien mort. Ne l'affichez pas.",
                            status={"status": "error"}
                        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                    else:
                        return wrap_tool_output(
                            text=f"⚠️ La vérification serveur est bloquée (Erreur {e.response.status_code}). Le navigateur de l'utilisateur y parviendra peut-être. Tentez le code en dernier recours : `![Description]({target})`",
                            status={"status": "warning"}
                        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                except Exception:
                    return wrap_tool_output(
                        text=f"⚠️ La vérification serveur a échoué (Réseau/Timeout). Le navigateur client y parviendra peut-être. Tentez le code : `![Description]({target})`",
                        status={"status": "warning"}
                    , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            else:
                fpath = self._resolve_extended_file_path(uid, __metadata__.get("chat_id"), target)
                if not fpath:
                    return wrap_tool_output(text=f"❌ Image '{target}' introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                mime, _ = mimetypes.guess_type(fpath)
                mime = mime or 'image/png'
                with open(fpath, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                title = f"Espace Personnel : {os.path.basename(fpath)}"

            # Injection JS pure dans le DOM Open WebUI — aucun retour HTMLResponse
            js_code = EchoUI.show_image_js(b64, mime, title, chat_id=__metadata__.get("chat_id") if __metadata__ else None)
            if events and (events.caller or events.emitter):
                await events.call_execute(js_code)
            
            await events.status("✅ Image affichée.", done=True)
            return wrap_tool_output(text=f"✅ Image affichée dans le viewer ECHO.\n\n**Titre :** {title}", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Échec affichage : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)


    async def get_file_metadata(self, file_id: str, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None) -> str:
        """Récupère la taille et le type MIME d'un fichier de l'Espace Personnel."""
        uid = __user__.get("id", "anonymous")
        fpath = self._resolve_extended_file_path(uid, __metadata__.get("chat_id"), file_id)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        stat = os.stat(fpath)
        mime, _ = get_gemini_mime(fpath)
        return wrap_tool_output(text=f"📊 **{os.path.basename(fpath)}**\nTaille: {stat.st_size} octets\nType: {mime}", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def calculate_file_hashes(
        self, 
        file_ids: List[str], 
        algorithm: Literal["sha256", "md5", "sha1", "sha512", "blake2b", "crc32"] = "sha256",
        __user__: dict = {}, 
        __metadata__: dict = {}, 
        __event_emitter__: Any = None
    ) -> str:
        """Calcule les empreintes cryptographiques (SHA256, MD5, etc.) d'une liste de fichiers."""
        import zlib
        uid = __user__.get("id", "anonymous")
        res = []
        for fid in file_ids:
            p = self._resolve_extended_file_path(uid, __metadata__.get("chat_id"), fid)
            if p:
                if algorithm == "crc32":
                    crc = 0
                    with open(p, 'rb') as f:
                        for chunk in iter(lambda: f.read(65536), b""): crc = zlib.crc32(chunk, crc)
                    res.append(f"{os.path.basename(p)} ({algorithm.upper()}): {crc & 0xFFFFFFFF:08x}")
                else:
                    h = hashlib.new(algorithm)
                    with open(p, 'rb') as f:
                        for chunk in iter(lambda: f.read(65536), b""): h.update(chunk)
                    res.append(f"{os.path.basename(p)} ({algorithm.upper()}): {h.hexdigest()}")
        return wrap_tool_output(text="\n".join(res), status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
