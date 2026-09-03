"""
title: ECHO Generalist Tools
author: Antigravity
version: 1.7
description: Composant système interne : ECHO Generalist Tools.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.7: Précision sur la saisie libre pour l'argument options de ask_user_input.
# 1.6: Précision dans la docstring de ask_user_input (les options génèrent des listes/boutons cliquables).
# 1.5: Mise à jour de la docstring de wait_timer (précision boucle agentique).
# 1.4: Refonte du Lazy-Loading JS des modales ECHO (get_custom_modals_js) pour ask_user_input (Anti-Spaghetti).
# 1.0: Outils utilitaires généraux. Inclus un Wait Timer asynchrone avec HUD visuel.

# ECHO CONFIG NAME : ECHO Generalist Tools

import asyncio
import sys
import json
from pydantic import BaseModel, Field
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents
from echo_constants import ECHO_MAX_WAIT_TIMER
from echo_ui import EchoUI

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
        await events.emit("execute", {"code": js_code})

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
        user_input = await __event_call__({"type": "execute", "data": {"code": js_code}})

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
