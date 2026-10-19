# -*- coding: utf-8 -*-
"""
title: Résume et Transfert vers un nouveau chat
author: ECHO Framework
version: 5.227.1.1
description: Migre le contexte de travail saturé vers une nouvelle conversation optimisée (clonage Workspace).
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMSAxNnYuNWExLjUgMS41IDAgMCAxLTEuNSAxLjVoLTZMMTIgMjBsLTIuNS0yLjVoLTZBMS41IDEuNSAwIDAgMSAyIDE2LjVWNGExLjUgMS41IDAgMCAxIDEuNS0xLjVoMTVBMS41IDEuNSAwIDAgMSAyMCA0djciLz48cGF0aCBkPSJtMTggMjIgMy0zLTMtMyIvPjxwb2x5bGluZSBwb2ludHM9IjIxIDE5IDEzIDE5Ii8+PC9zdmc+
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.227.1.1: Réintroduction et modernisation de l'action de migration de session (extraction complète de branche, backup SQLite atomique via echo_session_clone, pagination Qdrant COLLECTION_SESSION_RAG et timeouts RPC).
# 1.12: Injection de la requête racine (RAG macro-objectif) et ajout de la routine de reprise obligatoire (Onboarding cognitif).
# 1.11: Renommage de l'action et augmentation de l'historique de distillation à 80 messages.
# 1.9: Correction extraction des messages (compatibilité OWUI v0.3+), nettoyage de la clé redondante, cohérence ID HUD.
# 1.8: Remplacement des regex de purge par une regex globale sur <artifact> pour le nettoyage OWUI.

import asyncio
import inspect
import logging
import os
import re
import shutil
import sys
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple
import orjson as std_json
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_constants import OWUI_ORCHESTRATOR_MODEL
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


class Action:
    class Valves(BaseModel):
        priority: int = Field(default=20, description="Priorité d'affichage")

    def __init__(self):
        self.valves = self.Valves()

    @staticmethod
    async def _load_chat_and_branch(
        chat_id: str, fallback_messages: List[Dict]
    ) -> Tuple[Any, List[Dict]]:
        """Charge l'objet ChatModel et reconstruit la branche active complète jusqu'à history.currentId."""
        if not Chats or not chat_id:
            return None, fallback_messages
        try:
            chat_obj = await asyncio.to_thread(Chats.get_chat_by_id, chat_id)
            if asyncio.iscoroutine(chat_obj):
                chat_obj = await chat_obj
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
            current_id = history.get("currentId")

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
        """Extrait et nettoie le texte d'un message (après pré-troncature pour protéger la boucle d'événements)."""
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

        # Pré-troncature avant regex pour éviter tout blocage CPU sur des payloads massifs
        clipped = raw_text[:max_raw_scan]
        cleaned = _AEC_TAG_RE.sub("", clipped)
        cleaned = _REQ_TAG_RE.sub("", cleaned).strip()

        # Ajout synthétique des sorties d'outils stockées dans m["output"] (format natif OWUI)
        outputs = m.get("output")
        if isinstance(outputs, list):
            tool_snippets = []
            for item in outputs:
                if isinstance(item, dict) and item.get("type") == "function_call_output":
                    out_val = item.get("output")
                    if isinstance(out_val, str) and out_val.strip():
                        tool_snippets.append(out_val.strip()[:1500])
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

    @staticmethod
    def _insert_new_chat_sync(
        user_id: str,
        pre_generated_id: str,
        new_chat_payload: dict,
        folder_id: Optional[str] = None,
    ) -> Tuple[Optional[str], Any]:
        """Insère la nouvelle conversation dans webui.db en s'adaptant à la signature de Chats.insert_new_chat."""
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
            res = Chats.insert_new_chat(pre_generated_id, user_id, form)
        else:
            res = Chats.insert_new_chat(user_id, form)

        return pre_generated_id, res

    async def action(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        **kwargs,
    ) -> Optional[dict]:
        events = EchoEvents(__event_emitter__, __event_call__)

        old_chat_id = body.get("chat_id") or (__metadata__ or {}).get("chat_id")
        user_id = (__user__ or {}).get("id")

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
                __metadata__=__metadata__ or {"chat_id": old_chat_id},
                is_json=False,
                max_tokens=2048,
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
                __metadata__=__metadata__ or {"chat_id": old_chat_id},
                is_json=False,
                max_tokens=4096,
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

        old_models = None
        if old_chat and hasattr(old_chat, "chat") and isinstance(old_chat.chat, dict):
            old_models = old_chat.chat.get("models")
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

        message_id = str(uuid.uuid4())
        now_sec = int(time.time())
        assistant_msg = {
            "id": message_id,
            "parentId": None,
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
            "timestamp": now_sec * 1000,
            "history": {
                "currentId": message_id,
                "messages": {
                    message_id: assistant_msg,
                },
            },
            "messages": [assistant_msg],
        }

        pre_generated_id = str(uuid.uuid4())
        try:
            _, created_chat = await asyncio.to_thread(
                self._insert_new_chat_sync,
                user_id,
                pre_generated_id,
                new_chat_payload,
                old_folder_id,
            )
            if asyncio.iscoroutine(created_chat):
                created_chat = await created_chat
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

        # 4. Duplication atomique du conteneur de session ECHO (Vault, Codex Git, SQLite)
        await update_hud(75, "📁 Duplication de l'Espace Personnel et de la Base SQLite...")
        clone_lock = get_chat_clone_lock(new_chat_id)
        try:
            async with clone_lock:
                await asyncio.to_thread(
                    sync_resumed_session, user_id, old_chat_id, new_chat_id
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
                    await asyncio.to_thread(
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

        # 5. Clonage paginé de la mémoire vectorielle de session (COLLECTION_SESSION_RAG uniquement)
        await update_hud(90, "🧠 Clonage de la Mémoire Vectorielle de Session...")
        try:
            await duplicate_session_rag(user_id, old_chat_id, new_chat_id)
        except Exception as e:
            logger.warning(
                f"[ECHO-RESUME-ACTION] Avertissement clonage Qdrant SESSION_RAG : {e}"
            )

        # 6. Téléportation vers la nouvelle conversation
        await update_hud(100, "✅ Téléportation vers la nouvelle session...")
        await asyncio.sleep(0.8)
        if __event_call__:
            cleanup_js = (
                "const h = document.getElementById('echo-migration-hud'); "
                "if(h) h.remove(); "
                f"window.location.href = '/c/{new_chat_id}';"
            )
            await events.call_execute(cleanup_js, timeout=15.0)

        return None
