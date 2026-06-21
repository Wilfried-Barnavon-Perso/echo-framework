"""
title: Resume in New Chat
author: ECHO Framework
version: 1.2
description: 1.2: Nettoyage tokens (fichiers + balises proprioceptives) pour distillation optimisée.
             1.1: Migration complète du contexte saturé vers une nouvelle session distillée.
icon_url: data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0IiBmaWxsPSJub25lIiBzdHJva2U9ImN1cnJlbnRDb2xvciIgc3Ryb2tlLXdpZHRoPSIyIiBzdHJva2UtbGluZWNhcD0icm91bmQiIHN0cm9rZS1saW5lam9pbj0icm91bmQiPjxwYXRoIGQ9Ik0yMSAxNnYuNWExLjUgMS41IDAgMCAxLTEuNSAxLjVoLTZMMTIgMjBsLTIuNS0yLjVoLTZBMS41IDEuNSAwIDAgMSAyIDE2LjVWNGExLjUgMS41IDAgMCAxIDEuNS0xLjVoMTVBMS41IDEuNSAwIDAgMSAyMCA0djciLz48cGF0aCBkPSJtMTggMjIgMy0zLTMtMyIvPjxwb2x5bGluZSBwb2ludHM9IjIxIDE5IDEzIDE5Ii8+PC9zdmc+
"""

import sys
import os
import shutil
import sqlite3
import uuid
import logging
import asyncio
import httpx
import re
from typing import Optional, Any
from pydantic import BaseModel, Field

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, EchoGeminiClient
from echo_constants import ECHO_USERS_ROOT, ECHO_QDRANT_URL, MODEL_FLASH, COLLECTION_META_ARTIFACTS, COLLECTION_SESSION_RAG

try:
    from open_webui.models.chats import Chats, ChatForm
