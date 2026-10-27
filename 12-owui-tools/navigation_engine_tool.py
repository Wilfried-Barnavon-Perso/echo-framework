"""
title: ECHO Navigation Engine
author: Wilfried BARNAVON & ECHO Team
version: 5.231.20.1
description: Composant système interne : ECHO Navigation Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Isolation des sessions Playwright par sub_sid/sid dans delegate_web_browsing et close_web_thread, sessions éphémères parallélisables dans analyze/archive_web_page et nommage anti-écrasement des archives.
# 5.229.0.0: Compression radicale des docstrings (externalisation vers tool_guides).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 11.43: Séparation stricte entre chat_id (persistance SQLite/Vault) et browser_session_id (isolation Playwright par sub_sid) pour empêcher la création de dossiers chats/<sub_sid> orphelins.
# 11.42: Ajustement du thinking level de analyze_web_page à medium.

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
    wrap_tool_output,
    clamp_model,
    estimate_token_size,
    smart_truncate_history
)
from echo_state_manager import EchoStateManager
from echo_paths import generate_echo_file_id, get_echo_session_path
from echo_gemini_client import EchoGeminiClient
from echo_prompts import SYS_NAV_BROWSER
from echo_ui import EchoUI
from echo_browser_lib import EchoBrowserLib, BROWSER_TOOLS_SCHEMA, req_to_browser
from echo_constants import FILE_INGESTION_STATUS, CONTEXT_TRUNCATE_THRESHOLD, ECHO_MAX_CONTEXT_SIZE, PRUNE_CONTENT_THRESHOLD, DEFAULT_VISION_GRID_STEP, ECHO_SUBAGENT_CONTEXT

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
    browser_session_id: Optional[str] = None
):
    b64 = res_view.get("screenshot_b64", "")
    eff_session_id = browser_session_id or chat_id

    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.jpg"
            state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
            vault_path = get_echo_session_path(uid, chat_id, "files")
            os.makedirs(vault_path, exist_ok=True)
            filepath = os.path.join(vault_path, filename)

            def _write_img(b64_str, path):
                img_data = base64.b64decode(b64_str)
                with open(path, "wb") as f:
                    f.write(img_data)

            await asyncio.to_thread(_write_img, b64, filepath)

            state_manager.save_resource(
                id=file_id, name=filename, resource_type='media',
                status=FILE_INGESTION_STATUS['INDEXED'], mime='image/jpeg',
                storage_path=filepath
            )
        except Exception as e:
            import logging
            logging.error(f"Echo navigation_engine_tool write error: {e}")

    if not getattr(u_valves, 'SHOW_BROWSER_HUD', True) or not events:
        return

    metadata = res_view.get("metadata", [])

    await EchoUI.safe_deploy(
        events=events,
        monitor_func=EchoUI.monitor_ECHO,
        b64=b64,
        metadata=metadata,
        hud_id=f"nav-{eff_session_id[:8]}",
        state_key=f"nav_state_{eff_session_id}",
        current_url=res_view.get("url", ""),
        webp_b64=res_view.get("webp_b64")
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

    async def delegate_web_browsing(
        self, task_objective: str, start_url: Optional[str] = None, 
        target_model_key: Literal["MODEL_APOLLO", "MODEL_PROMETHEUS"] = "MODEL_APOLLO", 
        max_iterations: int = 30,
        __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None
    ) -> dict:
        """Délègue une navigation web interactive complexe (clics, formulaires, SPA) au Navigateur Autonome."""
        events = EchoEvents(__event_emitter__, __event_call__)
        
        # Récupération de l'identité du sous-agent via contextvars
        sub_context = ECHO_SUBAGENT_CONTEXT.get()
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        uid = (__user__ or {}).get("id", "anonymous")
        events = EchoEvents(__event_emitter__, __event_call__, user_id=uid, chat_id=chat_id)
        sub_sid = sub_context.get("sub_sid")
        sid = f"thread_web_{uuid.uuid4().hex[:8]}"
        
        # Isolation Playwright en RAM via browser_session_id dédié, persistance SQLite/Vault stricte sur chat_id
        browser_session_id = sub_sid if sub_sid else sid
        
        u_valves = (__user__ or {}).get("valves", self.UserValves())
        use_vision = getattr(u_valves, 'USE_MULTIMODAL_VISION', True)

        await events.status(f"📡 Agent Navigateur [{browser_session_id[:8]}]: Prise de contrôle...")
        if not await _verify_engine_status(self.valves.HTTP_TIMEOUT, browser_session_id, uid, u_valves, events, chat_id=chat_id):
            return wrap_tool_output(text="❌ Navigateur indisponible.", status={"status": "error"}, user_id=(__user__ or {}).get("id", "system"), chat_id=(__metadata__ or {}).get("chat_id"), metadata=__metadata__)

        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, browser_session_id, uid, vision_grid_step=getattr(u_valves, 'VISION_GRID_STEP', DEFAULT_VISION_GRID_STEP), chat_id=chat_id)
        registry = browser.get_registry()
        
        state = EchoStateManager(user_id=uid, chat_id=chat_id)

        if start_url:
            await events.status(f"🌐 Navigation vers {start_url}...")
            await browser.action_browser_control(command="navigate", value=start_url)
        
        res_view = await browser.highlight()
        await _deploy_navigation_monitor(res_view, chat_id, uid, u_valves, events, browser_session_id=browser_session_id)

        vision_requested = False

        sys_prompt = SYS_NAV_BROWSER.format(task_objective=task_objective)

        history = [{"role": "user", "parts": [{"text": sys_prompt}]}]
        state.save_thread_step(sid, chat_id, "navigator", 0, "user", history[0]["parts"])
        
        def push_state(res_view_dict, fn_name=None):
            dom_data = res_view_dict.get("metadata", [])
            parts = []
            
            if fn_name:
                parts.append({
                    "functionResponse": {
                        "name": fn_name,
                        "response": {
                            "status": "success",
                            "dom_map": dom_data
                        }
                    }
                })
            else:
                import orjson as json
                dom_text = json.dumps(dom_data).decode('utf-8')[:60000]
                viewport = res_view_dict.get("viewport")
                vp_text = f"\nDimensions de l'écran (Viewport) : {viewport['width']}x{viewport['height']} pixels." if viewport else ""
                parts.append({"text": f"Voici les éléments interactifs actuels (Carte DOM) :{vp_text}\n{dom_text}"})
                
            nonlocal vision_requested
            if use_vision and vision_requested and res_view_dict.get("screenshot_b64"):
                parts.append({"text": "Voici la capture d'écran demandée. Analyse-la attentivement pour résoudre ton blocage."})
                parts.append({"inlineData": {"mimeType": "image/jpeg", "data": res_view_dict["screenshot_b64"]}})
                vision_requested = False
                
            history.append({"role": "user", "parts": parts})
            state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "user", parts)
        def prune_heavy_context(history_list, threshold: int, is_zooming: bool = False):
            """Élagage proactif : Filtre universel avec seuil de tolérance élevé et troncature intelligente pour éviter le Token Bloat."""
            heavy_keys = ["dom_map", "a11y_tree", "content", "html", "search_dom"]
            
            for i, msg in enumerate(history_list):
                # Mémoire Glissante (T-1) : On préserve inlineData uniquement pour les 2 derniers messages utilisateur
                if "parts" in msg and not is_zooming:
                    # Tolérance si msg fait partie des 4 derniers éléments de l'historique (2 user, 2 model)
                    if i < len(history_list) - 4:
                        msg["parts"] = [p for p in msg["parts"] if "inlineData" not in p]
                    
                for part in msg.get("parts", []):
                    # Purge ou Troncature dans les retours d'outils
                    if "functionResponse" in part:
                        fr = part["functionResponse"]
                        resp = fr.get("response", {})
                        if isinstance(resp, dict):
                            for key in heavy_keys:
                                if key in resp:
                                    val = resp[key]
                                    if isinstance(val, str) and ("[PURGED" in val or "[TRUNCATED" in val):
                                        continue
                                    if isinstance(val, str) and len(val) > threshold:
                                        resp[key] = val[:threshold] + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"
                                    elif not isinstance(val, str) and len(str(val)) > threshold:
                                        resp[key] = "[PURGED_OBSOLETE_UI_STATE]"
                                
                    # Troncature du DOM initial en texte brut (push_state)
                    if "text" in part:
                        text = part["text"]
                        if text.startswith("Voici les éléments interactifs actuels") and "[TRUNCATED" not in text:
                            if len(text) > threshold:
                                part["text"] = text[:threshold] + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"

        push_state(res_view)

        iterations = 0
        zoom_attempts = 0
        is_zooming = False
        
        # Injection du Proxy Live Asynchrone
        stop_streaming = asyncio.Event()
        async def stream_proxy():
            last_id = 0
            import re
            clean_hud_id = re.sub(r'[^a-zA-Z0-9]', '_', f"nav-{browser_session_id[:8]}")
            while not stop_streaming.is_set():
                try:
                    resp = await req_to_browser(10, "/screencast/latest", {"session_id": browser_session_id, "last_frame_id": last_id}, uid)
                    if resp and resp.get("status") == "success":
                        new_id = resp.get("frame_id", last_id)
                        b64 = resp.get("frame_b64")
                        if b64 and new_id != last_id:
                            last_id = new_id
                            url = resp.get("url", "")
                            import json
                            safe_url = json.dumps(url)
                            update_code = f"if(window.echoWebPlayerUpdate_{clean_hud_id}) window.echoWebPlayerUpdate_{clean_hud_id}('{b64}', {new_id}, {safe_url});"
                            await events.emit_execute(update_code)
                except asyncio.CancelledError:
                    break
                except Exception:
                    pass
                await asyncio.sleep(0.1)
                
        if getattr(u_valves, 'SHOW_BROWSER_HUD', True):
            proxy_task = asyncio.create_task(stream_proxy())
        else:
            proxy_task = None
            
        try:
            last_fn_name = "action"
            while iterations < max_iterations:
                iterations += 1
                await events.status(f"🤖 Agent Navigateur: Analyse en cours (Étape {iterations})...", done=False)
            
                # Défense passive : Troncature de l'historique du navigateur
                current_size = estimate_token_size(history)
                max_tokens = ECHO_MAX_CONTEXT_SIZE
                if current_size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD:
                    try:
                        await events.toast(f"⚠️ Navigateur [{sid}] : saturation contextuelle ({int(current_size/max_tokens*100)}%). Troncature silencieuse active.", "warning")
                    except AttributeError:
                        if __event_emitter__: await events.toast(f"⚠️ Navigateur [{sid}] : saturation contextuelle. Troncature silencieuse active.", level="warning", title="ECHO Browser")
                    while current_size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD and len(history) > 3:
                        removed_size = smart_truncate_history(history, 1)
                        if not removed_size:
                            break
                        current_size -= removed_size
            
                schema_extensions = [
                    {
                        "name": "action_analyze_page",
                        "description": "Analyse intelligemment le contenu textuel complet de la page actuelle en tâche de fond (recherche précise, résumé large bande, etc.) sans saturer ta mémoire de travail.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "L'objectif de l'analyse (ex: 'Cherche la date de naissance', ou 'Résume les grands axes de cet article')."}
                            },
                            "required": ["query"]
                        }
                    },
                    {
                        "name": "action_archive_page",
                        "description": "Archive silencieusement tout le texte de la page actuelle dans la mémoire à long terme (RAG). À n'utiliser que si la page contient un savoir encyclopédique ou technique vital.",
                        "parameters": {"type": "object", "properties": {}}
                    }
                ]

                payload = {
                    "contents": history,
                    "tools": [{"functionDeclarations": BROWSER_TOOLS_SCHEMA + schema_extensions}],
                    "tool_config": {"functionCallingConfig": {"mode": "AUTO"}}
                }

                from echo_constants import get_generation_config
                # Élasticité Cognitive : HIGH pour l'analyse (Vision, DOM, A11y, ou démarrage initial), LOW pour l'exécution motrice (Clic, Scroll)
                thinking_mode = "high" if last_fn_name in ["action", "action_inspect_page"] else "low"
                payload["generationConfig"] = get_generation_config(target_model_key, override_thinking=thinking_mode)
                
                model = clamp_model(target_model_key, __metadata__, user_id=uid)
                data, _, err = await EchoGeminiClient.call_cascade(model, payload, uid, __metadata__, events, timeout=120)

                if err or not data:
                    return wrap_tool_output(text=f"❌ Erreur modèle: {err}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

                candidates = data.get("candidates", [])
                if not candidates or not candidates[0].get("content"): break
            
                # Conservation des parts BRUTES pour préserver thoughtSignature (Gemini 3.x)
                raw_parts = candidates[0]["content"].get("parts", [])
                tools_raw = [p for p in raw_parts if "functionCall" in p]
                text_raw = [p for p in raw_parts if "text" in p and not p.get("thought")]
            
                tool_called = len(tools_raw) > 0

                if tool_called:
                    # 1. Ajout du message modèle avec TOUTES les parts brutes (préservation thoughtSignature STRICTE)
                    history.append({"role": "model", "parts": raw_parts})
                
                    sig = next((p["thoughtSignature"] for p in raw_parts if "thoughtSignature" in p), None)
                    state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "model", raw_parts, sig)
                
                    response_parts = []
                    last_view = None
                    last_fn_name = "action"
                
                    # 2. Exécution des outils
                    try:
                        await browser.start_screencast()
                    except: pass
                
                    for index, part in enumerate(tools_raw):
                        fc = part["functionCall"]
                        fn_name = fc["name"]
                        fn_args = fc.get("args", {})
                        fn_id = fc.get("id") # Gemini 3.x parallèle
                        is_last_tool = (index == len(tools_raw) - 1)
                    
                        if fn_args.get("action_type") in ["download", "save_target"]:
                            file_id = f"DL_{str(uuid.uuid4())[:8]}"
                            fn_args["download_file_id"] = file_id
                        
                        await events.status(f"🖱️ Agent exécute : {fn_name}({fn_args})", done=False)
                    
                        _resp = {"status": "error", "message": "Outil inconnu."}
                        if fn_name in registry:
                            try:
                                action_res = await registry[fn_name](**fn_args)
                            
                                if action_res.get("_trigger_vision"):
                                    vision_requested = True
                                    grid = action_res.get("grid", False)
                                    zb = action_res.get("zoom_box")
                                    
                                    if action_res.get("is_zoom"):
                                        is_zooming = True
                                        zoom_attempts += 1
                                        cx, cy = action_res.get('cx'), action_res.get('cy')
                                        _resp = {"status": "success", "message": f"Zoom appliqué (Essai {zoom_attempts}/3). L'image jointe est rognée sur ta zone. Un VISEUR ROUGE a été dessiné au centre absolu (X={cx}, Y={cy}). Si le point central de ce viseur est STRICTEMENT sur ta cible, utilise action_interact_dom(x={cx}, y={cy}, action_type='click'). S'il est à côté, NE CLIQUE PAS : effectue un nouveau action_zoom_in plus serré centré sur ta cible."}
                                    elif action_res.get("is_zoom_out"):
                                        is_zooming = False
                                        zoom_attempts = 0
                                        _resp = {"status": "success", "message": "Zoom annulé. Retour à la vue globale."}
                                    else:
                                        _resp = {"status": "success", "message": "Capture d'écran demandée. Elle est jointe à ce message."}
                                        
                                    if grid:
                                        last_view = await browser.vision_capture(grid=True, zoom_box=zb)
                                    else:
                                        last_view = await browser.highlight()
                                        
                                    # On déploie avec highlight() spécifiquement pour le moniteur visuel, car la grille ne possède pas les hitboxes sémantiques.
                                    hud_view = await browser.highlight() if grid else last_view
                                    if is_last_tool:
                                        try:
                                            sc_res = await browser.stop_screencast()
                                            if sc_res and sc_res.get("webp_b64"):
                                                hud_view["webp_b64"] = sc_res["webp_b64"]
                                        except: pass
                                    
                                    await _deploy_navigation_monitor(hud_view, chat_id, uid, u_valves, events, browser_session_id=browser_session_id)
                                    last_fn_name = fn_name
                                    
                                elif action_res.get("status") != "error":
                                    if fn_name in ["action_interact_dom", "action_interact_a11y", "action_browser_control"]:
                                        is_zooming = False
                                        zoom_attempts = 0
                                    if is_last_tool:
                                        if "metadata" in action_res and "screenshot_b64" in action_res:
                                            last_view = action_res
                                        else:
                                            last_view = await browser.highlight()
                                            
                                        try:
                                            sc_res = await browser.stop_screencast()
                                            if sc_res and sc_res.get("webp_b64"):
                                                last_view["webp_b64"] = sc_res["webp_b64"]
                                        except: pass
                                    
                                        await _deploy_navigation_monitor(last_view, chat_id, uid, u_valves, events, browser_session_id=browser_session_id)
                                        _resp = {"status": "success", "dom_map": last_view.get("metadata", [])}
                                    
                                        if action_res.get("status") == "downloading":
                                            _resp["message"] = f"📥 Téléchargement asynchrone démarré. Identifiant du fichier attendu : {fn_args.get('download_file_id')}. Résultat en attente. Utilisez 'query_registry' ultérieurement pour vérifier son statut."

                                        # Intégrer les résultats spécifiques de l'action dans la réponse
                                        if "search_result" in action_res:
                                            _resp["search_result"] = action_res["search_result"]
                                        if "content" in action_res:
                                            _resp["content"] = action_res["content"]
                                        if "value" in action_res:
                                            _resp["value"] = action_res["value"]
                                    else:
                                        _resp = {"status": "success", "message": "Action exécutée avec succès."}
                                        if action_res.get("status") == "downloading":
                                            _resp["message"] = f"📥 Téléchargement asynchrone démarré. Identifiant du fichier attendu : {fn_args.get('download_file_id')}. Résultat en attente. Utilisez 'query_registry' ultérieurement pour vérifier son statut."
                                        if "content" in action_res:
                                            _resp["content"] = action_res["content"]
                                        if "value" in action_res:
                                            _resp["value"] = action_res["value"]
                                        
                                    last_fn_name = fn_name
                                else:
                                    _resp = {"status": "error", "error": action_res.get("message")}
                            except Exception as e:
                                _resp = {"status": "error", "error": str(e)}
                        elif fn_name == "action_analyze_page":
                            try:
                                current_url_resp = await browser.action_inspect_page(target="url")
                                raw_res = await self.analyze_web_page(current_url_resp.get("url", ""), fn_args.get("query", ""), __user__, __metadata__, __event_call__, __event_emitter__)
                                _resp = {"status": "success", "result": raw_res}
                            except Exception as e:
                                _resp = {"status": "error", "error": str(e)}
                        elif fn_name == "action_archive_page":
                            try:
                                current_url_resp = await browser.action_inspect_page(target="url")
                                raw_res = await self.archive_web_page(current_url_resp.get("url", ""), __user__, __metadata__, __event_call__, __event_emitter__)
                                _resp = {"status": "success", "result": raw_res}
                            except Exception as e:
                                _resp = {"status": "error", "error": str(e)}
                            
                        # Construction stricte du functionResponse avec l'ID natif
                        _fr = {"name": fn_name, "response": _resp}
                        if fn_id: _fr["id"] = fn_id
                        response_parts.append({"functionResponse": _fr})
                
                    # 3. Ajout du message utilisateur contenant toutes les réponses
                    # Injection de la vision base64 dans la functionResponse correspondante si elle a été demandée
                    if last_view and use_vision and vision_requested and last_view.get("screenshot_b64"):
                        # On attache la capture d'écran directement dans les 'parts' de la dernière functionResponse
                        # Règle Gemini 3.x : Multimodal function calling
                        # On trouve la functionResponse qui a déclenché la vision (last_fn_name) ou la dernière.
                        target_fr = None
                        for rp in reversed(response_parts):
                            if "functionResponse" in rp and rp["functionResponse"].get("name") == last_fn_name:
                                target_fr = rp["functionResponse"]
                                break
                        if not target_fr and response_parts:
                            target_fr = response_parts[-1]["functionResponse"]
                        
                        if target_fr:
                            target_fr["response"]["message"] = "Voici la capture d'écran demandée. Analyse-la attentivement."
                            response_parts.append(
                                {"inlineData": {"mimeType": "image/png", "data": last_view["screenshot_b64"]}}
                            )
                        vision_requested = False
                    
                    # Détection de saturation et trigger du Graceful Shutdown (40% max)
                    if estimate_token_size(history) > ECHO_MAX_CONTEXT_SIZE * 0.40:
                        response_parts.append({"text": "<system_alert>SATURATION DU CONTEXTE ATTEINTE. Appliquez la Règle 10.</system_alert>"})
                    
                    # Élagage des vieux contextes lourds (Seuil de tolérance généreux via constante centrale)
                    prune_heavy_context(history, getattr(u_valves, 'PRUNE_CONTENT_THRESHOLD', PRUNE_CONTENT_THRESHOLD), is_zooming)
                
                    history.append({"role": "user", "parts": response_parts})
                    state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "user", response_parts)
                
                else:
                    # Synthèse finale ou Auto-Relaunch
                    text_out = "".join(p.get("text", "") for p in text_raw)
                
                    history.append({"role": "model", "parts": raw_parts})
                    sig = next((p["thoughtSignature"] for p in raw_parts if "thoughtSignature" in p), None)
                    state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "model", raw_parts, sig)
                    
                    if "[SATURATION_CONTEXTE]" in text_out:
                        await events.status("🔄 Saturation mémoire : Auto-synthèse et Relance interne...", done=False)
                        synthesis = text_out.replace("[SATURATION_CONTEXTE]", "").strip()
                        
                        # Auto-Relaunch : Réinitialisation de la mémoire avec injection de la synthèse
                        history = [{"role": "user", "parts": [{"text": sys_prompt}]}]
                        history.append({
                            "role": "user", 
                            "parts": [{"text": f"Reprise de session interne.\nObjectif initial :\n{task_objective}\n\nSynthèse des recherches précédentes :\n{synthesis}\n\nContinuez la mission sans répéter les mêmes actions."}]
                        })
                        if last_view:
                            # push_state formate et ajoute automatiquement le DOM (et l'image) dans 'history'
                            push_state(last_view)
                        state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "user", history[1]["parts"])
                        continue  # Relance la boucle while
                
                    await events.status(f"✅ Mission terminée en {iterations} étapes.", done=True)
                    return wrap_tool_output(text=f"🤖 Synthèse du Navigateur :\n{text_out}", status={"status": "success", "steps": iterations, "sid": sid}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            fallback_msg = "Aucune synthèse (Interrompu en cours d'action)."
            return wrap_tool_output(
                text=(
                    f"ÉCHEC : Nombre maximum d'itérations atteint ({max_iterations}). "
                    "Le Modèle doit analyser la synthèse suivante et relancer l'outil `delegate_web_browsing` pour poursuivre la tâche.\n"
                    f"[SYNTHÈSE_NAVIGATEUR] :\n{text_out if 'text_out' in locals() else fallback_msg}"
                ), 
                status={"status": "too_many_tries"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        finally:
            stop_streaming.set()
            if proxy_task:
                proxy_task.cancel()

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
            return wrap_tool_output(text="Erreur critique : Session perdue.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
        return wrap_tool_output(text=f"Résultat de l'analyse pour '{query}' :\n{distillate}", status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)


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
            return wrap_tool_output(text="Erreur critique : Session perdue.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
            return wrap_tool_output(text="Erreur : Échec de l'extraction du texte.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
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
        return wrap_tool_output(
            text=f"Succès : La page {url} a été archivée physiquement ({filename}). Le processus d'ingestion vectorielle s'exécutera silencieusement en arrière-plan. Le Modèle peut poursuivre son analyse courante.", 
            status={"status": "success"}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def web_browse_reset(self, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None) -> dict:
        """Réinitialise la session courante du Navigateur Autonome."""
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        sub_sid = sub_ctx.get("sub_sid") if sub_ctx else None
        browser_session_id = sub_sid if sub_sid else chat_id
        uid = (__user__ or {}).get("id", "anonymous")
        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, browser_session_id, uid, chat_id=chat_id)
        res = await browser.reset_session()
        return wrap_tool_output(text="🚀 Navigateur réinitialisé.", status=res, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def close_web_thread(self, sub_sid: str, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None, __event_call__: Any = None, __event_emitter__: Any = None) -> dict:
        """Ferme définitivement une session de navigation web (`thread_web_*`) et purge son historique."""
        chat_id = (__metadata__ or {}).get("chat_id", "default_session")
        uid = (__user__ or {}).get("id", "anonymous")
        
        state = EchoStateManager(user_id=uid, chat_id=chat_id)
        threads = state.list_threads(chat_id)
        sids_in_chat = {t["sub_sid"] for t in threads}
        
        if sub_sid not in sids_in_chat or not sub_sid.startswith("thread_web_"):
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat ou de type invalide.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
        state.delete_thread(sub_sid)
        
        # Fermeture physique de la session Playwright du sous-agent ciblé (et non du chat_id parent)
        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, sub_sid, uid, chat_id=chat_id)
        await browser.reset_session()
        
        return wrap_tool_output(
            text=f"✅ Session web `{sub_sid}` fermée et purgée de l'historique.",
            status={"status": "success", "sid": sub_sid}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def get_browser_frames_history(self, depth: Optional[int] = 10, __user__: Optional[dict] = None, __metadata__: Optional[dict] = None) -> dict:
        """Consulte l'historique des captures d'écran de navigation de la session."""
        import orjson as json
        chat_id = (__metadata__ or {}).get("chat_id")
        uid = __user__.get("id", "anonymous")
        state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
        try:
            resources = state_manager.get_resources(resource_type='media')
            # Filtrer uniquement les captures de navigation web (ID pattern U_*_C_*_T_*)
            nav_resources = [r for r in resources if r['id'].startswith('U_') and '_C_' in r['id'] and '_T_' in r['id']]
            nav_resources = nav_resources[:depth]

            history, nouveaux = [],[]
            for r in nav_resources:
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get('created_at', 0)))
                history.append({"file_id": r['id'], "date": dt, "frame": r['name']})
                nouveaux.append({"nom": r['name'], "id": r['id'], "mime": r.get('mime') or 'image/png', "statut": r['status']})

            return wrap_tool_output(text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'), status={"status": "success"}, echo_tool_multiparts=nouveaux, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
