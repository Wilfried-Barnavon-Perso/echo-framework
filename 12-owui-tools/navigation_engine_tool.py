"""
title: ECHO Navigation Engine
author: Wilfried BARNAVON & ECHO Team
version: 6.6.30.1
description: Composant système interne : ECHO Navigation Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.6.30.1: Migration de delegate_web_browsing sur BrowserToolDispatcher + EchoSubagentController (support sub_sid, tranches supervisées) et suppression de close_web_thread.
# 6.6.5.1: Isolation des captures dans browserviews/ (resource_type='browserview', statut transient/retained), GC automatique, ouverture unique d'onglet et ajout de retain_browser_frame.
# 6.6.2.2: Migration de _deploy_navigation_monitor et stream_proxy vers get_window_manager() (open_tab/broadcast_browser_frame) et ajout de open_browser_ui.
# 6.6.2.1: Suivi temps réel du sous-agent navigator (mark_subagent_running, save_subagent_meta, update_subagent_meta_fields, update_subagent_status).
# 5.238.1.1: Ajout de MODEL_ATHENA dans target_model_key de delegate_web_browsing.

import os
import time
import asyncio
import sys
import uuid
import urllib.parse
import pybase64 as base64
from pydantic import BaseModel, Field
from typing import Optional, Literal, Any

sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_core import (
    echo_tool,
    wrap_tool_output,
    clamp_model,
)
from echo_state_manager import EchoStateManager
from echo_paths import generate_echo_file_id, get_echo_session_path, sanitize_echo_id
from echo_gemini_client import EchoGeminiClient
from echo_prompts import SYS_NAV_BROWSER
from echo_window_manager import get_window_manager
from echo_browser_lib import (
    EchoBrowserLib,
    req_to_browser,
    cleanup_expired_browserviews,
)
from echo_constants import (
    BROWSER_VIEWS_DIR_NAME,
    BROWSER_VIEW_STATUS,
    FILE_INGESTION_STATUS,
    PRUNE_CONTENT_THRESHOLD,
    DEFAULT_VISION_GRID_STEP,
    ECHO_SUBAGENT_CHECKPOINT_CALLS,
    ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
    ECHO_SUBAGENT_CONTEXT,
)
from echo_subagent_hub import (
    BrowserToolDispatcher,
    EchoSubagentController,
    validate_or_create_sub_sid,
)

async def _verify_engine_status(
    timeout: int,
    session_id: str,
    user_id: str,
    u_valves: Any,
    events: EchoEvents,
    chat_id: Optional[str] = None
) -> bool:
    res = await req_to_browser(timeout, "/action", {"session_id": session_id, "action": "ping"}, user_id)
    if res.get("message") == "RESTART_REQUIRED" or res.get("error_type") == "SESSION_NOT_FOUND":
        await events.status("🌐 Moteur de navigation initialisation...", done=False)
        start_res = await req_to_browser(timeout, "/start_session", {
            "session_id": session_id,
            "chat_id": chat_id or session_id,
            "idle_timeout": 900,
            "mode": getattr(u_valves, 'BROWSER_MODE', 'desktop')
        }, user_id)
        return start_res.get("status") == "success"
    return True

async def _deploy_navigation_monitor(
    res_view: dict,
    chat_id: str,
    uid: str,
    u_valves: Any,
    events: EchoEvents,
    browser_session_id: Optional[str] = None,
    open_tab_on_deploy: bool = False,
):
    b64 = res_view.get("screenshot_b64", "")
    eff_session_id = browser_session_id or chat_id
    file_id = ""
    filename = ""
    ts_now = int(time.time())

    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.jpg"
            state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
            vault_path = get_echo_session_path(uid, chat_id, BROWSER_VIEWS_DIR_NAME)
            os.makedirs(vault_path, exist_ok=True)
            filepath = os.path.join(vault_path, filename)

            def _write_img(b64_str, path):
                img_data = base64.b64decode(b64_str)
                with open(path, "wb") as f:
                    f.write(img_data)

            await asyncio.to_thread(_write_img, b64, filepath)

            state_manager.save_resource(
                id=file_id,
                name=filename,
                resource_type="browserview",
                status=BROWSER_VIEW_STATUS["TRANSIENT"],
                mime="image/jpeg",
                storage_path=f"{BROWSER_VIEWS_DIR_NAME}/{filename}",
                metadata={
                    "kind": "browser_frame",
                    "session_id": eff_session_id,
                    "url": res_view.get("url", ""),
                    "action": res_view.get("action", ""),
                    "timestamp": ts_now,
                    "retained": False,
                },
            )
        except Exception as e:
            import logging
            logging.error(f"Echo navigation_engine_tool write error: {e}")
            file_id = ""

    if not getattr(u_valves, 'SHOW_BROWSER_HUD', True):
        return

    wm = get_window_manager()
    if open_tab_on_deploy and file_id:
        await wm.open_tab(
            uid,
            chat_id,
            "echo-browser",
            {"sessionId": eff_session_id, "frameId": file_id, "auto": True},
        )
    await wm.broadcast_browser_frame(
        uid,
        chat_id,
        {
            "session_id": eff_session_id,
            "file_id": file_id,
            "filename": filename,
            "url": res_view.get("url", ""),
            "action": res_view.get("action", ""),
            "timestamp": ts_now,
            "image_url": (
                f"/echo/v1/cognition/{sanitize_echo_id(chat_id)}"
                f"/browser-frames/{file_id}/image"
                if file_id
                else ""
            ),
            "b64": res_view.get("webp_b64") or b64,
        },
    )

class Tools:
    class Valves(BaseModel):
        HTTP_TIMEOUT: int = Field(default=120, description="Timeout global (sec).")

    class UserValves(BaseModel):
        BROWSER_MODE: Literal["mobile", "desktop"] = Field(default="desktop", description="Mode de navigation")
        SHOW_BROWSER_HUD: bool = Field(default=True, description="Afficher le moniteur de navigation (HUD)")
        USE_MULTIMODAL_VISION: bool = Field(default=True, description="Fournir les captures d'écran à l'agent")
        VISION_GRID_STEP: int = Field(default=48, description="Pas de la grille de vision en pixels. La grille sera bicolore (Magenta/Cyan) avec des crénelures au quart du pas.")
        PRUNE_CONTENT_THRESHOLD: int = Field(default=1000, description="Seuil d'élagage (en caractères) des contenus lourds (A11y, HTML) obsolètes.")

    def __init__(self):
        self.valves = self.Valves()

    @echo_tool
    async def delegate_web_browsing(
        self,
        task_objective: str,
        start_url: Optional[str] = None,
        sub_sid: Optional[str] = None,
        target_model_key: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_APOLLO",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_call__: Any = None,
        __event_emitter__: Any = None,
    ) -> dict:
        """Délègue une navigation web interactive complexe (clics, formulaires, SPA) au Navigateur Autonome par tranches supervisées (reprise possible via sub_sid)."""
        sub_context = ECHO_SUBAGENT_CONTEXT.get() or {}
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        uid = (__user__ or {}).get("id", "anonymous")
        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=chat_id)
        parent_sub_sid = sub_context.get("sub_sid")
        sid = validate_or_create_sub_sid(sub_sid=sub_sid, prefix="thread_web")
        browser_session_id = parent_sub_sid if parent_sub_sid else sid

        u_valves = (__user__ or {}).get("valves", self.UserValves())
        state = EchoStateManager(user_id=uid, chat_id=chat_id)
        existing_history = state.get_thread_history(sid)
        is_new_session = len(existing_history) == 0

        async def _analyze_cb(url: str, query: str) -> Any:
            return await self.analyze_web_page(url, query, __user__, __metadata__, __event_call__, __event_emitter__)

        async def _archive_cb(url: str) -> Any:
            return await self.archive_web_page(url, __user__, __metadata__, __event_call__, __event_emitter__)

        dispatcher = BrowserToolDispatcher(
            sid=sid,
            browser_session_id=browser_session_id,
            chat_id=chat_id,
            user_id=uid,
            events=events,
            http_timeout=self.valves.HTTP_TIMEOUT,
            browser_mode=getattr(u_valves, "BROWSER_MODE", "desktop"),
            show_browser_hud=getattr(u_valves, "SHOW_BROWSER_HUD", True),
            use_multimodal_vision=getattr(u_valves, "USE_MULTIMODAL_VISION", True),
            vision_grid_step=getattr(u_valves, "VISION_GRID_STEP", DEFAULT_VISION_GRID_STEP),
            prune_threshold=getattr(u_valves, "PRUNE_CONTENT_THRESHOLD", PRUNE_CONTENT_THRESHOLD),
            analyze_page_cb=_analyze_cb,
            archive_page_cb=_archive_cb,
        )

        await events.status(f"📡 Agent Navigateur [{browser_session_id[:12]}]: Prise de contrôle...")
        res_view = await dispatcher.ensure_engine_ready(
            start_url=start_url, open_tab=is_new_session
        )
        if res_view is None:
            return wrap_tool_output(
                text="❌ Navigateur indisponible.",
                status={"status": "error", "sid": sid, "sub_sid": sid},
            )

        if is_new_session:
            import orjson as _orjson
            sys_prompt = SYS_NAV_BROWSER.format(task_objective=task_objective)
            state.save_thread_step(sid, chat_id, "navigator", 0, "user", [{"text": sys_prompt}])
            dom_data = res_view.get("metadata", [])
            dom_text = _orjson.dumps(dom_data).decode("utf-8")[:60000]
            vp = res_view.get("viewport")
            vp_text = f"\nDimensions de l'écran (Viewport) : {vp['width']}x{vp['height']} pixels." if vp else ""
            dom_parts = [{"text": f"Voici les éléments interactifs actuels (Carte DOM) :{vp_text}\n{dom_text}"}]
            state.save_thread_step(sid, chat_id, "navigator", 1, "user", dom_parts)
            effective_task = ""
        else:
            effective_task = task_objective

        return await EchoSubagentController.run_or_resume(
            user_id=uid,
            chat_id=chat_id,
            task=effective_task,
            sub_sid=sid,
            target_model_key=target_model_key,
            slice_max_calls=ECHO_SUBAGENT_CHECKPOINT_CALLS,
            slice_timeout_sec=ECHO_SUBAGENT_CHECKPOINT_TIMEOUT_SEC,
            autonomous_slice=False,
            engine="navigator",
            role_name="Navigateur Web",
            custom_dispatcher=dispatcher,
            __user__=__user__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

    @echo_tool
    async def analyze_web_page(self, url: str, query: str, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None) -> dict:
        """Extrait et analyse sémantiquement le contenu d'une URL connue pour répondre à une directive précise."""
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        uid = (__user__ or {}).get("id", "anonymous")
        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=chat_id)
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        sub_sid = sub_ctx.get("sub_sid") if sub_ctx else None
        base_session_id = sub_sid if sub_sid else chat_id
        ephem_session_id = f"{base_session_id}_analyze_{uuid.uuid4().hex[:6]}"
        u_valves = (__user__ or {}).get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves.HTTP_TIMEOUT, ephem_session_id, uid, u_valves, events, chat_id=chat_id):
            return "Erreur critique : Session perdue."

        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, ephem_session_id, uid, chat_id=chat_id)
        try:
            await events.status(f"🌐 Navigation vers {url}...")
            await browser.action_browser_control(command="navigate", value=url)
            
            await events.status("🧠 Extraction sémantique Markdown...")
            res_action = await browser.action_inspect_page(target="read_text")
            markdown_text = res_action.get("content", "")
        finally:
            try:
                await browser.reset_session()
            except Exception:
                pass
        
        vault_dir = get_echo_session_path(uid, chat_id, "files")
        os.makedirs(vault_dir, exist_ok=True)
        tmp_path = os.path.join(vault_dir, f"tmp_stream_{uuid.uuid4().hex[:8]}.txt")
        
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)
            
        await events.status("🧠 Analyse par le sous-agent...")
        
        from echo_constants import get_generation_config
        
        payload = {
            "contents": [{"role": "user", "parts": [
                {"text": f"<instruction>\nLe Modèle doit analyser ce document Web pour accomplir cette tâche : {query}\nLa réponse doit être factuelle, précise et issue du texte.\n</instruction>"},
                {"inlineData": {"mimeType": "text/plain", "data": f"___ECHO_STREAM_FILE___{tmp_path}___"}}
            ]}],
            "systemInstruction": {"parts": [{"text": SYS_NAV_BROWSER}]},
            "generationConfig": get_generation_config("MODEL_APOLLO", override_thinking="medium")
        }
        
        analyse_model = clamp_model("MODEL_APOLLO", __metadata__, user_id=uid)
        data, _, _ = await EchoGeminiClient.call_cascade(
            target_model_key=analyse_model,
            payload=payload,
            user_id=uid, metadata=__metadata__, events=events, timeout=120, include_thoughts=False
        )
        
        if os.path.exists(tmp_path): os.remove(tmp_path)
        
        distillate = "".join(p.get("text", "") for p in data.get("candidates", [])[0]["content"].get("parts", []))
        return f"Résultat de l'analyse pour '{query}' :\n{distillate}"

    @echo_tool
    async def archive_web_page(self, url: str, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None) -> dict:
        """Archive le texte intégral d'une page Web en tâche de fond dans la Mémoire Vectorielle de la session."""
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        uid = (__user__ or {}).get("id", "anonymous")
        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=chat_id)
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        sub_sid = sub_ctx.get("sub_sid") if sub_ctx else None
        base_session_id = sub_sid if sub_sid else chat_id
        ephem_session_id = f"{base_session_id}_archive_{uuid.uuid4().hex[:6]}"
        u_valves = (__user__ or {}).get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves.HTTP_TIMEOUT, ephem_session_id, uid, u_valves, events, chat_id=chat_id):
            return "Erreur critique : Session perdue."

        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, ephem_session_id, uid, chat_id=chat_id)
        try:
            await events.status(f"🌐 Navigation vers {url}...")
            await browser.action_browser_control(command="navigate", value=url)
            
            await events.status("🧠 Extraction sémantique (Markdown)...")
            res_action = await browser.action_inspect_page(target="read_text")
            markdown_text = res_action.get("content", "")
        finally:
            try:
                await browser.reset_session()
            except Exception:
                pass
        
        if not markdown_text:
            return "Erreur : Échec de l'extraction du texte."
            
        domain = urllib.parse.urlparse(url).netloc.replace(".", "_") or "web_page"
        short_uuid = uuid.uuid4().hex[:6]
        file_id = f"ARCHIVE_{domain}_{short_uuid}"
        filename = f"{domain}_{short_uuid}_archive.md"
        
        vault_dir = get_echo_session_path(uid, chat_id, "files")
        os.makedirs(vault_dir, exist_ok=True)
        filepath = os.path.join(vault_dir, filename)
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(f"# Archive Web : {url}\n\n{markdown_text}")
            
        state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
        state_manager.save_resource(
            id=file_id, name=filename, resource_type='codex',
            status=FILE_INGESTION_STATUS['PENDING_INGESTION'], mime='text/markdown',
            storage_path=filepath
        )
        
        await events.status(f"✅ Archive {filename} sauvegardée.", done=True)
        return f"Succès : La page {url} a été archivée physiquement ({filename}). Le processus d'ingestion vectorielle s'exécutera silencieusement en arrière-plan. Le Modèle peut poursuivre son analyse courante."

    @echo_tool
    async def web_browse_reset(self, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None) -> dict:
        """Réinitialise la session courante du Navigateur Autonome."""
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        sub_sid = sub_ctx.get("sub_sid") if sub_ctx else None
        browser_session_id = sub_sid if sub_sid else chat_id
        uid = (__user__ or {}).get("id", "anonymous")
        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, browser_session_id, uid, chat_id=chat_id)
        res = await browser.reset_session()
        await cleanup_expired_browserviews(uid, chat_id, force_discard_sids={browser_session_id})
        return wrap_tool_output(text="🚀 Navigateur réinitialisé.", status=res)

    @echo_tool
    async def get_browser_frames_history(self, depth: Optional[int] = 10, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None) -> dict:
        """Consulte l'historique des captures d'écran de navigation de la session."""
        import orjson as json
        chat_id = (__metadata__ or {}).get("chat_id")
        uid = __user__.get("id", "anonymous")
        await cleanup_expired_browserviews(uid, chat_id)
        state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
        try:
            bv_resources = state_manager.get_resources(resource_type='browserview') or []
            legacy_media = [
                r for r in (state_manager.get_resources(resource_type='media') or [])
                if r['id'].startswith('U_') and '_C_' in r['id'] and '_T_' in r['id'] and str(r.get('name') or '').endswith('_frame.jpg')
            ]
            seen_ids = {r['id'] for r in bv_resources}
            nav_resources = bv_resources + [r for r in legacy_media if r['id'] not in seen_ids]
            nav_resources.sort(key=lambda r: r.get('created_at', 0), reverse=True)
            nav_resources = nav_resources[:depth]

            history, nouveaux = [], []
            for r in nav_resources:
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get('created_at', 0)))
                st = r.get('status', 'transient')
                history.append({
                    "file_id": r['id'],
                    "date": dt,
                    "frame": r['name'],
                    "status": st,
                    "retained": st == BROWSER_VIEW_STATUS["RETAINED"],
                })
                nouveaux.append({"nom": r['name'], "id": r['id'], "mime": r.get('mime') or 'image/jpeg', "statut": st})

            return wrap_tool_output(
                text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'),
                status={"status": "success"},
                echo_tool_multiparts=nouveaux
            )
        except Exception as e:
            return f"❌ Erreur: {str(e)}"

    @echo_tool
    async def retain_browser_frame(
        self,
        file_id: str,
        retain: bool = True,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
    ) -> dict:
        """Marque une capture de navigation comme conservée durablement ou éphémère."""
        import orjson as json
        chat_id = (__metadata__ or {}).get("chat_id")
        uid = (__user__ or {}).get("id", "anonymous")
        if not chat_id:
            return "❌ Contexte de session introuvable."
        state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
        res = state_manager.get_resource(file_id)
        if not res or (
            res.get("resource_type") != "browserview"
            and not str(res.get("name") or "").endswith("_frame.jpg")
        ):
            return f"❌ Capture navigateur `{file_id}` introuvable."

        raw_meta = res.get("metadata")
        meta = {}
        if isinstance(raw_meta, dict):
            meta = dict(raw_meta)
        elif isinstance(raw_meta, str) and raw_meta:
            try:
                parsed = json.loads(raw_meta)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                meta = {}
        meta["retained"] = bool(retain)
        new_status = BROWSER_VIEW_STATUS["RETAINED"] if retain else BROWSER_VIEW_STATUS["TRANSIENT"]
        state_manager.update_resource_fields(
            file_id,
            status=new_status,
            metadata=json.dumps(meta).decode("utf-8"),
        )
        if not retain:
            await cleanup_expired_browserviews(uid, chat_id)
        return wrap_tool_output(
            text=f"✅ Capture `{file_id}` marquée comme `{new_status}`.",
            status={"status": "success", "file_id": file_id, "retained": bool(retain), "resource_status": new_status},
        )

    @echo_tool
    async def open_browser_ui(
        self,
        frame_index: Optional[int] = None,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> dict:
        """Ouvre l'onglet natif Navigateur Web (et positionne optionnellement sur une capture)."""
        import orjson as json
        chat_id = (__metadata__ or {}).get("chat_id") or "default"
        uid = (__user__ or {}).get("id", "anonymous")
        payload = {"frameIndex": frame_index} if frame_index is not None else {}

        wm = get_window_manager()
        await wm.open_tab(uid, chat_id, "echo-browser", payload)

        events = EchoEvents(__event_emitter__, __event_call__)
        detail_json = json.dumps(
            {"chatId": chat_id, "kind": "echo-browser", "payload": payload}
        ).decode("utf-8")
        await events.emit_execute(
            f"window.dispatchEvent(new CustomEvent('echo:open-tab', {{ detail: {detail_json} }}));"
        )

        return wrap_tool_output(
            text="✅ Onglet Navigateur Web ouvert dans l'interface ECHO.",
            status={"status": "success", "chat_id": chat_id, "payload": payload},
        )
