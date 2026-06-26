"""
title: ECHO New Context Filter V2
author: Wilfried BARNAVON
author_url: https://github.com/Wilfried-Barnavon-Perso
version: 7.37
description: 7.37: Refonte de l'ingestion par Synthèse Guidée par RAG (O(1)) et ajout de SMART_CONTEXT_CHUNK_LIMIT.
             7.36: Refactorisation statuts d'ingestion. Centralisation via FILE_INGESTION_STATUS.
             7.35: Renommage rag_ephemeral -> vectorized_sum_up et assouplissement de la directive système.
             7.34: Intégration native des PDF dans le Cas 1 (Injection Binaire Directe) via inline_data.
             7.33: Hotfix ImportError get_gemini_mime preventing file processing.
             7.32: Fix missing vault move for PDFs and media causing infinite reprocessing loop.
             7.31: Fallback SQLite transparent for small files without full reprocessing.
             7.30: Zero-RAM file processing. Deferred Registry V2 Sealing (echo_resources).
             7.27: Fix CAS 2 injection — retour de new_path même si Git/SQL échouent pour éviter FileNotFoundError.
             7.26: Factorisation : Création anticipée du Vault/DOMAIN au début de l'inlet pour sécuriser l'ingestion Codex.
             7.25: Sécurisation de l'ingestion Codex Zéro-RAM (shutil.copy2, gestion des commits vides, suppression source post-succès).
             7.5: Fix génération slug. 7.6: Migration Antigravity 2.1 — suppression GOOGLE_OAUTH_CODE_REGEX (PKCE legacy).
             7.7: Centralisation THINKING_LEVEL_FLASH (echo_constants v4.8) — suppression du "HIGH" hardcodé.
             7.8: Injection registre_plan dans environnement_contexte (Strategic Planner v1.0).
             7.9: Fix multimodal — extraction ordonnée des text-parts du content OWUI
             dans le Draft. Correction perte texte utilisateur avec images inline.
             7.10: Revert dégradation gracieuse CAS 3 (le fallback INDEXATION suffit).
             7.11: Cosmetic — Anonyme → anonyme (minuscule) dans nom_utilisateur.
             7.12: Injection registre_codex dans environnement_contexte (ECHO Codex v1.0).
             7.13: Isolation stricte des fichiers par session (resolve_upload_file_path avec chat_id).
             7.14: Refonte Smart Context (Mémoire Vectorisée de Session & Map-Reduce) + Indexation de la donnée brute.
             7.15: Correction hallucination outil RAG et unification directive impérative.
             7.16: Pipeline Mémoire Vectorisée de Session Transmodal. Extraction textuelle API (Phase 1)
             puis Map-Reduce local (Phase 2) pour unifier le traitement du binaire et du texte.
             7.17: Fast-Path pour bypass Map-Reduce sur textes courts (ECHO_MR_SUMMARY_MAX_WORDS).
             7.18: Correction bug multimodal (prompt texte écrasé par echo_utils) + Refonte Prompts.
             7.19: Refonte architecturale identifiants (Éradication du slug). Utilisation de
             file_id natif comme source_id pour la Mémoire Vectorisée de Session.
             7.20: Conversion Office → Markdown via MarkItDown (Microsoft, MIT). Fichiers
             docx/docm/xlsx/xlsm/pptx convertis en .md sur disque avec description d'images
             OOXML via LITE. UserValves ENABLE_OFFICE_CONVERSION et MAX_OFFICE_FILE_SIZE_MB.
             7.29: Architecture d'immutabilité. Fichiers déplacés dans vault 'files' et copiés dans 'codex'.
             Refactorisation CAS 3 Phase 2 en _index_and_summarize() réutilisable.
             7.21: Registre Unifié V2 — Suppression registre_fichiers, registre_plan,
             registre_codex de l'AEC. Remplacement par <evenement_systeme> évènementiel.
             Suppression mark_processed dans le filtre (CAS 3 et CAS 4). Watermark delta
             pour détecter les ressources créées par outils/HUD hors-tour.
             7.22: Fix routage PDF — Les PDF ne passent plus par le CAS 1 (binaire
             direct). Ils sont systématiquement traités par le CAS 3 (extraction +
             résumé Map-Reduce + Mémoire Vectorisée de Session) pour garantir un résumé persistant.
             7.23: Fix _dict_to_yaml — Ajout support des listes racine pour garantir la sérialisation correcte des structures de données dynamiques.
"""


from pydantic import BaseModel, Field
from typing import Optional, List, Union, Dict, Any
import orjson as json
import pybase64 as base64
import os
import sys
import re
import asyncio
import logging
import time
import uuid as _uuid_module
import hashlib
import shutil
import httpx
from concurrent.futures import ThreadPoolExecutor