except ImportError:
    Chats = None
    ChatForm = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Action:
    class Valves(BaseModel):
        priority: int = Field(default=5, description="Priorité d'affichage")

    def __init__(self):
        self.valves = self.Valves()

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
        
        old_chat_id = body.get("chat_id")
        user_id = __user__.get("id") if __user__ else None

        if not old_chat_id or not user_id:
            try:
                await events.toast("❌ Migration impossible : Identifiants manquants.", "error")
            except AttributeError:
                if __event_emitter__:
                    await __event_emitter__({"type": "toast", "data": {"title": "ECHO", "message": "❌ Migration impossible : Identifiants manquants.", "type": "error"}})
            return None

        if not Chats or not ChatForm:
            try:
                await events.toast("❌ Migration impossible : API Open WebUI non disponible.", "error")
            except AttributeError:
                if __event_emitter__:
                    await __event_emitter__({"type": "toast", "data": {"title": "ECHO", "message": "❌ Migration impossible : API Open WebUI non disponible.", "type": "error"}})
            return None

        # 1. Déploiement du HUD
        hud_js = """
        (function() {
          let div = document.getElementById('echo-migration-hud');
          if(div) div.remove();
          div = document.createElement('div');
          div.id = 'echo-migration-hud';
          div.style.cssText = 'position:fixed; top:50%; left:50%; transform:translate(-50%, -50%); z-index:9999; background:rgba(15,23,42,0.95); backdrop-filter:blur(10px); padding:20px; border-radius:12px; color:white; border:1px solid rgba(0, 212, 255, 0.4); text-align:center; min-width:300px; box-shadow: 0 10px 30px rgba(0,0,0,0.8); font-family: sans-serif;';
          div.innerHTML = `
            <h3 style="margin-top:0; color:#00d4ff; font-weight:600; text-transform:uppercase;">🚀 Migration de Session ECHO</h3>
            <div style="width:100%; height:6px; background:rgba(255,255,255,0.1); border-radius:3px; margin:15px 0; overflow:hidden;">
              <div id="migration-progress" style="width:0%; height:100%; background:#00d4ff; transition:width 0.3s; box-shadow: 0 0 10px #00d4ff;"></div>
            </div>
            <div id="migration-step" style="font-size:12px; color:#aaa; font-weight:500;">Initialisation...</div>
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
            await __event_call__({"type": "execute", "data": {"code": hud_js}})
        
        async def update_hud(pct, step):
            if __event_call__:
                safe_step = step.replace("'", "\\'")
                await __event_call__({"type": "execute", "data": {"code": f"window.updateMigration({pct}, '{safe_step}');"}})

        # 2. Distillation du contexte
        await update_hud(10, "🧠 Distillation cognitive en cours...")
        
        # Reconstruire le contexte brut depuis OWUI API
        old_chat = await Chats.get_chat_by_id(old_chat_id)
        messages = old_chat.chat.get("messages", []) if old_chat else body.get("messages", [])
        
        # Conversion du format messages (OpenAI) en texte lisible pour la distillation
        messages_text = ""
        for m in messages[-20:]: # On limite aux 20 derniers messages pour ne pas surcharger la distillation
            role = m.get("role", "user")
            content = m.get("content", "")
            
            if isinstance(content, str):
                # Nettoyage des balises de contexte proprioceptif pour optimiser le budget tokens
                content = re.sub(r'<smart_context>.*?</smart_context>', '', content, flags=re.DOTALL)
                content = re.sub(r'<environnement_contexte>.*?</environnement_contexte>', '', content, flags=re.DOTALL)
                content = re.sub(r'<evenement_systeme>.*?</evenement_systeme>', '', content, flags=re.DOTALL)
                content = content.strip()
                
            # Extraction des fichiers joints
            files = m.get("files", [])
            file_meta = ""
            if files:
                file_names = [f.get("name", "fichier_inconnu") for f in files]
                file_meta = f" [Fichiers : {', '.join(file_names)}]"
                
            if isinstance(content, str):
                messages_text += f"{role.upper()}: {content[:10000]}{file_meta}\n\n"
        
        prompt = (
            "Tu es l'architecte mémoire d'ECHO.\n"
            "Analyse l'historique de session ci-dessous. Résume très précisément l'état actuel de la session, "
            "les objectifs en cours, les plans d'action et le contexte technique acquis.\n"
            "Ce résumé sera le point de départ strict de la NOUVELLE session. Sois exhaustif.\n\n"
            f"--- HISTORIQUE ---\n{messages_text}"
        )
        
        try:
            distilled_res = await EchoGeminiClient.call_distillation(
                prompt=prompt,
                __user__=__user__ or {"id": user_id},
                __metadata__=__metadata__ or {"chat_id": old_chat_id},
                is_json=False,
                max_tokens=8192
            )
            distilled_summary = distilled_res if isinstance(distilled_res, str) else str(distilled_res)
        except Exception as e:
            logger.error(f"[MIGRATION] Erreur distillation: {e}")
            distilled_summary = "Impossible de distiller le contexte complet."

        new_chat_id = str(uuid.uuid4())
        old_title = old_chat.title if old_chat else "Ancienne Session"
        new_title = f"[Suite] {old_title}"

        await update_hud(40, "📝 Instanciation de la nouvelle session...")
        message_id = str(uuid.uuid4())
        old_models = old_chat.chat.get("models", ["pipe_engine"]) if old_chat and hasattr(old_chat, "chat") else ["pipe_engine"]
        
        new_chat_payload = {
            "title": new_title,
            "models": old_models,
            "history": {
                "currentId": message_id,
                "messages": {
                    message_id: {
                        "id": message_id,
                        "parentId": None,
                        "childrenIds": [],
                        "role": "assistant",
                        "content": f"**Session migrée et distillée.**\n\n*Résumé cognitif :*\n{distilled_summary}"
                    }
                },
                "currentId": message_id
            }
        }
        form = ChatForm(chat=new_chat_payload)
        try:
            await Chats.insert_new_chat(new_chat_id, user_id, form)
        except Exception as e:
            logger.error(f"[MIGRATION] Erreur non fatale lors de l'insertion DB: {e}")

        # 3. Clonage du FS Vault
        await update_hud(60, "📁 Duplication de l'Espace Personnel (Vault)...")
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        old_vault_path = os.path.join(ECHO_USERS_ROOT, safe_uid, "chats", old_chat_id)
        new_vault_path = os.path.join(ECHO_USERS_ROOT, safe_uid, "chats", new_chat_id)

        if os.path.exists(old_vault_path):
            try:
                shutil.copytree(old_vault_path, new_vault_path)
                # Renommage récursif
                for root, dirs, files in os.walk(new_vault_path, topdown=False):
                    for name in files:
                        if old_chat_id in name:
                            os.rename(os.path.join(root, name), os.path.join(root, name.replace(old_chat_id, new_chat_id)))
                    for name in dirs:
                        if old_chat_id in name:
                            os.rename(os.path.join(root, name), os.path.join(root, name.replace(old_chat_id, new_chat_id)))
            except Exception as e:
                logger.error(f"[MIGRATION] Erreur FS: {e}")

        # 4. Mutation SQLite
        await update_hud(80, "💾 Mutation des Bases de Données (SQLite)...")
        new_db_path = os.path.join(new_vault_path, "session.db")
        if os.path.exists(new_db_path):
            try:
                conn = sqlite3.connect(new_db_path)
                c = conn.cursor()
                
                c.execute("SELECT name FROM sqlite_master WHERE type='table';")
                existing_tables = [row[0] for row in c.fetchall()]
                
                for table in existing_tables:
                    c.execute(f'PRAGMA table_info("{table}")')
                    col_names = [r[1] for r in c.fetchall()]
                    for col in col_names:
                        try:
                            # Filtre WHERE LIKE pour ne muter que les enregistrements concernés et éviter des corruptions
                            c.execute(f'UPDATE "{table}" SET "{col}" = REPLACE("{col}", ?, ?) WHERE "{col}" LIKE ?', 
                                      (old_chat_id, new_chat_id, f"%{old_chat_id}%"))
                        except Exception as inner_e:
                            logger.warning(f"[MIGRATION] Impossible de muter {table}.{col}: {inner_e}")
                
                if "message_shadows" in existing_tables:
                    c.execute("DELETE FROM message_shadows")
                
                conn.commit()
                conn.close()
            except Exception as e:
                logger.error(f"[MIGRATION] Erreur SQLite: {e}")

        # 5. Duplication Qdrant
        await update_hud(95, "🧠 Clonage des Mémoires Vectorielles...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                for collection in [COLLECTION_META_ARTIFACTS, COLLECTION_SESSION_RAG]:
                    res = await client.post(f"{ECHO_QDRANT_URL}/collections/{collection}/points/scroll", json={
                        "filter": {"must": [{"key": "chat_id", "match": {"value": old_chat_id}}]},
                        "limit": 10000,
                        "with_payload": True,
                        "with_vector": True
                    })
                    if res.status_code == 200:
                        points = res.json().get("result", {}).get("points", [])
                        if points:
                            new_points = []
                            for p in points:
                                payload = p.get("payload", {})
                                payload["chat_id"] = new_chat_id
                                new_points.append({
                                    "id": str(uuid.uuid5(uuid.NAMESPACE_DNS, str(p["id"]) + new_chat_id)),
                                    "payload": payload,
                                    "vector": p.get("vector")
                                })
                            await client.put(f"{ECHO_QDRANT_URL}/collections/{collection}/points", json={"points": new_points})
        except Exception as e:
            logger.error(f"[MIGRATION] Erreur Qdrant: {e}")

        # 6. Téléportation
        await update_hud(100, "✅ Téléportation vers la nouvelle session...")
        await asyncio.sleep(1) # Laisser l'UI s'afficher
        if __event_call__:
            cleanup_js = f"const h = document.getElementById('migration-hud'); if(h) h.remove(); window.location.href = '/c/{new_chat_id}';"
            await __event_call__({"type": "execute", "data": {"code": cleanup_js}})

        return None
