"""
ECHO Ingestion Pipeline
Gestion unifiée, asynchrone et Zéro-RAM de l'ingestion des fichiers (CAS 1, 2, 3, 4).
Factorisé à partir de new_context_filter.py pour permettre le traitement en arrière-plan.
Version: 1.2 (Correction 400 Bad Request, Fix OOM Texte, et Robustesse Smart Context Multimédia)
"""

import os
import asyncio
import logging
import base64
import httpx
import uuid as _uuid_module
import time
import shutil

try:
    import aiofiles
except ImportError:
    aiofiles = None

from echo_utils import (
    resolve_upload_file_path, get_echo_session_path, get_echo_global_path, EchoGeminiClient, EchoStateManager
)
from echo_constants import (
    get_gemini_mime, 
    FILE_INGESTION_STATUS,
    MAX_DIRECT_TEXT_INJECT_SIZE, MAX_DIRECT_MMEDIA_INJECT_SIZE,
    CONVERTIBLE_OFFICE_EXTENSIONS, OOXML_IMAGE_EXTENSIONS, DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB,
    ECHO_QDRANT_URL, COLLECTION_SESSION_RAG, EMBEDDING_DIM_V2,
    ECHO_SESSION_RAG_CHUNK_SIZE, PROMPT_SENSORY_DISTILLATION
)

logger = logging.getLogger("ECHO-INGESTION")

class AgnosticStreamingSplitter:
    """
    Découpeur de flux texte asynchrone. Accumule les lignes jusqu'à la limite de caractères,
    puis envoie le bloc enrichi à Qdrant sans Overlap.
    """
    def __init__(self, filename: str, file_id: str, user_id: str, chat_id: str, u_ctx: dict, m_ctx: dict):
        self.filename = filename
        self.file_id = file_id
        self.user_id = user_id
        self.chat_id = chat_id
        self.u_ctx = u_ctx
        self.m_ctx = m_ctx
        
        self.buffer = ""
        self.line_count = 0
        self.chunk_index = 0
        self.points = []
        
    async def feed(self, text_chunk: str):
        self.buffer += text_chunk
        
        while len(self.buffer) > ECHO_SESSION_RAG_CHUNK_SIZE and "\n" in self.buffer:
            split_idx = self.buffer.rfind("\n", 0, ECHO_SESSION_RAG_CHUNK_SIZE)
            if split_idx == -1:
                split_idx = ECHO_SESSION_RAG_CHUNK_SIZE
                
            chunk_to_send = self.buffer[:split_idx].strip()
            self.buffer = self.buffer[split_idx:].lstrip('\n')
            
            if not chunk_to_send:
                continue
                
            lines_in_chunk = chunk_to_send.count('\n') + 1
            start_line = self.line_count + 1
            self.line_count += lines_in_chunk
            
            enriched_chunk = f"[Document: {self.filename} | ID: {self.file_id} | Lignes: {start_line}-{self.line_count}]\n{chunk_to_send}"
            
            vector = await EchoGeminiClient.generate_embedding(
                enriched_chunk, "document", self.u_ctx, self.m_ctx, title=self.file_id
            )
            
            if vector:
                point_id = str(_uuid_module.uuid5(_uuid_module.NAMESPACE_DNS, f"{self.user_id}_{self.chat_id}_{self.file_id}_{self.chunk_index}"))
                self.points.append({
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "user_id": self.user_id,
                        "chat_id": self.chat_id,
                        "source_id": self.file_id,
                        "chunk_index": self.chunk_index,
                        "lines_range": [start_line, self.line_count],
                        "text": enriched_chunk,
                        "timestamp": int(time.time())
                    }
                })
                
                # BATCH FLUSH : Évite la saturation RAM et le rejet 400 de Qdrant
                if len(self.points) >= 50:
                    async with httpx.AsyncClient(timeout=60) as client:
                        await client.put(
                            f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points?wait=true",
                            json={"points": self.points}
                        )
                    self.points.clear()
            
            self.chunk_index += 1

    async def flush(self, qdrant_client: httpx.AsyncClient) -> tuple:
        if self.buffer.strip():
            chunk_to_send = self.buffer.strip()
            lines_in_chunk = chunk_to_send.count('\n') + 1
            start_line = self.line_count + 1
            self.line_count += lines_in_chunk
            
            enriched_chunk = f"[Document: {self.filename} | ID: {self.file_id} | Lignes: {start_line}-{self.line_count}]\n{chunk_to_send}"
            
            vector = await EchoGeminiClient.generate_embedding(
                enriched_chunk, "document", self.u_ctx, self.m_ctx, title=self.file_id
            )
            if vector:
                point_id = str(_uuid_module.uuid5(_uuid_module.NAMESPACE_DNS, f"{self.user_id}_{self.chat_id}_{self.file_id}_{self.chunk_index}"))
                self.points.append({
                    "id": point_id,
                    "vector": vector,
                    "payload": {
                        "user_id": self.user_id,
                        "chat_id": self.chat_id,
                        "source_id": self.file_id,
                        "chunk_index": self.chunk_index,
                        "lines_range": [start_line, self.line_count],
                        "text": enriched_chunk,
                        "timestamp": int(time.time())
                    }
                })
                self.chunk_index += 1
                
        if self.points:
            upsert_resp = await qdrant_client.put(
                f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points?wait=true",
                json={"points": self.points}
            )
            self.points.clear()
            if upsert_resp.status_code not in (200, 206):
                return 0, f"Erreur Qdrant upsert : HTTP {upsert_resp.status_code} — {upsert_resp.text[:100]}"
            return len(self.points), ""
        
        return 0, "Aucun point généré."

