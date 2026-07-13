"""
title: ECHO App Drawer Filter
author: Wilfried BARNAVON
version: 1.7
description: Composant système interne : ECHO App Drawer Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.7: Normalisation globale de la priorité d'exécution (déplacement vers Valves).
# 1.6: Renommage du titre et de la description pour standardisation en "Filter".
# 1.4: Correction critique d'une erreur de syntaxe JS (bloc try/catch non fermé).
# 1.3: Optimisation Zéro-Requête (Extraction DOM Svelte) au lieu du téléchargement complet du chat.
# 1.2: Correction de l'endpoint API des actions (OWUI V0.3.x) vers /api/chat/actions.

import logging
from typing import Optional
from pydantic import BaseModel, Field

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=1000, hidden=True, description="Priorité d'exécution (0 = premier).")
        enable_drawer: bool = Field(default=True, description="Active ou désactive le HUD global des applications ECHO.")

    def __init__(self):
        self.valves = self.Valves()
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48bGluZSB4MT0iMTIiIHkxPSI1IiB4Mj0iMTIiIHkyPSIxOSI+PC9saW5lPjxsaW5lIHgxPSI1IiB5MT0iMTIiIHgyPSIxOSIgeTI9IjEyIj48L2xpbmU+PC9zdmc+"
        pass

    async def inlet(
        self,
        body: dict,
        __event_emitter__=None,
        __user__=None,
        __model__=None
    ) -> dict:
        """
        Intercepte la requête AVANT que le LLM ne commence à générer.
        Injecte le code JavaScript du HUD via un faux événement "message" silencieux.
        """
        if not self.valves.enable_drawer or not __event_emitter__:
            return body

        # Code JS autonome (V9) : Data Island Zéro-BDD qui requiert /api/v1/models
        js_code = r"""
