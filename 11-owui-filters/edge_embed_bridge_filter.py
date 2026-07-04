"""
title: Edge Embedding Bridge Filter
author: ECHO Framework
author_url: https://github.com/echo-framework
funding_url: https://github.com/echo-framework
description: 1.4: Migration vers le modèle Harrier-OSS (WebGPU ONNX), implémentation manuelle du pooling CLS et normalisation L2 en JS, et morphing du HUD en pastille avec animation.
             1.3: Injecte le bridge JavaScript WebGPU pour l'accélération matérielle des embeddings via bge-m3. Gestion intelligente du cache navigateur.
version: 1.4
"""

import os
import asyncio
from typing import Optional
from pydantic import BaseModel, Field

try:
    from echo_constants import DEFAULT_EDGE_EMBEDDING_TIMEOUT
except ImportError:
    DEFAULT_EDGE_EMBEDDING_TIMEOUT = 180

class Filter:
    # Priorité très haute (0) pour injecter le JS avant tout autre filtre
    priority: int = 0

    class Valves(BaseModel):
        pass

    class UserValves(BaseModel):
        ENABLE_EDGE_EMBEDDING: bool = Field(
            default=True,
            description="Activer le pont Edge Embedding (nécessite BunkerWeb ou un proxy WSS configuré)"
        )
        WAIT_FOR_EDGE_EMBEDDING: bool = Field(
            default=True,
            description="Bloque le flux jusqu'à l'initialisation du Edge Embedding."
        )
        EDGE_EMBEDDING_TIMEOUT: int = Field(
            default=DEFAULT_EDGE_EMBEDDING_TIMEOUT,
            description="Délai maximum d'attente (en secondes) avant de forcer le repli sur le CPU."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        
        # --- CONFIGURATION UI OPEN WEBUI ---
        self.toggle = True  # Affiche le switch dans le menu Intégrations (icône engrenage)
        self.icon = "data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0ibm9uZSIgc3Ryb2tlPSJjdXJyZW50Q29sb3IiIHN0cm9rZS13aWR0aD0iMiIgc3Ryb2tlLWxpbmVjYXA9InJvdW5kIiBzdHJva2UtbGluZWpvaW49InJvdW5kIj48cGF0aCBkPSJNMTMgMmwyIDYuNW0tNyAwaDIuNW0tMyA3LjVsMi41LTdtMyA3bC0yLjUtN20tNiA0aDEwbTEgMmEyIDIgMCAwIDEgMiAydjZhMiAyIDAgMCAxLTIgMmgtMTJhMiAyIDAgMCAxLTItMnYtNmEyIDIgMCAwIDEgMi0yaDEyIi8+PC9zdmc+"


    async def inlet(
        self,
        body: dict,
        __event_emitter__=None,
        __user__=None,
        __request__=None
    ) -> dict:
        """
        Intercepte la requête entrante.
        Injecte le pont JavaScript (Data Island).
        Bloque jusqu'à la préparation du modèle WebGPU (selon configuration).
        """
        # --- RÉCUPÉRATION SÉCURISÉE DES VALVES UTILISATEUR ---
        u_valves = __user__.get("valves") if __user__ and "valves" in __user__ else self.user_valves
        if isinstance(u_valves, dict):
            enable_edge = u_valves.get("ENABLE_EDGE_EMBEDDING", getattr(self.user_valves, "ENABLE_EDGE_EMBEDDING", True))
            wait_edge = u_valves.get("WAIT_FOR_EDGE_EMBEDDING", getattr(self.user_valves, "WAIT_FOR_EDGE_EMBEDDING", True))
            timeout_edge = u_valves.get("EDGE_EMBEDDING_TIMEOUT", getattr(self.user_valves, "EDGE_EMBEDDING_TIMEOUT", DEFAULT_EDGE_EMBEDDING_TIMEOUT))
        else:
            enable_edge = getattr(u_valves, "ENABLE_EDGE_EMBEDDING", getattr(self.user_valves, "ENABLE_EDGE_EMBEDDING", True))
            wait_edge = getattr(u_valves, "WAIT_FOR_EDGE_EMBEDDING", getattr(self.user_valves, "WAIT_FOR_EDGE_EMBEDDING", True))
            timeout_edge = getattr(u_valves, "EDGE_EMBEDDING_TIMEOUT", getattr(self.user_valves, "EDGE_EMBEDDING_TIMEOUT", DEFAULT_EDGE_EMBEDDING_TIMEOUT))

        # Si le pont est désactivé par l'utilisateur, on ne fait rien
        if not enable_edge:
            return body

        # Exécution de ce filtre uniquement si c'est un message utilisateur
        if not __event_emitter__:
            return body
            
        user_id = __user__.get("id", "anonymous") if __user__ else "anonymous"

        # Code JavaScript (Data Island) :
        # 1. Hardware Check
        # 2. HUD Echo discret (en bas à droite)
        # 3. Import Transformers.js
        # 4. Connexion WSS
        # 5. Gestion des requêtes
        js_code = """
        (async function initEdgeEmbedding() {
            const EDGE_VERSION = "2.0";
            console.log("🚀 Initialisation ECHO Edge Embedding Bridge v" + EDGE_VERSION);

            // 1. GESTION DU CACHE CONDITIONNELLE
            const storedVersion = localStorage.getItem('ECHO_EDGE_VERSION');
            if (storedVersion !== EDGE_VERSION) {
                console.log(`🔄 Nouvelle version détectée (${storedVersion} -> ${EDGE_VERSION}). Purge du cache...`);
                if (window.caches) {
                    try {
                        await caches.delete('transformers-cache');
                        console.log("🧹 Cache Transformers purgé avec succès.");
                    } catch(e) {
                        console.warn("Impossible de vider le cache:", e);
                    }
                }
                localStorage.setItem('ECHO_EDGE_VERSION', EDGE_VERSION);
            }

            // 2. HARDWARE CHECK
            const isMobile = /Mobi|Android/i.test(navigator.userAgent);
            const ram = navigator.deviceMemory || 8;
            if (isMobile || ram < 4) {
                console.log("⚠️ ECHO Edge: Appareil mobile ou RAM < 4GB détecté.");
                return; // Le backend fera un fallback CPU via timeout (ou on peut le bypasser si l'API est exposée)
            }

            // Vérification de version et d'état
            if (window._echoEdgeReady || window._echoEdgeConnecting) {
                if (window._echoEdgeVersion === EDGE_VERSION) return;
                console.log("🔄 Mise à jour du pont Edge détectée.");
            }
            window._echoEdgeVersion = EDGE_VERSION;
            window._echoEdgeConnecting = true;

            // Injection CSS pour l'animation
            const styleId = 'echo-edge-styles';
            if (!document.getElementById(styleId)) {
                const style = document.createElement('style');
                style.id = styleId;
                style.innerHTML = `
                    @keyframes echoComputePulse {
                        0% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.7); transform: scale(0.95); }
                        70% { box-shadow: 0 0 0 8px rgba(56, 189, 248, 0); transform: scale(1); }
                        100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); transform: scale(0.95); }
                    }
                    .echo-gpu-computing {
                        animation: echoComputePulse 1s infinite !important;
                        opacity: 1 !important;
                        background: rgba(56, 189, 248, 0.15) !important;
                        border-color: rgba(56, 189, 248, 0.8) !important;
                    }
                `;
                document.head.appendChild(style);
            }

            // 3. CREATION DU HUD DISCRET (TOAST)
            const hudId = 'echo-edge-hud-' + Date.now();
            const hud = document.createElement('div');
            hud.id = hudId;
            hud.style.cssText = `
                position: fixed;
                top: 65px;
                right: 20px;
                width: 250px;
                background: rgba(15, 23, 42, 0.85);
                backdrop-filter: blur(8px);
                border: 1px solid rgba(56, 189, 248, 0.25);
                border-radius: 8px;
                padding: 12px 14px;
                color: #cbd5e1;
                font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
                font-size: 12px;
                z-index: 10000;
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.3), 0 4px 6px -2px rgba(0, 0, 0, 0.15);
                transition: opacity 0.3s ease, border-color 0.3s ease;
                display: flex;
                flex-direction: column;
                gap: 8px;
                cursor: grab;
                user-select: none;
            `;
            
            // Header Row (Title + Close Button)
            const headerRow = document.createElement('div');
            headerRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; width: 100%;';
            
            const titleBox = document.createElement('div');
            titleBox.style.cssText = 'display: flex; align-items: center; gap: 8px; font-weight: 600; color: #38bdf8;';
            titleBox.innerHTML = `
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <rect x="4" y="4" width="16" height="16" rx="2" ry="2"></rect>
                    <rect x="9" y="9" width="6" height="6"></rect>
                    <line x1="9" y1="1" x2="9" y2="4"></line>
                    <line x1="15" y1="1" x2="15" y2="4"></line>
                    <line x1="9" y1="20" x2="9" y2="23"></line>
                    <line x1="15" y1="20" x2="15" y2="23"></line>
                    <line x1="20" y1="9" x2="23" y2="9"></line>
                    <line x1="20" y1="14" x2="23" y2="14"></line>
                    <line x1="1" y1="9" x2="4" y2="9"></line>
                    <line x1="1" y1="14" x2="4" y2="14"></line>
                </svg>
                <span>ECHO VRAM Loader</span>
            `;

            const closeBtn = document.createElement('div');
            closeBtn.innerHTML = '✕';
            closeBtn.style.cssText = 'cursor: pointer; color: #94a3b8; font-size: 14px; padding: 0 4px; font-weight: bold; transition: color 0.2s;';
            closeBtn.onmouseover = () => closeBtn.style.color = '#ef4444';
            closeBtn.onmouseout = () => closeBtn.style.color = '#94a3b8';

            headerRow.appendChild(titleBox);
            headerRow.appendChild(closeBtn);
            
            const textRow = document.createElement('div');
            textRow.id = hudId + '-text';
            textRow.style.color = '#94a3b8';
            textRow.style.lineHeight = '1.4';
            textRow.innerHTML = 'Connexion WSS...';
            
            const progressContainer = document.createElement('div');
            progressContainer.style.cssText = 'width: 100%; height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden; margin-top: 2px;';
            
            const progressBar = document.createElement('div');
            progressBar.id = hudId + '-bar';
            progressBar.style.cssText = 'width: 0%; height: 100%; background: #38bdf8; transition: width 0.1s linear;';
            
            progressContainer.appendChild(progressBar);
            hud.appendChild(headerRow);
            hud.appendChild(textRow);
            hud.appendChild(progressContainer);
            document.body.appendChild(hud);

            // Drag & Drop Logic
            let isDragging = false;
            let currentX;
            let currentY;
            let initialX;
            let initialY;
            let xOffset = 0;
            let yOffset = 0;

            function dragStart(e) {
                if (e.target === closeBtn || e.target.tagName.toLowerCase() === 'a') return;
                initialX = e.clientX - xOffset;
                initialY = e.clientY - yOffset;
                isDragging = true;
                hud.style.cursor = 'grabbing';
            }

            function dragEnd(e) {
                initialX = currentX;
                initialY = currentY;
                isDragging = false;
                hud.style.cursor = 'grab';
            }

            function drag(e) {
                if (isDragging) {
                    e.preventDefault();
                    currentX = e.clientX - initialX;
                    currentY = e.clientY - initialY;
                    
                    const rect = hud.getBoundingClientRect();
                    
                    // Constrain within window
                    const maxRight = 20; // Allow returning to initial
                    const maxLeft = -(window.innerWidth - rect.width - 20); 
                    const maxTop = -45; // 65px is initial top, allow moving up to 20px from top
                    const maxBottom = window.innerHeight - rect.height - 65; 
                    
                    xOffset = Math.min(Math.max(currentX, maxLeft), maxRight);
                    yOffset = Math.min(Math.max(currentY, maxTop), maxBottom);

                    hud.style.transform = `translate3d(${xOffset}px, ${yOffset}px, 0)`;
                }
            }

            hud.addEventListener('mousedown', dragStart);
            document.addEventListener('mouseup', dragEnd);
            document.addEventListener('mousemove', drag);

            // Cancel Logic
            let wsRef = null;
            let cancelTimeout = null;
            
            const handleCancel = () => {
                if (window._echoEdgeReady) return; // Ignore if already loaded
                
                // Set cancel flag (not persistent)
                window._echoEdgeConnecting = false;
                window._echoEdgeReady = false;
                
                // Fallback backend to CPU immediately
                if (wsRef && wsRef.readyState === WebSocket.OPEN) {
                    wsRef.send(JSON.stringify({ type: 'incompatible' }));
                    wsRef.close();
                }

                // Visual Update
                hud.style.borderColor = 'rgba(249, 115, 22, 0.5)';
                progressContainer.style.display = 'none';
                
                textRow.innerHTML = `
                    <span style="color:#f97316;">WebGPU ignoré.</span><br/>
                    Pour le désactiver, décochez ENABLE_EDGE_EMBEDDING.<br/>
                    <a href="#" id="${hudId}-restart" style="color:#38bdf8; text-decoration:none; margin-top:6px; display:inline-block; font-weight:bold;">⟳ Redémarrer</a>
                `;

                document.getElementById(`${hudId}-restart`).onclick = (e) => {
                    e.preventDefault();
                    if (cancelTimeout) clearTimeout(cancelTimeout);
                    hud.remove();
                    document.removeEventListener('mouseup', dragEnd);
                    document.removeEventListener('mousemove', drag);
                    initEdgeEmbedding(); // Restart
                };

                // Auto-remove after 10s
                cancelTimeout = setTimeout(() => {
                    hud.style.opacity = '0';
                    setTimeout(() => {
                        hud.remove();
                        document.removeEventListener('mouseup', dragEnd);
                        document.removeEventListener('mousemove', drag);
                    }, 300);
                }, 10000);
            };

            closeBtn.onclick = handleCancel;

            try {
                // 4. CONNEXION WEBSOCKET PRECOCE
                const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                const wsUrl = `${protocol}//${location.host}/ws/edge-embed`;
                const ws = new WebSocket(wsUrl);
                wsRef = ws;
                
                ws.onopen = async () => {
                    if (!window._echoEdgeConnecting) return; // Was cancelled

                    console.log("✅ WebSocket ECHO Edge connecté");
                    textRow.innerHTML = 'Préparation Harrier-OSS...';
                    
                    try {
                        // 5. IMPORT ET CHARGEMENT DU MODELE V3
                        const transformers = await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js');
                        transformers.env.allowLocalModels = false;
                        
                        let tokenizer, model;
                        try {
                            tokenizer = await transformers.AutoTokenizer.from_pretrained('onnx-community/harrier-oss-v1-0.6b-ONNX');
                            model = await transformers.AutoModel.from_pretrained('onnx-community/harrier-oss-v1-0.6b-ONNX', {
                                device: 'webgpu',
                                dtype: 'fp16',
                                progress_callback: (x) => {
                                    if (!window._echoEdgeConnecting) return; // Stop updates if cancelled
                                    if (x.status === 'downloading' || x.status === 'progress') {
                                        const percent = x.total ? Math.round((x.loaded / x.total) * 100) : 0;
                                        textRow.innerHTML = `Chargement modèle: <b>${percent}%</b>`;
                                        progressBar.style.width = `${percent}%`;
                                    }
                                }
                            });
                        } catch (pipelineErr) {
                            console.error("💥 Erreur lors du chargement du modèle (HTML Poison possible). Purge du cache...", pipelineErr);
                            if (window.caches) {
                                await caches.delete('transformers-cache');
                            }
                            throw pipelineErr; // Déclenche le catch(modelError) pour le repli CPU
                        }
                        
                        if (!window._echoEdgeConnecting) return; // Cancelled during download
                        
                        // Modèle prêt, on notifie le backend
                        ws.send(JSON.stringify({ type: 'ready' }));
                        window._echoEdgeReady = true;
                        
                        // Morphing du HUD en mini-moniteur
                        const svgIcon = titleBox.querySelector('svg');
                        hud.innerHTML = ''; // Nettoyage total du contenu précédent (plus de code mort)
                        hud.appendChild(svgIcon);
                        
                        // Transition CSS vers la pastille
                        hud.style.width = '32px';
                        hud.style.height = '32px';
                        hud.style.padding = '0';
                        hud.style.borderRadius = '50%';
                        hud.style.justifyContent = 'center';
                        hud.style.alignItems = 'center';
                        hud.style.opacity = '0.4';
                        
                        // Gestion des requêtes
                        ws.onmessage = async (event) => {
                            try {
                                const payload = JSON.parse(event.data);
                                if (payload.type === 'embed' && payload.texts && payload.request_id) {
                                    hud.classList.add('echo-gpu-computing');
                                    try {
                                        const texts = Array.isArray(payload.texts) ? payload.texts : [payload.texts];
                                        const inputs = tokenizer(texts, { padding: true, truncation: true });
                                        const outputs = await model(inputs);
                                        
                                        const hidden = outputs.last_hidden_state || outputs.logits || Object.values(outputs)[0];
                                        const dims = hidden.dims;
                                        const batch_size = dims[0];
                                        
                                        // Support des tenseurs 3D [batch, seq, dim] et 2D [batch, dim] (pooling intégré)
                                        const is3D = dims.length >= 3;
                                        const seq_len = is3D ? dims[1] : 1;
                                        const embed_dim = is3D ? dims[2] : dims[1];
                                        
                                        const embeddings = [];
                                        
                                        for (let i = 0; i < batch_size; i++) {
                                            // CLS Token pooling (Index 0) pour les modèles encodeurs
                                            const cls_token_idx = 0;
                                            
                                            let vec_start, vec_end;
                                            if (is3D) {
                                                vec_start = (i * seq_len + cls_token_idx) * embed_dim;
                                            } else {
                                                vec_start = i * embed_dim;
                                            }
                                            vec_end = vec_start + embed_dim;
                                            
                                            const vector = hidden.data.slice(vec_start, vec_end);
                                            
                                            // L2 Normalization (match PyTorch exact logic)
                                            let sum_sq = 0;
                                            for (let j = 0; j < embed_dim; j++) {
                                                sum_sq += vector[j] * vector[j];
                                            }
                                            const norm = Math.sqrt(sum_sq);
                                            
                                            const norm_vec = new Array(embed_dim);
                                            for (let j = 0; j < embed_dim; j++) {
                                                let val = Number(vector[j]);
                                                if (isNaN(val)) val = 0; // Sécurité anti-NaN
                                                norm_vec[j] = val / (norm || 1);
                                            }
                                            embeddings.push(norm_vec);
                                        }
                                        
                                        ws.send(JSON.stringify({
                                            type: 'result',
                                            request_id: payload.request_id,
                                            embedding: embeddings
                                        }));
                                    } finally {
                                        hud.classList.remove('echo-gpu-computing');
                                    }
                                }
                            } catch(err) {
                                console.error("❌ Erreur inférence Edge:", err);
                            }
                        };
                        
                    } catch (modelError) {
                        if (!window._echoEdgeConnecting) return; // Was cancelled
                        console.warn("💥 Erreur d'initialisation WebGPU:", modelError);
                        
                        // Notification au backend pour repli CPU immédiat
                        ws.send(JSON.stringify({ type: 'incompatible' }));
                        window._echoEdgeConnecting = false;
                        window._echoEdgeReady = false;
                        
                        // HUD: Repli discret
                        hud.style.borderColor = 'rgba(239, 68, 68, 0.4)';
                        textRow.innerHTML = 'WebGPU indisponible. Repli serveur...';
                        progressBar.style.background = '#ef4444';
                        setTimeout(() => { 
                            hud.style.opacity = '0'; 
                            setTimeout(() => {
                                hud.remove();
                                document.removeEventListener('mouseup', dragEnd);
                                document.removeEventListener('mousemove', drag);
                            }, 300); 
                        }, 2000);
                        
                        ws.close();
                    }
                };
                
                ws.onerror = (err) => {
                    if (!window._echoEdgeConnecting) return;
                    console.error("🔌 Erreur WebSocket ECHO Edge:", err);
                    window._echoEdgeConnecting = false;
                    window._echoEdgeReady = false;
                    hud.style.opacity = '0';
                    setTimeout(() => {
                        hud.remove();
                        document.removeEventListener('mouseup', dragEnd);
                        document.removeEventListener('mousemove', drag);
                    }, 300);
                };
                
                ws.onclose = () => {
                    // Only cleanup if it wasn't gracefully handled
                    if (!window._echoEdgeReady) {
                        window._echoEdgeConnecting = false;
                    }
                };
                
            } catch (error) {
                if (!window._echoEdgeConnecting) return;
                console.error("💥 Erreur globale ECHO Edge:", error);
                window._echoEdgeConnecting = false;
                window._echoEdgeReady = false;
                hud.style.opacity = '0';
                setTimeout(() => {
                    hud.remove();
                    document.removeEventListener('mouseup', dragEnd);
                    document.removeEventListener('mousemove', drag);
                }, 300);
            }
        })();
        """

        # Remplacement de l'ID utilisateur dans le JS pour la remontée d'incompatibilité
        js_code = js_code.replace('__USER_ID__', user_id)

        # Émission du code JS vers le DOM du navigateur
        await __event_emitter__({
            "type": "execute",
            "data": {
                "code": js_code
            }
        })

        if wait_edge:
            import httpx
            import time
            import logging
            
            logger = logging.getLogger("ECHO-EDGE-BRIDGE")
            logger.info(f"⏳ Attente de la préparation Edge WebGPU pour l'utilisateur {user_id}...")
            
            start_time = time.time()
            timeout = timeout_edge
            
            base_url = "http://echo-embedding:7997"
            
            async with httpx.AsyncClient() as client:
                while time.time() - start_time < timeout:
                    try:
                        resp = await client.get(f"{base_url}/internal/edge-status", params={"user_id": user_id})
                        if resp.status_code == 200:
                            status = resp.json().get("status")
                            if status == "ready":
                                logger.info(f"✅ WebGPU Ready détecté pour {user_id}. Poursuite du flux.")
                                break
                            elif status == "incompatible":
                                logger.warning(f"⚠️ WebGPU Incompatible/Erreur détecté pour {user_id}. Repli CPU immédiat.")
                                break
                            elif status == "unknown" and (time.time() - start_time > 3):
                                logger.warning(f"⚠️ Le pont Edge n'a donné aucun signe de vie après 3s pour {user_id}. Annulation de l'attente.")
                                break
                    except Exception:
                        pass
                    
                    await asyncio.sleep(1)
                else:
                    logger.error(f"❌ Timeout ({timeout}s) atteint en attendant WebGPU pour {user_id}. Repli CPU par défaut.")
        
        return body