class EchoIngestionPipeline:
    def __init__(self, valves=None):
        self.valves = valves
        self.ooxml_semaphore = asyncio.Semaphore(2)

    async def _convert_unsupported_file(self, path: str, ext: str, filename: str, user_id: str, chat_id: str, events: any) -> str:
        """Convertit un fichier non supporté nativement en texte Markdown, écrit sur disque."""
        if ext in CONVERTIBLE_OFFICE_EXTENSIONS:
            try:
                md_path = os.path.splitext(path)[0] + "_converted.md"
                if os.path.exists(md_path):
                    logger.info(f"⚡ Conversion en cache utilisée : {md_path}")
                    return md_path

                from markitdown import MarkItDown
                await events.status(f"📄 Conversion Office de {filename}...", False)
                md_converter = MarkItDown()
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, md_converter.convert, path)
                converted_text = result.text_content

                if not converted_text or not converted_text.strip():
                    raise ValueError("Conversion vide — fichier probablement corrompu.")

                if ext in OOXML_IMAGE_EXTENSIONS:
                    image_descriptions = await self._describe_ooxml_images(path, filename, user_id, chat_id, events)
                    if image_descriptions:
                        converted_text += "\n\n---\n## Images extraites du document\n\n"
                        converted_text += "\n\n".join(image_descriptions)

                md_path = os.path.splitext(path)[0] + "_converted.md"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(converted_text)
                
                md_size = os.path.getsize(md_path)
                logger.info(f"✅ Conversion réussie : {filename} → {md_path} ({md_size} octets)")
                return md_path
            except ImportError:
                logger.error("!! markitdown non installé — conversion impossible")
                return None
            except Exception as e:
                logger.error(f"!! Erreur conversion {filename}: {e}")
                return None
        return None

    async def _describe_ooxml_images(self, path: str, filename: str, user_id: str, chat_id: str, events: any) -> list:
        """Extrait les images d'un OOXML (docx/pptx) et les décrit via LITE."""
        import zipfile
        descriptions = []
        try:
            with zipfile.ZipFile(path, 'r') as z:
                image_files = [n for n in z.namelist() if '/media/' in n and any(n.lower().endswith(e) for e in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'))]
                if not image_files: return []

                await events.status(f"🖼️ Description de {len(image_files)} image(s) de {filename}...", False)
                u_ctx = {"id": user_id}
                m_ctx = {"chat_id": chat_id}

                for i, img_name in enumerate(image_files):
                    try:
                        img_data = z.read(img_name)
                        b64 = base64.b64encode(img_data).decode("utf-8")
                        img_ext = os.path.splitext(img_name)[1].lower()
                        mime_map = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
                        img_mime = mime_map.get(img_ext, 'image/png')

                        description = await EchoGeminiClient.call_distillation(
                            f"Décris cette image extraite du document '{filename}' de manière concise et précise (2-3 phrases max).",
                            u_ctx, m_ctx, is_json=False,
                            parts=[{"role": "user", "parts": [{"text": f"Décris cette image '{os.path.basename(img_name)}' extraite du document '{filename}'."}, {"inline_data": {"mime_type": img_mime, "data": b64}}]}],
                            target_model="MODEL_LITE"
                        )
                        if description and description != "Analyse indisponible.":
                            descriptions.append(f"**Image {i+1}** (`{os.path.basename(img_name)}`) : {description}")
                    except Exception as img_err:
                        logger.error(f"!! Image {img_name}: {img_err}")
        except zipfile.BadZipFile:
            logger.error(f"!! {filename} n'est pas un ZIP valide")
        return descriptions

    def _sync_file_to_global_vault(self, path: str, filename: str, file_id: str, user_id: str, chat_id: str) -> str:
        """Centralise un fichier dans le Vault Global et distribue les liens symboliques."""
        vault_name = os.path.basename(path) if file_id in os.path.basename(path) else f"{file_id}_{os.path.basename(filename)}"
        
        global_vault_dir = get_echo_global_path(user_id, "files")
        os.makedirs(global_vault_dir, exist_ok=True)
        global_path = os.path.join(global_vault_dir, vault_name)

        chat_vault_dir = get_echo_session_path(user_id, chat_id, "files")
        os.makedirs(chat_vault_dir, exist_ok=True)
        chat_vault_path = os.path.join(chat_vault_dir, vault_name)

        # 1. Sécurisation physique dans le Vault Global
        if path != global_path:
            if os.path.islink(path):
                # Si path est déjà un lien symbolique, cela veut dire qu'il est déjà pris en charge, on s'assure juste d'utiliser la vraie source
                global_path = os.path.realpath(path)
            elif os.path.exists(path):
                shutil.move(path, global_path)
                
        # 2. Lien symbolique absolu pour OWUI (dans /uploads)
        try:
            from echo_constants import ECHO_UPLOADS_TRANSIT_DIR
            upload_symlink = os.path.join(ECHO_UPLOADS_TRANSIT_DIR, vault_name)
            if not os.path.exists(upload_symlink) and not os.path.islink(upload_symlink):
                os.symlink(global_path, upload_symlink)
        except Exception as e:
            logger.warning(f"Erreur création symlink OWUI pour {vault_name}: {e}")

        # 3. Lien symbolique pour le Chat Vault
        if not os.path.exists(chat_vault_path) and not os.path.islink(chat_vault_path):
            os.symlink(global_path, chat_vault_path)

        return global_path

    def _sync_move_and_commit(self, path: str, filename: str, file_id: str, mime: str, user_id: str, chat_id: str) -> str:
        try:
            from echo_codex_git import CodexRepo
            import dulwich.porcelain
            
            repo = CodexRepo(user_id, chat_id)
            safe_name = os.path.basename(filename)
            new_path = os.path.join(repo.repo_path, safe_name)
            
            global_path = self._sync_file_to_global_vault(path, filename, file_id, user_id, chat_id)
            
            vault_name = os.path.basename(global_path)
            chat_vault_dir = get_echo_session_path(user_id, chat_id, "files")
            vault_path = os.path.join(chat_vault_dir, vault_name)
                
            shutil.copy2(vault_path, new_path)
            
            try:
                dulwich.porcelain.add(repo.repo_path, paths=[safe_name])
                try:
                    commit_sha = dulwich.porcelain.commit(
                        repo.repo_path,
                        message=b"Importation automatique via Upload",
                        author=b"ECHO Codex <codex@echo.local>",
                        committer=b"ECHO Codex <codex@echo.local>",
                    )
                    commit_hash = commit_sha.decode("ascii") if isinstance(commit_sha, bytes) else str(commit_sha)
                except Exception as commit_err:
                    logger.warning(f"Commit vide ignoré pour {filename}: {commit_err}")
                    commit_hash = repo.get_last_commit() or "unknown"
                
                state_manager = EchoStateManager(user_id, chat_id)
                lines = 0
                try:
                    with open(new_path, "r", encoding="utf-8", errors="replace") as f:
                        lines = sum(1 for _ in f)
                except: pass
                
                state_manager.save_resource(
                    id=file_id, name=filename, resource_type='codex',
                    status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'], mime=mime, git_tracked=True,
                    language=CodexRepo.detect_language(filename),
                    lines=lines,
                    last_commit=commit_hash, commit_msg="Importation automatique via Upload"
                )
                logger.info(f"📦 Fichier {filename} intégré au Codex Zéro-RAM avec succès.")
            except Exception as e:
                logger.error(f"!! Erreur mineure Codex pour {filename} (Git/SQL): {e}")
                
            return vault_path
        except Exception as e:
            logger.error(f"!! Erreur critique intégration Codex pour {filename}: {e}")
            return path

    async def _move_to_codex_and_commit(self, path: str, filename: str, file_id: str, mime: str, user_id: str, chat_id: str) -> str:
        return await asyncio.to_thread(self._sync_move_and_commit, path, filename, file_id, mime, user_id, chat_id)


    async def _index_and_summarize_streamed(
        self, file_id: str, filename: str, mime: str, 
        user_id: str, chat_id: str, events: any,
        filepath: str, is_text: bool
    ) -> dict:
        """Pipeline RAG Streamé End-to-End."""
        from echo_constants import (
            MODEL_DISTILLATION, TEMP_DISTILLATION, TOP_P_DISTILLATION
        )
        from echo_utils import EchoAuth
        import json
        
        u_ctx = {"id": user_id}
        m_ctx = {"chat_id": chat_id}
        
        await events.status(f"Vectorisation de {filename}...", False)
        splitter = AgnosticStreamingSplitter(filename, file_id, user_id, chat_id, u_ctx, m_ctx)
        
        if is_text:
            # Bypass API: Lecture asynchrone par ligne pour zéro impact RAM
            if aiofiles:
                async with aiofiles.open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    async for line in f:
                        await splitter.feed(line)
            else:
                def read_generator():
                    with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                        while True:
                            lines = f.readlines(1024 * 512) # 512 Ko max
                            if not lines:
                                break
                            yield lines
                
                gen = read_generator()
                while True:
                    try:
                        chunk = await asyncio.to_thread(lambda: next(gen))
                        for line in chunk:
                            await splitter.feed(line)
                    except StopIteration:
                        break
        else:
            # Flux Multimédia : Pipeline B64 injecté
            await events.status(f"👁️ Analyse multimodale de {filename}...", False)
            extraction_prompt = (
                "Tu es un extracteur de données brut. Ta mission est de décrire, transcrire et analyser "
                "ce document. Si le document est structuré reproduis et respecte strictement la structure. "
                "Si le document est textuel, respecte strictement son verbatim. Si le document est audiovisuel "
                "la description doit être précise, détaillée, complète, couvrant autant, le textuel, le visuel que l'audio, et parfaitement horosynchronisé."
            )
            
            payload = {
                "contents": [{"role": "user", "parts": [{"text": extraction_prompt}, {"inline_data": {"mime_type": mime, "data": f"___ECHO_STREAM_FILE___{filepath}___"}}]}],
                "generationConfig": {
                    "temperature": TEMP_DISTILLATION,
                    "topP": TOP_P_DISTILLATION,
                    "maxOutputTokens": 65535,
                }
            }
            
            providers = await EchoAuth(user_id=user_id).get_ordered_auth_providers(user_id)
            provider = providers[0] if providers else None
            if not provider:
                raise ValueError("Aucun fournisseur d'authentification valide.")
                
            req_ctx = await EchoGeminiClient._prepare_request_context(
                provider, target_model=MODEL_DISTILLATION, payload=payload, method="streamGenerateContent", chat_id=chat_id
            )
            if not req_ctx:
                raise ValueError("Erreur de préparation du contexte API.")
                
            stream_content, content_length = EchoGeminiClient._prepare_zero_ram_content(req_ctx["payload"])
            if content_length:
                req_ctx["headers"]["Content-Length"] = content_length
                
            async with httpx.AsyncClient(timeout=300) as client:
                request = client.build_request(
                    "POST", req_ctx["url"], 
                    headers=req_ctx["headers"],
                    content=stream_content
                )
                
                response = await client.send(request, stream=True)
                if response.status_code != 200:
                    await response.aread()
                    raise ValueError(f"Erreur API Gemini : {response.status_code} - {response.text}")
                    
                # SSE Stream Parsing
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                        
                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str == "[DONE]": break
                        try:
                            data_json = json.loads(data_str)
                            target = data_json.get("response", {}) if "response" in data_json else data_json
                            candidates = target.get("candidates", [])
                            if candidates:
                                parts_list = candidates[0].get("content", {}).get("parts", [])
                                if parts_list:
                                    text_chunk = parts_list[0].get("text", "")
                                    if text_chunk:
                                        await splitter.feed(text_chunk)
                                    else:
                                        logger.warning(f"⚠️ Chunk texte vide: {data_json}")
                                else:
                                    logger.warning(f"⚠️ Pas de parts dans candidates: {data_json}")
                            elif "error" in data_json:
                                logger.error(f"❌ Erreur API dans le stream: {data_json}")
                            else:
                                logger.warning(f"⚠️ Pas de candidates dans data_json: {data_json}")
                        except json.JSONDecodeError:
                            logger.error(f"❌ JSONDecodeError sur: {data_str}")
                    else:
                        logger.error(f"❌ Ligne API non-SSE inattendue: {line}")
        # Flush final
        qdrant_client = httpx.AsyncClient(timeout=60)
        try:
            points_count, err = await splitter.flush(qdrant_client)
            if err:
                logger.error(f"Erreur de flush Qdrant pour {filename}: {err}")
        finally:
            await qdrant_client.aclose()
            
        # -------------------------------------------------------------
        # SYNTHÈSE COGNITIVE MAP-REDUCE V2
        # -------------------------------------------------------------
        brief_summary = ""
                
        if not brief_summary:
            await events.status(f"Génération du résumé Smart Context pour {filename}...", False)
            search_query = "Introduction, résumé exécutif, objectif principal, description technique complète."
            query_vector = await EchoGeminiClient.generate_embedding(search_query, "query", u_ctx, m_ctx)
            
            context_chunks = []
            if query_vector:
                async with httpx.AsyncClient(timeout=60) as client:
                    qdrant_payload = {
                        "vector": query_vector,
                        "limit": getattr(self.valves, 'SMART_CONTEXT_CHUNK_LIMIT', 5),
                        "with_payload": True,
                        "filter": {
                            "must": [{"key": "source_id", "match": {"value": file_id}}]
                        }
                    }
                    resp = await client.post(f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/search", json=qdrant_payload)
                    if resp.status_code == 200:
                        hits = resp.json().get("result", [])
                        for hit in hits:
                            pl = hit.get("payload", {})
                            context_chunks.append(pl.get("text", ""))
                            
            if context_chunks:
                synthesis_prompt = (
                    f"Fais un résumé exhaustif et structuré (en markdown) de ce document '{filename}' "
                    "en te basant UNIQUEMENT sur les extraits suivants pertinents :\n\n" +
                    "\n\n---\n\n".join(context_chunks)
                )
                try:
                    brief_summary = await EchoGeminiClient.call_distillation(
                        synthesis_prompt, u_ctx, m_ctx, is_json=False, target_model=MODEL_DISTILLATION
                    )
                except Exception as e:
                    logger.error(f"Erreur distillation MR: {e}")
            
            if not brief_summary or brief_summary == "Analyse indisponible.":
                brief_summary = "L'indexation a été réalisée sans erreur, mais le résumé a échoué."
                
        res_text = (
            f"<smart_context filename=\"{filename}\" mime_type=\"{mime}\" mode=\"vectorized_sum_up\"\n"
            f"                source_id=\"{file_id}\">\n"
            f"{brief_summary}\n\n"
            f"> ⚙️ INFORMATION SYSTÈME : Les détails du fichier sont vectorisés et accessibles via `search_session_context`\n"
            f"</smart_context>"
        )
        return {"status": "success", "type": FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"], "source_id": file_id, "fid": file_id, "name": filename, "mime": mime, "content": res_text}

    async def process_file_task(self, user_id: str, file_obj: dict, chat_id: str, events: any) -> dict:
        """Point d'entrée principal pour l'ingestion d'un fichier."""
        file_id = file_obj.get("id") or file_obj.get("file", {}).get("id")
        filename = file_obj.get("name") or file_obj.get("file", {}).get("meta", {}).get("name", "inconnu")
        mime = file_obj.get("mime_type") or file_obj.get("file", {}).get("meta", {}).get("content_type", "application/octet-stream")
        
        path = file_obj.get("file", {}).get("path")
        if not path or not os.path.exists(path):
            path = resolve_upload_file_path(user_id, file_id, chat_id=chat_id)
            if not path:
                return {"status": "error", "fid": file_id, "name": filename, "error": "Fichier physique introuvable"}

        try:
            path = await asyncio.to_thread(self._sync_file_to_global_vault, path, filename, file_id, user_id, chat_id)
            size = await asyncio.to_thread(os.path.getsize, path)
        except Exception as e:
            logger.error(f"!! Erreur d'accès/déplacement pour {filename}: {e}")
            if events: await events.status(f"❌ Erreur I/O pour {filename}", False)
            return {"status": "error", "fid": file_id, "name": filename, "error": f"Erreur I/O: {str(e)}"}

        mime, is_supported = get_gemini_mime(path)
        
        logger.info(f"📄 Analyse de {filename} ({mime}) - Taille: {size} octets")

        ext = os.path.splitext(path)[1].lower()
        max_convert_bytes = getattr(self.valves, 'MAX_OFFICE_FILE_SIZE_MB', DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB) * 1024 * 1024

        if not is_supported and ext in CONVERTIBLE_OFFICE_EXTENSIONS and getattr(self.valves, 'ENABLE_OFFICE_CONVERSION', False) and size <= max_convert_bytes:
            converted_path = await self._convert_unsupported_file(path, ext, filename, user_id, chat_id, events)
            if converted_path and os.path.exists(converted_path):
                # On conserve l'original pour OWUI, mais on crée le symlink pour le MD dans le chat courant
                converted_filename = os.path.basename(converted_path)
                chat_vault_dir = get_echo_session_path(user_id, chat_id, "files")
                chat_md_path = os.path.join(chat_vault_dir, converted_filename)
                
                if not os.path.exists(chat_md_path) and not os.path.islink(chat_md_path):
                    try:
                        os.symlink(converted_path, chat_md_path)
                    except Exception as e:
                        logger.warning(f"Erreur création symlink MD pour {converted_filename}: {e}")
                        
                path = converted_path
                mime = "text/markdown"
                is_supported = True
                ext = ".md"
                size = await asyncio.to_thread(os.path.getsize, path)
                logger.info(f"🔄 Fichier {filename} converti et re-routé comme {mime} ({size} octets)")

        if is_supported and ("text/" in mime or "application/json" in mime):
            path = await self._move_to_codex_and_commit(path, filename, file_id, mime, user_id, chat_id)
            if os.path.exists(path):
                size = await asyncio.to_thread(os.path.getsize, path)

        if is_supported and size < MAX_DIRECT_TEXT_INJECT_SIZE and ("text/" in mime or "application/json" in mime):
            try:
                def read_text():
                    with open(path, "r", encoding="utf-8", errors="ignore") as f: return f.read()
                content = await asyncio.to_thread(read_text)
                ext_str = os.path.splitext(filename)[1].strip('.')
                lang = ext_str if ext_str else ""
                return {
                    "status": "success", "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"], "fid": file_id, "name": filename, "mime": mime, "sub_type": "text",
                    "storage_path": path,
                    "content": f"📄 **Fichier : {filename}**\n```{lang}\n{content}\n```\n\n"
                }
            except Exception as e:
                if events: await events.status(f"❌ Erreur extraction pour {filename}", False)
                return {"status": "error", "fid": file_id, "name": filename, "error": str(e)}

        if is_supported and any(x in mime for x in ["image/", "audio/", "video/", "application/pdf"]) and size < MAX_DIRECT_MMEDIA_INJECT_SIZE:
            try:
                await events.status(f"Encapsulation de {filename}...", False)
                def read_b64():
                    with open(path, "rb") as f: return base64.b64encode(f.read()).decode("utf-8")
                b64 = await asyncio.to_thread(read_b64)
                return {
                    "status": "success", "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"], "fid": file_id, "name": filename, "mime": mime, "sub_type": "binary",
                    "storage_path": path,
                    "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                }
            except Exception as e:
                if events: await events.status(f"❌ Erreur binaire pour {filename}", False)
                return {"status": "error", "fid": file_id, "name": filename, "mime": mime, "error": f"Erreur binaire : {str(e)}"}

        if is_supported and getattr(self.valves, 'ENABLE_SMART_CONTEXT', True):
            try:
                is_text = ("text/" in mime or "application/json" in mime)
                return await self._index_and_summarize_streamed(
                    file_id, filename, mime, user_id, chat_id, events, path, is_text
                )
            except Exception as e:
                logger.error(f"!! Exception CAS 3 pour {filename}: {e}")
        return {"status": "success", "type": FILE_INGESTION_STATUS["INDEXED"], "fid": file_id, "name": filename, "mime": mime, "storage_path": path}