(async function initAppDrawer() {
    if (window._echoAppDrawerReady) return;
    window._echoAppDrawerReady = true;

    console.log("🚀 Initialisation ECHO App Drawer (v9) - Fix Tooltips");

    // 1. STYLES CSS
    const styleId = 'echo-app-drawer-styles';
    if (!document.getElementById(styleId)) {
        const style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
            #echo-hud-container {
                position: fixed;
                width: 48px;
                height: 48px;
                bottom: 120px;
                right: 20px;
                z-index: 99999;
                touch-action: none; 
            }
            #echo-hud-container.dragging .echo-hud-toggle {
                cursor: grabbing;
                transform: scale(0.95);
            }
            .echo-hud-toggle {
                width: 48px;
                height: 48px;
                border-radius: 50%;
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(56, 189, 248, 0.4);
                display: flex;
                justify-content: center;
                align-items: center;
                cursor: pointer;
                color: #38bdf8;
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.5);
                transition: transform 0.2s, background 0.2s;
                user-select: none;
                position: absolute;
                top: 0;
                left: 0;
            }
            .echo-hud-toggle:hover {
                background: rgba(30, 41, 59, 0.95);
            }
            .echo-hud-toggle svg {
                width: 24px;
                height: 24px;
                transition: transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            }
            .echo-hud-toggle.open svg {
                transform: rotate(45deg);
                color: #f43f5e;
            }
            
            .echo-hud-menu {
                position: absolute;
                right: 0;
                width: 48px;
                display: flex;
                align-items: center;
                gap: 8px;
                background: rgba(15, 23, 42, 0.6);
                backdrop-filter: blur(12px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 30px;
                box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
                transition: max-height 0.4s cubic-bezier(0.4, 0, 0.2, 1), opacity 0.3s ease, padding 0.3s ease;
                max-height: var(--open-max-height, 60vh);
                
                overflow-y: auto;
                overflow-x: hidden;
                
                opacity: 1;
                pointer-events: auto;
                scrollbar-width: none;
            }
            .echo-hud-menu::-webkit-scrollbar { display: none; }
            
            .echo-hud-menu.collapsed {
                max-height: 0 !important;
                padding-top: 0 !important;
                padding-bottom: 0 !important;
                opacity: 0;
                pointer-events: none;
                border-color: transparent;
                box-shadow: none;
            }

            .echo-hud-menu.direction-up {
                bottom: 56px;
                top: auto;
                flex-direction: column-reverse;
                padding: 10px 0;
            }
            .echo-hud-menu.direction-down {
                top: 56px;
                bottom: auto;
                flex-direction: column; 
                padding: 10px 0;
            }

            .echo-app-btn {
                width: 40px;
                height: 40px;
                flex-shrink: 0;
                border-radius: 50%;
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                display: flex;
                justify-content: center;
                align-items: center;
                cursor: pointer;
                transition: all 0.2s ease;
                color: #e2e8f0;
                position: relative;
            }
            .echo-app-btn:hover {
                background: rgba(56, 189, 248, 0.2);
                border-color: rgba(56, 189, 248, 0.5);
                transform: scale(1.1);
                color: #38bdf8;
            }
            .echo-app-btn svg { width: 20px; height: 20px; fill: currentColor; }
            .echo-app-btn img { width: 20px; height: 20px; border-radius: 50%; }
            
            /* Bulle Flottante gérée par JS */
            .echo-tooltip-floating {
                position: fixed;
                background: rgba(15, 23, 42, 0.95);
                color: #cbd5e1;
                padding: 8px 12px;
                border-radius: 8px;
                font-size: 12px;
                font-family: system-ui, sans-serif;
                max-width: 250px;
                line-height: 1.4;
                border: 1px solid rgba(255, 255, 255, 0.1);
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
                z-index: 999999;
                pointer-events: none;
                transform: translateY(-50%);
                opacity: 0;
                transition: opacity 0.2s ease;
            }
            
            @keyframes pulseClick {
                0% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.7); }
                70% { box-shadow: 0 0 0 10px rgba(56, 189, 248, 0); }
                100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); }
            }
            .btn-clicked { animation: pulseClick 1s ease-out; }
        `;
        document.head.appendChild(style);
    }

    // 2. RECUPERATION DES DONNEES
    const token = localStorage.getItem('token');
    if (!token) return;

    try {
        const res = await fetch('/api/v1/models', {
            headers: { 'Authorization': `Bearer ${token}` }
        });
        if (!res.ok) throw new Error("Failed to fetch models");
        
        const modelsData = await res.json();
        const models = modelsData.data || modelsData;
        
        let actions = [];
        for (const m of models) {
            if (m.actions && m.actions.length > 0) {
                actions = m.actions; 
                break;
            }
        }
        if (actions.length === 0) return;

        // 3. CONSTRUCTION DU CONTENEUR
        const container = document.createElement('div');
        container.id = 'echo-hud-container';
        
        const menu = document.createElement('div');
        menu.className = 'echo-hud-menu direction-up collapsed';
        
        actions.forEach(action => {
            const btn = document.createElement('div');
            btn.className = 'echo-app-btn';
            
            // Format du texte (Titre + Description si disponible)
            let tipTitle = action.name || action.id;
            let tipDesc = action.description || '';
            let htmlContent = `<b>${tipTitle}</b>`;
            if (tipDesc && tipDesc !== tipTitle) {
                htmlContent += `<br><span style="font-size:10px;color:#94a3b8">${tipDesc}</span>`;
            }

            // Gestion de l'infobulle (Portal DOM)
            btn.addEventListener('mouseenter', () => {
                if (container.classList.contains('dragging')) return;
                
                const rect = btn.getBoundingClientRect();
                const tip = document.createElement('div');
                tip.className = 'echo-tooltip-floating';
                tip.innerHTML = htmlContent;
                
                // Positionnement au pixel près à gauche de l'icône
                tip.style.top = (rect.top + rect.height / 2) + 'px';
                tip.style.right = (window.innerWidth - rect.left + 15) + 'px'; 
                
                document.body.appendChild(tip);
                btn._activeTip = tip;
                
                // Animation d'apparition
                requestAnimationFrame(() => {
                    if(btn._activeTip) tip.style.opacity = '1';
                });
            });

            btn.addEventListener('mouseleave', () => {
                if (btn._activeTip) {
                    btn._activeTip.remove();
                    btn._activeTip = null;
                }
            });

            const iconUrl = action.icon;
            if (iconUrl) {
                if (iconUrl.startsWith('<svg')) {
                    btn.insertAdjacentHTML('beforeend', iconUrl);
                } else if (iconUrl.startsWith('http') || iconUrl.startsWith('data:')) {
                    const img = document.createElement('img');
                    img.src = iconUrl;
                    btn.appendChild(img);
                } else {
                    btn.insertAdjacentHTML('beforeend', `<svg viewBox="0 0 24 24"><path d="M12 2L2 22h20L12 2z"/></svg>`);
                }
            } else {
                btn.insertAdjacentHTML('beforeend', `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="9" y1="3" x2="9" y2="21"></line></svg>`);
            }

            btn.onclick = async () => {
                if (btn._activeTip) {
                    btn._activeTip.remove();
                    btn._activeTip = null;
                }
                btn.classList.add('btn-clicked');
                setTimeout(() => btn.classList.remove('btn-clicked'), 1000);
                
                const currentChatIdMatch = window.location.pathname.match(/\/c\/([a-z0-9-]+)/i);
                const currentChatId = currentChatIdMatch ? currentChatIdMatch[1] : null;
                if (!currentChatId) return;

                try {
                    // 1. On extrait l'ID du dernier message directement depuis le DOM Svelte
                    // La classe .message-assistant n'existe plus, on cherche le dernier VRAI message
                    const messageElements = document.querySelectorAll('[id^="message-"]');
                    let lastMsg = null;
                    
                    for (let i = messageElements.length - 1; i >= 0; i--) {
                        const el = messageElements[i];
                        const id = el.id;
                        // On ignore la zone de texte, l'édition, les notes, et les messages utilisateur
                        if (!id.includes('input') && !id.includes('edit') && !id.includes('index') && !id.includes('feedback') && !el.classList.contains('user-message')) {
                            lastMsg = el;
                            break;
                        }
                    }

                    if (!lastMsg) {
                        console.warn('ECHO HUD: Aucun message d\'assistant valide trouvé dans le DOM.');
                        return;
                    }
                    const messageId = lastMsg.id.replace('message-', '');
                    
                    // 2. On clique sur le bouton natif Svelte correspondant à cette action
                    const nativeBtn = lastMsg.querySelector(`button[aria-label="${action.name}"]`);
                    
                    if (nativeBtn) {
                        console.log(`ECHO HUD: Déclenchement du bouton natif pour "${action.name}"`);
                        nativeBtn.click();
                    } else {
                        console.warn(`ECHO HUD: Bouton natif "${action.name}" introuvable. Le message est-il terminé ou le modèle le supporte-t-il ?`);
                    }
                } catch (err) {
                    console.error("Erreur lors de l'appel d'action:", err);
                }
            };
            menu.appendChild(btn);
        });

        const toggleBtn = document.createElement('div');
        toggleBtn.className = 'echo-hud-toggle';
        toggleBtn.innerHTML = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>`;

        container.appendChild(menu);
        container.appendChild(toggleBtn);
        document.body.appendChild(container);

        // 4. GESTION DYNAMIQUE (DIRECTION ET OPTIMISATION D'ESPACE)
        let isDragging = false;
        let startY, initialTop;

        const updateDirection = () => {
            const rect = toggleBtn.getBoundingClientRect();
            const toggleMid = rect.top + (rect.height / 2);
            const screenMid = window.innerHeight / 2;
            const MARGIN = 20; 

            if (toggleMid < screenMid) {
                // Moitié haute -> se déploie vers le bas
                if (!menu.classList.contains('direction-down')) {
                    menu.classList.add('direction-down');
                    menu.classList.remove('direction-up');
                }
                const availableSpace = window.innerHeight - rect.bottom - MARGIN;
                menu.style.setProperty('--open-max-height', Math.max(100, availableSpace) + 'px');
            } else {
                // Moitié basse -> se déploie vers le haut
                if (!menu.classList.contains('direction-up')) {
                    menu.classList.add('direction-up');
                    menu.classList.remove('direction-down');
                }
                const availableSpace = rect.top - MARGIN;
                menu.style.setProperty('--open-max-height', Math.max(100, availableSpace) + 'px');
            }
        };

        updateDirection();
        window.addEventListener('resize', updateDirection);

        toggleBtn.addEventListener('mousedown', (e) => {
            isDragging = false; 
            startY = e.clientY;
            
            const rect = container.getBoundingClientRect();
            initialTop = rect.top;
            
            container.style.bottom = 'auto';
            container.style.top = initialTop + 'px';
            
            const onMouseMove = (moveEvent) => {
                const dy = moveEvent.clientY - startY;
                
                if (!isDragging && Math.abs(dy) > 3) {
                    isDragging = true;
                    container.classList.add('dragging');
                    // On nettoie les éventuels tooltips
                    const tips = document.querySelectorAll('.echo-tooltip-floating');
                    tips.forEach(t => t.remove());
                }

                if (isDragging) {
                    let newTop = initialTop + dy;
                    const maxTop = window.innerHeight - 48; 
                    newTop = Math.max(0, Math.min(newTop, maxTop));
                    container.style.top = newTop + 'px';
                    updateDirection();
                }
            };

            const onMouseUp = () => {
                window.removeEventListener('mousemove', onMouseMove);
                window.removeEventListener('mouseup', onMouseUp);
                
                if (!isDragging) {
                    updateDirection();
                    menu.classList.toggle('collapsed');
                    toggleBtn.classList.toggle('open');
                }
                setTimeout(() => container.classList.remove('dragging'), 50);
            };

            window.addEventListener('mousemove', onMouseMove);
            window.addEventListener('mouseup', onMouseUp);
        });

    } catch (e) {
        console.error("ECHO App Drawer Error:", e);
    }
})();
"""

        # Injection silencieuse via exécution native (sans polluer le chat)
        await __event_emitter__({
            "type": "execute",
            "data": {"code": js_code}
        })

        return body

    async def outlet(
        self,
        body: dict,
        __event_emitter__=None,
        __user__=None,
        __model__=None
    ) -> dict:
        return body
