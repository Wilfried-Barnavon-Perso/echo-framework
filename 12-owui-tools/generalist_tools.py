"""
title: ECHO Generalist Tools
author: Antigravity
version: 1.32
description: Composant système interne : ECHO Generalist Tools.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.32: Fixation déterministe du modèle sur OWUI_ORCHESTRATOR_MODEL ("echo_orchestrator") et suppression de model_id (zéro rétrocompatibilité).
# 1.31: Alignement du cache d'outils sur sys.modules["function_echo_orchestrator"].
# 1.30: Ajout de list_ui_calendar_events, read_ui_calendar_events, update_ui_calendar_event et update_ui_automation.
# 1.29: Ajout de generationConfig explicite avec thinking medium sur delegate_to_tool_consultant.
# 1.28: Fiabilisation de delegate_to_tool_consultant en sous-agent (résolution chat_id depuis __metadata__, filtrage BLACKLIST/Headless et call_cascade).

# ECHO CONFIG NAME : ECHO Generalist Tools

import asyncio
import sys
import json
import inspect
from pydantic import BaseModel, Field
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_core import wrap_tool_output
from echo_events import EchoEvents
from echo_constants import ECHO_MAX_WAIT_TIMER, get_generation_config, OWUI_ORCHESTRATOR_MODEL
from echo_ui import EchoUI
from echo_gemini_client import EchoGeminiClient
from echo_prompts import SYS_ORCHESTRATOR_CONSULTANT

from open_webui.tools.builtin import (
    search_calendar_events as _owui_search,
    create_calendar_event as _owui_create_cal,
    update_calendar_event as _owui_update_cal,
    delete_calendar_event as _owui_delete_cal,
    create_automation as _owui_create_auto,
    update_automation as _owui_update_auto,
    delete_automation as _owui_delete_auto,
    _get_user_tz,
    _ns_to_dt,
)
from open_webui.models.folders import Folders, FolderForm
from open_webui.models.automations import Automations
from open_webui.models.calendar import CalendarEvents
import logging

log = logging.getLogger(__name__)

async def safe_owui_call(func, *args, **kwargs):
    """Proxy sécurisé pour l'exécution et l'encapsulation ECHO des fonctions natives Open WebUI."""
    __user__ = kwargs.get("__user__", {})
    __metadata__ = kwargs.get("__metadata__", {})
    
    sig = inspect.signature(func)
    has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    filtered_kwargs = kwargs if has_var_keyword else {k: v for k, v in kwargs.items() if k in sig.parameters}
    
    try:
        res = await func(*args, **filtered_kwargs)
        status_dict = {"status": "success"}
        text_output = str(res)
        
        if isinstance(res, str):
            try:
                parsed = json.loads(res)
                if isinstance(parsed, dict):
                    if "error" in parsed:
                        status_dict = {"status": "error", "message": parsed["error"]}
                    elif "status" in parsed:
                        status_dict = {"status": str(parsed["status"])}
            except Exception:
                pass
                
        return wrap_tool_output(
            text=text_output, 
            status=status_dict, 
            user_id=__user__.get("id"), 
            chat_id=__metadata__.get("chat_id"), 
            metadata=__metadata__
        )
    except Exception as e:
        return wrap_tool_output(
            text=f"Exception interne de l'outil : {str(e)}", 
            status={"status": "error", "message": str(e)}, 
            user_id=__user__.get("id"), 
            chat_id=__metadata__.get("chat_id"), 
            metadata=__metadata__
        )

class Tools:
    class Valves(BaseModel):
        MAX_TIMER: int = Field(
            default=ECHO_MAX_WAIT_TIMER,
            description="Durée maximale autorisée pour le timer (en secondes)."
        )

    def __init__(self):
        self.valves = self.Valves()

    async def wait_timer(
        self,
        seconds: int,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Met en pause l'exécution. Strictement limité à 1 seule tentative par boucle agentique pour éviter les boucles infinies.
        :param seconds: Durée en secondes (Maximum autorisé: 300).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        # 1. Validation & Clamp de sécurité
        try:
            sec_val = int(seconds)
        except ValueError:
            sec_val = 10
            
        max_t = int(self.valves.MAX_TIMER)
        if sec_val < 1: sec_val = 1
        if sec_val > max_t: sec_val = max_t

        await events.status(f"⏱️ Démarrage d'un timer de {sec_val} secondes...")

        # 2. Injection du HUD Interactif (Front-end)
        hud_id = "echo-wait-timer-hud"
        js_code = f"""
        (function() {{
            const HUD_ID = '{hud_id}';
            let old = document.getElementById(HUD_ID);
            if (old) old.remove();

            let remaining = {sec_val};
            
            const hud = document.createElement('div');
            hud.id = HUD_ID;
            hud.style.cssText = 'position:fixed; z-index:10005; top:80px; right:30px; background-color:#0a0a0a; color:#ef4444; font-family:"Courier New", Courier, monospace; font-weight:bold; font-size:24px; border:2px solid #333; border-radius:10px; padding:10px 20px; box-shadow:0 8px 30px rgba(0,0,0,0.8); display:flex; align-items:center; gap:15px; user-select:none; cursor:move; min-width:120px; justify-content:center;';

            const timeDisplay = document.createElement('span');
            timeDisplay.style.cssText = 'letter-spacing: 2px;';
            timeDisplay.innerText = remaining + "s";
            
            const closeBtn = document.createElement('span');
            closeBtn.innerHTML = '&times;';
            closeBtn.style.cssText = 'color:#555; cursor:pointer; font-size:20px; transition:color 0.2s; position:absolute; top:-5px; right:5px; font-weight:normal; line-height:1;';
            closeBtn.onmouseover = () => closeBtn.style.color = '#ef4444';
            closeBtn.onmouseout = () => closeBtn.style.color = '#555';
            closeBtn.onclick = () => hud.remove();

            hud.appendChild(timeDisplay);
            hud.appendChild(closeBtn);
            document.body.appendChild(hud);

            // Fonctionnalité Drag & Drop
            let isDragging = false, startX, startY, startLeft, startTop;
            hud.onmousedown = (e) => {{
                if (e.target === closeBtn) return;
                isDragging = true;
                startX = e.clientX; startY = e.clientY;
                const rect = hud.getBoundingClientRect();
                startLeft = rect.left; startTop = rect.top;
            }};
            document.addEventListener('mousemove', (e) => {{
                if (!isDragging) return;
                hud.style.right = 'auto'; // Désactiver right lors du drag
                hud.style.left = (startLeft + e.clientX - startX) + 'px';
                hud.style.top = (startTop + e.clientY - startY) + 'px';
            }});
            document.addEventListener('mouseup', () => isDragging = false);

            // Moteur du Timer
            const interval = setInterval(() => {{
                remaining--;
                if (remaining <= 0) {{
                    remaining = 0;
                    timeDisplay.innerText = "0s";
                    clearInterval(interval);
                    // Disparition automatique après 1000ms
                    setTimeout(() => {{
                        let currentHud = document.getElementById(HUD_ID);
                        if (currentHud) currentHud.remove();
                    }}, 1000);
                }} else {{
                    timeDisplay.innerText = remaining + "s";
                }}
            }}, 1000);
        }})();
        """
        await events.emit_execute(js_code)

        # 3. Blocage Backend (Attente réelle)
        # On divise l'attente pour que si OWUI coupe le contexte, ça ne plante pas brutalement
        await asyncio.sleep(sec_val)

        await events.status("⏱️ Timer terminé.", done=True)
        return wrap_tool_output(text=f"Le timer de {sec_val} secondes est terminé avec succès.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def ask_user_input(
        self,
        question: str,
        input_type: str = "text",
        options: list[str] = None,
        timeout_seconds: int = 300,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> dict:
        """
        Interrompt brièvement l'exécution pour afficher une boîte de dialogue à l'utilisateur.
        Permet de demander une information (ex: Clé API) via 'text', ou une approbation (Oui/Non) via 'confirm'.

        :param question: La question ou le message à afficher à l'utilisateur pour lui demander une saisie ou une confirmation.
        :param input_type: Type de demande : 'text' (pour demander de taper un texte) ou 'confirm' (pour un simple choix Oui/Non).
        :param options: Liste optionnelle de suggestions (réponses prédéfinies). Génère des boutons/pilules cliquables dans l'interface que l'utilisateur peut sélectionner directement. L'utilisateur pourra choisir ou taper librement.
        :param timeout_seconds: Délai maximum en secondes avant l'annulation (uniquement pour 'text'). Par défaut 5 minutes.
        """
        if not __user__:
            return wrap_tool_output(text="Erreur : Contexte manquant.", status={"status": "error"})

        from echo_constants import ECHO_SUBAGENT_CONTEXT
        if ECHO_SUBAGENT_CONTEXT.get().get("is_subagent"):
            return wrap_tool_output(
                text="Erreur : L'environnement d'exécution (Headless/Sous-agent) ne permet pas de poser une question interactive à l'Utilisateur.",
                status={"status": "error"},
                user_id=__user__.get("id", "system") if __user__ else "system",
                chat_id=__metadata__.get("chat_id") if __metadata__ else None,
                metadata=__metadata__
            )

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status(f"En attente d'une saisie de l'utilisateur ({timeout_seconds}s)...")

        # Échappement propre pour le code JS
        question_escaped = json.dumps(question)
        options_escaped = json.dumps(options if options else [])

        # Lazy-Loading des définitions JS des Modales ECHO
        modals_injection = EchoUI.get_custom_modals_js()

        if input_type == "confirm":
            js_code = f"""
            {modals_injection}
            return await new Promise((resolve) => {{
                window.echoCustomConfirm({question_escaped}, (result) => resolve(result));
            }});
            """
        else:
            js_code = f"""
            {modals_injection}
            return await new Promise((resolve) => {{
                window.echoCustomPrompt({question_escaped}, {timeout_seconds}, {options_escaped}, (result) => resolve(result));
            }});
            """

        # __event_call__ lance le JS et attend la résolution de la promesse
        user_input = await events.call_execute(js_code)

        if user_input is None or user_input is False:
            await events.status("Opération refusée, annulée ou délai expiré.", done=True)
            return wrap_tool_output(
                text=json.dumps({"status": "cancelled", "message": "L'utilisateur a répondu Non, annulé la saisie ou le délai imparti est expiré.", "user_input": user_input}),
                status={"status": "cancelled"},
                user_id=__user__["id"],
                chat_id=__metadata__.get("chat_id"),
                metadata=__metadata__
            )

        await events.status("Saisie utilisateur reçue.", done=True)
        return wrap_tool_output(
            text=json.dumps({"status": "success", "user_input": user_input}),
            status={"status": "success"},
            user_id=__user__["id"],
            chat_id=__metadata__.get("chat_id"),
            metadata=__metadata__
        )

    async def search_ui_calendar_events(self, query: str = "", start: str = None, end: str = None, limit: int = 10, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT consulter l'agenda de l'Interface Utilisateur pour vérifier ses disponibilités et filtrer par dates ou mots-clés.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".

        :param query: Terme de recherche textuelle optionnel (titre, description, lieu).
        :param start: Date/heure minimale de début pour borner la recherche (optionnel).
        :param end: Date/heure maximale de début pour borner la recherche (optionnel).
        :param limit: Nombre maximal d'évènements à retourner (par défaut 10).
        """
        return await safe_owui_call(_owui_search, query=query, start=start, end=end, count=limit, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def list_ui_calendar_events(self, query: str = "", start: str = None, end: str = None, limit: int = 30, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Permet au Modèle de lister et explorer les évènements de son agenda dans l'Interface Utilisateur.
        Complément indispensable pour inspecter les disponibilités ou identifier les identifiants d'évènements avant modification ou suppression.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".

        :param query: Terme de recherche textuelle optionnel filtrant sur le titre, la description ou le lieu.
        :param start: Date/heure minimale de début pour borner la liste (optionnel).
        :param end: Date/heure maximale de début pour borner la liste (optionnel).
        :param limit: Nombre maximal d'évènements à lister (par défaut 30).
        """
        return await safe_owui_call(_owui_search, query=query, start=start, end=end, count=limit, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def read_ui_calendar_events(self, event_ids: list[str], __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT utiliser cet outil pour inspecter le contenu intégral et les détails techniques
        (titre, description, dates locales, récurrence RRULE, rappels, statut d'annulation, lieu, participants)
        d'un ou plusieurs évènements du calendrier de l'Interface Utilisateur à partir de leurs identifiants.
        Complément indispensable de list_ui_calendar_events pour analyser ou auditer un évènement avant modification ou suppression.

        :param event_ids: Liste des identifiants (IDs au format UUID) des évènements à inspecter (ou chaîne d'identifiants séparés par des virgules).
        """
        user_id = __user__.get("id")
        if not user_id:
            return wrap_tool_output(text=json.dumps({"error": "User context missing"}), status={"status": "error"}, metadata=__metadata__)

        if isinstance(event_ids, str):
            clean_ids = [eid.strip() for eid in event_ids.split(",") if eid.strip()]
        elif isinstance(event_ids, list):
            clean_ids = [str(eid).strip() for eid in event_ids if str(eid).strip()]
        else:
            clean_ids = []

        if not clean_ids:
            return wrap_tool_output(text=json.dumps({"error": "Aucun identifiant d'évènement valide fourni."}), status={"status": "error"}, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        try:
            tz = _get_user_tz(__user__)
            tasks = [CalendarEvents.get_event_by_id(eid) for eid in clean_ids]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            events = []
            not_found = []

            for eid, res in zip(clean_ids, raw_results):
                if isinstance(res, Exception) or res is None or res.user_id != user_id:
                    not_found.append(eid)
                    continue

                alert_minutes = 10
                if res.meta and "alert_minutes" in res.meta:
                    alert_minutes = res.meta["alert_minutes"]

                events.append({
                    "id": res.id,
                    "calendar_id": res.calendar_id,
                    "title": res.title,
                    "description": res.description or "",
                    "start": _ns_to_dt(res.start_at, tz) if res.start_at else None,
                    "end": _ns_to_dt(res.end_at, tz) if res.end_at else None,
                    "all_day": res.all_day,
                    "rrule": res.rrule or "",
                    "location": res.location or "",
                    "color": res.color or "",
                    "reminder_minutes": alert_minutes,
                    "is_cancelled": res.is_cancelled,
                    "attendees": [a.model_dump() if hasattr(a, "model_dump") else a for a in (res.attendees or [])],
                    "created_at": res.created_at,
                    "updated_at": res.updated_at
                })

            payload = {
                "events": events,
                "total_found": len(events)
            }
            if not_found:
                payload["not_found"] = not_found

            return wrap_tool_output(
                text=json.dumps(payload, ensure_ascii=False),
                status={"status": "success"},
                user_id=user_id,
                chat_id=__metadata__.get("chat_id"),
                metadata=__metadata__
            )
        except Exception as e:
            return wrap_tool_output(text=json.dumps({"error": str(e)}), status={"status": "error"}, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def create_ui_calendar_event(self, title: str, description: str = "", start: str = "", end: str = "", location: str = "", __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT créer un évènement dans le calendrier de l'Interface Utilisateur via cette fonction.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".
        """
        return await safe_owui_call(_owui_create_cal, title=title, description=description, start=start, end=end, location=location, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def update_ui_calendar_event(
        self,
        event_id: str,
        title: str = None,
        description: str = None,
        start: str = None,
        end: str = None,
        all_day: bool = None,
        location: str = None,
        is_cancelled: bool = None,
        reminder_minutes: int = None,
        __request__ = None,
        __user__: dict = {},
        __metadata__: dict = {}
    ) -> str:
        """
        Le Modèle DOIT mettre à jour un évènement existant dans le calendrier de l'Interface Utilisateur via cette fonction.
        Seuls les champs explicitement fournis sont modifiés ; les champs omis conservent leur valeur actuelle.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".

        :param event_id: Identifiant unique de l'évènement à modifier (obligatoire).
        :param title: Nouveau titre de l'évènement (optionnel).
        :param description: Nouvelle description ou notes de l'évènement (optionnel).
        :param start: Nouvelle date/heure de début locale (optionnel).
        :param end: Nouvelle date/heure de fin locale (optionnel).
        :param all_day: Indique si l'évènement dure toute la journée (optionnel).
        :param location: Nouveau lieu de l'évènement (optionnel).
        :param is_cancelled: Définir à True pour annuler l'évènement (optionnel).
        :param reminder_minutes: Délai en minutes avant l'évènement pour envoyer une notification de rappel (optionnel). 0 pour notification à l'heure pile, -1 pour désactiver.
        """
        return await safe_owui_call(
            _owui_update_cal,
            event_id=event_id,
            title=title,
            description=description,
            start=start,
            end=end,
            all_day=all_day,
            location=location,
            is_cancelled=is_cancelled,
            reminder_minutes=reminder_minutes,
            __request__=__request__,
            __user__=__user__,
            __metadata__=__metadata__
        )

    async def delete_ui_calendar_event(self, event_id: str, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """Le Modèle DOIT supprimer un évènement du calendrier de l'Interface Utilisateur s'il est devenu obsolète ou erroné."""
        return await safe_owui_call(_owui_delete_cal, event_id=event_id, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def create_ui_automation(self, name: str, prompt: str, rrule: str, target_folder_name: str = "Automations", __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        CRON Cognitif : Le Modèle DOIT programmer ses propres tâches de fond de l'Interface Utilisateur avec cet outil.
        L'action générera un nouveau chat indépendant à chaque déclenchement.
        Le Modèle DOIT préciser le nom du dossier via 'target_folder_name'. S'il n'existe pas, l'outil le créera automatiquement.
        
        RÈGLES STRICTES DE SYNTAXE RRULE :
        L'argument rrule DOIT être une chaîne iCalendar valide. Si 'COUNT' est spécifié, 'DTSTART' est OBLIGATOIRE.
        Le format DTSTART exige la syntaxe YYYYMMDDTHHMMSS.
        Exemples valides :
        - Une seule fois à une date précise : "DTSTART:20261015T140000\nRRULE:FREQ=DAILY;COUNT=1"
        - Tous les jours à 9h : "DTSTART:20261001T090000\nRRULE:FREQ=DAILY"
        - Toutes les heures : "RRULE:FREQ=HOURLY;INTERVAL=1"
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"error": "User context missing"})
            
        folders = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, 'get_folders_by_user_id') else []
        folder_id = None
        for f in folders:
            if f.name == target_folder_name:
                folder_id = f.id
                break
                
        if not folder_id:
            try:
                new_folder = await Folders.insert_new_folder(user_id, FolderForm(name=target_folder_name))
                folder_id = new_folder.id
            except Exception as e:
                log.error(f"ECHO: Failed to create target folder '{target_folder_name}' - {e}")
                folder_id = None
            
        call_metadata = dict(__metadata__ or {})
        call_metadata["model_id"] = OWUI_ORCHESTRATOR_MODEL
        if isinstance(call_metadata.get("model"), dict):
            call_metadata["model"] = dict(call_metadata["model"], id=OWUI_ORCHESTRATOR_MODEL)
        else:
            call_metadata["model"] = {"id": OWUI_ORCHESTRATOR_MODEL}

        return await safe_owui_call(
            _owui_create_auto,
            name=name,
            prompt=prompt,
            rrule=rrule,
            folder_id=folder_id,
            model_id=OWUI_ORCHESTRATOR_MODEL,
            __request__=__request__,
            __user__=__user__,
            __metadata__=call_metadata
        )

    async def update_ui_automation(
        self,
        automation_id: str,
        name: str = None,
        prompt: str = None,
        rrule: str = None,
        target_folder_name: str = None,
        __request__ = None,
        __user__: dict = {},
        __metadata__: dict = {}
    ) -> str:
        """
        CRON Cognitif : Le Modèle DOIT utiliser cet outil pour modifier une tâche de fond automatisée (CRON) existante dans l'Interface Utilisateur.
        Seuls les champs explicitement fournis sont modifiés ; les champs omis conservent leur valeur actuelle.
        Si 'target_folder_name' est spécifié, la tâche sera rattachée à ce dossier (créé automatiquement s'il n'existe pas).
        Le modèle d'exécution de la tâche est invariablement fixé et verrouillé sur l'Orchestrateur maître Open WebUI (echo_orchestrator).

        RÈGLES STRICTES DE SYNTAXE RRULE :
        L'argument rrule DOIT être une chaîne iCalendar valide. Si 'COUNT' est spécifié, 'DTSTART' est OBLIGATOIRE.
        Le format DTSTART exige la syntaxe YYYYMMDDTHHMMSS.
        Exemples valides :
        - Une seule fois à une date précise : "DTSTART:20261015T140000\nRRULE:FREQ=DAILY;COUNT=1"
        - Tous les jours à 9h : "DTSTART:20261001T090000\nRRULE:FREQ=DAILY"
        - Toutes les heures : "RRULE:FREQ=HOURLY;INTERVAL=1"

        :param automation_id: Identifiant unique de la tâche automatisée à modifier (obligatoire).
        :param name: Nouveau nom descriptif de l'automatisation (optionnel).
        :param prompt: Nouvelles instructions/prompt exécutées à chaque déclenchement (optionnel).
        :param rrule: Nouvelle périodicité au format RRULE iCalendar (optionnel).
        :param target_folder_name: Nom du dossier cible pour les chats générés (optionnel, conserve le dossier actuel si omis).
        """
        user_id = __user__.get("id")
        if not user_id:
            return json.dumps({"error": "User context missing"})

        folder_id = ""
        if target_folder_name is not None:
            folders = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, 'get_folders_by_user_id') else []
            folder_match = next((f for f in folders if f.name == target_folder_name), None)
            if folder_match:
                folder_id = folder_match.id
            else:
                try:
                    new_folder = await Folders.insert_new_folder(user_id, FolderForm(name=target_folder_name))
                    folder_id = new_folder.id
                except Exception as e:
                    log.error(f"ECHO: Failed to create target folder '{target_folder_name}' - {e}")
                    folder_id = ""

        call_metadata = dict(__metadata__ or {})
        call_metadata["model_id"] = OWUI_ORCHESTRATOR_MODEL
        if isinstance(call_metadata.get("model"), dict):
            call_metadata["model"] = dict(call_metadata["model"], id=OWUI_ORCHESTRATOR_MODEL)
        else:
            call_metadata["model"] = {"id": OWUI_ORCHESTRATOR_MODEL}

        return await safe_owui_call(
            _owui_update_auto,
            automation_id=automation_id,
            name=name,
            prompt=prompt,
            rrule=rrule,
            model_id=OWUI_ORCHESTRATOR_MODEL,
            folder_id=folder_id,
            __request__=__request__,
            __user__=__user__,
            __metadata__=call_metadata
        )

    async def delete_ui_automation(self, automation_id: str, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """Le Modèle DOIT utiliser cet outil pour supprimer une de ses tâches de fond de l'Interface Utilisateur si elle n'est plus nécessaire."""
        return await safe_owui_call(_owui_delete_auto, automation_id=automation_id, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def read_ui_automations(self, automation_ids: list[str], __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT utiliser cet outil pour inspecter le contenu intégral et la configuration technique (prompt complet, modèle cible, règle RRULE, statut) d'une ou plusieurs tâches de fond (CRON) de l'Interface Utilisateur à partir de leurs identifiants.
        Complément indispensable de list_ui_automations pour analyser ou auditer une automatisation avant modification ou suppression.

        :param automation_ids: Liste des identifiants (IDs au format UUID) des automatisations à inspecter.
        """
        user_id = __user__.get("id")
        if not user_id:
            return wrap_tool_output(text=json.dumps({"error": "User context missing"}), status={"status": "error"}, metadata=__metadata__)

        if isinstance(automation_ids, str):
            clean_ids = [aid.strip() for aid in automation_ids.split(",") if aid.strip()]
        elif isinstance(automation_ids, list):
            clean_ids = [str(aid).strip() for aid in automation_ids if str(aid).strip()]
        else:
            clean_ids = []

        if not clean_ids:
            return wrap_tool_output(text=json.dumps({"error": "Aucun identifiant d'automatisation valide fourni."}), status={"status": "error"}, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

        try:
            tasks = [Automations.get_by_id(aid) for aid in clean_ids]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

            automations = []
            not_found = []

            for aid, res in zip(clean_ids, raw_results):
                if isinstance(res, Exception) or res is None or res.user_id != user_id:
                    not_found.append(aid)
                    continue

                automations.append({
                    "id": res.id,
                    "name": res.name,
                    "folder_id": res.folder_id,
                    "prompt": res.data.get("prompt", "") if res.data else "",
                    "model_id": res.data.get("model_id", "") if res.data else "",
                    "rrule": res.data.get("rrule", "") if res.data else "",
                    "target": res.data.get("target") if res.data else None,
                    "is_active": res.is_active,
                    "last_run_at": res.last_run_at,
                    "next_run_at": res.next_run_at,
                    "created_at": res.created_at,
                    "updated_at": res.updated_at
                })

            payload = {
                "automations": automations,
                "total_found": len(automations)
            }
            if not_found:
                payload["not_found"] = not_found

            return wrap_tool_output(
                text=json.dumps(payload, ensure_ascii=False),
                status={"status": "success"},
                user_id=user_id,
                chat_id=__metadata__.get("chat_id"),
                metadata=__metadata__
            )
        except Exception as e:
            return wrap_tool_output(text=json.dumps({"error": str(e)}), status={"status": "error"}, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def list_ui_automations(self, query: str = "", limit: int = 30, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Permet au Modèle de lister et rechercher ses tâches de fond automatisées (CRON) de l'Interface Utilisateur.
        Retourne l'ID, le nom, la périodicité (rrule) et le statut. Indispensable avant d'utiliser delete_ui_automation.
        """
        user_id = __user__.get("id")
        if not user_id:
            return wrap_tool_output(text=json.dumps({"error": "User context missing"}), status={"status": "error"})
            
        try:
            result = await Automations.search_automations(user_id=user_id, query=query, limit=limit)
            items = []
            for item in result.items:
                items.append({
                    "id": item.id,
                    "name": item.name,
                    "rrule": item.data.get("rrule", "") if item.data else "",
                    "is_active": item.is_active,
                    "next_run_at": item.next_run_at
                })
            res_json = json.dumps(items)
            return wrap_tool_output(text=res_json, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=json.dumps({"error": str(e)}), status={"status": "error"}, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def list_ui_folders(self, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Permet au Modèle de scanner l'arborescence des UI Folders de l'Interface Utilisateur.
        Retourne la liste complète des dossiers de l'Utilisateur pour de l'organisation spatiale.
        """
        user_id = __user__.get("id")
        folders = await Folders.get_folders_by_user_id(user_id) if hasattr(Folders, 'get_folders_by_user_id') else []
        res = json.dumps([{"id": f.id, "name": f.name} for f in folders])
        return wrap_tool_output(text=res, user_id=user_id, chat_id=__metadata__.get("chat_id"), metadata=__metadata__)

    async def delegate_to_tool_consultant(
        self,
        objective: str = Field(
            ...,
            description="L'objectif technique précis à accomplir (ex: 'Chercher X sur le web puis l'injecter dans N8N, via un plan et après discussion entre experts...')."
        ),
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Agent Stateless. À invoquer AVANT toute séquence complexe d'outils.
        Analyse dynamiquement les outils disponibles et retourne en un seul jet 
        un plan détaillé et structuré (Quels outils utiliser, pour quoi faire, et dans quel ordre).
        Ne possède aucune capacité d'exécution d'outil.
        """
        if not __user__:
            return wrap_tool_output(text="Erreur : contexte OWUI manquant.", status={"status": "error"}, metadata=__metadata__)

        events = EchoEvents(__event_emitter__, __event_call__)
        await events.status("🧠 Conseiller Outils : Analyse du catalogue d'outils en cours...")

        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        user_id = __user__.get("id", "system")

        # Introspection Dynamique : Extraction sécurisée du cache Pipe
        _pipe_mod = sys.modules.get("function_echo_orchestrator")
        _tools_cache = (getattr(_pipe_mod, "_TOOLS_CACHE", {}) or {}) if _pipe_mod else {}
        _tools_dict = _tools_cache.get(chat_id, {})
        
        if not _tools_dict:
            _tools_dict = (__metadata__ or {}).get("_echo_tools_dict", {})

        from echo_constants import ECHO_SUBAGENT_CONTEXT, DELEGATE_AGENT_BLACKLIST
        sub_ctx = ECHO_SUBAGENT_CONTEXT.get()
        is_subagent = bool(sub_ctx.get("is_subagent"))
        if is_subagent and "sub_tools_names" in sub_ctx:
            allowed = set(sub_ctx["sub_tools_names"])
            _tools_dict = {k: v for k, v in _tools_dict.items() if k in allowed}

        # Liste noire : Ne pas lister ces outils au conseiller pour éviter la récursion
        blacklist = {"delegate_to_agent", "delegate_to_tool_consultant"}
        if is_subagent:
            blacklist |= set(DELEGATE_AGENT_BLACKLIST)

        # Construction textuelle du catalogue d'outils
        tools_docs = []
        for t_name, t_spec in _tools_dict.items():
            if t_name in blacklist:
                continue
            spec = t_spec.get("spec", {})
            desc = spec.get("description", "Pas de description")
            params = spec.get("parameters", {}).get("properties", {})
            
            # Formatage dense pour injection dans {tools_catalog}
            tools_docs.append(f"### OUTIL: {t_name} ###\n- DESCRIPTION: {desc}\n- PARAMÈTRES: {list(params.keys())}")
        
        tools_catalog = "\n\n".join(tools_docs) if tools_docs else "Aucun outil détecté."

        # Formatage du prompt centralisé depuis echo_prompts
        system_prompt = SYS_ORCHESTRATOR_CONSULTANT.format(tools_catalog=tools_catalog)

        try:
            payload = {
                "contents": [{"role": "user", "parts": [{"text": objective}]}],
                "generationConfig": get_generation_config("MODEL_PROMETHEUS", override_thinking="medium"),
                "systemInstruction": {"role": "system", "parts": [{"text": system_prompt}]}
            }
            # Inférence Stateless via call_cascade (Clamping + résilience) sans exposer la section tools
            response, _, _ = await EchoGeminiClient.call_cascade(
                target_model_key="MODEL_PROMETHEUS",
                payload=payload,
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                chat_id=chat_id,
                include_thoughts=False,
            )
            if not response:
                await events.status("❌ Erreur Conseiller Outils : Cascade épuisée", done=True)
                return wrap_tool_output(
                    text="Erreur lors de la délégation au Conseiller Outils : Aucun modèle disponible (cascade épuisée).",
                    status={"status": "error", "message": "cascade exhausted"},
                    user_id=user_id,
                    chat_id=chat_id,
                    metadata=__metadata__
                )

            text_result = ""
            candidates = response.get("candidates", [])
            if candidates and candidates[0].get("content"):
                for p in candidates[0]["content"].get("parts", []):
                    if "text" in p and not p.get("thought"):
                        text_result += p["text"]
            await events.status("✅ Conseiller Outils : Planification terminée.", done=True)
            return wrap_tool_output(
                text=f"Recommandation du Conseiller Outils :\n\n{text_result}",
                user_id=user_id,
                chat_id=chat_id,
                metadata=__metadata__
            )
        except Exception as e:
            await events.status("❌ Erreur Conseiller Outils", done=True)
            return wrap_tool_output(
                text=f"Erreur lors de la délégation au Conseiller Outils : {str(e)}",
                status={"status": "error", "message": str(e)},
                user_id=user_id,
                chat_id=chat_id,
                metadata=__metadata__
            )
