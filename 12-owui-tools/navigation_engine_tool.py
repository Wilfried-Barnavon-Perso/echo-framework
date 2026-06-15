"""
title: ECHO Navigation Engine
author: Wilfried BARNAVON & ECHO Team
version: 11.5
description: 11.5: Fix - Utilisation stricte de raw_parts dans l'historique pour empêcher le rejet 400 de la thoughtSignature par Gemini 3.x.
             11.4: Hotfix - Restauration de la persistance Vault et SQLite (echo_resources) des captures de navigation.
             11.3: Hotfix - Restitution du payload (content/value) pour a11y_tree, read_text et url dans l'orchestrateur.
             11.2: Refonte Full-Stack API à 4 piliers, Mode Lot Modéré, Hiérarchie A11y.
             11.1: Mode hybride Lidar/Vision (ajout des coordonnées x,y en fallback) et renforcement de la règle Vision-On-Demand.
             11.0: Vision-On-Demand (retrait de la vision systématique, ajout de action_request_vision), retrait du hard-limit DOM, intégration du Code de Comportement étendu.
             10.9: Mise à jour de la docstring de delegate_web_browsing pour exiger la transmission du contexte spatio-temporel (date, heure, lieu) dans l'objectif de la mission.
             10.4: Registre Unifié V2 — mark_processed → save_resource,
             requête processed_files → get_resources.
             10.3: Intégration de la vision multimodale (Base64) native et exposition de la carte DOM à l'agent.
             10.2: Ajout close_web_thread et purge automatique de session en fin de mission.
             10.1: Persistance ThoughtSignatures Gemini 3.x — save_thread_step sur chaque tour (model + user) avec extraction de la signature. SID exposé dans le retour.
             10.0: Architecture multi-agentique. Remplacement de l'interaction manuelle par la boucle OODA autonome (delegate_web_browsing). Intégration Native Gemini Tool Calling et vision multimodale (Base64). Déportation de la logique mécanique dans echo_browser_lib.
"""

import os
import time
import sys
import uuid
import urllib.parse
import pybase64 as base64
from pydantic import BaseModel, Field
from typing import Optional, Literal, Dict, Any, List

sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager, generate_echo_file_id, EchoGeminiClient, clamp_model, get_echo_session_path
from echo_ui import EchoUI
from echo_browser_lib import EchoBrowserLib, BROWSER_TOOLS_SCHEMA, req_to_browser
from echo_constants import FILE_INGESTION_STATUS

async def _verify_engine_status(timeout: int, chat_id: str, user_id: str, u_valves: Any, events: EchoEvents) -> bool:
    res = await req_to_browser(timeout, "/action", {"session_id": chat_id, "action": "ping"}, user_id)
    if res.get("message") == "RESTART_REQUIRED" or res.get("error_type") == "SESSION_NOT_FOUND":
        await events.status("🌐 Moteur de navigation initialisation...", done=False)
        start_res = await req_to_browser(timeout, "/start_session", {
            "session_id": chat_id,
            "idle_timeout": 900,
            "mode": getattr(u_valves, 'BROWSER_MODE', 'desktop')
        }, user_id)
        return start_res.get("status") == "success"
    return True

async def _deploy_navigation_monitor(res_view: dict, chat_id: str, uid: str, u_valves: Any, events: EchoEvents):
    b64 = res_view.get("screenshot_b64", "")
    
    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.png"
            state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
            vault_path = get_echo_session_path(uid, chat_id, "files")
            
            img_data = base64.b64decode(b64)
            filepath = os.path.join(vault_path, filename)
            with open(filepath, "wb") as f: 
                f.write(img_data)
            
            state_manager.save_resource(
                id=file_id, name=filename, resource_type='media',
                status=FILE_INGESTION_STATUS['INDEXED'], mime='image/png',
                storage_path=filepath
            )
        except Exception as e:
            pass

    if not getattr(u_valves, 'SHOW_BROWSER_HUD', True) or not events:
        return

    metadata = res_view.get("metadata", [])
    
    await EchoUI.safe_deploy(
        events=events,
        monitor_func=EchoUI.monitor_ECHO,
        b64=b64,
        metadata=metadata,
        hud_id=f"nav-{chat_id[:8]}",
        state_key=f"nav_state_{chat_id}",
        current_url=res_view.get("url", "")
    )

