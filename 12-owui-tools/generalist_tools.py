"""
title: ECHO Generalist Tools
author: Antigravity
version: 1.22
description: Composant système interne : ECHO Generalist Tools.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.22: Correction de la formulation de la docstring pour create_ui_calendar_event.
# 1.21: Précision "de l'Interface Utilisateur" dans les docstrings des outils UI.
# 1.20: Ajout de l'outil search_ui_automations pour rendre autonome la gestion des CRONs.
# 1.19: Filtrage strict des kwargs via inspect.signature dans safe_owui_call pour compatibilité OWUI API.
# 1.18: Implémentation de safe_owui_call pour les outils natifs OWUI (compliance avec le protocole ECHO).
# 1.17: Exposition de l'argument location et documentation explicite du format ISO 8601 pour les méthodes calendrier.
# 1.16: Ajout des contraintes strictes iCalendar RRULE dans la docstring de create_ui_automation.
# 1.15: Renommage des actions d'automatisation en *_ui_automation.
# 1.14: Suppression du préfixe 'action_' sur les outils pour simplifier l'interface LLM.
# 1.0: Outils utilitaires généraux. Inclus un Wait Timer asynchrone avec HUD visuel.

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
from echo_constants import ECHO_MAX_WAIT_TIMER
from echo_ui import EchoUI

from open_webui.tools.builtin import (
    search_calendar_events as _owui_search,
    create_calendar_event as _owui_create_cal,
    delete_calendar_event as _owui_delete_cal,
    create_automation as _owui_create_auto,
    delete_automation as _owui_delete_auto
)
from open_webui.models.folders import Folders, FolderForm
from open_webui.models.automations import Automations
import logging

log = logging.getLogger(__name__)

async def safe_owui_call(func, *args, **kwargs):
    """Proxy sécurisé pour l'exécution et l'encapsulation ECHO des fonctions natives Open WebUI."""
    __user__ = kwargs.get("__user__", {})
    __metadata__ = kwargs.get("__metadata__", {})
    
    sig = inspect.signature(func)
    filtered_kwargs = {k: v for k, v in kwargs.items() if k in sig.parameters}
    
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

    async def search_ui_calendar_events(self, query: str = "", start: str = None, end: str = None, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT consulter l'agenda de l'Interface Utilisateur pour vérifier ses disponibilités et filtrer par dates.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".
        """
        return await safe_owui_call(_owui_search, query=query, start=start, end=end, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def create_ui_calendar_event(self, title: str, description: str = "", start: str = "", end: str = "", location: str = "", __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """
        Le Modèle DOIT créer un évènement dans le calendrier de l'Interface Utilisateur via cette fonction.
        Format attendu pour start/end : Chaîne ISO 8601 (ex: "2026-10-10T10:00:00Z") ou "YYYY-MM-DD HH:MM".
        """
        return await safe_owui_call(_owui_create_cal, title=title, description=description, start=start, end=end, location=location, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

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
            
        return await safe_owui_call(_owui_create_auto, name=name, prompt=prompt, rrule=rrule, folder_id=folder_id, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def delete_ui_automation(self, automation_id: str, __request__ = None, __user__: dict = {}, __metadata__: dict = {}) -> str:
        """Le Modèle DOIT utiliser cet outil pour supprimer une de ses tâches de fond de l'Interface Utilisateur si elle n'est plus nécessaire."""
        return await safe_owui_call(_owui_delete_auto, automation_id=automation_id, __request__=__request__, __user__=__user__, __metadata__=__metadata__)

    async def search_ui_automations(self, query: str = "", limit: int = 30, __user__: dict = {}, __metadata__: dict = {}) -> str:
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
