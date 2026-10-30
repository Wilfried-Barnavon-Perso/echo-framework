"""
title: ECHO Explorateur de l'Espace Personnel
author: Wilfried BARNAVON
version: 5.231.33.2
description: Composant système interne : ECHO Explorateur de l'Espace Personnel.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.33.2: Factorisation du pipeline multimodal (_execute_multimodal_analysis, _call_apollo_with_heartbeat), auto-résolution FFmpeg (_ensure_ffmpeg_bin via imageio-ffmpeg sur open-webui:main-slim) et segmentation Map-Reduce 100% en mémoire vive (FFmpeg -c copy pipe:1 / pypdf BytesIO) sur MAX_MULTIMODAL_SIZE_KB et 45 min / 1M tokens (ECHO_MAX_CONTEXT_SIZE).
# 5.231.32.1: Surcharge d'instruction pour read_multimedia_file, correction d'assignation SYS/USR prompt et docstring descriptive étendue.
# 5.231.29.1: Alignement de _resolve_extended_file_path sur les constantes SSOT CODEX_DIR_NAME, CODEX_WORKSPACE_MAIN et CODEX_WORKSPACE_SANDBOX.
# 5.231.20.1: Isolation multi-chat du viewer d'image via transmission de chat_id à EchoUI.show_image_js.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).

import io
import os
import re
import sys
import shutil
import base64
import httpx
import mimetypes
import hashlib
import asyncio
from typing import AsyncGenerator, List, Any, Literal, Optional, Tuple
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
    ECHO_MAX_CONTEXT_SIZE,
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
                warning_msg = f"\n\n[ATTENTION SYSTÈME ECHO]\nCe fichier est un binaire ({mime}). Si le texte extrait ci-dessus est illisible, incomplet ou ne correspond pas aux attentes, le Modèle a la STRICTE INTERDICTION de boucler en rappelant l'outil avec d'autres lignes/offsets.\n=> Le Modèle DOIT utiliser output_mode='base64' pour une injection multimodale native (PDF/Images/Media), ou déléguer l'analyse via `semantic_probe` (extraction ciblée) ou `read_multimedia_file` (distillation descriptive complète)."

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

    @staticmethod
    def _format_hms(seconds: float) -> str:
        """Formate un nombre de secondes en HH:MM:SS."""
        total = max(0, int(round(seconds)))
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    @classmethod
    def _shift_timestamps(cls, text: str, offset_sec: float) -> str:
        """Recale dynamiquement les horodatages [HH:MM:SS] ou [MM:SS] d'un rapport selon l'offset temporel du segment."""
        if not text or offset_sec <= 0:
            return text
        delta = int(round(offset_sec))

        def _parse_and_shift(ts_str: str) -> str:
            parts = [int(p) for p in ts_str.split(":")]
            if len(parts) == 3:
                sec = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                sec = parts[0] * 60 + parts[1]
            return cls._format_hms(sec + delta)

        def _replacer(match: re.Match) -> str:
            t1 = _parse_and_shift(match.group(1))
            t2 = match.group(2)
            if t2:
                return f"[{t1} - {_parse_and_shift(t2)}]"
            return f"[{t1}]"

        return re.sub(
            r"\[(\d{1,2}:\d{2}(?::\d{2})?)(?:\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?))?\]",
            _replacer,
            text,
        )

    _FFMPEG_BIN: Optional[str] = None

    async def _ensure_ffmpeg_bin(self) -> str:
        """
        Résout le chemin de l'exécutable ffmpeg (natif ou statique via imageio-ffmpeg).
        Sur open-webui:main-slim dépourvu de /usr/bin/ffmpeg, provisionne imageio-ffmpeg à chaud si nécessaire.
        """
        if Tools._FFMPEG_BIN and os.path.exists(Tools._FFMPEG_BIN):
            return Tools._FFMPEG_BIN

        sys_ffmpeg = shutil.which("ffmpeg")
        if sys_ffmpeg:
            Tools._FFMPEG_BIN = sys_ffmpeg
            return sys_ffmpeg

        try:
            import imageio_ffmpeg
            exe = await asyncio.to_thread(imageio_ffmpeg.get_ffmpeg_exe)
            if exe and os.path.exists(exe):
                Tools._FFMPEG_BIN = exe
                return exe
        except ImportError:
            try:
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "pip", "install", "--quiet",
                    "--break-system-packages", "imageio-ffmpeg",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=60.0)
                import imageio_ffmpeg
                exe = await asyncio.to_thread(imageio_ffmpeg.get_ffmpeg_exe)
                if exe and os.path.exists(exe):
                    Tools._FFMPEG_BIN = exe
                    return exe
            except Exception:
                pass
        except Exception:
            pass

        return "ffmpeg"

    async def _probe_media_duration(self, fpath: str) -> Optional[float]:
        """Sonde la durée exacte (en secondes) d'un flux audio ou vidéo via ffprobe ou ffmpeg."""
        ffprobe_bin = shutil.which("ffprobe")
        if ffprobe_bin:
            try:
                proc = await asyncio.create_subprocess_exec(
                    ffprobe_bin,
                    "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    fpath,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                val = stdout.decode("utf-8", errors="ignore").strip()
                if val and val != "N/A":
                    dur = float(val)
                    if dur > 0:
                        return dur
            except Exception:
                pass

        ffmpeg_bin = await self._ensure_ffmpeg_bin()
        try:
            proc = await asyncio.create_subprocess_exec(
                ffmpeg_bin, "-hide_banner", "-i", fpath,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=15.0)
            err_txt = stderr.decode("utf-8", errors="ignore")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err_txt)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
        return None

    @staticmethod
    def _get_ffmpeg_muxer_and_mime(mime: str, ext: str) -> Tuple[List[str], str]:
        """Sélectionne le conteneur streamable (pipe:1) sans aucun ré-encodage (-c copy)."""
        if mime.startswith("audio/"):
            if mime in ("audio/mp3", "audio/mpeg") or ext == ".mp3":
                return ["-f", "mp3"], "audio/mp3"
            if mime in ("audio/aac", "audio/mp4", "audio/x-m4a") or ext in (".aac", ".m4a"):
                return ["-f", "adts"], "audio/aac"
            if mime in ("audio/ogg", "audio/opus") or ext in (".ogg", ".opus"):
                return ["-f", "ogg"], "audio/ogg"
            if mime == "audio/flac" or ext == ".flac":
                return ["-f", "flac"], "audio/flac"
            if mime in ("audio/wav", "audio/x-wav") or ext == ".wav":
                return ["-f", "wav"], "audio/wav"
            return ["-f", "matroska"], "audio/webm"

        if mime == "video/webm" or ext in (".webm", ".mkv"):
            return ["-f", "matroska"], "video/webm"
        if mime in ("video/mpeg", "video/mp2t") or ext in (".mpeg", ".mpg", ".ts"):
            return ["-f", "mpegts"], "video/mpeg"
        return [
            "-f", "mp4",
            "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        ], "video/mp4"

    async def _extract_media_segment_bytes(
        self,
        fpath: str,
        mime: str,
        start_sec: float,
        end_sec: float,
        max_bytes: int,
        allow_overflow: bool = False,
    ) -> Tuple[Optional[bytes], str, int]:
        """
        Extrait une tranche [start_sec, end_sec] 100% en mémoire vive via FFmpeg (-c copy ... pipe:1).
        Aucun canal audio ou vidéo n'est ré-encodé ou altéré.
        Retourne (raw_bytes, out_mime, status_code) où status_code vaut :
          0 = succès, 1 = dépassement max_bytes (overflow VBR), -1 = EOF ou erreur.
        """
        ext = os.path.splitext(fpath)[1].lower()
        muxer_args, out_mime = self._get_ffmpeg_muxer_and_mime(mime, ext)
        ffmpeg_bin = await self._ensure_ffmpeg_bin()
        seg_duration = max(0.1, end_sec - start_sec)

        async def _run_ffmpeg(m_args: List[str]) -> Tuple[Optional[bytes], int]:
            cmd = [
                ffmpeg_bin,
                "-hide_banner",
                "-loglevel", "error",
                "-ss", f"{start_sec:.3f}",
                "-t", f"{seg_duration:.3f}",
                "-i", fpath,
                "-c", "copy",
                *m_args,
                "pipe:1",
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            buf = bytearray()
            overflow = False
            try:
                while True:
                    chunk = await proc.stdout.read(65536)
                    if not chunk:
                        break
                    buf.extend(chunk)
                    if not allow_overflow and len(buf) > max_bytes:
                        overflow = True
                        proc.kill()
                        break
                await proc.wait()
            except Exception:
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                return None, -1

            if overflow:
                return None, 1
            if proc.returncode != 0 or len(buf) == 0:
                return None, -1
            return bytes(buf), 0

        data, code = await _run_ffmpeg(muxer_args)
        if code == 1:
            return None, out_mime, 1
        if data is None and mime.startswith("video/") and muxer_args[1] != "matroska":
            # Fallback conteneur universel Matroska si le codec source est incompatible avec fMP4 en stream copy
            data, code = await _run_ffmpeg(["-f", "matroska"])
            out_mime = "video/webm"
            if code == 1:
                return None, out_mime, 1
        return data, out_mime, code

    async def _iter_pdf_memory_segments(
        self, fpath: str, max_bytes: int
    ) -> AsyncGenerator[Tuple[str, str, float, Optional[str]], None]:
        """Segmente un PDF volumineux (> MAX_MULTIMODAL_SIZE_KB) par lots de pages 100% en mémoire (io.BytesIO)."""
        def _extract_pdf_batches() -> List[Tuple[bytes, str]]:
            try:
                from pypdf import PdfReader, PdfWriter
                reader = PdfReader(fpath)
                total_pages = len(reader.pages)
                if total_pages == 0:
                    return []
                batches: List[Tuple[bytes, str]] = []
                writer = PdfWriter()
                batch_start = 1
                last_valid_bytes = b""

                for idx, page in enumerate(reader.pages, start=1):
                    writer.add_page(page)
                    bio = io.BytesIO()
                    writer.write(bio)
                    cur_bytes = bio.getvalue()
                    if len(cur_bytes) > max_bytes and len(writer.pages) > 1:
                        batches.append((last_valid_bytes, f"Pages {batch_start}-{idx - 1}/{total_pages}"))
                        writer = PdfWriter()
                        writer.add_page(page)
                        bio = io.BytesIO()
                        writer.write(bio)
                        last_valid_bytes = bio.getvalue()
                        batch_start = idx
                    else:
                        last_valid_bytes = cur_bytes

                if len(writer.pages) > 0 and last_valid_bytes:
                    batches.append((last_valid_bytes, f"Pages {batch_start}-{total_pages}/{total_pages}"))
                return batches
            except Exception:
                return []

        batches = await asyncio.to_thread(_extract_pdf_batches)
        if not batches:
            yield f"___ECHO_STREAM_FILE___{fpath}___", "application/pdf", 0.0, None
            return
        for raw_pdf, label in batches:
            marker = EchoGeminiClient.register_memory_stream(raw_pdf)
            yield marker, "application/pdf", 0.0, label

    async def _iter_in_memory_segments(
        self, fpath: str, mime: str
    ) -> AsyncGenerator[Tuple[str, str, float, Optional[str]], None]:
        """
        Générateur JIT de segments multimodaux en mémoire vive :
        - Fast-Path Zéro-RAM sur fichier direct si Taille <= MAX_MULTIMODAL_SIZE_KB et Durée <= 45 min / 1M tokens.
        - Segmentation en mémoire vive (FFmpeg -c copy pipe:1 ou pypdf BytesIO) avec subdivision dichotomique anti-VBR sinon.
        """
        max_bytes = self.valves.MAX_MULTIMODAL_SIZE_KB * 1024
        max_duration_sec = int(2700 * (ECHO_MAX_CONTEXT_SIZE / 1048576))
        file_size = os.path.getsize(fpath)

        is_av = mime.startswith(("audio/", "video/"))
        duration = await self._probe_media_duration(fpath) if is_av else None

        # 1. Fast-Path : Le fichier respecte à la fois le plafond d'octets (30 Mo) et le plafond temporel (45 min / 1M tokens)
        if file_size <= max_bytes and (duration is None or duration <= max_duration_sec):
            yield f"___ECHO_STREAM_FILE___{fpath}___", mime, 0.0, None
            return

        # 2. Segmentation PDF en mémoire vive
        if mime == "application/pdf" and file_size > max_bytes:
            async for seg in self._iter_pdf_memory_segments(fpath, max_bytes):
                yield seg
            return

        # 3. Segmentation Audio/Vidéo en mémoire vive (Stream Copy -c copy vers pipe:1)
        if is_av:
            if duration and duration > 0:
                bps = file_size / duration
                safe_target_bytes = int(max_bytes * 0.85)
                step_by_size = (safe_target_bytes / bps) if bps > 0 else float(max_duration_sec)
                step_sec = max(15.0, min(float(max_duration_sec), step_by_size))
                overlap_sec = 5.0 if step_sec > 20.0 else 0.0

                queue: List[Tuple[float, float]] = []
                cur = 0.0
                while cur < duration:
                    nxt = min(duration, cur + step_sec)
                    queue.append((cur, nxt))
                    if nxt >= duration:
                        break
                    cur = nxt - overlap_sec
            else:
                # Si la durée d'en-tête est absente (Duration: N/A), progresse par fenêtres glissantes jusqu'à EOF
                step_sec = 180.0
                overlap_sec = 5.0
                queue = [(0.0, step_sec)]

            unknown_dur = not (duration and duration > 0)
            while queue:
                s_sec, e_sec = queue.pop(0)
                span = e_sec - s_sec
                can_split = span > 10.0
                raw_bytes, seg_mime, status_code = await self._extract_media_segment_bytes(
                    fpath, mime, s_sec, e_sec, max_bytes, allow_overflow=not can_split
                )
                if status_code == 1 and can_split:
                    # Subdivision dichotomique immédiate en cas de pic de débit VBR dépassant 30 Mo
                    mid = (s_sec + e_sec) / 2.0
                    sub_overlap = min(2.0, span * 0.05)
                    queue.insert(0, (max(s_sec, mid - sub_overlap), e_sec))
                    queue.insert(0, (s_sec, mid))
                    continue
                if raw_bytes:
                    marker = EchoGeminiClient.register_memory_stream(raw_bytes)
                    label = f"Segment [{self._format_hms(s_sec)} - {self._format_hms(e_sec)}]"
                    yield marker, seg_mime, s_sec, label
                    if unknown_dur and not queue and len(raw_bytes) > 4096:
                        nxt_start = e_sec - overlap_sec
                        queue.append((nxt_start, nxt_start + step_sec))
            return

        # 4. Fallback de sécurité si un binaire non-segmentable dépasse MAX_MULTIMODAL_SIZE_KB
        if file_size > max_bytes:
            raise ValueError(
                f"Max Size Exceeded : Le fichier ({file_size / (1024 * 1024):.1f} Mo) dépasse la limite autorisée de {self.valves.MAX_MULTIMODAL_SIZE_KB / 1024:.1f} Mo et son format ({mime}) ne supporte pas la segmentation."
            )
        yield f"___ECHO_STREAM_FILE___{fpath}___", mime, 0.0, None

    async def _call_apollo_with_heartbeat(
        self,
        payload: dict,
        uid: str,
        metadata: dict,
        events: EchoEvents,
        heartbeats: List[str],
    ) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str], Optional[str]]:
        """
        Exécute de façon factorisée l'appel EchoGeminiClient.call_cascade sur MODEL_APOLLO
        avec maintien des heartbeats UI toutes les 15s, gestion des timeouts et extraction des pensées.
        Retourne (clean_text, thought_text, model_used, reason, error_msg).
        """
        cascade_task = asyncio.create_task(
            EchoGeminiClient.call_cascade(
                target_model_key="MODEL_APOLLO",
                payload=payload,
                user_id=uid,
                metadata=metadata,
                events=events,
                threshold=ECHO_API_KEY_RETRIES,
                max_retries=ECHO_API_MAX_RETRIES,
                timeout=self.valves.PROBE_TIMEOUT,
                include_thoughts=True,
            )
        )

        step_idx = 0
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
            return None, None, None, None, str(cascade_task.exception())

        data, model_used, reason = cascade_task.result()
        if not data:
            return None, None, model_used, reason, "Cascade épuisée : aucun modèle disponible."

        target = data.get("response", {}) if "response" in data else data
        candidates = target.get("candidates", [])
        if not candidates:
            return None, None, model_used, reason, "Réponse vide du modèle."
        cand = candidates[0]
        full_text = "".join([p["text"] for p in cand.get("content", {}).get("parts", []) if "text" in p])
        clean, thought = split_thought_process(full_text)
        return clean, thought, model_used, reason, None

    async def _execute_multimodal_analysis(
        self,
        file_id: str,
        user_prompt: str,
        mode: Literal["probe", "sensory"],
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Pipeline maître factorisé pour semantic_probe ("probe") et read_multimedia_file ("sensory").
        Gère la résolution multi-espaces, le streaming Zéro-RAM ou la segmentation Map-Reduce 100% en mémoire vive,
        le recalage chronologique des timestamps et la synthèse de consolidation.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "system") if __user__ else "system"
        cid = __metadata__.get("chat_id") if __metadata__ else None
        fpath = self._resolve_extended_file_path(uid, cid, file_id)
        if not fpath:
            return wrap_tool_output(
                text="❌ Fichier introuvable.",
                status={"status": "error"},
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__,
            )

        mime, supported = get_gemini_mime(fpath)
        if not supported:
            return wrap_tool_output(
                text=f"❌ Type {mime} non supporté.",
                status={"status": "error"},
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__,
            )

        safe_name = os.path.basename(fpath)
        if "_" in safe_name and len(safe_name.split("_")[0]) >= 32:
            safe_name = safe_name.split("_", 1)[1]

        system_prompt = (
            SYS_EXPLORE_SENSORY.format(filename=safe_name)
            if mode == "sensory"
            else SYS_EXPLORER_PROBE
        )

        segment_outputs: List[Tuple[Optional[str], str]] = []
        thoughts_acc: List[str] = []
        last_model_used = "MODEL_APOLLO"
        last_reason = None
        seg_idx = 0

        try:
            async for data_marker, seg_mime, start_offset_sec, seg_label in self._iter_in_memory_segments(fpath, mime):
                seg_idx += 1
                try:
                    if seg_label:
                        seg_prompt = (
                            f"[CONTEXTE DE SEGMENTATION : {seg_label} du fichier '{safe_name}'. "
                            f"Pour tout horodatage, partir de 00:00:00 au début de cet extrait ; le système recalera automatiquement l'offset.]\n\n"
                            f"{user_prompt}"
                        )
                        hb_prefix = f"{safe_name} ({seg_label})"
                    else:
                        seg_prompt = user_prompt
                        hb_prefix = safe_name

                    if mode == "sensory":
                        heartbeats = [
                            f"👁️ L'agent sensoriel visionne {hb_prefix}...",
                            f"👁️ Analyse multimodale en cours ({hb_prefix})...",
                            f"👁️ Extraction et synchronisation des détails ({hb_prefix})...",
                        ]
                    else:
                        heartbeats = [
                            f"🤖 Analyse sémantique de {hb_prefix}...",
                            f"🤖 Parcours des vecteurs sémantiques ({hb_prefix})...",
                            f"🤖 Extraction chirurgicale des concepts ({hb_prefix})...",
                        ]

                    payload = {
                        "contents": [
                            {
                                "role": "user",
                                "parts": [
                                    {"text": seg_prompt},
                                    {"inline_data": {"mime_type": seg_mime, "data": data_marker}},
                                ],
                            }
                        ],
                        "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
                        "systemInstruction": {"parts": [{"text": system_prompt}]},
                    }

                    clean, thought, model_used, reason, err = await self._call_apollo_with_heartbeat(
                        payload=payload,
                        uid=uid,
                        metadata=__metadata__,
                        events=events,
                        heartbeats=heartbeats,
                    )
                finally:
                    if data_marker.startswith("___ECHO_STREAM_MEM___"):
                        EchoGeminiClient.release_memory_stream(data_marker)

                if err:
                    prefix_err = "❌ Erreur de l'agent sensoriel" if mode == "sensory" else "❌ Erreur Sonde"
                    return wrap_tool_output(
                        text=f"{prefix_err} : {err}",
                        status={"status": "error"},
                        user_id=uid,
                        chat_id=cid,
                        metadata=__metadata__,
                    )

                if model_used:
                    last_model_used = model_used
                if reason:
                    last_reason = reason
                if thought:
                    thoughts_acc.append(f"[{seg_label}] {thought}" if seg_label else thought)

                shifted_clean = self._shift_timestamps(clean or "", start_offset_sec)
                segment_outputs.append((seg_label, shifted_clean))

            if not segment_outputs:
                return wrap_tool_output(
                    text="❌ Aucun segment exploitable n'a pu être extrait du fichier.",
                    status={"status": "error"},
                    user_id=uid,
                    chat_id=cid,
                    metadata=__metadata__,
                )

            # Assemblage final (Fast-Path mono-segment vs Map-Reduce multi-segments)
            if len(segment_outputs) == 1 and segment_outputs[0][0] is None:
                final_clean = segment_outputs[0][1]
                if mode == "sensory":
                    final_text = f"### 👁️ Rapport de Distillation ({safe_name})\n{final_clean}"
                else:
                    final_text = final_clean
            else:
                if mode == "sensory":
                    blocks = [
                        f"#### ⏱️ {lbl}\n{txt}" if lbl else txt
                        for lbl, txt in segment_outputs
                    ]
                    final_text = (
                        f"### 👁️ Rapport de Distillation ({safe_name} — {len(segment_outputs)} segments)\n\n"
                        + "\n\n---\n\n".join(blocks)
                    )
                else:
                    # Consolidation Map-Reduce pour semantic_probe : synthèse chirurgicale unifiée répondant à query
                    compiled_notes = "\n\n".join(
                        [f"=== {lbl or f'Segment {i + 1}'} ===\n{txt}" for i, (lbl, txt) in enumerate(segment_outputs)]
                    )
                    reduce_prompt = (
                        f"Requête d'extraction initiale : {user_prompt}\n\n"
                        f"Voici les relevés extraits segment par segment du fichier '{safe_name}' (horodatages déjà recalés) :\n\n"
                        f"{compiled_notes}\n\n"
                        "Le Modèle DOIT synthétiser et consolider ces relevés en une réponse unique, directe, précise et rigoureuse "
                        "répondant exactement à la requête initiale, en conservant les citations, données et repères temporels ou paginés pertinents."
                    )
                    reduce_payload = {
                        "contents": [{"role": "user", "parts": [{"text": reduce_prompt}]}],
                        "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium"),
                        "systemInstruction": {"parts": [{"text": SYS_EXPLORER_PROBE}]},
                    }
                    red_clean, red_thought, red_model, red_reason, red_err = await self._call_apollo_with_heartbeat(
                        payload=reduce_payload,
                        uid=uid,
                        metadata=__metadata__,
                        events=events,
                        heartbeats=[f"🤖 Consolidation Map-Reduce des {len(segment_outputs)} segments de {safe_name}..."],
                    )
                    if not red_err and red_clean:
                        final_text = red_clean
                        if red_thought:
                            thoughts_acc.append(f"[Consolidation] {red_thought}")
                        if red_model:
                            last_model_used = red_model
                        if red_reason:
                            last_reason = red_reason
                    else:
                        final_text = compiled_notes

            done_msg = "✅ Distillation sensorielle terminée." if mode == "sensory" else "✅ Analyse terminée."
            await events.status(done_msg, True)
            combined_thought = "\n\n".join(thoughts_acc) if thoughts_acc else None

            return wrap_cascade_output(
                text=final_text,
                model_requested="MODEL_APOLLO",
                model_used=last_model_used,
                status={"status": "success", "segments": len(segment_outputs)},
                echo_tool_multiparts=[{"type": "thought", "content": combined_thought}] if combined_thought else [],
                reason=last_reason,
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__,
            )
        except Exception as e:
            prefix_err = "❌ Erreur" if mode == "sensory" else "❌ Erreur Sonde"
            return wrap_tool_output(
                text=f"{prefix_err} : {str(e)}",
                status={"status": "error"},
                user_id=uid,
                chat_id=cid,
                metadata=__metadata__,
            )

    async def semantic_probe(
        self,
        file_id: str,
        query: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Extrait de manière chirurgicale les données, réponses ou concepts ciblés (query) d'un document ou média volumineux (PDF, texte, image, audio, vidéo) sans saturer le contexte."""
        return await self._execute_multimodal_analysis(
            file_id=file_id,
            user_prompt=query,
            mode="probe",
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

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
        user_prompt = instruction.strip() if instruction and instruction.strip() else USR_EXPLORE_SENSORY
        return await self._execute_multimodal_analysis(
            file_id=file_id,
            user_prompt=user_prompt,
            mode="sensory",
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

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
