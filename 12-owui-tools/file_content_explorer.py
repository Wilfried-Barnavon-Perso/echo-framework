"""
title: ECHO Explorateur de l'Espace Personnel
author: Wilfried BARNAVON
version: 5.109.26
description: Composant système interne : ECHO Explorateur de l'Espace Personnel.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.109.25: Ajout de la sécurité de taille maximale (MAX_MULTIMODAL_SIZE_KB) dans semantic_probe et read_multimedia_file.
# 5.109.24: Correction 400 Bad Request (inlineData -> inline_data) et interception robuste des exceptions sondes.
# 5.109.17: Suppression de la classe UserValves vide pour éviter le bug UI Open WebUI (Aucune vanne trouvée).
# 5.109.16: Transformation de show_image_to_user en Sonde Visuelle pour les URL distantes (3 états: success, warning, error). 5.109.15: Délégation des URLs distantes au Markdown, WebPlayer limité au Base64 local. 5.109.5: Refactorisation terminologique (Vault Explorer → Explorateur de l'Espace Personnel). 5.109.6: Correction show_image_to_user (injection JS via events). 5.109.7: Ajout UserValves ANALYSE_MODEL pour semantic_probe (MODEL_FLASH → niveau cognitif paramétrable). 5.109.8: Fix import manquant TEMP_DEFAULT/TOP_P_DEFAULT (NameError dans semantic_probe). 5.109.9: Fix semantic_probe — thinkingLevel forcé à HIGH, suppression du paramètre libre thinking_level (confusion LLM avec le nom de modèle). 5.109.10: show_image_to_user — fallback client si vérification serveur échoue (CDN restrictifs type Wikimedia). 5.109.11: Suppression ANALYSE_MODEL UserValve, migration semantic_probe vers call_cascade(). 5.109.12: Injection __metadata__ et chat_id pour respect isolation fichiers par session. 5.109.13: Fix hallucination ID fichiers via docstring explicite et résolution résiliente. 5.109.14: Registre Unifié V2 — mark_processed → save_resource.
# 5.109.18: Suppression de download_from_url (redondant, court-circuitage Registre V2).
# 5.109.26: Nettoyage du code : suppression des imports inutilisés (PEP8).

import os
import sys
import base64
import httpx
import mimetypes
import hashlib
import asyncio
from typing import List, Any, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    EchoEvents, wrap_tool_output, wrap_cascade_output,
    resolve_upload_file_path, split_thought_process,
    EchoGeminiClient, get_stealth_headers
)
from echo_ui import EchoUI
from echo_constants import (
    ECHO_UPLOADS_TRANSIT_DIR, get_gemini_mime, ECHO_API_KEY_THRESHOLD,
    ECHO_API_MAX_RETRIES, TEMP_DEFAULT, TOP_P_DEFAULT,
    PROMPT_SENSORY_DISTILLATION
)

class Tools:
    class Valves(BaseModel):
        PROBE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour le sondage sémantique.")
        MAX_READ_SIZE_KB: int = Field(default=16, description="Taille maximale (en Ko) pour la lecture brute (RAW).")
        MAX_MULTIMODAL_SIZE_KB: int = Field(default=30720, description="Taille maximale (en Ko) pour l'injection multimédia (défaut: 30 Mo).")

    def __init__(self):
        self.valves = self.Valves()
        self.uploads_dir = ECHO_UPLOADS_TRANSIT_DIR

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
        """
        Lecture sécurisée d'un fichier personnel (Espace Utilisateur). Validation Registre (query_registry) requise.
        [CONTRAINTE CRITIQUE : byte_offset est mutuellement exclusif avec start_line/end_line. Maximum conseillé pour byte_offset/lignes : éviter de saturer le contexte (ex: 100 lignes max).]
        :param file_id: Identifiant strict Registre (ex: a1b2c3d4).
        :param start_line: (Optionnel) Ligne de départ.
        :param end_line: (Optionnel) Ligne de fin.
        :param output_mode: (Optionnel) Format: 'text' (défaut/extraction standard), 'base64' (pour injection multimodale ou encapsulation de binaires), 'hex' (analyse structurelle).
        :param byte_offset: (Optionnel) Décalage binaire (exclut start_line/end_line).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
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
            is_text = "text/" in mime or "application/json" in mime or "xml" in mime or ext in [".md", ".csv"] # Fallback

            warning_msg = ""
            if output_mode == "text" and not is_text:
                warning_msg = f"\n\n[ATTENTION SYSTÈME ECHO]\nCe fichier est un binaire ({mime}). Si le texte extrait ci-dessus est illisible, incomplet ou ne correspond pas à vos attentes, NE BOUCLEZ PAS en rappelant l'outil avec d'autres lignes/offsets.\n=> Utilisez output_mode='base64' pour une injection multimodale native (PDF/Images/Media), ou interrogez le RAG via `semantic_probe`."

            if output_mode == "text":
                lines = []
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    f.seek(byte_offset)
                    
                    # ALGORITHMIC FIX: Skip lines if byte_offset is 0 and start_line > 1
                    if byte_offset == 0 and start_line > 1:
                        for _ in range(start_line - 1):
                            if not f.readline(MAX_CHARS): break
                            
                    # Start reading from the actual requested range
                    for i in range(start_line, end_line + 1):
                        line = f.readline(MAX_CHARS)
                        if not line: break
                        clean_line = line.replace('\x00', '').rstrip()
                        lines.append(f"+{i} | {clean_line}")
                        if len("\n".join(lines)) >= MAX_CHARS: break
                    new_offset = f.tell()
                res_text = f"--- CONTENU TEXTE (Lignes {start_line} à {end_line}, Offset {byte_offset} à {new_offset}) ---\n\n" + "\n".join(lines) + warning_msg
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
        """Sonde sémantiquement un fichier volumineux ou complexe. Validation Registre requise.
        :param file_id: Identifiant strict Registre (utiliser query_registry, ne jamais l'inventer).
        :param query: Motif d'analyse sémantique.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        fpath = resolve_upload_file_path(user_id, file_id, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
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
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                }
            }
            cascade_task = asyncio.create_task(EchoGeminiClient.call_cascade(
                target_model_key="MODEL_FLASH",
                payload=payload, 
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                threshold=ECHO_API_KEY_THRESHOLD,
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
                text=clean, model_requested="MODEL_FLASH", model_used=model_used,
                status={"status": "success"},
                echo_tool_multiparts=[{"type": "thought", "content": thought}] if thought else [],
                reason=reason
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur Sonde : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def read_multimedia_file(
        self, 
        file_id: str, 
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Sonde sensorielle : Délègue l'analyse d'un média à un sous-agent pour obtenir un rapport textuel (Zéro-RAM)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
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
        
        prompt = PROMPT_SENSORY_DISTILLATION.format(filename=safe_name)
        
        try:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": prompt}, {"inline_data": {"mime_type": mime, "data": f"___ECHO_STREAM_FILE___{fpath}___"}}]}]
            }

            cascade_task = asyncio.create_task(EchoGeminiClient.call_cascade(
                target_model_key="MODEL_FLASH",
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
                model_requested="MODEL_FLASH", model_used=model_used,
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
        """Avertissement à l'Utilisateur et syntaxe markdown en dernier recours. Le Modèle est multimédia natif (pas d'outil requis pour visionner)."""
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
                fpath = resolve_upload_file_path(uid, target, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
                if not fpath:
                    return wrap_tool_output(text=f"❌ Image '{target}' introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                mime, _ = mimetypes.guess_type(fpath)
                mime = mime or 'image/png'
                with open(fpath, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                title = f"Espace Personnel : {os.path.basename(fpath)}"

            # Injection JS pure dans le DOM Open WebUI — aucun retour HTMLResponse
            js_code = EchoUI.show_image_js(b64, mime, title)
            if events and (events.caller or events.emitter):
                await events.call("execute", {"code": js_code})
            
            await events.status("✅ Image affichée.", done=True)
            return wrap_tool_output(text=f"✅ Image affichée dans le viewer ECHO.\n\n**Titre :** {title}", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Échec affichage : {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)


    async def get_file_metadata(self, file_id: str, __user__: dict = {}, __metadata__: dict = {}, __event_emitter__: Any = None) -> str:
        """Récupère les métadonnées techniques d'un fichier."""
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
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
        """Calcul d'empreintes numériques (intégrité) pour l'Espace Personnel.
        :param file_ids: Liste des identifiants stricts de fichiers (Registre).
        :param algorithm: Algorithme de hachage à utiliser.
        """
        import zlib
        uid = __user__.get("id", "anonymous")
        res = []
        for fid in file_ids:
            p = resolve_upload_file_path(uid, fid, self.uploads_dir, chat_id=__metadata__.get("chat_id"))
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