# Importations ECHO Strictes (Volume Docker)
sys.path.append("/app/backend/echo_libs")
from echo_utils import resolve_upload_file_path, EchoStateManager, EchoGeminiClient, EchoEvents, EchoAuth
from echo_constants import (
    get_gemini_mime, ECHO_USERS_ROOT, 
    GOOGLE_API_KEY_REGEX, MODEL_FLASH,
    ECHO_QDRANT_URL,
    THINKING_LEVEL_FLASH,
    MAX_DIRECT_TEXT_INJECT_SIZE, MAX_DIRECT_MMEDIA_INJECT_SIZE,
    ECHO_MR_CHUNK_SIZE, ECHO_MR_OVERLAP_SIZE, ECHO_MR_MAX_TOKENS, ECHO_MR_SUMMARY_MAX_WORDS,
    CONVERTIBLE_OFFICE_EXTENSIONS, DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB, OOXML_IMAGE_EXTENSIONS,
    FILE_INGESTION_STATUS
)

# Configuration du Logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-FILTER")

class Filter:
    # Priorité basse (1) pour s'exécuter en tout premier (Inlet) avant les autres filtres
    priority: int = 20

    class Valves(BaseModel):
        DEBUG_MODE: bool = Field(default=False)

    class UserValves(BaseModel):
        ENABLE_SMART_CONTEXT: bool = Field(default=True, description="Active le résumé intelligent des fichiers volumineux via Gemini Flash.")
        SMART_CONTEXT_CHUNK_LIMIT: int = Field(default=10, ge=2, le=50, description="🧠 Nombre de passages extraits pour la Synthèse Guidée par RAG (2-50).")
        ENABLE_OFFICE_CONVERSION: bool = Field(default=True, description="📄 Convertit automatiquement les fichiers Office (Word, Excel, PowerPoint) en texte pour l'analyse.")
        MAX_OFFICE_FILE_SIZE_MB: int = Field(default=DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB, description="📏 Taille maximale (Mo) des fichiers Office acceptés pour la conversion automatique.")
        ENABLE_USER_NAME: bool = Field(default=False, description="🔒 Partager mon nom avec le modèle.")
        OVERRIDE_LOCATION: str = Field(default="", description="📍 Surcharger ma position géographique (Ex: Paris, France).")

    def __init__(self):
        # ==============================================================================
        # INFRASTRUCTURE ECHO : CONTRÔLE DU RAG NATIF
        # ==============================================================================
        self.file_handler = True
        # ==============================================================================

        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self.auth = EchoAuth()
        self.toggle = True
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIzIi8+PHBhdGggZD0iTTEyIDdWNW0wIDE0di0yTTcgMTJINW0xNCAwaC0ybTEuNS01LjVsLTEuNSAxLjVNOCAxNmwtMS41IDEuNU0xNy41IDE3LjVsLTEuNS0xLjVNOCA4TDYuNSA2LjUiLz48cGF0aCBkPSJNMiAxMmg0bTExIDBoNW0tMyAwbDMtM20tMyAzbDMgMyIvPjwvc3ZnPg=="

    async def _convert_unsupported_file(
        self, path: str, ext: str, filename: str,
        user_id: str, chat_id: str, events: Any
    ) -> Optional[str]:
        """Convertit un fichier non supporté nativement en texte Markdown, écrit sur disque.
        
        Architecture extensible : le routage par extension permet d'ajouter
        de futures stratégies de conversion sans modifier _process_file_task.
        Retourne le chemin du fichier .md créé, ou None en cas d'échec.
        """
        # --- STRATÉGIE : MARKITDOWN (Office OOXML) ---
        if ext in CONVERTIBLE_OFFICE_EXTENSIONS:
            try:
                from markitdown import MarkItDown

                # 1. Conversion textuelle (sync, déportée en thread — CPU-bound)
                await events.status(f"📄 Conversion Office de {filename}...", False)
                md_converter = MarkItDown()
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, md_converter.convert, path)
                converted_text = result.text_content

                if not converted_text or not converted_text.strip():
                    raise ValueError("Conversion vide — fichier probablement corrompu.")

                # 2. Extraction et description des images embarquées (OOXML)
                if ext in OOXML_IMAGE_EXTENSIONS:
                    image_descriptions = await self._describe_ooxml_images(
                        path, filename, user_id, chat_id, events
                    )
                    if image_descriptions:
                        converted_text += "\n\n---\n## Images extraites du document\n\n"
                        converted_text += "\n\n".join(image_descriptions)

                # 3. Écriture sur disque — même répertoire que l'original
                md_path = os.path.splitext(path)[0] + "_converted.md"
                with open(md_path, "w", encoding="utf-8") as f:
                    f.write(converted_text)

                md_size = os.path.getsize(md_path)
                print(f"[ECHO-FILTER] ✅ Conversion réussie : {filename} → "
                      f"{md_path} ({md_size} octets)", flush=True)
                return md_path

            except ImportError:
                print("[ECHO-FILTER] !! markitdown non installé — conversion impossible", flush=True)
                return None
            except Exception as e:
                print(f"[ECHO-FILTER] !! Erreur conversion {filename}: {e}", flush=True)
                return None

        # --- FUTURES STRATÉGIES (extensibilité) ---
        # elif ext in SOME_OTHER_CONVERTIBLE_SET:
        #     ...

        return None

    async def _describe_ooxml_images(
        self, path: str, filename: str,
        user_id: str, chat_id: str, events: Any
    ) -> List[str]:
        """Extrait les images d'un OOXML (docx/pptx) et les décrit via LITE."""
        import zipfile

        descriptions = []
        try:
            with zipfile.ZipFile(path, 'r') as z:
                image_files = [
                    n for n in z.namelist()
                    if '/media/' in n and any(
                        n.lower().endswith(e)
                        for e in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')
                    )
                ]

                if not image_files:
                    return []

                await events.status(
                    f"🖼️ Description de {len(image_files)} image(s) de {filename}...", False
                )

                u_ctx = {"id": user_id}
                m_ctx = {"chat_id": chat_id}

                for i, img_name in enumerate(image_files):
                    try:
                        img_data = z.read(img_name)
                        b64 = base64.b64encode(img_data).decode("utf-8")

                        # Détection MIME par extension
                        img_ext = os.path.splitext(img_name)[1].lower()
                        mime_map = {
                            '.png': 'image/png', '.jpg': 'image/jpeg',
                            '.jpeg': 'image/jpeg', '.gif': 'image/gif',
                            '.webp': 'image/webp', '.bmp': 'image/bmp'
                        }
                        img_mime = mime_map.get(img_ext, 'image/png')

                        description = await EchoGeminiClient.call_distillation(
                            f"Décris cette image extraite du document '{filename}' "
                            f"de manière concise et précise (2-3 phrases max).",
                            u_ctx, m_ctx, is_json=False,
                            parts=[{"role": "user", "parts": [
                                {"text": f"Décris cette image '{os.path.basename(img_name)}' "
                                         f"extraite du document '{filename}'."},
                                {"inline_data": {"mime_type": img_mime, "data": b64}}
                            ]}],
                            target_model="MODEL_LITE"
                        )

                        if description and description != "Analyse indisponible.":
                            descriptions.append(
                                f"**Image {i+1}** (`{os.path.basename(img_name)}`) : {description}"
                            )
                    except Exception as img_err:
                        print(f"[ECHO-FILTER] !! Image {img_name}: {img_err}", flush=True)

        except zipfile.BadZipFile:
            print(f"[ECHO-FILTER] !! {filename} n'est pas un ZIP valide", flush=True)

        return descriptions

    async def _index_and_summarize(
        self, source_text: str, file_id: str, filename: str,
        mime: str, user_id: str, chat_id: str, events: Any
    ) -> dict:
        """Pipeline réutilisable : Indexation Mémoire Vectorisée de Session + Map-Reduce.
        
        Utilisé par :
        - CAS 3 (texte/multimodal large supporté nativement)
        - Post-conversion de fichiers non supportés (Office → MD sur disque)
        """
        u_ctx = {"id": user_id}
        m_ctx = {"chat_id": chat_id}

        # --- ETAPE 1 : Indexation Brute (Rapide) ---
        await events.status(f"Vectorisation de {filename}...", False)
        nb_points, err = await EchoGeminiClient.index_text_in_ephemeral_rag(
            source_text, file_id, user_id, chat_id, u_ctx, m_ctx
        )
        if nb_points == 0:
            print(f"[ECHO-FILTER] !! Vectorisation échouée pour {filename} : {err}", flush=True)
            raise ValueError(f"Vectorisation échouée : {err}")

        # --- ETAPE 2 : Synthèse Guidée par RAG (O(1)) ---
        brief_summary = "Résumé indisponible (fichier trop complexe)."
        try:
            words_len = len(source_text.split())
            if words_len <= ECHO_MR_SUMMARY_MAX_WORDS:
                await events.status(f"Fast-Path appliqué pour {filename} (texte court)...", False)
                brief_summary = source_text
            else:
                await events.status(f"Extraction RAG des thèmes clés de {filename}...", False)
                
                # Requête stratégique
                query_text = "Introduction, résumé exécutif, conclusion, thèmes clés et informations principales"
                vector = await EchoGeminiClient.generate_embedding(query_text, "query", u_ctx, m_ctx)
                
                if vector:
                    from echo_constants import COLLECTION_SESSION_RAG
                    limit = getattr(self.user_valves, 'SMART_CONTEXT_CHUNK_LIMIT', 10)
                    
                    async with httpx.AsyncClient(timeout=60) as client:
                        search_payload = {
                            "vector": vector, "limit": limit, "with_payload": True,
                            "filter": {
                                "must": [
                                    {"key": "user_id", "match": {"value": user_id}},
                                    {"key": "chat_id", "match": {"value": chat_id}},
                                    {"key": "source_id", "match": {"value": file_id}}
                                ]
                            }
                        }
                        resp = await client.post(
                            f"{ECHO_QDRANT_URL}/collections/{COLLECTION_SESSION_RAG}/points/search",
                            json=search_payload
                        )
                        
                        if resp.status_code == 200:
                            results = resp.json().get("result", [])
                            if results:
                                await events.status(f"Distillation globale de {filename}...", False)
                                combined = "\n\n---\n\n".join([r["payload"].get("text", "") for r in results])
                                prompt = f"Fais un résumé exhaustif et structuré (en markdown) de {ECHO_MR_SUMMARY_MAX_WORDS} mots maximum de ce document en te basant UNIQUEMENT sur les extraits suivants pertinents :\n\n{combined}"
                                brief_summary = await EchoGeminiClient.call_distillation(
                                    prompt, u_ctx, m_ctx, is_json=False, max_tokens=ECHO_MR_MAX_TOKENS
                                )
                            else:
                                brief_summary = "⚠️ *Aucun extrait pertinent n'a pu être récupéré via RAG.*"
                        else:
                            brief_summary = f"⚠️ *Erreur Qdrant lors de l'extraction RAG ({resp.status_code}).*"
                else:
                    brief_summary = "⚠️ *Échec de la vectorisation de la requête stratégique.*"

        except Exception as mr_err:
            print(f"[ECHO-FILTER] !! Erreur Synthèse Guidée par RAG pour {filename} : {mr_err}", flush=True)
            # On continue car l'indexation brute a réussi.
            brief_summary = "⚠️ *Le résumé automatique a échoué en raison d'une erreur interne.*"

        res_text = (
            f"<smart_context filename=\"{filename}\" mime_type=\"{mime}\" mode=\"vectorized_sum_up\"\n"
            f"                source_id=\"{file_id}\">\n"
            f"{brief_summary}\n\n"
            f"> ⚙️ INFORMATION SYSTÈME : Les détails du fichier sont vectorisés et accessibles via `search_session_context`\n"
            f"</smart_context>"
        )

        # Scellement déféré dans le pipe (V2 : plus de mark_processed dans le filtre)
        print(f"[ECHO-FILTER] ✅ {filename} → Mémoire Vectorisée de Session (source_id={file_id}).", flush=True)
        return {"status": "success", "type": FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"], "source_id": file_id, "fid": file_id, "name": filename, "mime": mime, "content": res_text}

    def _move_to_codex_and_commit(self, path: str, filename: str, file_id: str, mime: str, user_id: str, chat_id: str) -> str:
        """Déplace physiquement le fichier dans le Codex Git (Zero-RAM) et l'enregistre dans SQLite."""
        try:
            import shutil
            from echo_codex_git import CodexRepo
            from echo_utils import EchoStateManager, get_echo_session_path
            import dulwich.porcelain
            
            repo = CodexRepo(user_id, chat_id)
            safe_name = os.path.basename(filename)
            new_path = os.path.join(repo.repo_path, safe_name)
            
            # Préservation du snapshot immutable dans le vault 'files'
            vault_dir = get_echo_session_path(user_id, chat_id, "files")
            os.makedirs(vault_dir, exist_ok=True)
            vault_name = os.path.basename(path) # Typiquement file_id_*
            vault_path = os.path.join(vault_dir, vault_name)
            
            if path != vault_path:
                shutil.move(path, vault_path)
                
            # Copie vers le codex pour édition (branche de travail)
            shutil.copy2(vault_path, new_path)
            
            try:
                # Commit Git
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
                    # Gère le cas "No changes added to commit" (fichier identique)
                    print(f"[ECHO-FILTER] Commit vide ignoré pour {filename}: {commit_err}", flush=True)
                    commit_hash = repo.get_last_commit() or "unknown"
                
                # Mise à jour SQLite
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
                
                print(f"[ECHO-FILTER] 📦 Fichier {filename} intégré au Codex Zéro-RAM avec succès.", flush=True)
            except Exception as e:
                import traceback
                print(f"[ECHO-FILTER] !! Erreur mineure Codex pour {filename} (Git/SQL): {e}\n{traceback.format_exc()}", flush=True)
                
            return vault_path
        except Exception as e:
            import traceback
            print(f"[ECHO-FILTER] !! Erreur critique intégration Codex pour {filename}: {e}\n{traceback.format_exc()}", flush=True)
            return path

    async def _process_file_task(self, user_id: str, file_obj: dict, tokens: List[str], project_id: str, thinking_level: str, chat_id: str, events: Any) -> dict:
        """Tâche de traitement de fichier (Smart Context, Binaire ou Index)."""
        file_id = file_obj.get("id") or file_obj.get("file", {}).get("id")
        filename = file_obj.get("name") or file_obj.get("file", {}).get("meta", {}).get("name", "inconnu")
        mime = file_obj.get("mime_type") or file_obj.get("file", {}).get("meta", {}).get("content_type", "application/octet-stream")
        
        path = file_obj.get("file", {}).get("path")
        if not path or not os.path.exists(path):
            from echo_utils import resolve_upload_file_path
            path = resolve_upload_file_path(user_id, file_id, chat_id=chat_id)
            if not path:
                return {"status": "error", "fid": file_id, "name": filename, "error": "Fichier physique introuvable"}

        # Sécurisation immédiate dans le Vault pour TOUS les fichiers
        from echo_utils import get_echo_session_path
        import shutil
        vault_dir = get_echo_session_path(user_id, chat_id, "files")
        os.makedirs(vault_dir, exist_ok=True)
        vault_name = os.path.basename(path)
        vault_path = os.path.join(vault_dir, vault_name)
        if path != vault_path and os.path.exists(path):
            shutil.move(path, vault_path)
            path = vault_path

        size = os.path.getsize(path)
        mime, is_supported = get_gemini_mime(path)
        
        print(f"[ECHO-FILTER] 📄 Analyse de {filename} ({mime}) - Taille: {size} octets", flush=True)

        # === BLOC CONVERSION : Fichiers non supportés mais convertibles ===
        ext = os.path.splitext(path)[1].lower()
        max_convert_bytes = getattr(
            self.user_valves, 'MAX_OFFICE_FILE_SIZE_MB',
            DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB
        ) * 1024 * 1024

        if (not is_supported
            and ext in CONVERTIBLE_OFFICE_EXTENSIONS
            and self.user_valves.ENABLE_OFFICE_CONVERSION
            and size <= max_convert_bytes):

            converted_path = await self._convert_unsupported_file(
                path, ext, filename, user_id, chat_id, events
            )

            if converted_path:
                # Suppression de la source originale (DocX)
                try:
                    os.remove(path)
                    print(f"[ECHO-FILTER] 🗑️ Suppression de la source originale : {os.path.basename(path)}", flush=True)
                except Exception as e:
                    print(f"[ECHO-FILTER] !! Échec suppression source {path}: {e}", flush=True)

                # Substitution transparente : le fichier converti remplace l'original
                # dans le pipeline. Le nom reste inchangé pour le registre.
                path = converted_path
                size = os.path.getsize(converted_path)
                mime, is_supported = get_gemini_mime(converted_path)
                # → mime="text/plain", is_supported=True (.md dans MIME_MAPPING_TXT)
                # Le filename affiché dans les status suivants indique la conversion
                filename = f"{filename} (→ MD)"
                print(f"[ECHO-FILTER] 🔄 Substitution : {filename} → {mime} "
                      f"({size} octets) [converti]", flush=True)
            else:
                # Échec conversion → avertissement + fallthrough vers CAS 4
                await events.status(
                    f"⚠️ Conversion de {filename} échouée. Indexation par défaut.", True
                )

        # Ingestion Codex anticipée (Zero-RAM)
        if is_supported and ("text/" in mime or "application/json" in mime):
            path = self._move_to_codex_and_commit(path, filename, file_id, mime, user_id, chat_id)
            if os.path.exists(path):
                size = os.path.getsize(path)

        # --- CAS 1 : IMAGE / AUDIO / VIDEO / PDF (Injection Binaire Directe si petit) ---
        # Note: Délégation du traitement PDF en natif aux modèles Gemini 1.5.
        if is_supported and any(x in mime for x in ["image/", "audio/", "video/", "application/pdf"]) and size < MAX_DIRECT_MMEDIA_INJECT_SIZE:
            try:
                print(f"[ECHO-FILTER] --> Mode: BINAIRE (Base64)", flush=True)
                await events.status(f"Encapsulation de {filename}...", False)
                with open(path, "rb") as f:
                    b64 = base64.b64encode(f.read()).decode("utf-8")
                return {
                    "status": "success", "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"], "fid": file_id, "name": filename, "mime": mime, "sub_type": "binary",
                    "storage_path": path,
                    "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                }
            except Exception as e:
                print(f"[ECHO-FILTER] !! Erreur binaire: {e}", flush=True)
                return {"status": "error", "fid": file_id, "name": filename, "mime": mime, "error": f"Erreur binaire : {str(e)}"}

        # --- CAS 2 : TEXTE PETIT (Injection Directe) ---
        if is_supported and size < MAX_DIRECT_TEXT_INJECT_SIZE and ("text/" in mime or "application/json" in mime):
            try:
                print(f"[ECHO-FILTER] --> Mode: INJECTION_DIRECTE (Texte)", flush=True)
                with open(path, "r", encoding="utf-8", errors="ignore") as f: content = f.read()
                ext = os.path.splitext(filename)[1].strip('.')
                lang = ext if ext else ""
                return {
                    "status": "success", "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"], "fid": file_id, "name": filename, "mime": mime, "sub_type": "text",
                    "storage_path": path,
                    "content": f"📄 **Fichier : {filename}**\n```{lang}\n{content}\n```\n\n"
                }
            except Exception as e:
                print(f"[ECHO-FILTER] !! Erreur lecture: {e}", flush=True)
                return {"status": "error", "fid": file_id, "name": filename, "mime": mime, "error": f"Erreur lecture : {str(e)}"}

        # --- CAS 3 : TEXTE LARGE / MULTIMODAL LARGE (Mémoire Vectorisée de Session v8.0 Map-Reduce Transmodal) ---
        if self.user_valves.ENABLE_SMART_CONTEXT and is_supported:
            try:
                import unicodedata as _ud
                u_ctx = {"id": user_id}
                m_ctx = {"chat_id": chat_id}
                
                is_text = ("text/" in mime or "application/json" in mime)
                
                # --- PHASE 1 : EXTRACTION / TRANSCRIPTION ---
                source_text = ""
                if not is_text:
                    print(f"[ECHO-FILTER] --> Phase 1 (Extraction Multimédia) pour {filename}", flush=True)
                    with open(path, "rb") as f: b64_data = base64.b64encode(f.read()).decode("utf-8")
                    content_part = {"inline_data": {"mime_type": mime, "data": b64_data}}
                    
                    await events.status(f"Transcription API de {filename}...", False)
                    extraction_prompt = (
                        "Tu es un extracteur de données brut. Ta mission est de décrire, transcrire et analyser "
                        "ce document. Si le document est structuré reproduis et respecte strictement la structure. "
                        "Si le document est textuel, respecte strictement son verbatim. Si le document est audiovisuel "
                        "la description doit être précise, détaillée, complète, couvrant autant, le textuel, le visuel que l'audio, et parfaitement horosynchronisé."
                    )
                    multimodal_parts = [{"text": extraction_prompt}, content_part]
                    
                    source_text = await EchoGeminiClient.call_distillation(
                        extraction_prompt,
                        u_ctx, m_ctx, is_json=False,
                        parts=[{"role": "user", "parts": multimodal_parts}],
                        target_model="MODEL_DISTILLATION"
                    )
                    if not source_text:
                        raise ValueError("Transcription multimédia vide ou échouée.")
                else:
                    print(f"[ECHO-FILTER] --> Phase 1 (Lecture Texte) pour {filename}", flush=True)
                    with open(path, "r", encoding="utf-8", errors="ignore") as f: source_text = f.read()
                
                # --- PHASE 2 : MAP-REDUCE UNIVERSEL (délégué à _index_and_summarize) ---
                print(f"[ECHO-FILTER] --> Phase 2 (Indexation Brute + Map-Reduce) pour {filename}", flush=True)
                return await self._index_and_summarize(
                    source_text, file_id, filename, mime, user_id, chat_id, events
                )

            except Exception as e:
                print(f"[ECHO-FILTER] !! Exception CAS 3 pour {filename}: {e}", flush=True)
                # Fallback → CAS 4 (indexation)

        # CAS 4 : Scellement déféré dans le pipe (V2)
        print(f"[ECHO-FILTER] --> Mode: INDEXATION (Fallback)", flush=True)
        return {"status": "success", "type": FILE_INGESTION_STATUS["INDEXED"], "fid": file_id, "name": filename, "mime": mime}

    def _dict_to_yaml(self, d: Any, indent: int = 0) -> str:
        """Sérialiseur YAML minimaliste pour ECHO."""
        lines = []
        space = "  " * indent
        if isinstance(d, list):
            for item in d:
                if isinstance(item, dict):
                    lines.append(f"{space}-")
                    lines.append(self._dict_to_yaml(item, indent + 1))
                else:
                    lines.append(f"{space}- {item}")
        elif isinstance(d, dict):
            for k, v in d.items():
                if isinstance(v, dict):
                    if not v: lines.append(f"{space}{k}: {{}}")
                    else:
                        lines.append(f"{space}{k}:")
                        lines.append(self._dict_to_yaml(v, indent + 1))
                elif isinstance(v, list):
                    if not v: lines.append(f"{space}{k}: []")
                    else:
                        lines.append(f"{space}{k}:")
                        for item in v:
                            if isinstance(item, dict):
                                lines.append(f"{space}  -")
                                lines.append(self._dict_to_yaml(item, indent + 2))
                            else:
                                lines.append(f"{space}  - {item}")
                else:
                    val = str(v).replace("\n", " ") if v is not None else ""
                    lines.append(f"{space}{k}: {val}")
        return "\n".join(lines)

    async def inlet(self, body: dict, __user__: Optional[dict] = None, __metadata__: Optional[Dict] = None, __event_emitter__: Optional[Any] = None) -> dict:
        try:
            from echo_utils import EchoEvents, get_echo_version, EchoStateManager
            events = EchoEvents(__event_emitter__)
            
            meta = __metadata__ or body.get("metadata", {})
            chat_id = meta.get("chat_id")
            user_id = __user__.get("id", "system") if __user__ else "system"
            
            # Factorisation : Création anticipée du DOMAIN (Vault) Utilisateur-Chat
            state_manager = None
            if chat_id:
                state_manager = EchoStateManager(user_id=user_id, chat_id=chat_id)
            
            all_files_dict = {}
            for f in body.get("files", []):
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid: all_files_dict[fid] = f
                
            user_msg_files = meta.get("user_message", {}).get("files", [])
            for f in user_msg_files:
                fid = f.get("id") or f.get("file", {}).get("id")
                if fid: all_files_dict[fid] = f
                
            all_files = list(all_files_dict.values())
            
            msgs = body.get("messages", [])
            if not msgs: return body

            if len(msgs) >= 2:
                prev_content = str(msgs[-2].get("content", ""))
                # Interception clé API AI Studio (fallback OAuth2).
                # L'ancien bloc de détection code 4/… (PKCE) est supprimé :
                # le Device Flow (RFC 8628) ne génère pas de code dans le chat.
                last_content = str(msgs[-1].get("content", "")).strip()
                keys = re.findall(GOOGLE_API_KEY_REGEX, last_content)
                if "(ECHO_SESSION_AUTH_PENDING)" in prev_content and keys:
                    body["_api_key"] = last_content
                    msgs[-1]["content"] = "🔐 *Vérification de la clé API Google en cours...*"
                    return body

            tokens = []
            if __user__ and "id" in __user__:
                tokens = self.auth.get_api_keys(__user__["id"])

            files_to_process = []
            files_already_processed = []
            if chat_id:
                from echo_utils import get_echo_session_path
                vault_dir = os.path.normpath(get_echo_session_path(user_id, chat_id, "files"))
                for f in all_files:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    if fid:
                        path = resolve_upload_file_path(user_id, fid, chat_id=chat_id)
                        if path:
                            if os.path.normpath(path).startswith(vault_dir):
                                files_already_processed.append(f)
                            else:
                                files_to_process.append(f)
                
            # [NEW] Injection des téléchargements Playwright en attente d'ingestion
            if chat_id and state_manager:
                pending_resources = state_manager.get_resources(
                    status=FILE_INGESTION_STATUS.get("PENDING_INGESTION", "pending_ingestion")
                )
                for pr in pending_resources:
                    f_obj = {
                        "id": pr["id"],
                        "name": pr["name"],
                        "mime_type": pr.get("mime", "application/octet-stream"),
                        "file": {
                            "id": pr["id"],
                            "name": pr["name"],
                            "path": pr.get("storage_path", "")
                        }
                    }
                    if f_obj not in files_to_process:
                        files_to_process.append(f_obj)

            results_to_seal = []
            if files_to_process and chat_id:
                await events.status(f"Aiguillage de {len(files_to_process)} fichiers...", False)
                tasks = [self._process_file_task(user_id, f, tokens, None, THINKING_LEVEL_FLASH, chat_id, events) for f in files_to_process]
                for task in tasks:
                    results_to_seal.append(await task)
                    await asyncio.sleep(0.5)

            results = list(results_to_seal)
            
            # Réhydratation hybride : Disque (Codex) / Base (Images/PDFs)
            if files_already_processed and chat_id and state_manager:
                for f in files_already_processed:
                    fid = f.get("id") or f.get("file", {}).get("id")
                    res = state_manager.get_resource(fid)
                    if res:
                        if res.get("resource_type") == "codex" and res.get("storage_path") and os.path.exists(res["storage_path"]):
                            with open(res["storage_path"], "r", encoding="utf-8") as file_obj:
                                content = file_obj.read()
                            filename = res.get("name", "fichier")
                            results.append({
                                "status": "success",
                                "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
                                "sub_type": "text",
                                "content": f"📄 **Fichier : {filename}**\n\n```\n{content}\n```"
                            })
                        elif res.get("resource_type") == "media" and res.get("status") == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"] and res.get("storage_path") and os.path.exists(res["storage_path"]):
                            # Réhydratation d'un média binaire petit (CAS 1)
                            import base64
                            with open(res["storage_path"], "rb") as file_obj:
                                b64 = base64.b64encode(file_obj.read()).decode("utf-8")
                            mime = res.get("mime", "application/octet-stream")
                            filename = res.get("name", "fichier")
                            results.append({
                                "status": "success",
                                "type": FILE_INGESTION_STATUS["PUT_IN_CONTEXT"],
                                "sub_type": "binary",
                                "content": {"anchor": f"📎 **Fichier joint : {filename}** ({mime})", "mime": mime, "data": b64}
                            })
                        elif res.get("summary"):
                            # CAS 3 (Vectorisé) ou image résumée
                            results.append({
                                "status": "success",
                                "type": res.get("status", FILE_INGESTION_STATUS["PUT_IN_CONTEXT"]),
                                "sub_type": "text" if "text" in str(res.get("mime", "")) or "json" in str(res.get("mime", "")) else "image",
                                "content": res["summary"]
                            })

            idx = -1
            ordered_user_parts = []  # Parts user en ordre (texte + images entrelacés)
            for i in range(len(msgs)-1, -1, -1):
                if msgs[i].get("role") == "user": 
                    idx = i
                    orig_content = msgs[i].get("content")
                    if isinstance(orig_content, list):
                        # Content multipart OWUI (texte + images inline) : extraction ordonnée
                        for p in orig_content:
                            if isinstance(p, dict):
                                if p.get("type") == "image_url" or "inline_data" in p or "inlineData" in p:
                                    ordered_user_parts.append(p)
                                elif p.get("type") == "text" and p.get("text", "").strip():
                                    ordered_user_parts.append({"text": p["text"]})
                    break

            if idx != -1:
                meta_vars = meta.get("variables", {})
                u_v = __user__.get("valves") if __user__ else self.user_valves
                display_name = __user__.get("name", "anonyme") if getattr(u_v, "ENABLE_USER_NAME", False) else "anonyme"
                
                sys_loc = meta_vars.get("{{USER_LOCATION}}", "Inconnu")
                u_loc = getattr(u_v, "OVERRIDE_LOCATION", "")
                final_loc = u_loc if u_loc else sys_loc
                
                # === AEC V2 : Snapshot minimaliste (sans registres) ===
                env_snapshot = {
                    "version_framework_echo": get_echo_version() or "##ECHO_VERSION##",
                    "modèle_actuel": "##MODEL_ID##",
                    "modèle_origine": "##MODEL_ORIGIN##",
                    "nom_utilisateur": display_name,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "localisation": final_loc,
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC"),
                }

                body.setdefault("metadata", {})
                body["metadata"]["_echo_env_info"] = {
                    "nom_utilisateur": display_name, "localisation": final_loc,
                    "date_et_heure": meta_vars.get("{{CURRENT_DATETIME}}", "Inconnu"),
                    "timezone": meta_vars.get("{{CURRENT_TIMEZONE}}", "UTC")
                }
                
                rich_parts = []
                yaml_str = self._dict_to_yaml(env_snapshot)
                rich_parts.append({"text": f"<environnement_contexte>\n{yaml_str}\n</environnement_contexte>\n\n"})

                # === AEC V2 : Évènements système (fichiers uploadés ce tour) ===
                sys_events = []
                for r in results:
                    if r.get("status") == "success":
                        evt = {"type": r.get("type"), "name": r.get("name"), "mime": r.get("mime")}
                        if r.get("source_id"): evt["source_id"] = r["source_id"]
                        sys_events.append(evt)

                # === AEC V2 : Détection delta (ressources créées par outils/HUD hors-tour) ===
                if chat_id:
                    last_check = body.get("metadata", {}).get("_echo_last_event_check_at")
                    if last_check:
                        delta_resources = state_manager.get_resources(created_after=int(last_check))
                        for dr in delta_resources:
                            # Ne pas dupliquer les fichiers du tour courant
                            if not any(e.get("name") == dr["name"] for e in sys_events):
                                sys_events.append({
                                    "type": dr["status"], "name": dr["name"],
                                    "mime": dr.get("mime"), "resource_type": dr["resource_type"],
                                    "source": "outil/HUD"
                                })
                    # Sauvegarder le timestamp actuel pour le prochain delta
                    body["metadata"]["_echo_last_event_check_at"] = int(time.time())

                # Injection des évènements dans l'AEC (uniquement s'il y en a)
                if sys_events:
                    events_yaml = self._dict_to_yaml(sys_events)
                    rich_parts.append({"text": f"<evenement_systeme>\n{events_yaml}\n"
                                              f"> Utilisez `query_registry` pour consulter l'état complet des ressources.\n"
                                              f"</evenement_systeme>\n\n"})

                if ordered_user_parts: rich_parts.extend(ordered_user_parts)
                
                for res in results:
                    if res.get("status") == "success":
                        if res["type"] == FILE_INGESTION_STATUS["VECTORIZED_SUM_UP"]: rich_parts.append({"text": res["content"]})
                        elif res["type"] == FILE_INGESTION_STATUS["PUT_IN_CONTEXT"]:
                            if res["sub_type"] == "text": rich_parts.append({"text": res["content"]})
                            else:
                                rich_parts.append({"text": res["content"]["anchor"]})
                                rich_parts.append({"inline_data": {"mime_type": res["content"]["mime"], "data": res["content"]["data"]}})
                
                body["metadata"]["_echo_user_parts_draft"] = rich_parts
                body["metadata"]["_echo_user_msg_id"] = msgs[idx].get("id")
                body["metadata"]["_echo_user_msg_updated_at"] = msgs[idx].get("updated_at")
                body["metadata"]["_echo_files_to_seal"] = results_to_seal

            if all_files:
                body.setdefault("metadata", {})
                body["metadata"]["_echo_files"] = all_files
                body["files"] = []
                body["citations"] = False

            return body
        except Exception as e:
            print(f"[ECHO-FILTER] ❌ CRITICAL ERROR: {e}", flush=True)
            return body

    async def outlet(self, body: dict, __user__: Optional[dict] = None) -> dict:
        msgs = body.get("messages", [])
        for m in msgs:
            content = str(m.get("content", ""))
            # Masquage des clés API AI Studio (AIza...) si elles apparaissent dans l'historique
            if re.search(GOOGLE_API_KEY_REGEX, content):
                content = re.sub(GOOGLE_API_KEY_REGEX, "[CLÉ API GOOGLE MASQUÉE PAR SÉCURITÉ]", content)
            m["content"] = content
        return body
