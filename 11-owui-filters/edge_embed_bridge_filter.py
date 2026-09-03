"""
title: Edge Embedding Bridge Filter
author: ECHO Framework
author_url: https://github.com/echo-framework
version: 1.20
description: Composant système interne : Edge Embedding Bridge Filter.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.20: Fast-Failover WebGPU, sécurisation mobile q4 et tenseur sentence_embedding.
# 1.19: Restauration de q4f16 pour test pilote GPU Mali.
# 1.18: Purge des références bge-m3. Fix fallback q4 universel pour GPU mobiles (Android).
# 1.17: Fix compatibilité Android (suppression du setTimeout et de la transparence causant l'invisibilité de l'icône).
# 1.16: Repositionnement du HUD WebGPU (top center) avec animation fluide (transition CSS) et overflow hidden.
# 1.14: Fix HUD WebGPU Mobile (Opacité/Morphing) et standardisation 1024D (Harrier 0.6b).

import asyncio
from pydantic import BaseModel, Field

try:
    from echo_constants import DEFAULT_EDGE_EMBEDDING_TIMEOUT
except ImportError:
    DEFAULT_EDGE_EMBEDDING_TIMEOUT = 180

class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=1, hidden=True, description="Priorité d'exécution (0 = premier).")
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
        # --- RÉCUPÉRATION SÉCURISÉE DES VALVES ---
        enable_edge = self.valves.ENABLE_EDGE_EMBEDDING
        wait_edge = self.valves.WAIT_FOR_EDGE_EMBEDDING
        timeout_edge = self.valves.EDGE_EMBEDDING_TIMEOUT

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
        SCRIPT_VERSION = "1.20"
        
        # --- SYNCHRONISATION DYNAMIQUE DU MODÈLE (CPU -> GPU) ---
        import httpx
        cpu_model = "microsoft/Harrier-OSS-v1-0.6B" # Par défaut
        try:
            with httpx.Client(timeout=1.5) as client:
                resp = client.get("http://echo-embedding:7997/health")
                if resp.status_code in (200, 503):
                    cpu_model = resp.json().get("model", cpu_model)
        except Exception:
            pass

        # Assignation stricte (Harrier-OSS)
        target_repo = "onnx-community/harrier-oss-v1-0.6b-ONNX"
        
        # NOTE: Le target_dtype injecté ici servira de fallback absolu pour Mobile.
        # Sur PC, le JavaScript (via isMobile) basculera automatiquement sur 'fp16'
        # pour exploiter la pleine puissance du GPU.
        target_dtype = "q4"
        hud_title = "Harrier 0.6B"

        js_code = """
        (async function initEdgeEmbedding() {
            const INJECTED_SCRIPT_VERSION = "__INJECTED_SCRIPT_VERSION__";
            const MODEL_CACHE_VERSION = "2.1";
            
            // 1. Verrou Singleton Intelligent & Auto-Reload SPA
            if (window._echoEdgeScriptVersion) {
                if (window._echoEdgeScriptVersion !== INJECTED_SCRIPT_VERSION) {
                    console.warn(`🔄 [ECHO] Mise à jour du script détectée (${window._echoEdgeScriptVersion} -> ${INJECTED_SCRIPT_VERSION}). Hard Reload de la SPA en cours...`);
                    location.reload();
                    return;
                }
                return; // Idempotence respectée
            }
            window._echoEdgeScriptVersion = INJECTED_SCRIPT_VERSION;
            
            const clientId = crypto.randomUUID();
            console.log(`🚀 Initialisation ECHO Edge Bridge | Script v${INJECTED_SCRIPT_VERSION} | Modèle v${MODEL_CACHE_VERSION} | Client: ${clientId}`);

            // GESTION DU CACHE CONDITIONNELLE (Basée sur le Modèle)
            const storedVersion = localStorage.getItem('ECHO_EDGE_MODEL_VERSION');
            if (storedVersion !== MODEL_CACHE_VERSION) {
                console.log(`🔄 Nouveau modèle détecté (${storedVersion} -> ${MODEL_CACHE_VERSION}). Purge du cache...`);
                if (window.caches) {
                    try {
                        await caches.delete('transformers-cache');
                        console.log("🧹 Cache Transformers purgé avec succès.");
                    } catch(e) {
                        console.warn("Impossible de vider le cache:", e);
                    }
                }
                localStorage.setItem('ECHO_EDGE_MODEL_VERSION', MODEL_CACHE_VERSION);
            }

            // FACTORISATION DU FALLBACK
            function sendIncompatibleAndExit(reason) {
                console.warn(`⚠️ ECHO Edge: ${reason}. Fallback CPU immédiat.`);
                try {
                    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const wsUrl = `${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`;
                    const ws = new WebSocket(wsUrl);
                    ws.onopen = () => { ws.send(JSON.stringify({ type: 'incompatible' })); ws.close(); };
                } catch(e) {}
            }

            // HARDWARE CHECK REVISE ET ACTIF
            if (typeof navigator === 'undefined' || !navigator.gpu) {
                sendIncompatibleAndExit("WebGPU non exposé par le navigateur");
                return;
            }

            const adapter = await navigator.gpu.requestAdapter();
            if (!adapter) {
                sendIncompatibleAndExit("Adaptateur WebGPU refusé par le pilote");
                return;
            }

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

            // CREATION DU HUD DISCRET (TOAST)
            const hudId = 'echo-edge-hud-' + Date.now();
            const hud = document.createElement('div');
            hud.id = hudId;
            hud.style.cssText = `
                position: fixed;
                top: 65px;
                left: calc(100vw - 270px);
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
                transition: all 0.6s cubic-bezier(0.4, 0, 0.2, 1);
                overflow: hidden;
                display: flex;
                flex-direction: column;
                gap: 8px;
                cursor: grab;
                user-select: none;
            `;
            
            // Header Row (Title)
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

            headerRow.appendChild(titleBox);
            
            const textRow = document.createElement('div');
            textRow.id = hudId + '-text';
            textRow.style.color = '#94a3b8';
            textRow.style.lineHeight = '1.4';
            textRow.innerHTML = 'Initialisation WSS...';
            
            const progressContainer = document.createElement('div');
            progressContainer.style.cssText = 'width: 100%; height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden; margin-top: 2px;';
            
            const progressBar = document.createElement('div');
            progressBar.id = hudId + '-bar';
            progressBar.style.cssText = 'width: 0%; height: 100%; background: #38bdf8; transition: width 0.1s linear;';
            progressContainer.appendChild(progressBar);
            
            const waitRow = document.createElement('div');
            waitRow.style.color = '#64748b';
            waitRow.style.fontSize = '11px';
            waitRow.style.marginTop = '6px';
            waitRow.style.fontStyle = 'italic';
            waitRow.innerHTML = 'Cette opération peut prendre quelques instants...<br><strong style="color:#38bdf8">Il est fortement recommandé de laisser le modèle charger.</strong>';

            hud.appendChild(headerRow);
            hud.appendChild(textRow);
            hud.appendChild(progressContainer);
            hud.appendChild(waitRow);
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
                if (e.target.tagName.toLowerCase() === 'a') return;
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
                    xOffset = currentX;
                    yOffset = currentY;
                    hud.style.transform = "translate3d(" + currentX + "px, " + currentY + "px, 0)";
                }
            }
            function touchDragStart(e) {
                if (e.touches.length !== 1) return;
                const touch = e.touches[0];
                initialX = touch.clientX - xOffset;
                initialY = touch.clientY - yOffset;
                isDragging = true;
            }
            function touchDrag(e) {
                if (isDragging && e.touches.length === 1) {
                    e.preventDefault();
                    const touch = e.touches[0];
                    currentX = touch.clientX - initialX;
                    currentY = touch.clientY - initialY;
                    xOffset = currentX;
                    yOffset = currentY;
                    hud.style.transform = "translate3d(" + currentX + "px, " + currentY + "px, 0)";
                }
            }
            hud.addEventListener('mousedown', dragStart);
            document.addEventListener('mouseup', dragEnd);
            document.addEventListener('mousemove', drag);
            hud.addEventListener('touchstart', touchDragStart, { passive: false });
            document.addEventListener('touchend', dragEnd);
            document.addEventListener('touchmove', touchDrag, { passive: false });

            // 3. CONNEXION WEBSOCKET & SELF-HEALING
            let wsRef = null;
            let isConnecting = false;
            let isFatalError = false;
            let globalTokenizer = null;
            let globalModel = null;
            let isModelLoaded = false;
            
            // Notification de la visibilité au serveur (Proprioception)
            document.addEventListener("visibilitychange", () => {
                const state = document.hidden ? 'idle' : 'active';
                if (wsRef && wsRef.readyState === WebSocket.OPEN) {
                    wsRef.send(JSON.stringify({ type: 'visibility', state: state }));
                }
                // Reconnexion immédiate si retour sur l'onglet et socket tombée
                if (!document.hidden && (!wsRef || wsRef.readyState === WebSocket.CLOSED)) {
                    connectWebSocket();
                }
            });

            async function connectWebSocket() {
                if (isConnecting || (wsRef && wsRef.readyState === WebSocket.OPEN)) return;
                isConnecting = true;
                
                try {
                    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                    const wsUrl = `${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`;
                    const ws = new WebSocket(wsUrl);
                    wsRef = ws;
                    
                    ws.onopen = async () => {
                        isConnecting = false;
                        console.log("✅ WebSocket ECHO Edge connecté");
                        textRow.innerHTML = 'Préparation Harrier-OSS...';
                        hud.style.borderColor = 'rgba(56, 189, 248, 0.25)';
                        
                        try {
                            const transformers = await import('https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js');
                            transformers.env.allowLocalModels = false;
                            
                            if (!isModelLoaded) {
                                try {
                                    const isMobile = /Mobi|Android/i.test(navigator.userAgent);
                                    const targetRepo = '__TARGET_REPO__';
                                    const targetDtype = isMobile ? '__TARGET_DTYPE__' : 'fp16';
                                    
                                    globalTokenizer = await transformers.AutoTokenizer.from_pretrained(targetRepo);
                                    globalModel = await transformers.AutoModel.from_pretrained(targetRepo, {
                                        device: 'webgpu',
                                        dtype: targetDtype,
                                        progress_callback: (x) => {
                                            if (ws.readyState !== WebSocket.OPEN) return;
                                            if (x.status === 'downloading' || x.status === 'progress') {
                                                const percent = x.total ? Math.round((x.loaded / x.total) * 100) : 0;
                                                textRow.innerHTML = `Chargement modèle: <b>${percent}%</b>`;
                                                progressBar.style.width = `${percent}%`;
                                            }
                                        }
                                    });
                                    isModelLoaded = true;
                                } catch (pipelineErr) {
                                    console.error("💥 Erreur chargement modèle. Purge cache...", pipelineErr);
                                    if (window.caches) await caches.delete('transformers-cache');
                                    throw pipelineErr;
                                }
                            } else {
                                textRow.innerHTML = `Modèle en mémoire: <b>Ready</b>`;
                                progressBar.style.width = `100%`;
                            }
                            
                            if (ws.readyState !== WebSocket.OPEN) return;
                            
                            // Modèle prêt
                            ws.send(JSON.stringify({ type: 'ready' }));
                            if (!document.hidden) ws.send(JSON.stringify({ type: 'visibility', state: 'active' }));
                            
                            // Morphing sécurisé du HUD en icône flottante
                            if (hud.style.width !== '36px') {
                                hud.innerHTML = `
                                    <div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:#38bdf8;">
                                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
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
                                    </div>
                                `;

                                hud.style.width = '36px';
                                hud.style.height = '36px';
                                hud.style.padding = '0';
                                hud.style.borderRadius = '50%';
                                hud.style.background = 'rgba(15, 23, 42, 0.9)';
                                hud.style.border = '1px solid rgba(56, 189, 248, 0.6)';
                                
                                hud.style.top = '12px';
                                hud.style.bottom = 'auto';
                                hud.style.right = 'auto';
                                
                                xOffset = 0;
                                yOffset = 0;
                                hud.style.transform = 'translate3d(0, 0, 0)';
                                
                                const isMobileDevice = /Mobi|Android/i.test(navigator.userAgent);
                                if (isMobileDevice) {
                                    hud.style.left = 'calc(50% + 75px)';
                                    hud.style.opacity = '1';
                                } else {
                                    hud.style.left = 'calc(50% + 90px)';
                                    hud.style.opacity = '0.85';
                                }
                                
                                hud.title = "ECHO Edge WebGPU actif (__HUD_TITLE__)";
                            }
                            
                            ws.onmessage = async (event) => {
                                try {
                                    const payload = JSON.parse(event.data);
                                    if (payload.type === 'embed' && payload.texts && payload.request_id) {
                                        hud.classList.add('echo-gpu-computing');
                                        try {
                                            const texts = Array.isArray(payload.texts) ? payload.texts : [payload.texts];
                                            const inputs = globalTokenizer(texts, { padding: true, truncation: true });
                                            const outputs = await globalModel(inputs);
                                            
                                            const tensor = outputs.sentence_embedding || outputs.last_hidden_state || Object.values(outputs)[0];
                                            const dims = tensor.dims;
                                            const batch_size = dims[0];
                                            
                                            const is3D = dims.length >= 3;
                                            const seq_len = is3D ? dims[1] : 1;
                                            const embed_dim = is3D ? dims[2] : dims[1];
                                            
                                            const embeddings = [];
                                            
                                            for (let i = 0; i < batch_size; i++) {
                                                let vec_start, vec_end;
                                                if (is3D) {
                                                    vec_start = (i * seq_len) * embed_dim;
                                                } else {
                                                    vec_start = i * embed_dim;
                                                }
                                                vec_end = vec_start + embed_dim;
                                                const vector = tensor.data.slice(vec_start, vec_end);
                                                
                                                let sum_sq = 0;
                                                for (let j = 0; j < embed_dim; j++) sum_sq += vector[j] * vector[j];
                                                const norm = Math.sqrt(sum_sq);
                                                
                                                const norm_vec = new Array(embed_dim);
                                                for (let j = 0; j < embed_dim; j++) {
                                                    let val = Number(vector[j]);
                                                    norm_vec[j] = (isNaN(val) ? 0 : val) / (norm || 1);
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
                            console.warn("💥 Erreur d'initialisation WebGPU:", modelError);
                            isFatalError = true;
                            if (ws.readyState === WebSocket.OPEN) {
                                ws.send(JSON.stringify({ type: 'incompatible' }));
                                ws.close();
                            }
                            hud.style.borderColor = 'rgba(239, 68, 68, 0.4)';
                            // Retrait discret car l'appareil est fondamentalement incompatible
                            setTimeout(() => { 
                                hud.style.opacity = '0'; 
                                setTimeout(() => { hud.remove(); }, 300); 
                            }, 2000);
                        }
                    };
                    
                    ws.onerror = (err) => {
                        console.error("🔌 Erreur WebSocket ECHO Edge:", err);
                    };
                    
                    ws.onclose = () => {
                        isConnecting = false;
                        if (isFatalError) return;
                        // Mode Résilience (Self-Healing)
                        console.warn("🔌 Socket fermée. Reconnexion en cours...");
                        hud.classList.remove('echo-gpu-computing');
                        hud.style.borderColor = '#f59e0b'; // Orange
                        setTimeout(connectWebSocket, 5000); // Exponential backoff basique
                    };
                    
                } catch (error) {
                    isConnecting = false;
                    setTimeout(connectWebSocket, 5000);
                }
            }
            
            // Démarrage initial
            connectWebSocket();
        })();
        """.replace("__INJECTED_SCRIPT_VERSION__", SCRIPT_VERSION)

        # Injections dynamiques
        js_code = js_code.replace('__TARGET_REPO__', target_repo)
        js_code = js_code.replace('__TARGET_DTYPE__', target_dtype)
        js_code = js_code.replace('__HUD_TITLE__', hud_title)
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
