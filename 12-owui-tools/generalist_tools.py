"""
title: ECHO Generalist Tools
author: Antigravity
version: 1.0
description: 1.0: Outils utilitaires généraux. Inclus un Wait Timer asynchrone avec HUD visuel.
"""

# ECHO CONFIG NAME : ECHO Generalist Tools

import asyncio
import sys
from pydantic import BaseModel, Field
from typing import Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents
from echo_constants import ECHO_MAX_WAIT_TIMER

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
        __event_call__: Any = None
    ) -> str:
        """
        Déclenche une attente temporelle (Timer) bloquant l'exécution de l'agent.
        Utile pour imposer une pause stricte avant de poursuivre.
        :param seconds: Durée de l'attente en secondes (entre 1 et 180 par défaut).
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
        return wrap_tool_output(text=f"Le timer de {sec_val} secondes est terminé avec succès.")
