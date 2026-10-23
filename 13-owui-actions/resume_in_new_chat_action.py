"""
title: Résume et Transfert vers un nouveau chat
author: ECHO Framework
version: 5.229.2.1
description: Migre le contexte de travail saturé vers une nouvelle conversation optimisée (clonage Workspace).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxsaW5lIHgxPSI0IiB5MT0iNCIgeDI9IjQiIHkyPSIyMCIvPjxsaW5lIHgxPSI4IiB5MT0iMTIiIHgyPSIxNiIgeTI9IjEyIi8+PHBvbHlsaW5lIHBvaW50cz0iMTIgOCAxNiAxMiAxMiAxNiIvPjxsaW5lIHgxPSIyMCIgeTE9IjQiIHgyPSIyMCIgeTI9IjIwIi8+PC9zdmc+
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.229.2.1: Propagation et héritage direct des tags du chat parent vers la nouvelle conversation migrée.
# 5.228.4.2: Support asynchrone des routines Chats (_call_owui), purge des blocs <details type="reasoning"> avant troncature, ancrage utilisateur racine pour le Session RAG, synchronisation atomique sous clone_lock et redirection window.location.replace.
# 5.228.1.1: Suppression de l'en-tête de codage UTF-8 obsolète pour alignement sur extract_frontmatter OWUI (affichage icône).
# 5.228.0.1: Intégration de l'icône SVG base64 borne à borne (|->|) pour le bouton d'action.
# 5.227.1.1: Réintroduction et modernisation de l'action de migration de session (extraction complète de branche, backup SQLite atomique via echo_session_clone, pagination Qdrant COLLECTION_SESSION_RAG et timeouts RPC).

import asyncio
import inspect
import logging
import os
import re
import shutil
import sys
import types
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
import orjson as std_json
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_constants import OWUI_ORCHESTRATOR_MODEL
from echo_core import split_thought_process
from echo_events import EchoEvents
from echo_gemini_client import EchoGeminiClient
from echo_paths import get_chat_clone_lock, get_echo_session_path
from echo_prompts import USR_ACTION_RESUME_GLOBAL, USR_ACTION_RESUME_RECENT
from echo_session_clone import duplicate_session_rag, sync_resumed_session

try:
    from open_webui.models.chats import ChatForm, Chats
except ImportError:
    Chats = None
    ChatForm = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ECHO-RESUME-ACTION")

_AEC_TAG_RE = re.compile(r"<artifact\b[^>]*>.*?</artifact>", flags=re.DOTALL)
_REQ_TAG_RE = re.compile(r"</?REQUETE_UTILISATEUR>", flags=re.DOTALL)
_REASONING_DETAILS_RE = re.compile(
    r"<details\b[^>]*\btype=[\"']reasoning[\"'][^>]*>.*?</details>",
    flags=re.DOTALL | re.IGNORECASE,
)


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=20, description="Priorité d'affichage")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    async def _call_owui(func, *args, **kwargs) -> Any:
        """Exécute une routine publiée d'Open WebUI en supportant transparentement sync et async."""
        res = func(*args, **kwargs)
        if inspect.isawaitable(res):
            return await res
        return res

    @staticmethod
    async def _load_chat_and_branch(
        chat_id: str, fallback_messages: List[Dict]
    ) -> Tuple[Any, List[Dict]]:
        """Charge l'objet ChatModel et reconstruit la branche active complète jusqu'à history.currentId."""
        if not Chats or not chat_id:
            return None, fallback_messages
        try:
            chat_obj = await Action._call_owui(Chats.get_chat_by_id, chat_id)
            if (
                not chat_obj
                or not hasattr(chat_obj, "chat")
                or not isinstance(chat_obj.chat, dict)
            ):
                return chat_obj, fallback_messages

            chat_dict = chat_obj.chat
            history = (
                chat_dict.get("history")
                if isinstance(chat_dict.get("history"), dict)
                else {}
            )
            messages_map = (
                history.get("messages")
                if isinstance(history.get("messages"), dict)
                else {}
            )
            current_id = getattr(chat_obj, "current_message_id", None) or history.get("currentId")

            if messages_map and current_id and current_id in messages_map:
                chain = []
                visited = set()
                curr = current_id
                while curr and curr in messages_map and curr not in visited:
                    visited.add(curr)
                    msg_node = messages_map[curr]
                    if isinstance(msg_node, dict):
                        chain.append({**msg_node, "id": msg_node.get("id") or curr})
                        curr = msg_node.get("parentId")
                    else:
                        break
                chain.reverse()
                if chain:
                    return chat_obj, chain

            db_msgs = chat_dict.get("messages")
            if isinstance(db_msgs, list) and db_msgs:
                return chat_obj, db_msgs
            return chat_obj, fallback_messages
        except Exception as e:
            logger.warning(
                f"[ECHO-RESUME-ACTION] Fallback Chats.get_chat_by_id échoué : {e}"
            )
            return None, fallback_messages

    @staticmethod
    def _extract_message_text_and_meta(m: Dict, max_raw_scan: int) -> Tuple[str, str]:
        """Extrait et nettoie le texte d'un message (après purge du bloc reasoning et pré-troncature)."""
        content = m.get("content", "")
        if isinstance(content, list):
            parts_txt = []
            for p in content:
                if isinstance(p, dict) and p.get("type") == "text":
                    parts_txt.append(str(p.get("text", "")))
            raw_text = "\n".join(parts_txt)
        elif isinstance(content, str):
            raw_text = content
        else:
            raw_text = str(content or "")

        outputs = m.get("output")
        if not raw_text.strip() and isinstance(outputs, list):
            msg_parts = []
            for item in outputs:
                if isinstance(item, dict) and item.get("type") == "message":
                    for part in item.get("content") or []:
                        if isinstance(part, dict) and part.get("text") is not None:
                            msg_parts.append(str(part.get("text")))
            if msg_parts:
                raw_text = "\n".join(msg_parts)

        # Purge des blocs de pensée (<think>/<thought> et <details type="reasoning">) pour préserver la réponse utile
        raw_text, _ = split_thought_process(raw_text)
        raw_text = _REASONING_DETAILS_RE.sub("", raw_text)
        # Pré-troncature avant regex AEC pour éviter tout blocage CPU sur des payloads massifs
        clipped = raw_text[:max_raw_scan]
        cleaned = _AEC_TAG_RE.sub("", clipped)
        cleaned = _REQ_TAG_RE.sub("", cleaned).strip()

        # Ajout synthétique des sorties d'outils stockées dans m["output"] (format natif OWUI)
        if isinstance(outputs, list):
            tool_snippets = []
            for item in outputs:
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    out_val = item.get("output")
                    if isinstance(out_val, str) and out_val.strip():
                        tool_snippets.append(out_val.strip()[:1500])
                    elif isinstance(out_val, list):
                        parts_str = "".join(
                            str(p.get("text", ""))
                            for p in out_val
                            if isinstance(p, dict) and p.get("type") != "input_image"
                        ).strip()
                        if parts_str:
                            tool_snippets.append(parts_str[:1500])
            if tool_snippets:
                cleaned = (
                    f"{cleaned}\n[Résultats Outils : {' | '.join(tool_snippets)}]"
                    if cleaned
                    else f"[Résultats Outils : {' | '.join(tool_snippets)}]"
                )

        files = m.get("files") or []
        file_names = []
        for f in files:
            if isinstance(f, dict):
                fn = f.get("name") or (f.get("file") or {}).get("name") or "inconnu"
                file_names.append(fn)
        file_meta = f" [Fichiers : {', '.join(file_names)}]" if file_names else ""
        return cleaned, file_meta

    @classmethod
    def _prepare_transcripts(cls, messages: List[Dict]) -> Tuple[str, str]:
        """Construit les transcriptions globale (tronquée à 3000 chars/msg) et récente (80 derniers messages, 10000 chars/msg)."""
        full_chunks = []
        for m in messages:
            role = str(m.get("role", "user")).upper()
            cleaned, file_meta = cls._extract_message_text_and_meta(
                m, max_raw_scan=12000
            )
            if cleaned or file_meta:
                full_chunks.append(f"{role}: {cleaned[:3000]}{file_meta}")

        recent_chunks = []
        for m in messages[-80:]:
            role = str(m.get("role", "user")).upper()
            cleaned, file_meta = cls._extract_message_text_and_meta(
                m, max_raw_scan=25000
            )
            if cleaned or file_meta:
                recent_chunks.append(f"{role}: {cleaned[:10000]}{file_meta}")

        return "\n\n".join(full_chunks), "\n\n".join(recent_chunks)

    @classmethod
    async def _insert_new_chat(
        cls,
        user_id: str,
        pre_generated_id: str,
        new_chat_payload: dict,
        folder_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Any]:
        """Insère la nouvelle conversation via la routine publiée Chats.insert_new_chat."""
        try:
            form_kwargs = {"chat": new_chat_payload}
            if folder_id and hasattr(ChatForm, "model_fields") and "folder_id" in ChatForm.model_fields:
                form_kwargs["folder_id"] = folder_id
            form = ChatForm(**form_kwargs)
        except Exception:
            form = ChatForm(chat=new_chat_payload)

        sig = inspect.signature(Chats.insert_new_chat)
        params = [
            p
            for p in sig.parameters.values()
            if p.name != "self"
            and p.kind
            in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
            )
        ]
        if len(params) >= 3:
            res = await cls._call_owui(Chats.insert_new_chat, pre_generated_id, user_id, form)
        else:
            res = await cls._call_owui(Chats.insert_new_chat, user_id, form)

        return pre_generated_id, res

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        **kwargs,
    ) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)

        old_chat_id = body.get("chat_id")
        user_id = (__user__ or {}).get("id")
        distill_meta = {"chat_id": old_chat_id}

        if not old_chat_id or not user_id:
            await events.toast(
                "❌ Migration impossible : Identifiants manquants.", "error"
            )
            return None

        if not Chats or not ChatForm:
            await events.toast(
                "❌ Migration impossible : API Open WebUI non disponible.", "error"
            )
            return None

        if not await events.confirm(
            "🚀 Migration de Session",
            "Voulez-vous clôturer cette session et migrer le contexte saturé vers une nouvelle conversation (Distillation) ?",
        ):
            return None

        # 1. Déploiement du HUD de progression
        hud_js = """
        (function() {
          let div = document.getElementById('echo-migration-hud');
          if(div) div.remove();
          div = document.createElement('div');
          div.id = 'echo-migration-hud';
          div.style.cssText = 'position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:99999; background:rgba(15,23,42,0.95); backdrop-filter:blur(10px); padding:20px; border-radius:12px; color:white; border:1px solid rgba(0, 212, 255, 0.4); text-align:center; min-width:320px; box-shadow: 0 10px 30px rgba(0,0,0,0.8); font-family: system-ui, sans-serif;';
          div.innerHTML = `
            <h3 style="margin-top:0; color:#00d4ff; font-weight:600; text-transform:uppercase; font-size:14px;">🚀 Migration de Session ECHO</h3>
            <div style="width:100%; height:6px; background:rgba(255,255,255,0.1); border-radius:3px; margin:15px 0; overflow:hidden;">
              <div id="migration-progress" style="width:0%; height:100%; background:#00d4ff; transition:width 0.3s; box-shadow: 0 0 10px #00d4ff;"></div>
            </div>
            <div id="migration-step" style="font-size:12px; color:#cbd5e1; font-weight:500;">Initialisation...</div>
          `;
          document.body.appendChild(div);
          window.updateMigration = function(pct, step) {
              const p = document.getElementById('migration-progress');
              const s = document.getElementById('migration-step');
              if(p) p.style.width = pct + '%';
              if(s) s.innerText = step;
          };
        })();
        """
        if __event_call__:
            await events.call_execute(hud_js, timeout=15.0)

        async def update_hud(pct: int, step: str):
            if __event_call__:
                safe_step = std_json.dumps(step).decode("utf-8")
                await events.call_execute(
                    f"if (window.updateMigration) window.updateMigration({pct}, {safe_step});",
                    timeout=15.0,
                )

        async def remove_hud():
            if __event_call__:
                await events.call_execute(
                    "const h = document.getElementById('echo-migration-hud'); if(h) h.remove();",
                    timeout=15.0,
                )

        # 2. Chargement de la branche complète et préparation des transcriptions hors event-loop
        await update_hud(10, "🧠 Lecture de l'historique complet...")
        fallback_msgs = body.get("messages") or []
        old_chat, messages = await self._load_chat_and_branch(old_chat_id, fallback_msgs)

        if not messages:
            await remove_hud()
            await events.toast(
                "❌ Migration annulée : Aucun message trouvé dans cette session.",
                "error",
            )
            return None

        full_history_text, recent_history_text = await asyncio.to_thread(
            self._prepare_transcripts, messages
        )

        prompt_global = USR_ACTION_RESUME_GLOBAL.format(full_history=full_history_text)
        prompt_recent = USR_ACTION_RESUME_RECENT.format(
            recent_history=recent_history_text
        )

        await update_hud(20, "🧠 Extraction du macro-objectif (Passe 1/2)...")
        distilled_global = ""
        try:
            res_global = await EchoGeminiClient.call_distillation(
                prompt=prompt_global,
                __user__=__user__ or {"id": user_id},
                __metadata__=distill_meta,
                is_json=False,
            )
            if isinstance(res_global, str):
                distilled_global = res_global.strip()
            elif res_global is not None:
                distilled_global = str(res_global).strip()
        except Exception as e:
            logger.error(f"[ECHO-RESUME-ACTION] Erreur distillation globale: {e}")

        await update_hud(40, "🧠 Analyse de l'état actuel (Passe 2/2)...")
        distilled_recent = ""
        try:
            res_recent = await EchoGeminiClient.call_distillation(
                prompt=prompt_recent,
                __user__=__user__ or {"id": user_id},
                __metadata__=distill_meta,
                is_json=False,
            )
            if isinstance(res_recent, str):
                distilled_recent = res_recent.strip()
            elif res_recent is not None:
                distilled_recent = str(res_recent).strip()
        except Exception as e:
            logger.error(f"[ECHO-RESUME-ACTION] Erreur distillation récente: {e}")

        if not distilled_global and not distilled_recent:
            await remove_hud()
            await events.toast(
                "❌ Échec de la distillation cognitive (API indisponible). La session actuelle reste intacte.",
                "error",
            )
            return None

        if not distilled_global:
            distilled_global = "*(Synthèse globale indisponible — voir l'état détaillé ci-dessous)*"
        if not distilled_recent:
            distilled_recent = "*(Synthèse récente indisponible — voir le macro-objectif ci-dessus)*"

        distilled_summary = f"{distilled_global}\n\n{distilled_recent}"

        # 3. Instanciation de la nouvelle conversation dans Open WebUI
        await update_hud(60, "📝 Instanciation de la nouvelle session...")
        old_title = (
            getattr(old_chat, "title", None)
            or (old_chat.chat.get("title") if old_chat and isinstance(getattr(old_chat, "chat", None), dict) else None)
            or "Ancienne Session"
        )
        new_title = f"[Suite] {old_title}"
        old_folder_id = getattr(old_chat, "folder_id", None) if old_chat else None
        old_tags = (
            (old_chat.meta or {}).get("tags", [])
            if old_chat and hasattr(old_chat, "meta") and isinstance(old_chat.meta, dict)
            else []
        )

        old_chat_dict = (
            old_chat.chat
            if (old_chat and hasattr(old_chat, "chat") and isinstance(old_chat.chat, dict))
            else {}
        )
        old_models = old_chat_dict.get("models")
        if not old_models or not isinstance(old_models, list):
            old_models = [OWUI_ORCHESTRATOR_MODEL]

        routine_reprise = (
            "\n\n---\n"
            "**ROUTINE DE REPRISE OBLIGATOIRE**\n"
            "En tant qu'ECHO, je m'engage fermement à exécuter les actions suivantes dès le premier tour de parole.\n"
            "Je DOIS ABSOLUMENT via mes outils et agents :\n"
            "1. Prendre connaissance du profil de l'Utilisateur et de l'environnement (AEC).\n"
            "2. Explorer les ressources de mon registre (`query_registry`).\n"
            "3. Interroger la mémoire inter-session (`search_sessions_context`) pour affiner ma compréhension.\n"
            "4. Reprendre la conversation en continuité stricte avec le résumé cognitif ci-dessus."
        )
        new_content = f"**Session migrée et distillée.**\n\n*Résumé cognitif :*\n{distilled_summary}{routine_reprise}"

        user_msg_id = str(uuid.uuid4())
        assistant_msg_id = str(uuid.uuid4())
        now_sec = int(time.time())
        user_root_msg = {
            "id": user_msg_id,
            "parentId": None,
            "childrenIds": [assistant_msg_id],
            "role": "user",
            "content": f"🔄 [Système ECHO] Migration et distillation du contexte de la session précédente ({old_title}).",
            "timestamp": now_sec - 1,
        }
        assistant_msg = {
            "id": assistant_msg_id,
            "parentId": user_msg_id,
            "childrenIds": [],
            "role": "assistant",
            "content": new_content,
            "model": old_models[0],
            "modelName": "Écho",
            "done": True,
            "timestamp": now_sec,
        }
        new_chat_payload = {
            "title": new_title,
            "models": old_models,
            "params": old_chat_dict.get("params", {}),
            "files": old_chat_dict.get("files", []),
            "timestamp": now_sec * 1000,
            "history": {
                "currentId": assistant_msg_id,
                "messages": {
                    user_msg_id: user_root_msg,
                    assistant_msg_id: assistant_msg,
                },
            },
            "messages": [user_root_msg, assistant_msg],
        }

        pre_generated_id = str(uuid.uuid4())
        try:
            _, created_chat = await self._insert_new_chat(
                user_id,
                pre_generated_id,
                new_chat_payload,
                old_folder_id,
            )
        except Exception as e:
            logger.error(f"[ECHO-RESUME-ACTION] Erreur critique insert_new_chat: {e}")
            await remove_hud()
            await events.toast(
                f"❌ Échec de la création de la nouvelle conversation : {e}", "error"
            )
            return None

        new_chat_id = (
            getattr(created_chat, "id", None)
            or (created_chat.get("id") if isinstance(created_chat, dict) else None)
        )
        if not new_chat_id:
            await remove_hud()
            await events.toast(
                "❌ Échec de la création de la nouvelle conversation (ID nul).",
                "error",
            )
            return None

        if old_tags:
            try:
                user_obj = types.SimpleNamespace(id=user_id)
                await Action._call_owui(Chats.update_chat_tags_by_id, new_chat_id, old_tags, user_obj)
                logger.info(f"[ECHO-RESUME-ACTION] Tags du chat parent propagés à la nouvelle session : {old_tags}")
            except Exception as tag_err:
                logger.warning(f"[ECHO-RESUME-ACTION] Avertissement propagation tags : {tag_err}")

        # 4. Duplication atomique du conteneur de session ECHO et de la mémoire vectorielle sous verrou
        await update_hud(75, "📁 Duplication de l'Espace Personnel et de la Base SQLite...")
        clone_lock = get_chat_clone_lock(new_chat_id)
        try:
            async with clone_lock:
                await asyncio.to_thread(
                    sync_resumed_session,
                    user_id,
                    old_chat_id,
                    new_chat_id,
                    [user_msg_id, assistant_msg_id],
                )
                await update_hud(90, "🧠 Clonage de la Mémoire Vectorielle de Session...")
                try:
                    await duplicate_session_rag(user_id, old_chat_id, new_chat_id)
                except Exception as rag_err:
                    logger.warning(
                        f"[ECHO-RESUME-ACTION] Avertissement clonage Qdrant SESSION_RAG : {rag_err}"
                    )
        except Exception as e:
            logger.error(f"[ECHO-RESUME-ACTION] Erreur duplication conteneur: {e}")
            try:
                new_session_dir = os.path.dirname(
                    get_echo_session_path(user_id, new_chat_id, "db")
                )
                if os.path.exists(new_session_dir):
                    shutil.rmtree(new_session_dir, ignore_errors=True)
                if hasattr(Chats, "delete_chat_by_id_and_user_id"):
                    await self._call_owui(
                        Chats.delete_chat_by_id_and_user_id, new_chat_id, user_id
                    )
            except Exception:
                pass
            await remove_hud()
            await events.toast(
                f"❌ Échec lors de la duplication du conteneur de session : {e}",
                "error",
            )
            return None

        # 6. Téléportation vers la nouvelle conversation
        await update_hud(100, "✅ Téléportation vers la nouvelle session...")
        await asyncio.sleep(0.8)
        if __event_call__:
            cleanup_js = (
                "const h = document.getElementById('echo-migration-hud'); "
                "if(h) h.remove(); "
                f"window.location.replace('/c/{new_chat_id}');"
            )
            await events.call_execute(cleanup_js, timeout=15.0)

        return None