class Tools:
    class Valves(BaseModel):
        HTTP_TIMEOUT: int = Field(default=120, description="Timeout global (sec).")

    class UserValves(BaseModel):
        BROWSER_MODE: Literal["mobile", "desktop"] = Field(default="desktop", description="Mode de navigation")
        SHOW_BROWSER_HUD: bool = Field(default=True, description="Afficher le moniteur de navigation (HUD)")
        USE_MULTIMODAL_VISION: bool = Field(default=True, description="Fournir les captures d'écran à l'agent")
        VISION_GRID_STEP: int = Field(default=100, description="Pas de la grille de vision en pixels (ex: 50, 100).")

    def __init__(self):
        self.valves = self.Valves()

    async def delegate_web_browsing(
        self, task_objective: str, start_url: Optional[str] = None, 
        target_model_key: Literal["MODEL_FLASH", "MODEL_PRO"] = "MODEL_FLASH", 
        max_iterations: int = Field(default=30, description="Nombre max d'itérations. À augmenter pour les tâches longues (ex: 60 questions). Max: 100."),
        __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None
    ) -> dict:
        """Lance l'Agent Navigateur autonome pour accomplir une mission web complexe.
        IMPORTANT : Tu DOIS inclure la date, l'heure et le lieu actuels dans la description de `task_objective` pour que l'agent navigateur ait conscience de son contexte temporel et géographique lors de ses recherches.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        use_vision = getattr(u_valves, 'USE_MULTIMODAL_VISION', True)

        await events.status("📡 Agent Navigateur: Prise de contrôle...")
        if not await _verify_engine_status(self.valves.HTTP_TIMEOUT, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Navigateur indisponible.", status={"status": "error"})

        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, chat_id, uid, vision_grid_step=getattr(u_valves, 'VISION_GRID_STEP', 100))
        registry = browser.get_registry()
        
        sid = f"thread_web_{uuid.uuid4().hex[:8]}"
        state = EchoStateManager(user_id=uid, chat_id=chat_id)

        if start_url:
            await events.status(f"🌐 Navigation vers {start_url}...")
            await browser.action_browser_control(command="navigate", value=start_url)
        
        res_view = await browser.highlight()
        await _deploy_navigation_monitor(res_view, chat_id, uid, u_valves, events)

        vision_requested = False

        sys_prompt = (
            f"Tu es l'Agent Navigateur Autonome d'ECHO.\nMISSION : {task_objective}\n\n"
            f"=== MANUEL D'EXPLOITATION COGNITIVE ===\n"
            f"Règle 1 (Perception Séquentielle) : Tu dois inspecter la page dans cet ordre strict selon ton besoin de compréhension : `action_inspect_page(target='a11y_tree')` (Priorité absolue pour lire) -> `dom_map` -> `vision` -> `read_text`/`read_html`.\n"
            f"Règle 2 (Hiérarchie d'Interaction) : 1) Tente d'abord `action_interact_a11y` sur l'arbre A11y. Pour un élément interactif (button, link, textbox), utilise `method='role'` ET le paramètre `name` pour cibler précisément l'élément. Si l'élément est du texte simple, utilise `method='text'`. 2) Si complexe, utilise l'index de la `dom_map` avec `action_interact_dom`. 3) En dernier recours, utilise les coordonnées x, y issues d'une inspection vision.\n"
            f"Règle 3 (Mode Lot Modéré) : Tu PEUX grouper plusieurs actions non-mutantes dans le même tour pour aller très vite (ex: remplir plusieurs champs). Cependant, tu NE DOIS PAS enchaîner une action si la précédente risque de modifier drastiquement la page (ex: cliquer sur 'Soumettre', ouvrir une modale, changer de page). Si une action est mutante, elle DOIT être la dernière de ton lot.\n"
            f"Règle 4 (Bannières & Pop-ups) : Si un overlay, un bouton 'Accepter les cookies' ou 'Fermer' bloque la navigation, ta priorité absolue est d'utiliser `action_interact_dom(action_type='click')` ou `action_interact_a11y` pour t'en débarrasser.\n"
            f"Règle 5 (Formulaires) : Remplis les champs avec `action_interact_dom(action_type='type')`. Fais un `action_browser_control(command='pause')` si tu attends une liste d'autocomplétion. Si la liste apparaît ensuite, clique dessus. Sinon, valide avec `action_browser_control(command='press_key', value='Enter')`.\n"
            f"Règle 6 (Scroll & Pagination) : Si une information est absente du DOM, scrolle vers le bas via `action_browser_control(command='scroll', value='down')` avant d'abandonner.\n"
            f"Règle 7 (Erreurs & Repli) : Si `action_browser_control(command='press_key')` échoue, cherche et clique sur le bouton de soumission. Si une action ne produit aucun effet, change d'approche.\n"
            f"Règle 8 (Synthèse Obligatoire) : Ta synthèse finale DOIT être une phrase complète. Il est STRICTEMENT INTERDIT de renvoyer uniquement un nombre ou un mot isolé.\n"
        )

        history = [{"role": "user", "parts": [{"text": sys_prompt}]}]
        state.save_thread_step(sid, chat_id, "navigator", 0, "user", history[0]["parts"])
        
        def push_state(res_view_dict, fn_name=None):
            dom_data = res_view_dict.get("metadata", [])
            parts = []
            
            if fn_name:
                # Structure stricte exigée par Gemini pour répondre à un functionCall
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
                dom_text = str(dom_data)[:30000]
                parts.append({"text": f"Voici les éléments interactifs actuels (Carte DOM) :\n{dom_text}"})
                
            nonlocal vision_requested
            if use_vision and vision_requested and res_view_dict.get("screenshot_b64"):
                parts.append({"text": "Voici la capture d'écran demandée. Analyse-la attentivement pour résoudre ton blocage."})
                parts.append({"inlineData": {"mimeType": "image/png", "data": res_view_dict["screenshot_b64"]}})
                vision_requested = False
                
            history.append({"role": "user", "parts": parts})
            state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "user", parts)

        push_state(res_view)

        max_iterations = 15
        iterations = 0
        
        while iterations < max_iterations:
            iterations += 1
            await events.status(f"🤖 Agent Navigateur: Analyse en cours (Étape {iterations})...", done=False)
            
            payload = {
                "contents": history,
                "tools": [{"function_declarations": BROWSER_TOOLS_SCHEMA}],
                "tool_config": {"function_calling_config": {"mode": "AUTO"}}
            }

            model = clamp_model(target_model_key, __metadata__, user_id=uid)
            data, _, err = await EchoGeminiClient.call_cascade(model, payload, uid, __metadata__, events, timeout=120)

            if err or not data:
                return wrap_tool_output(text=f"❌ Erreur modèle: {err}", status={"status": "error"})

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
                for index, part in enumerate(tools_raw):
                    fc = part["functionCall"]
                    fn_name = fc["name"]
                    fn_args = fc.get("args", {})
                    fn_id = fc.get("id") # Gemini 3.x parallèle
                    is_last_tool = (index == len(tools_raw) - 1)
                    
                    await events.status(f"🖱️ Agent exécute : {fn_name}({fn_args})", done=False)
                    
                    _resp = {"status": "error", "message": "Outil inconnu."}
                    if fn_name in registry:
                        try:
                            action_res = await registry[fn_name](**fn_args)
                            
                            if action_res.get("_trigger_vision"):
                                vision_requested = True
                                grid = action_res.get("grid", False)
                                if grid:
                                    last_view = await browser.vision_grid()
                                else:
                                    last_view = await browser.highlight()
                                # On déploie avec highlight() spécifiquement pour le moniteur visuel, car la grille ne possède pas les hitboxes sémantiques.
                                hud_view = await browser.highlight() if grid else last_view
                                await _deploy_navigation_monitor(hud_view, chat_id, uid, u_valves, events)
                                _resp = {"status": "success", "message": "Capture d'écran demandée. Elle est jointe à ce message."}
                                last_fn_name = fn_name
                                
                            elif action_res.get("status") != "error":
                                if is_last_tool:
                                    last_view = await browser.highlight()
                                    await _deploy_navigation_monitor(last_view, chat_id, uid, u_valves, events)
                                    _resp = {"status": "success", "dom_map": last_view.get("metadata", [])}
                                    
                                    # Intégrer les résultats spécifiques de l'action dans la réponse
                                    if "search_result" in action_res:
                                        _resp["search_result"] = action_res["search_result"]
                                    if "content" in action_res:
                                        _resp["content"] = action_res["content"]
                                    if "value" in action_res:
                                        _resp["value"] = action_res["value"]
                                else:
                                    _resp = {"status": "success", "message": "Action exécutée avec succès."}
                                    if "content" in action_res:
                                        _resp["content"] = action_res["content"]
                                    if "value" in action_res:
                                        _resp["value"] = action_res["value"]
                                        
                                last_fn_name = fn_name
                            else:
                                _resp = {"status": "error", "error": action_res.get("message")}
                        except Exception as e:
                            _resp = {"status": "error", "error": str(e)}
                            
                    # Construction stricte du functionResponse avec l'ID natif
                    _fr = {"name": fn_name, "response": _resp}
                    if fn_id: _fr["id"] = fn_id
                    response_parts.append({"functionResponse": _fr})
                
                # 3. Ajout du message utilisateur contenant toutes les réponses
                # Injection de la vision base64 dans les parts si elle a été demandée
                if last_view and use_vision and vision_requested and last_view.get("screenshot_b64"):
                    response_parts.append({"text": "Voici la capture d'écran demandée. Analyse-la attentivement."})
                    response_parts.append({"inlineData": {"mimeType": "image/png", "data": last_view["screenshot_b64"]}})
                    vision_requested = False
                    
                history.append({"role": "user", "parts": response_parts})
                state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "user", response_parts)
                
            else:
                # Synthèse finale
                text_out = "".join(p.get("text", "") for p in text_raw)
                
                history.append({"role": "model", "parts": raw_parts})
                sig = next((p["thoughtSignature"] for p in raw_parts if "thoughtSignature" in p), None)
                state.save_thread_step(sid, chat_id, "navigator", len(history) - 1, "model", raw_parts, sig)
                
                await events.status(f"✅ Mission terminée en {iterations} étapes.", done=True)
                return wrap_tool_output(text=f"🤖 Synthèse du Navigateur :\n{text_out}", status={"status": "success", "steps": iterations, "sid": sid})

        return wrap_tool_output(text="❌ Mission interrompue: nombre maximum d'étapes atteint.", status={"status": "timeout"})

    async def distill_web_page(self, url: str, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """Navigue vers une URL, extrait le contenu source et l'indexe dans le RAG."""
        events = EchoEvents(__event_emitter__, __event_call__)
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        u_valves = __user__.get("valves", self.UserValves())
        
        if not await _verify_engine_status(self.valves.HTTP_TIMEOUT, chat_id, uid, u_valves, events):
            return wrap_tool_output(text="❌ Session perdue.", status={"status": "error"})

        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, chat_id, uid)
        
        await events.status(f"🌐 Navigation vers {url}...")
        await browser.action_browser_control(command="navigate", value=url)
        
        res_action = await req_to_browser(self.valves.HTTP_TIMEOUT, "/action", {"session_id": chat_id, "action": "read_html"}, uid)
        b64_html = res_action.get("content", "")
        
        domain = urllib.parse.urlparse(url).netloc.replace(".", "_")
        source_id = f"{domain}_{uuid.uuid4().hex[:4]}"
        
        await events.status(f"🧠 Distillation de la page → vectorisation locale...")
        try:
            html_text = base64.b64decode(b64_html).decode('utf-8', errors='ignore')
            prompt = f"SOURCE HTML :\n{html_text[:100000]}\n\nINSTRUCTION :\nAnalyse ce code HTML et génère un résumé structuré, sémantique et très détaillé de tout le contenu de la page."
            
            analyse_model = clamp_model("MODEL_FLASH", __metadata__, user_id=uid)
            data, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key=analyse_model,
                payload={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"maxOutputTokens": 8192}},
                user_id=uid, metadata=__metadata__, events=events, timeout=120, include_thoughts=False
            )
            
            if not data: return wrap_tool_output(text="❌ Cascade épuisée.", status={"status": "error"})
            
            candidates = data.get("candidates", [])
            distillate = "".join(p.get("text", "") for p in candidates[0]["content"].get("parts", []) if "text" in p) if candidates else ""
            
            nb_points, err = await EchoGeminiClient.index_text_in_ephemeral_rag(distillate, source_id, uid, chat_id, __user__, __metadata__)
            if nb_points == 0: return wrap_tool_output(text=f"❌ Vectorisation échouée. {err}", status={"status": "error"})

            await events.status(f"✅ Page indexée dans la Mémoire Vectorisée de Session ({nb_points} vecteurs).", done=True)
            return wrap_tool_output(text=f"✅ Page `{url}` indexée ({nb_points} vecteurs).\nSource ID: `{source_id}`\nUtilisez `search_session_context` pour l'interroger.", status={"status": "success"})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur distillation: {str(e)}", status={"status": "error"})

    async def web_browse_reset(self, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """Réinitialise la session du navigateur (Fermeture et Nettoyage)."""
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, chat_id, uid)
        res = await browser.reset_session()
        return wrap_tool_output(text="🚀 Navigateur réinitialisé.", status=res)

    async def close_web_thread(self, sub_sid: str, __user__: dict = {}, __metadata__: dict = {}, __event_call__=None, __event_emitter__=None) -> dict:
        """
        Ferme définitivement une session du navigateur web et purge son historique (irréversible).
        :param sub_sid: L'identifiant de la session web à fermer (ex: thread_web_abc123)
        """
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        
        state = EchoStateManager(user_id=uid, chat_id=chat_id)
        threads = state.list_threads(chat_id)
        sids_in_chat = {t["sub_sid"] for t in threads}
        
        if sub_sid not in sids_in_chat or not sub_sid.startswith("thread_web_"):
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat ou de type invalide.",
                status={"status": "error"}
            )
            
        state.delete_thread(sub_sid)
        
        # Fermeture physique de la session Playwright
        browser = EchoBrowserLib(self.valves.HTTP_TIMEOUT, chat_id, uid)
        await browser.reset_session()
        
        return wrap_tool_output(
            text=f"✅ Session web `{sub_sid}` fermée et purgée de l'historique.",
            status={"status": "success", "sid": sub_sid}
        )

    async def get_browser_frames_history(self, depth: Optional[int] = 10, __user__: dict = {}, __metadata__: dict = {}) -> dict:
        """
        Consulte l'historique des captures d'écran de la session actuelle dans le Vault.
        Utilise cet outil avec tes capacités de vision multimodale pour voir à quoi ressemble la page web, 
        analyser l'interface visuelle ou comprendre comment interagir avec elle.
        """
        import orjson as json
        chat_id = __metadata__.get("chat_id", "default_session")
        uid = __user__.get("id", "anonymous")
        state_manager = EchoStateManager(user_id=uid)
        try:
            conn = state_manager._get_connection()
            cursor = conn.cursor()
            resources = state_manager.get_resources(resource_type='media')
            # Filtrer uniquement les captures de navigation web (ID pattern U_*_C_*_T_*)
            nav_resources = [r for r in resources if r['id'].startswith('U_') and '_C_' in r['id'] and '_T_' in r['id']]
            nav_resources = nav_resources[:depth]

            history, nouveaux = [],[]
            for r in nav_resources:
                dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r.get('created_at', 0)))
                history.append({"file_id": r['id'], "date": dt, "frame": r['name']})
                nouveaux.append({"nom": r['name'], "id": r['id'], "mime": r.get('mime') or 'image/png', "statut": r['status']})

            return wrap_tool_output(text=json.dumps(history, option=json.OPT_INDENT_2).decode('utf-8'), status={"status": "success"}, nouveaux_fichiers=nouveaux)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"})
