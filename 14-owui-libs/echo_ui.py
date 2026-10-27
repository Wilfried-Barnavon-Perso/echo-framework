"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 5.231.17.1
description: Composant système interne : ECHO UI Rendering Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.17.1: Remplacement d'Empreinte tour par Δ Capacité avec inversion de signe (perte en négatif, libération en positif).
# 5.231.15.1: Formatage fixe à 2 décimales dans la capsule, libellés unifiés Empreinte tour et élimination des inversions de signes.
# 5.231.14.2: Neutralisation de la capsule différentielle (style sobre slate-400 et affichage du pourcentage seul).
# 5.231.14.1: Calcul de la croissance du contexte sur promptTokenCount (Option A) et gestion dynamique Gain/Perte de capacité (fix double tiret).
# 5.231.13.2: Suppression de la garde if (!window.echoRenderContextGauge) pour autoriser le Hot-Update direct du renderer JS.


import sys
import orjson as std_json
from typing import Optional, Any, List, Dict
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")

from echo_constants import (
    ECHO_GLOBAL_TENANT_PROJECT_ID,
    ECHO_MAX_CONTEXT_SIZE,
    ECHO_EDGE_ONNX_REPO,
    ECHO_EDGE_HUD_TITLE,
    ECHO_EDGE_MODEL_CACHE_VERSION,
    ECHO_EDGE_DTYPE,
    ECHO_EDGE_MIN_BUFFER_BYTES,
)


class EchoRichUI:
    """Usine de rendu de composants visuels riches pour ECHO."""

    ECHO_ICONS = {
        "X": "<line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/>",
        "Minus": "<line x1='5' y1='12' x2='19' y2='12'/>",
        "Maximize": "<path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/>",
        "Eye": "<path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/>",
        "Edit": "<path d='M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z'/>",
        "Trash": "<polyline points='3 6 5 6 21 6'/><path d='M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2'/>",
        "ChevronLeft": "<polyline points='15 18 9 12 15 6'/>",
        "ChevronRight": "<polyline points='9 18 15 12 9 6'/>",
        "Download": "<path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='7 10 12 15 17 10'/><line x1='12' y1='15' x2='12' y2='3'/>",
        "Copy": "<rect x='9' y='9' width='13' height='13' rx='2' ry='2'/><path d='M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'/>",
        "Present": "<path d='M6 8l4 4-4 4M18 8l-4 4 4 4M12 4v16'/>",
        "CornerUpLeft": "<polyline points='9 14 4 9 9 4'/><path d='M20 20v-7a4 4 0 0 0-4-4H4'/>",
        "RotateCcw": "<polyline points='1 4 1 10 7 10'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/>",
        "SkipBack": "<polygon points='19 20 9 12 19 4 19 20'/><line x1='5' y1='19' x2='5' y2='5'/>",
        "SkipForward": "<polygon points='5 4 15 12 5 20 5 4'/><line x1='19' y1='5' x2='19' y2='19'/>",
        "Play": "<polygon points='5 3 19 12 5 21 5 3'/>",
        "Rewind": "<polygon points='11 19 2 12 11 5 11 19'/><polygon points='22 19 13 12 22 5 22 19'/>",
        "Pause": "<rect x='6' y='4' width='4' height='16'/><rect x='14' y='4' width='4' height='16'/>",
        "Crop": "<path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/>",
        "Cpu": "<rect x='4' y='4' width='16' height='16' rx='2' ry='2'/><rect x='9' y='9' width='6' height='6'/><line x1='9' y1='1' x2='9' y2='4'/><line x1='15' y1='1' x2='15' y2='4'/><line x1='9' y1='20' x2='9' y2='23'/><line x1='15' y1='20' x2='15' y2='23'/><line x1='20' y1='9' x2='23' y2='9'/><line x1='20' y1='14' x2='23' y2='14'/><line x1='1' y1='9' x2='4' y2='9'/><line x1='1' y1='14' x2='4' y2='14'/>"
    }

    @staticmethod
    def get_icon(name: str, size: int = 16, color: str = "currentColor", stroke_width: int = 2, css_class: str = "") -> str:
        """Générateur SVG universel."""
        path = EchoRichUI.ECHO_ICONS.get(name, "")
        cls = f" class='{css_class}'" if css_class else ""
        return f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='{stroke_width}' stroke-linecap='round' stroke-linejoin='round'{cls}>{path}</svg>"

    @staticmethod
    def get_esm_loader_js() -> str:
        """Génère le chargeur ESM universel isolé en <script type='module' crossorigin='anonymous'> (immunisé contre crossorigin='use-credentials')."""
        return """
        if (!window.echoImportModule) {
            window.echoImportModule = function(url) {
                return new Promise((resolve, reject) => {
                    const cbId = '_echoModCb_' + Math.random().toString(36).slice(2) + '_' + Date.now();
                    const s = document.createElement('script');
                    s.type = 'module';
                    s.crossOrigin = 'anonymous';
                    window[cbId] = function(mod, err) {
                        delete window[cbId];
                        try { s.remove(); } catch (e) {}
                        if (err) reject(err);
                        else resolve(mod);
                    };
                    s.textContent = (
                        'import(' + JSON.stringify(String(url)) + ').then('
                        + 'm => window.' + cbId + ' && window.' + cbId + '(m, null), '
                        + 'e => window.' + cbId + ' && window.' + cbId + '(null, e));'
                    );
                    s.onerror = function() {
                        if (window[cbId]) window[cbId](null, new Error('Failed to load module script: ' + url));
                    };
                    (document.head || document.documentElement).appendChild(s);
                });
            };
        }
        """

    @staticmethod
    def get_webgpu_probe_js() -> str:
        """Génère la sonde matérielle et fonctionnelle WebGPU factorisée (window.echoProbeWebGPU)."""
        return """
        if (!window.echoProbeWebGPU) {
            window.echoProbeWebGPU = async function(opts) {
                opts = opts || {};
                const requireFp16 = opts.requireFp16 !== false;
                const minBufferBytes = opts.minBufferBytes || 134217728;
                const timeoutMs = opts.timeoutMs || 4000;
                const cacheKey = (requireFp16 ? 'fp16' : 'fp32') + '_' + minBufferBytes;

                if (window._echoWebGPUProbeResult && window._echoWebGPUProbeResult.key === cacheKey) {
                    return window._echoWebGPUProbeResult.result;
                }

                const runProbe = async () => {
                    if (typeof navigator === 'undefined' || !navigator.gpu) {
                        return { compatible: false, reason: 'WebGPU non exposé par le navigateur' };
                    }

                    let adapter = null;
                    try {
                        adapter = await navigator.gpu.requestAdapter({ powerPreference: 'high-performance' });
                    } catch (e) {
                        return { compatible: false, reason: 'Erreur lors de requestAdapter WebGPU' };
                    }

                    if (!adapter) {
                        return { compatible: false, reason: 'Adaptateur WebGPU refusé par le pilote' };
                    }
                    if (adapter.isFallbackAdapter || (adapter.info && adapter.info.isFallbackAdapter)) {
                        return { compatible: false, reason: 'Adaptateur WebGPU logiciel (Fallback) rejeté' };
                    }
                    if (requireFp16 && (!adapter.features || !adapter.features.has('shader-f16'))) {
                        return { compatible: false, reason: 'Extension matérielle shader-f16 non supportée par le GPU' };
                    }

                    const limits = adapter.limits || {};
                    if (
                        (limits.maxBufferSize && limits.maxBufferSize < minBufferBytes) ||
                        (limits.maxStorageBufferBindingSize && limits.maxStorageBufferBindingSize < minBufferBytes)
                    ) {
                        return { compatible: false, reason: 'Limites de buffer VRAM insuffisantes (< 128 Mo)' };
                    }

                    let device = null;
                    let testBuffer = null;
                    try {
                        const reqFeatures = requireFp16 ? ['shader-f16'] : [];
                        device = await adapter.requestDevice({ requiredFeatures: reqFeatures });
                        if (!device) {
                            return { compatible: false, reason: 'Allocation du GPUDevice refusée' };
                        }

                        device.pushErrorScope('validation');
                        device.pushErrorScope('out-of-memory');

                        const wgsl = requireFp16
                            ? 'enable f16;\\n@group(0) @binding(0) var<storage, read_write> data: array<f16>;\\n@compute @workgroup_size(1) fn main() { data[0] = f16(1.0) + f16(1.0); }'
                            : '@group(0) @binding(0) var<storage, read_write> data: array<f32>;\\n@compute @workgroup_size(1) fn main() { data[0] = 1.0 + 1.0; }';

                        const shaderModule = device.createShaderModule({ code: wgsl });
                        const pipeline = await device.createComputePipelineAsync({
                            layout: 'auto',
                            compute: { module: shaderModule, entryPoint: 'main' }
                        });

                        testBuffer = device.createBuffer({
                            size: 16,
                            usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC
                        });

                        const bindGroup = device.createBindGroup({
                            layout: pipeline.getBindGroupLayout(0),
                            entries: [{ binding: 0, resource: { buffer: testBuffer } }]
                        });

                        const commandEncoder = device.createCommandEncoder();
                        const passEncoder = commandEncoder.beginComputePass();
                        passEncoder.setPipeline(pipeline);
                        passEncoder.setBindGroup(0, bindGroup);
                        passEncoder.dispatchWorkgroups(1);
                        passEncoder.end();
                        device.queue.submit([commandEncoder.finish()]);
                        await device.queue.onSubmittedWorkDone();

                        const oomErr = await device.popErrorScope();
                        const valErr = await device.popErrorScope();
                        if (oomErr || valErr) {
                            const errMsg = (oomErr && oomErr.message) || (valErr && valErr.message) || 'Erreur GPU Compute';
                            return { compatible: false, reason: 'Échec du Compute Smoke Test (' + errMsg + ')' };
                        }
                        return { compatible: true, reason: 'OK' };
                    } catch (gpuErr) {
                        return {
                            compatible: false,
                            reason: 'Échec Compute WebGPU (' + (gpuErr && gpuErr.message ? gpuErr.message : String(gpuErr)) + ')'
                        };
                    } finally {
                        try { if (testBuffer) testBuffer.destroy(); } catch (e) {}
                        try { if (device) device.destroy(); } catch (e) {}
                    }
                };

                let result;
                try {
                    result = await Promise.race([
                        runProbe(),
                        new Promise(resolve => setTimeout(() => resolve({
                            compatible: false,
                            reason: 'Timeout lors du test matériel WebGPU'
                        }), timeoutMs))
                    ]);
                } catch (err) {
                    result = { compatible: false, reason: 'Exception sonde WebGPU' };
                }

                window._echoWebGPUProbeResult = { key: cacheKey, result: result };
                return result;
            };
        }
        """

    @staticmethod
    def get_edge_embedding_bridge_js(
        script_version: str,
        target_repo: str = ECHO_EDGE_ONNX_REPO,
        hud_title: str = ECHO_EDGE_HUD_TITLE,
        model_cache_version: str = ECHO_EDGE_MODEL_CACHE_VERSION,
        target_dtype: str = ECHO_EDGE_DTYPE,
        min_buffer_bytes: int = ECHO_EDGE_MIN_BUFFER_BYTES,
    ) -> str:
        """Génère le pont JS d'offload Edge Embedding avec validation matérielle WebGPU stricte en amont."""
        cpu_icon_16 = EchoRichUI.get_icon("Cpu", size=16)
        cpu_icon_18 = EchoRichUI.get_icon("Cpu", size=18)
        esm_js = EchoRichUI.get_esm_loader_js()
        probe_js = EchoRichUI.get_webgpu_probe_js()
        geo_js = EchoRichUI.get_hud_geometry_js()

        bridge_template = """
        (async function initEdgeEmbedding() {
            const INJECTED_SCRIPT_VERSION = "__INJECTED_SCRIPT_VERSION__";
            const MODEL_CACHE_VERSION = "__MODEL_CACHE_VERSION__";
            const TARGET_REPO = "__TARGET_REPO__";
            const TARGET_DTYPE = "__TARGET_DTYPE__";
            const MIN_BUFFER_BYTES = __MIN_BUFFER_BYTES__;
            const HUD_TITLE = "__HUD_TITLE__";

            // 1. Verrou Singleton Intelligent & Auto-Reload SPA
            if (window._echoEdgeScriptVersion) {
                if (window._echoEdgeScriptVersion !== INJECTED_SCRIPT_VERSION) {
                    console.warn(`🔄 [ECHO] Mise à jour du script détectée (${window._echoEdgeScriptVersion} -> ${INJECTED_SCRIPT_VERSION}). Hard Reload de la SPA en cours...`);
                    location.reload();
                    return;
                }
                return;
            }
            window._echoEdgeScriptVersion = INJECTED_SCRIPT_VERSION;

            const clientId = crypto.randomUUID();
            let isFatalError = false;

            // Fonction factorisée unique d'interruption et de repli CPU
            function reportIncompatibleAndAbort(reason, activeWs, hudEl) {
                isFatalError = true;
                window._echoEdgeIncompatible = true;
                console.warn(`⚠️ ECHO Edge: ${reason}. Interruption de la suite et Fallback CPU immédiat.`);
                if (hudEl && hudEl.isConnected) {
                    hudEl.style.borderColor = 'rgba(239, 68, 68, 0.4)';
                    setTimeout(() => {
                        hudEl.style.opacity = '0';
                        setTimeout(() => { try { hudEl.remove(); } catch (e) {} }, 300);
                    }, 1500);
                }
                try {
                    const payload = JSON.stringify({ type: 'incompatible', reason: reason });
                    if (activeWs && activeWs.readyState === WebSocket.OPEN) {
                        activeWs.send(payload);
                        setTimeout(() => { try { activeWs.close(); } catch (e) {} }, 150);
                    } else {
                        const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
                        const wsUrl = `${protocol}//${location.host}/ws/edge-embed?client_id=${clientId}`;
                        const ws = new WebSocket(wsUrl);
                        ws.onopen = () => {
                            ws.send(payload);
                            setTimeout(() => { try { ws.close(); } catch (e) {} }, 150);
                        };
                    }
                } catch (e) {}
            }

            // 2. TEST DE COMPATIBILITÉ WEBGPU EN AMONT (avant cache, HUD, WSS et téléchargement modèle)
            const probe = await window.echoProbeWebGPU({
                requireFp16: TARGET_DTYPE === 'fp16',
                minBufferBytes: MIN_BUFFER_BYTES
            });
            if (!probe || !probe.compatible) {
                reportIncompatibleAndAbort((probe && probe.reason) || "Matériel WebGPU non compatible", null, null);
                return;
            }

            console.log(`🚀 Initialisation ECHO Edge Bridge | Script v${INJECTED_SCRIPT_VERSION} | Modèle v${MODEL_CACHE_VERSION} | Client: ${clientId}`);

            // 3. GESTION DU CACHE CONDITIONNELLE (exécutée uniquement si le GPU est compatible)
            const storedVersion = localStorage.getItem('ECHO_EDGE_MODEL_VERSION');
            if (storedVersion !== MODEL_CACHE_VERSION) {
                console.log(`🔄 Nouveau modèle détecté (${storedVersion} -> ${MODEL_CACHE_VERSION}). Purge du cache...`);
                if (window.caches) {
                    try {
                        await caches.delete('transformers-cache');
                        console.log("🧹 Cache Transformers purgé avec succès.");
                    } catch (e) {
                        console.warn("Impossible de vider le cache:", e);
                    }
                }
                localStorage.setItem('ECHO_EDGE_MODEL_VERSION', MODEL_CACHE_VERSION);
            }

            // 4. INJECTION CSS & CRÉATION DU HUD (factorisé avec EchoUI)
            const styleId = 'echo-edge-styles';
            if (!document.getElementById(styleId)) {
                const style = document.createElement('style');
                style.id = styleId;
                style.innerHTML = `
                    @keyframes echoComputePulse {
                        0% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0.7); scale: 0.95; }
                        70% { box-shadow: 0 0 0 8px rgba(56, 189, 248, 0); scale: 1; }
                        100% { box-shadow: 0 0 0 0 rgba(56, 189, 248, 0); scale: 0.95; }
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

            const hudId = 'echo-edge-hud-' + Date.now();
            const hud = document.createElement('div');
            hud.id = hudId;
            hud.setAttribute('data-echo-scope', 'global');
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
                transition: width 0.4s ease, height 0.4s ease, border-radius 0.4s ease, opacity 0.4s ease;
                overflow: hidden;
                display: flex;
                flex-direction: column;
                gap: 8px;
                cursor: grab;
                user-select: none;
            `;

            const headerRow = document.createElement('div');
            headerRow.style.cssText = 'display: flex; justify-content: space-between; align-items: center; width: 100%;';

            const titleBox = document.createElement('div');
            titleBox.style.cssText = 'display: flex; align-items: center; gap: 8px; font-weight: 600; color: #38bdf8;';
            titleBox.innerHTML = `__CPU_ICON_16__<span>ECHO VRAM Loader</span>`;
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

            const hudGeo = window.echoBindHudGeometry(hud, hud, {
                defaultW: '250px',
                defaultH: 'auto',
                defaultX: Math.max(10, (window.innerWidth || 1280) - 270) + 'px',
                defaultY: '65px',
                defaultTx: 'none',
                minW: '36px',
                minH: '36px',
                allowResize: false
            });

            // 5. CONNEXION WEBSOCKET & CHARGEMENT DU MODÈLE
            let wsRef = null;
            let isConnecting = false;
            let globalTokenizer = null;
            let globalModel = null;
            let isModelLoaded = false;

            document.addEventListener("visibilitychange", () => {
                if (isFatalError || window._echoEdgeIncompatible) return;
                const state = document.hidden ? 'idle' : 'active';
                if (wsRef && wsRef.readyState === WebSocket.OPEN) {
                    wsRef.send(JSON.stringify({ type: 'visibility', state: state }));
                }
                if (!document.hidden && (!wsRef || wsRef.readyState === WebSocket.CLOSED)) {
                    connectWebSocket();
                }
            });

            async function connectWebSocket() {
                if (isFatalError || window._echoEdgeIncompatible || isConnecting || (wsRef && wsRef.readyState === WebSocket.OPEN)) return;
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
                            const tfUrl = 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3/dist/transformers.min.js';
                            const transformers = await (window.echoImportModule ? window.echoImportModule(tfUrl) : import(tfUrl));
                            transformers.env.allowLocalModels = false;

                            if (!isModelLoaded) {
                                globalTokenizer = await transformers.AutoTokenizer.from_pretrained(TARGET_REPO);
                                globalModel = await transformers.AutoModel.from_pretrained(TARGET_REPO, {
                                    device: 'webgpu',
                                    dtype: TARGET_DTYPE,
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
                            } else {
                                textRow.innerHTML = `Modèle en mémoire: <b>Ready</b>`;
                                progressBar.style.width = `100%`;
                            }

                            if (ws.readyState !== WebSocket.OPEN) return;

                            ws.send(JSON.stringify({ type: 'ready' }));
                            if (!document.hidden) ws.send(JSON.stringify({ type: 'visibility', state: 'active' }));

                            if (hud.style.width !== '36px') {
                                hud.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;width:100%;height:100%;color:#38bdf8;">__CPU_ICON_18__</div>`;
                                hud.style.width = '36px';
                                hud.style.height = '36px';
                                hud.style.padding = '0';
                                hud.style.borderRadius = '50%';
                                hud.style.background = 'rgba(15, 23, 42, 0.9)';
                                hud.style.border = '1px solid rgba(56, 189, 248, 0.6)';
                                hud.style.top = '12px';
                                hud.style.bottom = 'auto';
                                hud.style.right = 'auto';
                                hud.style.left = 'calc(50% + 125px)';
                                hud.style.transform = 'none';
                                hud.style.opacity = '0.85';
                                hud.title = `ECHO Edge WebGPU actif (${HUD_TITLE})`;
                                if (hudGeo && hudGeo.normalBounds) {
                                    hudGeo.normalBounds.w = '36px';
                                    hudGeo.normalBounds.h = '36px';
                                    hudGeo.normalBounds.x = 'calc(50% + 125px)';
                                    hudGeo.normalBounds.y = '12px';
                                    hudGeo.normalBounds.tx = 'none';
                                }
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
                                                const vec_start = is3D ? (i * seq_len) * embed_dim : i * embed_dim;
                                                const vec_end = vec_start + embed_dim;
                                                const vector = tensor.data.slice(vec_start, vec_end);

                                                let sum_sq = 0;
                                                for (let j = 0; j < embed_dim; j++) sum_sq += vector[j] * vector[j];
                                                const norm = Math.sqrt(sum_sq);

                                                const norm_vec = new Array(embed_dim);
                                                for (let j = 0; j < embed_dim; j++) {
                                                    const val = Number(vector[j]);
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
                                } catch (err) {
                                    console.error("❌ Erreur inférence Edge:", err);
                                }
                            };
                        } catch (modelError) {
                            reportIncompatibleAndAbort(
                                modelError && modelError.message ? modelError.message : String(modelError),
                                ws,
                                hud
                            );
                        }
                    };

                    ws.onerror = (err) => {
                        console.error("🔌 Erreur WebSocket ECHO Edge:", err);
                    };

                    ws.onclose = () => {
                        isConnecting = false;
                        if (isFatalError || window._echoEdgeIncompatible) return;
                        console.warn("🔌 Socket fermée. Reconnexion en cours...");
                        hud.classList.remove('echo-gpu-computing');
                        hud.style.borderColor = '#f59e0b';
                        setTimeout(connectWebSocket, 5000);
                    };
                } catch (error) {
                    isConnecting = false;
                    if (!isFatalError && !window._echoEdgeIncompatible) {
                        setTimeout(connectWebSocket, 5000);
                    }
                }
            }

            connectWebSocket();
        })();
        """

        bridge_js = (
            bridge_template
            .replace("__INJECTED_SCRIPT_VERSION__", str(script_version))
            .replace("__MODEL_CACHE_VERSION__", str(model_cache_version))
            .replace("__TARGET_REPO__", str(target_repo))
            .replace("__TARGET_DTYPE__", str(target_dtype))
            .replace("__MIN_BUFFER_BYTES__", str(int(min_buffer_bytes)))
            .replace("__HUD_TITLE__", str(hud_title))
            .replace("__CPU_ICON_16__", cpu_icon_16)
            .replace("__CPU_ICON_18__", cpu_icon_18)
        )
        return esm_js + "\n" + probe_js + "\n" + geo_js + "\n" + bridge_js

    @staticmethod
    def get_hud_geometry_js() -> str:
        """Génère le contrôleur géométrique universel (Drag SVG-safe, Clamping Viewport, Minimize, Fullscreen, ResizeObserver filtré)."""
        return """
        {
            window.echoBindHudGeometry = function(hud, header, opts) {
                if (header && !opts && (typeof header === 'object') && !header.nodeType) {
                    opts = header;
                    header = null;
                }
                opts = opts || {};
                if (header && typeof header.setAttribute === 'function') header.setAttribute('data-echo-hud-header', '1');
                if (typeof document !== 'undefined' && document.head && !document.getElementById('echo-hud-geometry-style')) {
                    const st = document.createElement('style');
                    st.id = 'echo-hud-geometry-style';
                    st.textContent = '[data-echo-minimized="1"] > :not([data-echo-hud-header="1"]) { display: none !important; }';
                    document.head.appendChild(st);
                }
                const defaultW = opts.defaultW || '700px';
                const defaultH = opts.defaultH || '500px';
                const defaultX = opts.defaultX !== undefined ? opts.defaultX : '50%';
                const defaultY = opts.defaultY !== undefined ? opts.defaultY : '60px';
                const defaultTx = opts.defaultTx !== undefined ? opts.defaultTx : (defaultX === '50%' ? 'translateX(-50%)' : 'none');
                const minW = opts.minW || '200px';
                const minH = opts.minH || '100px';
                const minCollapsedH = opts.minimizedH || 'auto';
                const minCollapsedMinH = opts.minimizedMinH || '0';
                const fsW = opts.fsW || '95vw';
                const fsH = opts.fsH || '95vh';
                const fsX = opts.fsX !== undefined ? opts.fsX : '2.5vw';
                const fsY = opts.fsY !== undefined ? opts.fsY : '2.5vh';
                const allowResize = opts.allowResize !== false;

                const isInvalidDim = (v) => !v || v === 'auto' || v === '42px' || v === '0px' || v === '95vw' || v === '95vh' || v === '100vw' || v === '100vh' || (typeof v === 'string' && v.endsWith('px') && parseFloat(v) < parseFloat(minH));
                const isInvalidW = (v) => !v || v === 'auto' || v === '95vw' || v === '100vw' || (typeof v === 'string' && v.endsWith('px') && parseFloat(v) < parseFloat(minW));
                const isGlobalHud = Boolean(hud && hud.getAttribute && hud.getAttribute('data-echo-scope') === 'global');
                const wmBounds = (!isGlobalHud && window.EchoWM && typeof window.EchoWM.loadGeometry === 'function' && hud && hud.id) ? (window.EchoWM.loadGeometry(hud) || {}) : {};
                const init = Object.assign({}, wmBounds, opts.initialBounds || {});
                const fmtCoord = (v, def) => (v !== undefined && v !== null && v !== '') ? (typeof v === 'number' ? v + 'px' : String(v)) : def;
                const hasSavedX = init.x !== undefined && init.x !== null && init.x !== '';

                const normalBounds = {
                    w: isInvalidW(init.w) ? defaultW : String(init.w),
                    h: isInvalidDim(init.h) ? defaultH : String(init.h),
                    x: fmtCoord(init.x, defaultX),
                    y: fmtCoord(init.y, defaultY),
                    tx: hasSavedX ? (String(init.x) === '50%' ? 'translateX(-50%)' : 'none') : defaultTx
                };

                const ctrl = {
                    isMinimized: false,
                    isFullscreen: false,
                    isDragging: false,
                    normalBounds: normalBounds,
                    applyNormalBounds: function() {
                        if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                        hud.style.maxWidth = '98vw';
                        hud.style.maxHeight = '96vh';
                        hud.style.minWidth = minW;
                        hud.style.minHeight = minH;
                        hud.style.width = normalBounds.w;
                        hud.style.height = normalBounds.h;
                        hud.style.left = normalBounds.x;
                        hud.style.top = normalBounds.y;
                        hud.style.transform = normalBounds.tx || 'none';
                        if (allowResize && !opts.customResizer) hud.style.resize = 'both';
                    },
                    captureNormalBounds: function() {
                        if (ctrl.isMinimized || ctrl.isFullscreen) return;
                        if (hud.offsetWidth >= parseFloat(minW)) normalBounds.w = hud.offsetWidth + 'px';
                        else if (!isInvalidW(hud.style.width)) normalBounds.w = hud.style.width;
                        if (hud.offsetHeight >= parseFloat(minH)) normalBounds.h = hud.offsetHeight + 'px';
                        else if (!isInvalidDim(hud.style.height)) normalBounds.h = hud.style.height;
                        if (hud.style.left) normalBounds.x = hud.style.left;
                        if (hud.style.top) normalBounds.y = hud.style.top;
                        normalBounds.tx = hud.style.transform || 'none';
                    },
                    clampToViewport: function() {
                        if (ctrl.isFullscreen || !hud.isConnected) return;
                        if (hud.style.transform && hud.style.transform.includes('translateX')) return;
                        const vw = window.innerWidth || 1280;
                        const vh = window.innerHeight || 800;
                        const w = hud.offsetWidth || parseFloat(normalBounds.w) || 600;
                        const maxX = Math.max(0, vw - Math.min(w, vw));
                        const maxY = Math.max(0, vh - 44);
                        let curX = parseFloat(hud.style.left);
                        let curY = parseFloat(hud.style.top);
                        if (!isNaN(curX)) {
                            curX = Math.max(0, Math.min(curX, maxX));
                            hud.style.left = curX + 'px';
                            normalBounds.x = hud.style.left;
                        }
                        if (!isNaN(curY)) {
                            curY = Math.max(0, Math.min(curY, maxY));
                            hud.style.top = curY + 'px';
                            normalBounds.y = hud.style.top;
                        }
                    },
                    save: function() {
                        ctrl.captureNormalBounds();
                        if (typeof opts.onSave === 'function') opts.onSave(normalBounds, ctrl);
                        if (!isGlobalHud && window.EchoWM && typeof window.EchoWM.saveGeometry === 'function' && hud && hud.id) window.EchoWM.saveGeometry(hud, normalBounds, ctrl);
                    },
                    close: function() {
                        if (ctrl.isMinimized) {
                            ctrl.isMinimized = false;
                            if (!isGlobalHud && window.EchoWM && typeof window.EchoWM.saveGeometry === 'function' && hud && hud.id) {
                                window.EchoWM.saveGeometry(hud, normalBounds, ctrl);
                            }
                        }
                        if (typeof opts.onClose === 'function') opts.onClose(ctrl);
                    },
                    toggleMinimize: function() {
                        if (!ctrl.isMinimized) {
                            ctrl.captureNormalBounds();
                            ctrl.isMinimized = true;
                            if (typeof hud.setAttribute === 'function') hud.setAttribute('data-echo-minimized', '1');
                            hud.style.resize = 'none';
                            hud.style.minHeight = minCollapsedMinH;
                            hud.style.height = minCollapsedH;
                        } else {
                            ctrl.isMinimized = false;
                            if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                            if (ctrl.isFullscreen) {
                                hud.style.maxWidth = '100vw';
                                hud.style.maxHeight = '100vh';
                                hud.style.minHeight = minH;
                                hud.style.width = fsW;
                                hud.style.height = fsH;
                                hud.style.resize = 'none';
                            } else {
                                hud.style.maxWidth = '98vw';
                                hud.style.maxHeight = '96vh';
                                hud.style.minHeight = minH;
                                hud.style.width = normalBounds.w;
                                hud.style.height = normalBounds.h;
                                if (allowResize && !opts.customResizer) hud.style.resize = 'both';
                                ctrl.clampToViewport();
                            }
                        }
                        if (typeof opts.onMinimize === 'function') opts.onMinimize(ctrl.isMinimized, ctrl);
                        ctrl.save();
                        return ctrl.isMinimized;
                    },
                    toggleFullscreen: function() {
                        if (ctrl.isMinimized) {
                            ctrl.isMinimized = false;
                            if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                            if (typeof opts.onMinimize === 'function') opts.onMinimize(false, ctrl);
                        }
                        if (!ctrl.isFullscreen) {
                            ctrl.captureNormalBounds();
                            ctrl.isFullscreen = true;
                            hud.style.maxWidth = '100vw';
                            hud.style.maxHeight = '100vh';
                            hud.style.minHeight = minH;
                            hud.style.width = fsW;
                            hud.style.height = fsH;
                            hud.style.left = fsX;
                            hud.style.top = fsY;
                            hud.style.transform = 'none';
                            hud.style.resize = 'none';
                        } else {
                            ctrl.isFullscreen = false;
                            ctrl.applyNormalBounds();
                            ctrl.clampToViewport();
                        }
                        if (typeof opts.onFullscreen === 'function') opts.onFullscreen(ctrl.isFullscreen, ctrl);
                        ctrl.save();
                        return ctrl.isFullscreen;
                    },
                    resetSize: function() {
                        ctrl.isFullscreen = false;
                        ctrl.isMinimized = false;
                        if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                        normalBounds.w = defaultW;
                        normalBounds.h = defaultH;
                        ctrl.applyNormalBounds();
                        if (typeof opts.onMinimize === 'function') opts.onMinimize(false, ctrl);
                        if (typeof opts.onFullscreen === 'function') opts.onFullscreen(false, ctrl);
                        ctrl.save();
                    }
                };

                const savedIsMin = Boolean(init && init.isMinimized);
                const savedIsFs = Boolean(init && init.isFullscreen);

                if (savedIsMin) {
                    ctrl.isMinimized = true;
                    if (typeof hud.setAttribute === 'function') hud.setAttribute('data-echo-minimized', '1');
                    hud.style.resize = 'none';
                    hud.style.minHeight = minCollapsedMinH;
                    hud.style.height = minCollapsedH;
                    if (typeof opts.onMinimize === 'function') opts.onMinimize(true, ctrl);
                } else if (savedIsFs) {
                    if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                    ctrl.isFullscreen = true;
                    hud.style.maxWidth = '100vw';
                    hud.style.maxHeight = '100vh';
                    hud.style.minHeight = minH;
                    hud.style.width = fsW;
                    hud.style.height = fsH;
                    hud.style.left = fsX;
                    hud.style.top = fsY;
                    hud.style.transform = 'none';
                    hud.style.resize = 'none';
                    if (typeof opts.onFullscreen === 'function') opts.onFullscreen(true, ctrl);
                } else {
                    if (typeof hud.removeAttribute === 'function') hud.removeAttribute('data-echo-minimized');
                    ctrl.applyNormalBounds();
                    ctrl.clampToViewport();
                    if (typeof opts.onMinimize === 'function') opts.onMinimize(false, ctrl);
                    if (typeof opts.onFullscreen === 'function') opts.onFullscreen(false, ctrl);
                }

                if (header) {
                    header.onmousedown = function(e) {
                        if (e.target && e.target.closest && e.target.closest('button, input, select, textarea, a, .no-drag')) return;
                        if (e.target && (e.target.tagName === 'BUTTON' || e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT')) return;
                        if (ctrl.isFullscreen) return;
                        if (e.preventDefault) e.preventDefault();
                        if (hud.style.transform && hud.style.transform !== 'none') {
                            const rect = hud.getBoundingClientRect ? hud.getBoundingClientRect() : null;
                            const realLeft = (rect && rect.width > 0) ? rect.left : hud.offsetLeft;
                            const realTop = (rect && rect.height > 0) ? rect.top : hud.offsetTop;
                            hud.style.transform = 'none';
                            hud.style.left = realLeft + 'px';
                            hud.style.top = realTop + 'px';
                            normalBounds.tx = 'none';
                            normalBounds.x = hud.style.left;
                            normalBounds.y = hud.style.top;
                        }
                        ctrl.isDragging = true;
                        const startLeft = parseFloat(hud.style.left) || hud.offsetLeft || 0;
                        const startTop = parseFloat(hud.style.top) || hud.offsetTop || 0;
                        const dragOffsetX = e.clientX - startLeft;
                        const dragOffsetY = e.clientY - startTop;
                        hud.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = 'none');

                        const onMove = function(me) {
                            if (!ctrl.isDragging) return;
                            const vw = window.innerWidth || 1280;
                            const vh = window.innerHeight || 800;
                            const maxX = Math.max(0, vw - (hud.offsetWidth || 200));
                            const maxY = Math.max(0, vh - 44);
                            const newX = Math.max(0, Math.min(me.clientX - dragOffsetX, maxX));
                            const newY = Math.max(0, Math.min(me.clientY - dragOffsetY, maxY));
                            hud.style.left = newX + 'px';
                            hud.style.top = newY + 'px';
                            normalBounds.x = hud.style.left;
                            normalBounds.y = hud.style.top;
                            normalBounds.tx = 'none';
                        };
                        const onUp = function() {
                            document.removeEventListener('mousemove', onMove);
                            document.removeEventListener('mouseup', onUp);
                            if (ctrl.isDragging) {
                                ctrl.isDragging = false;
                                hud.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = '');
                                ctrl.save();
                            }
                        };
                        document.addEventListener('mousemove', onMove);
                        document.addEventListener('mouseup', onUp);
                    };
                }

                if (typeof ResizeObserver !== 'undefined') {
                    new ResizeObserver(function() {
                        if (!ctrl.isMinimized && !ctrl.isFullscreen && !ctrl.isDragging) {
                            const w = hud.style.width;
                            const h = hud.style.height;
                            if (!isInvalidW(w)) normalBounds.w = w;
                            if (!isInvalidDim(h)) normalBounds.h = h;
                            if (typeof opts.onSave === 'function') opts.onSave(normalBounds, ctrl);
                            if (!isGlobalHud && window.EchoWM && typeof window.EchoWM.saveGeometry === 'function' && hud && hud.id) window.EchoWM.saveGeometry(hud, normalBounds, ctrl);
                        }
                    }).observe(hud);
                }

                window.addEventListener('resize', function() {
                    ctrl.clampToViewport();
                });

                return ctrl;
            };
        }
        """

    @staticmethod
    def get_hud_rpc_bridge_js() -> str:
        """Génère le pont RPC résilient universel (file d'attente, heartbeat 5s/25s anti-timeout 60s, reconnexion post-standby et garde-fou SPA)."""
        return """
        {
            window.echoCreateHudRpcBridge = function(cfg) {
                const hudId = cfg.hudId;
                const actionId = cfg.actionId;
                const resolveVar = cfg.resolveVar;
                const sendFn = cfg.sendFn;
                const readyFn = cfg.readyFn;
                const cid = cfg.cid || '';
                const mid = cfg.mid || '';
                const modelId = cfg.modelId || 'echo_orchestrator';
                const prePingActions = cfg.prePingActions || [];
                const noWatchdogActions = cfg.noWatchdogActions || ['close'];
                const visiblePingMs = cfg.visiblePingMs || 25000;
                const hiddenPingMs = cfg.hiddenPingMs || 25000;
                const watchdogMs = cfg.watchdogMs || 8000;

                let actionQueue = [];
                let isReconnecting = false;
                let pendingReconnectUi = false;
                let inFlightPayload = null;
                let inFlightWatchdog = null;
                let waitResolveTimer = null;
                let lastReadyTs = Date.now();
                let lastPingTs = 0;

                function isOwnedHudAlive() {
                    const el = document.getElementById(hudId);
                    if (!el) return false;
                    const elCid = (window.EchoWM && typeof window.EchoWM.getElementChatId === 'function')
                        ? window.EchoWM.getElementChatId(el)
                        : (el.getAttribute('data-echo-chat-id') || el.getAttribute('data-chat-id') || '');
                    if (cid && elCid && elCid !== cid) return false;
                    return true;
                }

                if (window.EchoWM && window.EchoWM.isReplaying) {
                    window[resolveVar] = null;
                }

                async function reconnectBackend() {
                    if (isReconnecting || !isOwnedHudAlive()) return;
                    isReconnecting = true;
                    pendingReconnectUi = true;
                    if (typeof cfg.onReconnectStart === 'function') cfg.onReconnectStart();
                    try {
                        const token = localStorage.token ? localStorage.token.replace(/^"|"$/g, '') : '';
                        let msgId = mid;
                        if (!msgId) {
                            const msgs = document.querySelectorAll('[id^="message-"]');
                            for (let i = msgs.length - 1; i >= 0; i--) {
                                const rawId = msgs[i].id.replace(/^message-/, '');
                                if (rawId && !rawId.includes('input') && !rawId.includes('edit') && !rawId.includes('feedback')) {
                                    msgId = rawId;
                                    break;
                                }
                            }
                        }
                        const extra = (typeof cfg.getExtraReconnect === 'function') ? (cfg.getExtraReconnect() || {}) : {};
                        await fetch('/api/chat/actions/' + actionId, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token },
                            body: JSON.stringify(Object.assign({
                                chat_id: cid, model: modelId, id: msgId || 'hud-reconnect', session_id: '', messages: [],
                                reconnect_only: true
                            }, extra))
                        });
                    } catch (e) {
                        console.error('[ECHO HUD] Reconnect error (' + actionId + '):', e);
                    } finally {
                        setTimeout(() => {
                            isReconnecting = false;
                            if (actionQueue.length > 0 && typeof window[resolveVar] !== 'function') {
                                flushQueue();
                            }
                        }, 2000);
                    }
                }

                function getPingPayload() {
                    return (typeof cfg.getPingPayload === 'function') ? cfg.getPingPayload() : { action: 'ping' };
                }

                function flushQueue() {
                    if (actionQueue.length === 0) return;
                    if (typeof window[resolveVar] === 'function' && lastReadyTs > 0 && (Date.now() - lastReadyTs > 45000)) {
                        window[resolveVar] = null;
                    }
                    if (typeof window[resolveVar] === 'function') {
                        if (actionQueue[0] && prePingActions.indexOf(actionQueue[0].action) !== -1 && (Date.now() - lastReadyTs > 10000)) {
                            actionQueue.unshift(getPingPayload());
                        }
                        if (waitResolveTimer) { clearTimeout(waitResolveTimer); waitResolveTimer = null; }
                        const resolveFn = window[resolveVar];
                        window[resolveVar] = null;
                        const payload = actionQueue.shift();
                        inFlightPayload = payload;
                        resolveFn(payload);

                        if (inFlightWatchdog) clearTimeout(inFlightWatchdog);
                        if (payload && noWatchdogActions.indexOf(payload.action) === -1) {
                            inFlightWatchdog = setTimeout(() => {
                                if (typeof window[resolveVar] !== 'function') {
                                    if (inFlightPayload && inFlightPayload.action !== 'ping') {
                                        actionQueue.unshift(inFlightPayload);
                                    } else if (typeof cfg.onWatchdogFallback === 'function') {
                                        const fb = cfg.onWatchdogFallback();
                                        if (fb) actionQueue.push(fb);
                                    }
                                    inFlightPayload = null;
                                    reconnectBackend();
                                }
                            }, watchdogMs);
                        }
                    } else {
                        if (!waitResolveTimer) {
                            waitResolveTimer = setTimeout(() => {
                                waitResolveTimer = null;
                                if (typeof window[resolveVar] !== 'function' && actionQueue.length > 0) {
                                    reconnectBackend();
                                }
                            }, 1000);
                        }
                    }
                }

                window[readyFn] = function() {
                    if (inFlightWatchdog) { clearTimeout(inFlightWatchdog); inFlightWatchdog = null; }
                    if (waitResolveTimer) { clearTimeout(waitResolveTimer); waitResolveTimer = null; }
                    inFlightPayload = null;
                    isReconnecting = false;
                    lastReadyTs = Date.now();
                    if (pendingReconnectUi) {
                        pendingReconnectUi = false;
                        if (typeof cfg.onReconnectEnd === 'function') {
                            try { cfg.onReconnectEnd(); } catch (e) {}
                        }
                    }
                    flushQueue();
                };

                window[sendFn] = function(payload) {
                    if (!payload) return;
                    if (payload.action === 'ping') {
                        if (actionQueue.length > 0 || inFlightPayload) return;
                    } else {
                        actionQueue = actionQueue.filter(p => p.action !== 'ping');
                    }
                    actionQueue.push(payload);
                    flushQueue();
                };

                const pingInterval = setInterval(() => {
                    if (!isOwnedHudAlive()) { clearInterval(pingInterval); return; }
                    const el = document.getElementById(hudId);
                    const now = Date.now();
                    const isParked = el.getAttribute('data-echo-parked') === '1';
                    const threshold = (document.hidden || isParked) ? hiddenPingMs : visiblePingMs;
                    if (now - lastPingTs >= threshold) {
                        lastPingTs = now;
                        window[sendFn](getPingPayload());
                    }
                }, Math.min(5000, visiblePingMs));

                const wakeUpHandler = () => {
                    if (!isOwnedHudAlive()) {
                        document.removeEventListener('visibilitychange', wakeUpHandler);
                        window.removeEventListener('focus', wakeUpHandler);
                        return;
                    }
                    if (!document.hidden) {
                        window[sendFn](getPingPayload());
                    }
                };
                document.addEventListener('visibilitychange', wakeUpHandler);
                window.addEventListener('focus', wakeUpHandler);

                return {
                    send: window[sendFn],
                    cleanup: () => {
                        clearInterval(pingInterval);
                        document.removeEventListener('visibilitychange', wakeUpHandler);
                        window.removeEventListener('focus', wakeUpHandler);
                    }
                };
            };
        }
        """

    @staticmethod
    def get_floating_window_class_js() -> str:
        """
        # ==============================================================================
        # TEMPLATE STANDARD : ECHO FLOATING WINDOW (Moteur JS)
        # Génère la classe ES6 `EchoFloatingWindow` utilisée par tous les HUDs flottants
        # pour gérer nativement le rendu Glassmorphism, le Drag, Resize et les contrôles.
        # ==============================================================================
        """
        return EchoRichUI.get_hud_geometry_js() + """
        if (!window.EchoFloatingWindow) {
            window.EchoFloatingWindow = class {
                constructor(config) {
                    this.id = config.id;
                    this.title = config.title || '';
                    this.icon = config.icon || '';
                    this.bodyHtml = config.bodyHtml || '';
                    this.customHeader = config.customHeader || '';
                    this.width = config.width || '500px';
                    this.height = config.height || '400px';
                    this.minWidth = config.minWidth || '200px';
                    this.minHeight = config.minHeight || '100px';
                    this.allowResize = config.allowResize !== false;
                    this.onClose = config.onClose || null;
                    this.onMinimize = config.onMinimize || null;
                    
                    // Centrage basique
                    this.posX = Math.max(20, (window.innerWidth / 2) - (parseInt(this.width)/2 || 250));
                    this.posY = 100;
                    
                    this.isMinimized = false;
                    this.isFullscreen = false;
                    this.geo = null;
                }
                
                render() {
                    let hud = document.getElementById(this.id);
                    if (hud) hud.remove();
                    
                    hud = document.createElement('div');
                    hud.id = this.id;
                    hud.style.cssText = `position:fixed; left:${this.posX}px; top:${this.posY}px; width:${this.width}; height:${this.height}; max-width:98vw; max-height:96vh; z-index:10000; background:rgba(12,12,12,0.98); backdrop-filter:blur(25px); border:1px solid #333; border-radius:12px; box-shadow:0 25px 70px rgba(0,0,0,0.9); color:white; font-family:system-ui,sans-serif; display:flex; flex-direction:column; overflow:hidden; min-width:${this.minWidth}; min-height:${this.minHeight};`;
                    
                    const iconHtml = this.icon ? `<span style="font-size:14px; padding:3px 8px; border-radius:8px; background:rgba(0,212,255,0.1); color:#00d4ff;">${this.icon}</span>` : '';
                    
                    hud.innerHTML = `
                      <div id="${this.id}-header" style="height:44px; padding:0 15px; background:rgba(255,255,255,0.02); display:flex; align-items:center; gap:12px; border-bottom:1px solid #222; cursor:move; user-select:none; box-sizing:border-box; flex-shrink:0;">
                        ${iconHtml}
                        <div style="font-weight:600; font-size:13px; color:#a3a3a3; display:flex; align-items:center; gap:8px;">${this.title}</div>
                        ${this.customHeader}
                        <div style="display:flex; gap:12px; align-items:center; margin-left:auto;">
                          <button id="${this.id}-btn-zoom" title="Maximiser" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px; padding:0; display:flex; align-items:center;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/></svg></button>
                          <button id="${this.id}-btn-reset" title="Taille réelle (1:1)" style="background:none; border:none; color:#777; cursor:pointer; font-size:11px; font-weight:bold; padding:0;">1:1</button>
                          <button id="${this.id}-btn-min" title="Réduire" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px; padding:0; display:flex; align-items:center;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='5' y1='12' x2='19' y2='12'/></svg></button>
                          <button id="${this.id}-btn-close" title="Fermer" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:18px; padding:0; display:flex; align-items:center;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/></svg></button>
                        </div>
                      </div>
                      <div id="${this.id}-body" style="flex:1; position:relative; overflow:hidden; display:flex; flex-direction:column; background:white;">
                        ${this.bodyHtml}
                        ${this.allowResize ? `<div id="${this.id}-resizer" style="position:absolute; bottom:0; right:0; width:16px; height:16px; cursor:nwse-resize; z-index:101; background:linear-gradient(135deg, transparent 50%, rgba(0,0,0,0.4) 50%); border-bottom-right-radius: 12px;"></div>` : ''}
                      </div>
                    `;
                    document.body.appendChild(hud);
                }
                
                attachBaseEvents() {
                    const hud = document.getElementById(this.id);
                    if (!hud) return;
                    
                    hud.addEventListener('mousedown', () => {
                        document.querySelectorAll('[id^="echo-"]').forEach(el => {
                            if (el.style.zIndex && parseInt(el.style.zIndex) >= 10000) {
                                el.style.zIndex = '10000';
                            }
                        });
                        hud.style.zIndex = '10001';
                    });
                    
                    const header = document.getElementById(this.id + '-header');
                    const body = document.getElementById(this.id + '-body');
                    this.geo = window.echoBindHudGeometry(hud, header, {
                        defaultW: this.width,
                        defaultH: this.height,
                        defaultX: this.posX + 'px',
                        defaultY: this.posY + 'px',
                        defaultTx: 'none',
                        minW: this.minWidth,
                        minH: this.minHeight,
                        minimizedH: 'auto',
                        minimizedMinH: '0',
                        fsW: '100vw',
                        fsH: '100vh',
                        fsX: '0px',
                        fsY: '0px',
                        allowResize: this.allowResize,
                        customResizer: true,
                        onMinimize: (isMin) => {
                            this.isMinimized = isMin;
                            if (body) body.style.display = isMin ? 'none' : 'flex';
                            if (this.onMinimize) this.onMinimize(isMin);
                        },
                        onFullscreen: (isFs) => {
                            this.isFullscreen = isFs;
                        }
                    });
                    
                    if (this.allowResize) {
                        const resizer = document.getElementById(this.id + '-resizer');
                        let isResizing = false, rStartX, rStartY, startW, startH;
                        
                        resizer.onmousedown = (e) => {
                            e.preventDefault(); e.stopPropagation();
                            isResizing = true;
                            rStartX = e.clientX;
                            rStartY = e.clientY;
                            startW = hud.offsetWidth || parseInt(hud.style.width) || 500;
                            startH = hud.offsetHeight || parseInt(hud.style.height) || 400;
                            hud.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = 'none');
                        };
                        
                        document.addEventListener('mousemove', (e) => {
                            if (isResizing) {
                                e.preventDefault();
                                hud.style.width = Math.max(parseInt(this.minWidth) || 200, startW + (e.clientX - rStartX)) + 'px';
                                hud.style.height = Math.max(parseInt(this.minHeight) || 100, startH + (e.clientY - rStartY)) + 'px';
                            }
                        });
                        
                        document.addEventListener('mouseup', () => {
                            if (isResizing) {
                                isResizing = false;
                                hud.querySelectorAll('iframe').forEach(f => f.style.pointerEvents = '');
                                if (this.geo) this.geo.save();
                            }
                        });
                    }
                    
                    document.getElementById(this.id + '-btn-close').onclick = () => {
                        hud.remove();
                        if (this.onClose) this.onClose();
                    };
                    
                    document.getElementById(this.id + '-btn-min').onclick = (e) => {
                        e.stopPropagation();
                        this.geo.toggleMinimize();
                    };
                    
                    document.getElementById(this.id + '-btn-zoom').onclick = () => {
                        this.geo.toggleFullscreen();
                    };
                    
                    document.getElementById(this.id + '-btn-reset').onclick = () => {
                        this.geo.resetSize();
                    };
                }
            };
        }
        """

    @staticmethod
    def get_event_dispatcher_js() -> str:
        """Injecte le moteur asynchrone UCTP d'ECHO sur le frontend."""
        return """
        if (!window.echoDispatcher) {
            window.echoDispatcher = {
                _resolvers: {},
                listen: function(channel) {
                    return new Promise(resolve => { 
                        this._resolvers[channel] = resolve; 
                        setTimeout(() => {
                            if (this._resolvers[channel] === resolve) {
                                this.dispatch(channel, { action: "ping" });
                            }
                        }, 45000);
                    });
                },
                dispatch: function(channel, payload) {
                    if (this._resolvers[channel]) {
                        this._resolvers[channel](payload);
                        this._resolvers[channel] = null;
                    }
                }
            };
        }
        """

    @staticmethod
    def get_media_viewport_js(container_id: str, image_id: str, crop_box_id: str = "") -> str:
        """Encapsule le zoom wheel via ctrlKey."""
        crop_logic = ""
        if crop_box_id:
            crop_logic = f"""
                        var cropBox = document.getElementById('{crop_box_id}');
                        if (cropBox && cropBox.style.display !== 'none') {{
                            cropBox.style.width = canvas.offsetWidth + "px";
                            cropBox.style.height = canvas.offsetHeight + "px";
                            cropBox.style.transform = 'translate3d(0, 0, 0)';
                        }}
            """
        return f"""
        var _echo_vp = document.getElementById('{container_id}');
        var _echo_img = document.getElementById('{image_id}');
        var currentZoom = (typeof currentZoom !== 'undefined') ? currentZoom : 1.0;
        
        if (_echo_vp && _echo_img) {{
            _echo_vp.addEventListener('wheel', (e) => {{
                if (e.ctrlKey) {{
                    e.preventDefault();
                    var delta = e.deltaY > 0 ? 0.9 : 1.1;
                    currentZoom = Math.min(Math.max(0.1, currentZoom * delta), 15);
                    var canvas = _echo_vp.querySelector('div'); 
                    if (_echo_img.naturalHeight && canvas) {{
                        var r = _echo_img.naturalHeight / _echo_img.naturalWidth;
                        var targetH = Math.min(window.innerHeight * 0.75, _echo_img.naturalHeight);
                        var targetW = targetH / r;
                        var scaleW = targetW > (window.innerWidth * 0.95) ? (window.innerWidth * 0.95) / targetW : 1;
                        canvas.style.height = (targetH * currentZoom * scaleW) + "px";
                        canvas.style.width = (targetW * currentZoom * scaleW) + "px";
                        {crop_logic}
                    }}
                }}
            }}, {{ passive: false }});
        }}
        """

    @staticmethod
    def _get_boilerplate(content: str, title: str = "ECHO Visual") -> str:
        """Encapsulation HTML standard avec détection de thème hybride (Open WebUI Native)."""
        return f"""
    <!DOCTYPE html>
    <html lang="fr" class="light">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{title}</title>
      <style>
        :root {{
          --echo-bg: #ffffff;
          --echo-text: #171717;
          --echo-hud-bg: #f9f9f9;
          --echo-hud-border: #e5e5e5;
          --echo-hud-text: #666666;
          --echo-btn-bg: #f3f4f6;
          --echo-btn-border: #d1d5db;
          --echo-btn-text: #374151;
          --echo-accent: #3b82f6;
        }}
        html.dark {{
          --echo-bg: #171717;
          --echo-text: #ececec;
          --echo-hud-bg: #262626;
          --echo-hud-border: #404040;
          --echo-hud-text: #a3a3a3;
          --echo-btn-bg: #262626;
          --echo-btn-border: #404040;
          --echo-btn-text: #ececec;
        }}
        html.oled-dark {{
          --echo-bg: #000000;
          --echo-text: #ffffff;
          --echo-hud-bg: #101010;
          --echo-hud-border: #262626;
          --echo-hud-text: #a3a3a3;
        }}
        html, body {{ margin: 0; padding: 0; background: var(--echo-bg); color: var(--echo-text); font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; min-height: 100px; overflow-x: hidden; transition: background 0.2s, color 0.2s; }}
        .echo-container {{ width: 100%; display: flex; flex-direction: column; }}
        #hud-bar {{ background: var(--echo-hud-bg); padding: 8px 16px; border-bottom: 1px solid var(--echo-hud-border); display: flex; align-items: center; justify-content: space-between; z-index: 100; color: var(--echo-hud-text); font-size: 12px; }}
        .btn {{ background: var(--echo-btn-bg); border: 1px solid var(--echo-btn-border); color: var(--echo-btn-text); padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 11px; transition: all 0.2s; font-weight: 500; }}
        .btn:hover {{ filter: brightness(1.2); border-color: var(--echo-accent); }}

        @media (prefers-color-scheme: dark) {{
          html:not(.light) {{
            --echo-bg: #171717;
            --echo-text: #ececec;
          }}
        }}
      </style>
    </head>
    <body>
      <div class="echo-container">
        {content}
      </div>
    <script>
      let lastHeight = 0;
      let reportTimeout = null;
      let disableAutoResize = false;

      function reportHeight() {{
        if (disableAutoResize) return;
        const h = Math.max(document.body.offsetHeight, document.documentElement.scrollHeight, 100);
        if (Math.abs(h - lastHeight) > 5) {{
            lastHeight = h;
            parent.postMessage({{ type: 'iframe:height', height: h + 10 }}, '*');
        }}
      }}

      function applyThemeClass(theme) {{
        document.documentElement.classList.remove('light', 'dark', 'oled-dark');
        if (theme === 'oled-dark') {{
            document.documentElement.classList.add('dark', 'oled-dark');
        }} else if (theme === 'dark') {{
            document.documentElement.classList.add('dark');
        }} else {{
            document.documentElement.classList.add('light');
        }}
        if (typeof reportHeight === 'function') reportHeight();
      }}

      function syncWithParent() {{
        try {{
            if (parent && parent.document && parent.document.documentElement) {{
                const isDark = parent.document.documentElement.classList.contains('dark');
                const isOled = parent.document.documentElement.style.getPropertyValue('--color-gray-900') === '#000000';
                applyThemeClass(isOled ? 'oled-dark' : (isDark ? 'dark' : 'light'));
                return true;
            }}
        }} catch(e) {{ }}
        return false;
      }}

      window.addEventListener('message', (event) => {{
        if (event.data && event.data.type === 'theme-update') {{
            applyThemeClass(event.data.theme);
        }}
      }});

      const darkMQ = window.matchMedia('(prefers-color-scheme: dark)');
      darkMQ.addEventListener('change', (e) => {{
        if (!localStorage.getItem('theme-override')) {{
            applyThemeClass(e.matches ? 'dark' : 'light');
        }}
      }});

      function debouncedReport() {{
        clearTimeout(reportTimeout);
        reportTimeout = setTimeout(reportHeight, 150);
      }}

      window.addEventListener('load', () => {{
        if (!syncWithParent()) {{
            applyThemeClass(darkMQ.matches ? 'dark' : 'light');
        }}
        reportHeight();
      }});

      if (window.ResizeObserver) {{
          const ro = new ResizeObserver(() => {{
            if (!disableAutoResize) debouncedReport();
          }});
          ro.observe(document.body);
      }}

      setTimeout(reportHeight, 500);
    </script>
    </body>
    </html>
    """


class EchoUI(EchoRichUI):
    """Moteur de pilotage HUD pour ECHO."""

    @staticmethod
    def get_mobile_guard_js(
            hud_id: str,
            block_execution: bool = False,
            error_msg: str = "Incompatible sur mobile.") -> str:
        """Génère le garde-fou JS centralisé pour l'adaptation ou le blocage sur mobile."""
        return f"""
      const isMobile = window.matchMedia('(max-width: 768px)').matches || /Mobi|Android/i.test(navigator.userAgent);
      if (isMobile) {{
          {f"window.parent.postMessage({{ type: 'toast', message: `{error_msg}`, level: 'warning' }}, '*'); return;" if block_execution else f'''
          const styleId = '{hud_id}-mobile-style';
          if (!document.getElementById(styleId)) {{
              const styleEl = document.createElement('style');
              styleEl.id = styleId;
              styleEl.innerHTML = `
                  #{hud_id} {{ position: fixed !important; top: 0 !important; left: 0 !important; max-width: 100vw !important; max-height: 100dvh !important; min-width: 0 !important; min-height: 0 !important; border-radius: 0 !important; z-index: 10005 !important; transform: none !important; }}
                  #{hud_id}-resizer, .cp {{ display: none !important; }}
                  /* Factorisation du Responsive pour les structures classiques (Codex, Agent Monitor) */
                  #{hud_id}-header {{ flex-wrap: wrap !important; height: auto !important; min-height: 40px !important; padding: 6px !important; gap: 4px !important; }}
                  #{hud_id}-body {{ flex-direction: column !important; }}
                  #{hud_id}-sidebar {{ width: 100% !important; height: auto !important; max-height: 140px !important; flex-direction: row !important; flex-wrap: wrap !important; overflow-y: auto !important; overflow-x: hidden !important; border-right: none !important; border-bottom: 1px solid var(--border-color, #444) !important; padding: 4px !important; align-content: flex-start !important; }}
                  #{hud_id}-sidebar > div {{ flex: 0 0 auto !important; margin: 2px !important; border: 1px solid var(--border-color, #444) !important; border-radius: 6px !important; }}
                  #{hud_id}-sidebar-splitter {{ display: none !important; }}
              `;
              document.head.appendChild(styleEl);
          }}
          // Factorisation : Force l'UI en plein écran par défaut au chargement via styles en ligne (permettant le redimensionnement JS futur) et désactive le drag&drop.
          setTimeout(() => {{
              const hud = document.getElementById('{hud_id}');
              if (hud && !hud.dataset.mobileInit) {{
                  hud.style.width = '100vw';
                  hud.style.height = '100dvh';
                  hud.dataset.mobileInit = 'true';
              }}
              const header = document.getElementById('{hud_id}-header');
              if (header) {{
                  header.onmousedown = null;
                  header.ontouchstart = null;
                  header.style.cursor = 'default';
              }}
          }}, 100);
          '''}
      }}
      """

    @staticmethod
    async def safe_deploy(events: Any, monitor_func: Any, **kwargs):
        """Déploiement sécurisé du HUD (Anti-Crash si events/caller absent)."""
        if not events or (not events.emitter and not events.caller):
            return False
        try:
            await monitor_func(events=events, **kwargs)
            return True
        except Exception as e:
            print(f"[EchoUI] Safe Deploy Error: {e}")
            return False

    @staticmethod
    def _generate_webplayer_js(
            b64: str,
            mime: str,
            metadata: list,
            current_url: str,
            hud_id: str,
            state_key: str,
            icon: str = "👁️") -> str:
        """Génère le moteur de pilotage ECHO WEBPLAYER (v5.20 Équilibre Souverain Pro)."""
        meta_j = std_json.dumps(metadata).decode('utf-8')
        b64_j = std_json.dumps(b64).decode('utf-8')
        url_j = std_json.dumps(current_url).decode('utf-8')
        mime_j = std_json.dumps(mime).decode('utf-8')

        return f"""
  (function() {{
    const HUD_ID = '{hud_id}';
    {EchoUI.get_mobile_guard_js(hud_id)}
    const STATE_KEY = '{state_key}';
    const ENGINE_KEY = 'echoWebPlayer_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_');

    // Expose a global update function for the backend to push frames
    window['echoWebPlayerUpdate_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_')] = function(newB64, newId, newUrl) {{
        if (window[ENGINE_KEY]) {{
            const img = document.getElementById(HUD_ID + "-img");
            if (img) img.src = 'data:image/jpeg;base64,' + newB64;
            if (newUrl !== undefined) {{
                const urlEl = document.getElementById(HUD_ID + "-url");
                if (urlEl) urlEl.value = newUrl;
            }}
        }}
    }};

    const payload = {{
      b64: {b64_j}, mime: {mime_j}, metadata: {meta_j},
      url: {url_j}
    }};

    if (!window[ENGINE_KEY]) {{
      window[ENGINE_KEY] = {{
        hud: null, ratio: 1.0, posX: 30, posY: 30,
        imgScale: 1.0, imgX: 0, imgY: 0,
        isDragging: false, headerH: 45,

        getInitialScale: function(imgW, imgH) {{
          const targetW = window.innerWidth * 0.5;
          const targetH = window.innerHeight * 0.8;
          const sW = targetW / imgW;
          const sH = (targetH - this.headerH) / imgH;
          return Math.min(sW, sH, 1.0);
        }},

        clampHud: function() {{
          if (!this.hud) return;
          const vw = window.innerWidth, vh = window.innerHeight;
          const w = this.hud.offsetWidth, h = this.hud.offsetHeight;
          if (this.posX < 0) this.posX = 0;
          if (this.posY < 0) this.posY = 0;
          if (this.posX + w > vw) this.posX = Math.max(0, vw - w);
          if (this.posY + h > vh) this.posY = Math.max(0, vh - h);
          this.hud.style.left = this.posX + "px";
          this.hud.style.top = this.posY + "px";
        }},

        syncLayout: function(fromResize = false) {{
          const img = document.getElementById(HUD_ID + "-img");
          const matrix = document.getElementById(HUD_ID + "-matrix");
          const area = document.getElementById(HUD_ID + "-area");
          if (!img || !img.naturalWidth) return;

          const vw = window.innerWidth, vh = window.innerHeight;

          let targetW = img.naturalWidth * this.imgScale;
          let targetH = img.naturalHeight * this.imgScale;

          if (area.style.display !== 'none') {{
            this.hud.style.minHeight = "150px";
            const frameW = Math.min(targetW, vw - 20);
            const frameH = Math.min(targetH, vh - this.headerH - 20);
            this.hud.style.width = frameW + "px";
            this.hud.style.height = (frameH + this.headerH) + "px";

            const minX = frameW - targetW;
            const minY = frameH - targetH;
            this.imgX = Math.min(0, Math.max(this.imgX, minX));
            this.imgY = Math.min(0, Math.max(this.imgY, minY));
          }} else {{
            this.hud.style.minHeight = "0px";
            this.hud.style.height = this.headerH + "px";
          }}

          matrix.style.transform = `translate3d(${{this.imgX}}px, ${{this.imgY}}px, 0) scale(${{this.imgScale}})`;
          const boxes = document.getElementById(HUD_ID + "-hitboxes").children;
          const invS = 1 / this.imgScale;
          for (let b of boxes) b.style.transform = `translate(-50%, -50%) scale(${{invS}})`;

          this.clampHud();
          this.saveState();
        }},

        saveState: function() {{
          if (!this.hud || this.isDragging) return;
          const area = document.getElementById(HUD_ID + "-area");
          localStorage.setItem(STATE_KEY, JSON.stringify({{
            x: this.posX, y: this.posY, s: this.imgScale,
            ix: this.imgX, iy: this.imgY, m: (area.style.display === 'none')
          }}));
        }},

        attachEvents: function() {{
          const area = document.getElementById(HUD_ID + "-area");
          const header = document.getElementById(HUD_ID + "-header");

          area.addEventListener('wheel', (e) => {{
            e.preventDefault();
            const delta = e.deltaY > 0 ? 0.92 : 1.08;
            this.imgScale = Math.min(Math.max(0.02, this.imgScale * delta), 15);
            this.syncLayout();
          }}, {{ passive: false }});

          // Gestion native Pinch-to-zoom
          let initialPinchDistance = null;
          let initialPinchScale = null;
          const matrix = document.getElementById(HUD_ID + "-matrix");

          matrix.addEventListener('touchstart', (e) => {{
            if (e.touches.length === 2) {{
              initialPinchDistance = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
              initialPinchScale = this.imgScale;
              e.preventDefault();
            }}
          }}, {{passive: false}});

          matrix.addEventListener('touchmove', (e) => {{
            if (e.touches.length === 2 && initialPinchDistance) {{
              const dist = Math.hypot(e.touches[0].pageX - e.touches[1].pageX, e.touches[0].pageY - e.touches[1].pageY);
              this.imgScale = Math.max(0.1, Math.min(10, initialPinchScale * (dist / initialPinchDistance)));
              this.syncLayout();
              e.preventDefault();
            }}
          }}, {{passive: false}});

          matrix.addEventListener('touchend', (e) => {{
            if (e.touches.length < 2) {{
              initialPinchDistance = null;
              initialPinchScale = null;
            }}
          }});

          area.onmousedown = (e) => {{
            if (e.button === 0 || e.button === 1) {{
              e.preventDefault();
              this.isDragging = true;
              this.startMouseX = e.clientX; this.startMouseY = e.clientY;
              area.style.cursor = 'grabbing';
            }}
          }};

          window.addEventListener('mousemove', (e) => {{
            if (this.isDragging) {{
              this.imgX += (e.clientX - this.startMouseX);
              this.imgY += (e.clientY - this.startMouseY);
              this.startMouseX = e.clientX; this.startMouseY = e.clientY;
              this.syncLayout();
            }}
          }});

          window.addEventListener('mouseup', () => {{
            if (this.isDragging) {{
              this.isDragging = false;
              area.style.cursor = 'crosshair';
              this.saveState();
            }}
          }});

          header.onmousedown = (e) => {{
            if (e.target.closest && e.target.closest('button, input, select, a')) return;
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
            e.preventDefault();
            let ox = e.clientX, oy = e.clientY;
            const move = (me) => {{
              this.posX += (me.clientX - ox); this.posY += (me.clientY - oy);
              ox = me.clientX; oy = me.clientY;
              this.clampHud();
            }};
            const up = () => {{
              document.removeEventListener('mousemove', move);
              document.removeEventListener('mouseup', up);
              this.saveState();
            }};
            document.addEventListener('mousemove', move);
            document.addEventListener('mouseup', up);
          }};

          document.getElementById(HUD_ID + "-btn-zoom").onclick = () => {{
            const img = document.getElementById(HUD_ID + "-img");
            const vw = window.innerWidth, vh = window.innerHeight;
            const sW = (vw - 40) / img.naturalWidth;
            const sH = (vh - this.headerH - 40) / img.naturalHeight;
            this.imgScale = Math.min(sW, sH);
            this.syncLayout();
          }};

          document.getElementById(HUD_ID + "-btn-reset").onclick = () => {{
            this.imgScale = 1.0; this.syncLayout();
          }};

          document.getElementById(HUD_ID + "-btn-min").onclick = (e) => {{
            e.stopPropagation();
            const a = document.getElementById(HUD_ID + "-area");
            a.style.display = (a.style.display === 'none') ? 'block' : 'none';
            this.syncLayout();
          }};

          document.getElementById(HUD_ID + "-btn-close").onclick = () => this.hud.remove();

          const resizer = document.getElementById(HUD_ID + "-resizer");
          if (resizer) {{
             resizer.onmousedown = (e) => {{
                e.preventDefault(); e.stopPropagation();
                const startX = e.clientX;
                const startScale = this.imgScale;
                const img = document.getElementById(HUD_ID + "-img");
                if (!img || !img.naturalWidth) return;

                const doDrag = (me) => {{
                   const deltaX = me.clientX - startX;
                   this.imgScale = Math.max(0.05, startScale + (deltaX / img.naturalWidth));
                   this.syncLayout(false);
                }};
                const stopDrag = () => {{
                   document.removeEventListener('mousemove', doDrag);
                   document.removeEventListener('mouseup', stopDrag);
                   this.saveState();
                }};
                document.addEventListener('mousemove', doDrag);
                document.addEventListener('mouseup', stopDrag);
             }};
          }}

          window.addEventListener('resize', () => {{
             this.syncLayout(false);
          }});
        }},

        create: function(data) {{
          const old = document.getElementById(HUD_ID); if(old) old.remove();
          this.hud = document.createElement('div');
          this.hud.id = HUD_ID;
          this.hud.style.cssText = 'position:fixed; z-index:10000; background:rgba(12,12,12,0.98); backdrop-filter:blur(25px); border:1px solid #333; border-radius:12px; box-shadow:0 25px 70px rgba(0,0,0,0.9); color:white; font-family:sans-serif; display:flex; flex-direction:column; overflow:hidden; min-width:200px; min-height:100px;';

          this.hud.innerHTML = `
            <div id="${{HUD_ID}}-header" style="height:${{this.headerH}}px; padding:0 15px; background:rgba(255,255,255,0.02); display:flex; align-items:center; gap:12px; border-bottom:1px solid #222; cursor:move; user-select:none; box-sizing:border-box;">
              <span style="font-size:14px; padding:3px 8px; border-radius:8px; background:rgba(0,212,255,0.1); color:#00d4ff;">{icon}</span>
              <input id="${{HUD_ID}}-url" type="text" placeholder="URL du navigateur..." style="flex:1; background:rgba(0,0,0,0.4); border:1px solid #333; border-radius:6px; color:#00d4ff; font-size:11px; padding:6px 12px; outline:none; font-family:monospace;" readonly />
              <div style="display:flex; gap:8px;">
                <button id="${{HUD_ID}}-btn-zoom" title="Maximiser (Ajuster)" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/></svg></button>
                <button id="${{HUD_ID}}-btn-reset" title="Taille réelle (1:1)" style="background:none; border:none; color:#777; cursor:pointer; font-size:11px; font-weight:bold;">1:1</button>
                <button id="${{HUD_ID}}-btn-min" title="Minimiser" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='5' y1='12' x2='19' y2='12'/></svg></button>
                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:18px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/></svg></button>
              </div>
            </div>
            <div id="${{HUD_ID}}-area" style="flex:1; position:relative; background:#000; overflow:hidden; cursor:crosshair;">
              <div id="${{HUD_ID}}-matrix" style="position:absolute; top:0; left:0; transform-origin: 0 0; will-change: transform;">
                <img id="${{HUD_ID}}-img" style="display:block; user-select:none; pointer-events:none; width:100%; height:100%; max-width:none !important;" draggable="false" />
                <div id="${{HUD_ID}}-hitboxes" style="position:absolute; inset:0; pointer-events:none;"></div>
              </div>
              <div id="${{HUD_ID}}-resizer" style="position:absolute; bottom:0; right:0; width:16px; height:16px; cursor:nwse-resize; z-index:101; background:linear-gradient(135deg, transparent 50%, rgba(255,255,255,0.4) 50%); border-bottom-right-radius: 12px;"></div>
            </div>
          `;
          document.body.appendChild(this.hud);
          this.attachEvents();

          const saved = localStorage.getItem(STATE_KEY);
          if (saved) {{
            const s = JSON.parse(saved);
            this.posX = s.x; this.posY = s.y; this.imgScale = s.s;
            this.imgX = s.ix; this.imgY = s.iy;
            if (s.m) document.getElementById(HUD_ID + "-area").style.display = 'none';
          }}
        }},

        update: function(data) {{
          if (!document.getElementById(HUD_ID)) this.create(data);
          this.hud = document.getElementById(HUD_ID);
          const img = document.getElementById(HUD_ID + "-img");
          const boxes = document.getElementById(HUD_ID + "-hitboxes");
          const matrix = document.getElementById(HUD_ID + "-matrix");
          document.getElementById(HUD_ID + "-url").value = data.url;

          img.onload = () => {{
            const newNatW = img.naturalWidth;
            if (this.lastNatWidth && newNatW !== this.lastNatWidth && this.imgScale) {{
                this.imgScale = this.imgScale * (this.lastNatWidth / newNatW);
                if (this.saveState) this.saveState();
            }}
            this.lastNatWidth = newNatW;

            this.ratio = img.naturalHeight / newNatW;
            matrix.style.width = newNatW + "px";
            matrix.style.height = img.naturalHeight + "px";

            if (!localStorage.getItem(STATE_KEY)) {{
                this.imgScale = this.getInitialScale(newNatW, img.naturalHeight);
                this.posX = (window.innerWidth - (newNatW * this.imgScale)) / 2;
                this.posY = (window.innerHeight - (img.naturalHeight * this.imgScale + this.headerH)) / 2;
            }}

            boxes.innerHTML = "";
            data.metadata.forEach(m => {{
                if (m.x !== undefined) {{
                    const dot = document.createElement('div');
                    dot.style.cssText = `position:absolute; left:${{m.x}}px; top:${{m.y}}px; width:12px; height:12px; background:rgba(0, 212, 255, 0.7); border:2px solid #fff; border-radius:50%; box-shadow:0 0 10px rgba(0, 212, 255, 0.5); cursor:pointer; pointer-events:auto;`;
                    boxes.appendChild(dot);
                }}
            }});
            this.syncLayout();
          }};

          img.src = "data:" + data.mime + ";base64," + data.b64;

          const area = document.getElementById(HUD_ID + "-area");
          if (data.mime === "image/webp") {{
             area.ondblclick = () => {{
                let currentSrc = img.src;
                img.src = "";
                setTimeout(() => {{ img.src = currentSrc; }}, 50);
             }};
          }} else {{
             area.ondblclick = null;
          }}
        }}
      }};
    }}
    window[ENGINE_KEY].update(payload);
  }})();
    """

    @staticmethod
    async def monitor_ECHO(
            events: Any,
            b64: str,
            metadata: List[Dict] = None,
            hud_id: str = "echo-webplayer",
            state_key: str = "echo_webplayer_state",
            current_url: str = "",
            webp_b64: str = None):
        """Déploie le moniteur visuel interactif (HUD) haute performance."""
        if webp_b64:
            js_code = EchoUI._generate_webplayer_js(
                webp_b64,
                "image/webp",
                metadata or [],
                current_url,
                hud_id,
                state_key,
                icon="🌐")
        else:
            js_code = EchoUI._generate_webplayer_js(
                b64, "image/jpeg", metadata or [], current_url, hud_id, state_key, icon="🌐")
        await events.emit_execute(js_code)

    @staticmethod
    def calculate_context_delta(
        current: Optional[Dict[str, Any]],
        previous: Optional[Dict[str, Any]],
        max_context_size: int = ECHO_MAX_CONTEXT_SIZE
    ) -> Dict[str, Any]:
        """Calcule le différentiel unitaire de tokens et l'impact capacitaire entre deux tours."""
        cur = current or {}
        prev = previous or {}

        c_prompt = int(cur.get("promptTokenCount", 0))
        c_candidates = int(cur.get("candidatesTokenCount", 0))
        c_cache = int(cur.get("cachedContentTokenCount", 0))
        c_total = int(cur.get("totalTokenCount", 0))

        p_prompt = int(prev.get("promptTokenCount", 0))
        p_candidates = int(prev.get("candidatesTokenCount", 0))
        p_cache = int(prev.get("cachedContentTokenCount", 0))
        p_total = int(prev.get("totalTokenCount", 0))

        has_prev = p_total > 0

        delta_total = (c_total - p_total) if has_prev else 0
        delta_prompt = (c_prompt - p_prompt) if has_prev else 0
        delta_candidates = (c_candidates - p_candidates) if has_prev else 0
        delta_cache = (c_cache - p_cache) if has_prev else 0

        max_size = max(1, int(max_context_size or ECHO_MAX_CONTEXT_SIZE))
        capacity_lost_pct = (delta_total / max_size) * 100.0 if has_prev else 0.0

        if has_prev and p_prompt > 0:
            growth_pct = (delta_prompt / p_prompt) * 100.0
        elif has_prev and p_total > 0:
            growth_pct = (delta_total / p_total) * 100.0
        else:
            growth_pct = 0.0

        p_q5 = prev.get("quota_5h_fraction")
        c_q5 = cur.get("quota_5h_fraction")
        delta_q5 = round((float(c_q5) - float(p_q5)) * 100.0, 1) if (p_q5 is not None and c_q5 is not None) else None

        p_qw = prev.get("quota_weekly_fraction")
        c_qw = cur.get("quota_weekly_fraction")
        delta_qw = round((float(c_qw) - float(p_qw)) * 100.0, 1) if (p_qw is not None and c_qw is not None) else None

        return {
            "has_delta": has_prev,
            "delta_total": delta_total,
            "delta_prompt": delta_prompt,
            "delta_candidates": delta_candidates,
            "delta_cache": delta_cache,
            "capacity_lost_pct": round(capacity_lost_pct, 3),
            "growth_pct": round(growth_pct, 1),
            "delta_quota_5h": delta_q5,
            "delta_quota_weekly": delta_qw,
            "is_first_turn": not has_prev,
        }

    @staticmethod
    def get_context_gauge_renderer_js() -> str:
        """Génère la fonction globale window.echoRenderContextGauge(stats)
        idempotente et Hot-Updatable, réutilisée à la fois par le loader à froid et par le filtre à chaud."""
        return """
  window.echoRenderContextGauge = function(stats) {
    if (!stats || typeof stats !== 'object') return;
    var max_t = stats.max_tokens || 1048576;
    var c_t = stats.cachedContentTokenCount || 0;
    var active_p_t = Math.max(0, (stats.promptTokenCount || 0) - c_t);
    var g_t = stats.candidatesTokenCount || 0;
    var total_t = stats.totalTokenCount || (c_t + active_p_t + g_t);
    var is_pending = !!stats.is_pending;
    var chatId = stats.chat_id || (window.EchoWM && window.EchoWM.getCurrentChatId ? window.EchoWM.getCurrentChatId() : '');

    var cache_pct = (c_t / max_t) * 100;
    var prompt_pct = (active_p_t / max_t) * 100;
    var gen_pct = (g_t / max_t) * 100;
    var quota_frac = typeof stats.quota_fraction === 'number' ? stats.quota_fraction : 1.0;

    function _color_for_frac(f) {
      if (f < 0.2) return "#ef4444";
      if (f < 0.5) return "#f59e0b";
      return "#10b981";
    }
    function _format_quota_delta(deltaVal) {
      if (typeof deltaVal !== 'number') return '<span style="color:#94a3b8;">—</span>';
      if (deltaVal < 0) return `<span style="color:#ef4444;font-weight:bold;">${deltaVal}%</span>`;
      if (deltaVal > 0) return `<span style="color:#10b981;font-weight:bold;">+${deltaVal}%</span>`;
      return '<span style="color:#94a3b8;">0%</span>';
    }
    var q_color = _color_for_frac(quota_frac);
    var dash_array = 2 * Math.PI * 8;
    var dash_offset = dash_array * (1 - quota_frac);

    var styleId = 'echo-hud-styles';
    if (!document.getElementById(styleId)) {
      var style = document.createElement('style');
      style.id = styleId;
      style.innerHTML = `
        .echo-tooltip { position: relative; display: flex; align-items: center; }
        .echo-tooltip .tooltip-box {
          visibility: hidden; width: 280px; background: rgba(15, 23, 42, 0.98);
          backdrop-filter: blur(12px); color: #f8fafc; text-align: left;
          border-radius: 8px; padding: 12px; position: absolute; z-index: 9999;
          top: 120%; left: 50%; transform: translateX(-50%); opacity: 0;
          transition: opacity 0.3s, transform 0.3s; border: 1px solid rgba(0, 212, 255, 0.4);
          box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); font-size: 11px; pointer-events: none;
        }
        .echo-tooltip:hover .tooltip-box { visibility: visible; opacity: 1; transform: translateX(-50%) translateY(-5px); }
        .tooltip-title { color: #00d4ff; font-weight: bold; margin-bottom: 8px; border-bottom: 1px solid rgba(0, 212, 255, 0.2); padding-bottom: 4px; text-transform: uppercase; }
        .tooltip-row { display: flex; justify-content: space-between; margin-bottom: 4px; }
        .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }
        .echo-delta-badge {
          display: inline-flex; align-items: center; font-size: 10px; font-weight: 500;
          padding: 1px 6px; border-radius: 10px; margin-left: 6px; white-space: nowrap;
          background: rgba(148, 163, 184, 0.12); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.25);
        }
        @media (max-width: 768px) { .tooltip-box { width: 90vw !important; left: 50% !important; transform: translateX(-50%) translateY(-5px) !important; white-space: normal !important; } }
      `;
      document.head.appendChild(style);
    }

    var hudWrapper = document.getElementById('echo-nav-context-hud-wrapper');
    if (!hudWrapper) {
      hudWrapper = document.createElement('div');
      hudWrapper.id = 'echo-nav-context-hud-wrapper';
      hudWrapper.style.cssText = 'position:fixed;left:50%;top:22px;transform:translateX(-50%);width:auto;min-width:300px;display:flex;justify-content:center;align-items:center;z-index:9999;pointer-events:none;';
      document.body.appendChild(hudWrapper);
    }

    if (chatId) {
      hudWrapper.setAttribute('data-chat-id', chatId);
      hudWrapper.setAttribute('data-echo-chat-id', chatId);
    }

    var hud = document.getElementById('echo-nav-context-hud');
    if (!hud) {
      hud = document.createElement('div');
      hud.id = 'echo-nav-context-hud';
      hud.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:12px;pointer-events:auto;background:rgba(0,0,0,0.2);padding:4px 12px;border-radius:20px;backdrop-filter:blur(4px);';
      hudWrapper.appendChild(hud);
    }

    var tier_html = stats.user_tier ? `<div class="tooltip-row"><span>💎 Tier:</span> <span style="color:#38bdf8;font-weight:bold;">${stats.user_tier}</span></div>` : '';
    var proj_html = (stats.project_id && stats.project_id !== stats.tenant_id) ? `<div class="tooltip-row"><span>🏗️ Projet Perso:</span> <span>${stats.project_id}</span></div>` : '';

    var q_rows_html = '';
    var d5 = (stats.delta && typeof stats.delta.delta_quota_5h === 'number') ? stats.delta.delta_quota_5h : null;
    var dw = (stats.delta && typeof stats.delta.delta_quota_weekly === 'number') ? stats.delta.delta_quota_weekly : null;
    var has_5h = stats.quota_5h_fraction != null || stats.quota_5h_disabled;
    var has_w = stats.quota_weekly_fraction != null;

    if (has_5h || has_w) {
      var tbody_rows = '';
      if (stats.quota_5h_disabled) {
        tbody_rows += '<tr><td style="padding:2px 4px;">⏱️ 5h</td><td style="padding:2px 4px;text-align:right;color:#94a3b8;">Off</td><td style="padding:2px 4px;text-align:right;color:#94a3b8;">—</td><td style="padding:2px 4px;text-align:right;color:#94a3b8;">—</td></tr>';
      } else if (stats.quota_5h_fraction != null) {
        var c5 = _color_for_frac(stats.quota_5h_fraction);
        var v5 = Math.round(stats.quota_5h_fraction * 100) + '%';
        var d5_str = _format_quota_delta(d5);
        var r5_clean = (['N/A', '—', ''].indexOf(stats.quota_5h_reset) !== -1) ? '—' : stats.quota_5h_reset;
        tbody_rows += `<tr><td style="padding:2px 4px;">⏱️ 5h</td><td style="padding:2px 4px;text-align:right;"><b style="color:${c5};">${v5}</b></td><td style="padding:2px 4px;text-align:right;">${d5_str}</td><td style="padding:2px 4px;text-align:right;color:#94a3b8;font-size:9px;">${r5_clean}</td></tr>`;
      }
      if (stats.quota_weekly_fraction != null) {
        var cw = _color_for_frac(stats.quota_weekly_fraction);
        var vw = Math.round(stats.quota_weekly_fraction * 100) + '%';
        var dw_str = _format_quota_delta(dw);
        var rw_clean = (['N/A', '—', ''].indexOf(stats.quota_weekly_reset) !== -1) ? '—' : stats.quota_weekly_reset;
        tbody_rows += `<tr><td style="padding:2px 4px;">📅 Hebdo</td><td style="padding:2px 4px;text-align:right;"><b style="color:${cw};">${vw}</b></td><td style="padding:2px 4px;text-align:right;">${dw_str}</td><td style="padding:2px 4px;text-align:right;color:#94a3b8;font-size:9px;">${rw_clean}</td></tr>`;
      }
      q_rows_html = `<table style="width:100%;margin-top:6px;margin-bottom:6px;border-collapse:collapse;font-size:10px;text-align:left;"><thead><tr style="border-bottom:1px solid rgba(255,255,255,0.15);color:#94a3b8;"><th style="padding:2px 4px;">Quota</th><th style="padding:2px 4px;text-align:right;">Dispo</th><th style="padding:2px 4px;text-align:right;">Δ Tour</th><th style="padding:2px 4px;text-align:right;">Reset</th></tr></thead><tbody>${tbody_rows}</tbody></table>`;
    } else {
      var r_str = (['N/A', '—', ''].indexOf(stats.quota_reset) !== -1) ? '' : ` <span style="color:#94a3b8;font-size:10px;">(🔄 ${stats.quota_reset})</span>`;
      q_rows_html = `<div class="tooltip-row"><span>📊 Quota:</span> <span><b style="color:${q_color};">${(quota_frac * 100).toFixed(1)}%</b>${r_str}</span></div>`;
    }

    var rpd_html = (stats.quota_rpd_rem && stats.quota_rpd_rem !== 'N/A') ? `<div class="tooltip-row"><span>📆 Req/jour:</span> <span>${stats.quota_rpd_rem} / ${stats.quota_rpd_lim}</span></div>` : '';
    var rpm_html = (stats.quota_rpm_rem && stats.quota_rpm_rem !== 'N/A') ? `<div class="tooltip-row"><span>⚡ Req/min:</span> <span>${stats.quota_rpm_rem} / ${stats.quota_rpm_lim}</span></div>` : '';

    var deltaBadge = '';
    var deltaTooltipRows = '';
    if (stats.delta && stats.delta.has_delta) {
      var dTot = stats.delta.delta_total || 0;
      var capLost = stats.delta.capacity_lost_pct || 0;
      var sign = dTot > 0 ? '+' : '';
      var capLostFixed = Math.abs(capLost).toFixed(2);
      var badgeText = (capLost > 0 ? '-' : (capLost < 0 ? '+' : '')) + capLostFixed + '%';
      deltaBadge = `<span class="echo-delta-badge">${badgeText}</span>`;

      var signGrowth = (stats.delta.growth_pct > 0 ? '+' : '') + stats.delta.growth_pct + '%';
      var growthLabel = (stats.delta.growth_pct < 0) ? 'Variation contexte:' : 'Croissance contexte:';

      var capRow = '';
      if (capLost > 0) {
        capRow = `<div class="tooltip-row"><span>Δ Capacité:</span> <b style="color:#ef4444;">-${capLostFixed}%</b></div>`;
      } else if (capLost < 0) {
        capRow = `<div class="tooltip-row"><span>Δ Capacité:</span> <b style="color:#10b981;">+${capLostFixed}% (libéré)</b></div>`;
      } else {
        capRow = `<div class="tooltip-row"><span>Δ Capacité:</span> <span>0%</span></div>`;
      }

      deltaTooltipRows = `
        <div class="tooltip-title" style="margin-top:8px;border-top:1px solid rgba(0,212,255,0.2);padding-top:6px;">DIFFÉRENTIEL (DERNIER TOUR)</div>
        <div class="tooltip-row"><span>Δ Tokens Total:</span> <span style="font-weight:bold;">${sign}${dTot}</span></div>
        ${capRow}
        <div class="tooltip-row"><span>${growthLabel}</span> <span>${signGrowth}</span></div>
      `;
    }

    var pulseStyle = is_pending ? 'animation: echo-pulse 1.5s infinite;' : '';

    var iconHtml = `<div class="echo-tooltip"><svg width="20" height="20" viewBox="0 0 20 20" style="${pulseStyle}"><circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" /><circle cx="10" cy="10" r="8" fill="none" stroke="${q_color}" stroke-width="2" stroke-dasharray="${dash_array}" stroke-dashoffset="${dash_offset}" transform="rotate(-90 10 10)" stroke-linecap="round" /><path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" /></svg><div class="tooltip-box" style="width:310px;"><div class="tooltip-title">AUTHENTIFICATION</div><div class="tooltip-row"><span>🔐 Source:</span> <span>${stats.auth_sources ? stats.auth_sources.join(', ') : 'N/A'}</span></div><div class="tooltip-row"><span>👤 Compte:</span> <span>${stats.user_email || 'N/A'}</span></div>${tier_html}${proj_html}<div class="tooltip-row"><span>🌐 Tenant:</span> <span style="color:#10b981;">${stats.tenant_id || 'ECHO'}</span></div><div class="tooltip-title" style="margin-top:8px;border-top:1px solid rgba(0,212,255,0.2);padding-top:6px;">QUOTAS</div><div class="tooltip-row"><span>💳 Crédits IA:</span> <b style="color:#10b981;">${stats.credits_val || 'N/A'}</b></div><div class="tooltip-row"><span>🤖 Modèle CA:</span> <span style="color:#a3a3a3;font-size:10px;">${stats.quota_model || '—'}</span></div>${q_rows_html}${rpd_html}${rpm_html}<div class="tooltip-row"><span>🏷️ Type:</span> <span style="color:#a3a3a3;font-size:10px;">${stats.quota_type || 'UNKNOWN'}</span></div></div></div>`;

    var barHtml = `<div class="echo-tooltip" style="min-width:180px;"><div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;"><div style="width:${cache_pct}%;background:#8b5cf6;"></div><div style="width:${prompt_pct}%;background:#10b981;"></div><div style="width:${gen_pct}%;background:#f59e0b;"></div></div>${deltaBadge}<div class="tooltip-box" style="width:260px;"><div class="tooltip-title">CONTEXTE ${is_pending ? '(CALCUL EN COURS...)' : ''}</div><div class="tooltip-row"><span>Cache:</span> <span>${c_t}</span></div><div class="tooltip-row"><span>Prompt:</span> <span>${active_p_t}</span></div><div class="tooltip-row"><span>Génération:</span> <span>${g_t}</span></div><div class="tooltip-row" style="font-weight:bold;margin-top:4px;"><span>Total:</span> <span>${total_t} / ${max_t}</span></div>${deltaTooltipRows}</div></div>`;

    hud.innerHTML = iconHtml + barHtml;

    if (window.EchoWM && window.EchoWM.registerMountedWindow && chatId) {
      window.EchoWM.registerMountedWindow('echo-nav-context-hud-wrapper', chatId);
    }
  };
"""

    @staticmethod
    async def deploy_context_gauge(
            events: Any,
            plan_name: str = "",
            credits_val: str = "N/A",
            quota_str: str = "",
            c_t: int = 0,
            active_p_t: int = 0,
            g_t: int = 0,
            max_t: int = ECHO_MAX_CONTEXT_SIZE,
            cache_pct: float = 0.0,
            prompt_pct: float = 0.0,
            gen_pct: float = 0.0,
            user_email: Optional[str] = None,
            user_tier: Optional[str] = None,
            project_id: Optional[str] = None,
            auth_sources: Optional[list] = None,
            quota_fraction: float = 1.0,
            quota_reset: str = "N/A",
            quota_type: str = "UNKNOWN",
            quota_model: str = "",
            quota_rpd_rem: str = "N/A",
            quota_rpd_lim: str = "N/A",
            quota_rpm_rem: str = "N/A",
            quota_rpm_lim: str = "N/A",
            quota_5h_fraction: Optional[float] = None,
            quota_5h_reset: str = "N/A",
            quota_5h_disabled: bool = False,
            quota_weekly_fraction: Optional[float] = None,
            quota_weekly_reset: str = "N/A",
            delta: Optional[Dict[str, Any]] = None,
            is_pending: bool = False,
            chat_id: Optional[str] = None):
        """Déploie ou met à jour le HUD contextuel ECHO de façon unifiée et non-bloquante."""
        payload = {
            "chat_id": chat_id or (getattr(events, "chat_id", None) if events else None),
            "tenant_id": ECHO_GLOBAL_TENANT_PROJECT_ID,
            "plan_name": plan_name,
            "credits_val": credits_val,
            "quota_str": quota_str,
            "cachedContentTokenCount": c_t,
            "promptTokenCount": (c_t + active_p_t),
            "candidatesTokenCount": g_t,
            "totalTokenCount": (c_t + active_p_t + g_t),
            "max_tokens": max_t,
            "user_email": user_email,
            "user_tier": user_tier,
            "project_id": project_id,
            "auth_sources": auth_sources or [],
            "quota_fraction": quota_fraction,
            "quota_reset": quota_reset,
            "quota_type": quota_type,
            "quota_model": quota_model,
            "quota_rpd_rem": quota_rpd_rem,
            "quota_rpd_lim": quota_rpd_lim,
            "quota_rpm_rem": quota_rpm_rem,
            "quota_rpm_lim": quota_rpm_lim,
            "quota_5h_fraction": quota_5h_fraction,
            "quota_5h_reset": quota_5h_reset,
            "quota_5h_disabled": quota_5h_disabled,
            "quota_weekly_fraction": quota_weekly_fraction,
            "quota_weekly_reset": quota_weekly_reset,
            "delta": delta or {},
            "is_pending": is_pending,
        }
        json_payload = std_json.dumps(payload).decode("utf-8")
        js_code = f"""
(function() {{
  {EchoUI.get_context_gauge_renderer_js().strip()}
  if (typeof window.echoRenderContextGauge === 'function') {{
    window.echoRenderContextGauge({json_payload});
  }}
}})();
"""
        await events.emit_execute(js_code)

    @staticmethod
    def show_image_js(b64: str, mime: str = "image/png",
                      title: str = "Aperçu Image") -> str:
        """Réutilise le moteur WebPlayer (HUD navigateur) pour afficher une image locale (Base64).
        Utiliser via events.call('execute', {'code': ...}).
        N'utilise pas HTMLResponse — retour 100% propre, sans pollution du contexte Gemini."""
        return EchoUI._generate_webplayer_js(
            b64=b64, mime=mime, metadata=[], current_url=title,
            hud_id="echo-preview", state_key="echo_preview_state", icon="🖼️"
        )

    @classmethod
    def image_viewer(
            cls,
            img_url: str,
            title: str = "Aperçu Image") -> HTMLResponse:
        """Maintenu pour les Actions OWUI. Pour les Tools, utiliser show_image_js() + events.call."""
        content = f"""
    <div id="hud-bar"><span style="font-weight:bold;">👁️ ECHO Vision Explorer</span></div>
    <div id="canvas-area" style="width:100%; height:600px; overflow:hidden; position:relative; background:#f1f5f9; display:flex; align-items:center; justify-content:center;">
      <img src="{img_url}" style="max-width:100%; max-height:100%;">
    </div>
    """
        html = cls._get_boilerplate(content, title)
        return HTMLResponse(
            content=html, headers={
                "Content-Disposition": "inline"})

    @classmethod
    def player_ui(cls, session_id: str, total_steps: int) -> HTMLResponse:
        content = "<div style='padding:20px;'>Interface Replay v5.136 active via Action.</div>"
        html = cls._get_boilerplate(content, "ECHO Navigation Replay")
        return HTMLResponse(
            content=html, headers={
                "Content-Disposition": "inline"})

    @classmethod
    def map_viewer(
            cls,
            query: str,
            title: str = "Localisation") -> HTMLResponse:
        """Affiche une carte Google Maps interactive via l'embed natif.
        Utilise conjointement avec le grounding googleMaps de Gemini (gemini_maps_grounding.py)."""
        from urllib.parse import quote
        safe_query = quote(query.strip())
        content = f"""
    <div id="hud-bar"><span style="font-weight:bold;">🗺️ ECHO Maps Explorer</span></div>
    <div style='width: 100%; height: 600px; background: white;'>
      <iframe width='100%' height='100%' frameborder='0' style='border:0;'
        src='https://www.google.com/maps?q={safe_query}&output=embed' allowfullscreen>
      </iframe>
    </div>
    """
        html = cls._get_boilerplate(content, title)
        return HTMLResponse(
            content=html, headers={
                "Content-Disposition": "inline"})

    @classmethod
    def generate_rich_view(
            cls,
            moteur: str,
            payload: str,
            title: str = "ECHO Rendu Visuel",
            cdn_timeout_ms: int = 30000) -> tuple:
        """Usine de rendu universelle (Restauration intégrale v5.121)."""
        from echo_visuals import VisualEngine
        cfg = VisualEngine.get_config(
            moteur, payload, cdn_timeout_ms=cdn_timeout_ms)

        styles_list = [s for s in cfg.get("scripts", []) if s.endswith('.css')]
        scripts_list = [
            s for s in cfg.get(
                "scripts",
                []) if not s.endswith('.css')]

        styles_html = "\n".join(
            [f'<link rel="stylesheet" href="{s}">' for s in styles_list])
        scripts_html = "\n".join(
            [f'<script src="{s}"></script>' for s in scripts_list])

        content = f"""
    <style>
      #visual-target {{ display: block; width: 100%; min-height: 400px; }}
      {cfg.get('style', '')}
    </style>
    {styles_html}
    {cfg.get('container', '<div id="visual-target"></div>')}
    {scripts_html}

    <script>
      {cfg.get('init', '')}
    </script>
    """
        html = cls._get_boilerplate(content, title)
        response = HTMLResponse(
            content=html, headers={
                "Content-Disposition": "inline"})
        return response, {"status": "success",
                          "message": f"Visualisation {moteur} générée."}

    @staticmethod
    def get_print_isolation_js(target_selectors: str) -> str:
        """Génère le script JS d'isolation CSS Path-Marking + window.print() natif."""
        return f"""
return new Promise(function(resolve) {{
    var STYLE_ID = 'echo-print-isolation-css';
    var chatContainer = document.querySelector('{target_selectors}');

    if (!chatContainer) {{
        resolve({{ success: false, error: 'Conteneur cible introuvable' }});
        return;
    }}

    var iframe = chatContainer.querySelector('iframe');
    if (iframe && iframe.contentWindow) {{
        iframe.contentWindow.postMessage('echo-print', '*');
        resolve({{ success: true, method: 'iframe_postMessage' }});
        return;
    }}

    var printStyle = document.createElement('style');
    printStyle.id = STYLE_ID;
    printStyle.textContent =
        '@media print {{\\n' +
        '  html, body.echo-printing {{\\n' +
        '    overflow: visible !important;\\n' +
        '    height: auto !important;\\n' +
        '    max-height: none !important;\\n' +
        '  }}\\n' +
        '  body.echo-printing > *:not(.echo-print-ancestor):not(.echo-print-target) {{\\n' +
        '    display: none !important;\\n' +
        '  }}\\n' +
        '  .echo-print-ancestor > *:not(.echo-print-ancestor):not(.echo-print-target) {{\\n' +
        '    display: none !important;\\n' +
        '  }}\\n' +
        '  .echo-print-ancestor {{\\n' +
        '    display: block !important;\\n' +
        '    position: static !important;\\n' +
        '    overflow: visible !important;\\n' +
        '    height: auto !important;\\n' +
        '    max-height: none !important;\\n' +
        '    width: 100% !important;\\n' +
        '    background: transparent !important;\\n' +
        '    padding: 0 !important;\\n' +
        '    margin: 0 !important;\\n' +
        '    border: none !important;\\n' +
        '    box-shadow: none !important;\\n' +
        '    transform: none !important;\\n' +
        '  }}\\n' +
        '  .echo-print-target {{\\n' +
        '    display: block !important;\\n' +
        '    position: static !important;\\n' +
        '    width: 100% !important;\\n' +
        '    height: auto !important;\\n' +
        '    max-height: none !important;\\n' +
        '    overflow: visible !important;\\n' +
        '    padding: 0 !important;\\n' +
        '    margin: 0 !important;\\n' +
        '  }}\\n' +
        '  .echo-print-target * {{\\n' +
        '    overflow: visible !important;\\n' +
        '    max-height: none !important;\\n' +
        '  }}\\n' +
        '  .echo-print-target iframe {{\\n' +
        '    overflow: visible !important;\\n' +
        '    max-height: none !important;\\n' +
        '  }}\\n' +
        '  @page {{ margin: 15mm; }}\\n' +
        '}}';
    document.head.appendChild(printStyle);

    var ancestors = [];
    var ancestor = chatContainer.parentElement;
    while (ancestor && ancestor !== document.body) {{
        ancestor.classList.add('echo-print-ancestor');
        ancestors.push(ancestor);
        ancestor = ancestor.parentElement;
    }}
    document.body.classList.add('echo-printing');
    chatContainer.classList.add('echo-print-target');

    var resolved = false;
    function cleanup(outcome) {{
        if (resolved) return;
        resolved = true;
        document.body.classList.remove('echo-printing');
        chatContainer.classList.remove('echo-print-target');
        ancestors.forEach(function(a) {{ a.classList.remove('echo-print-ancestor'); }});
        var styleEl = document.getElementById(STYLE_ID);
        if (styleEl) styleEl.remove();
        resolve(outcome);
    }}

    window.addEventListener('afterprint', function onAfterPrint() {{
        window.removeEventListener('afterprint', onAfterPrint);
        cleanup({{ success: true }});
    }});

    setTimeout(function() {{
        cleanup({{ success: true, timeout: true }});
    }}, 60000);

    setTimeout(function() {{
        window.print();
    }}, 3000);
}});
"""

    # =====================================================================
    # ECHO DOM SECURITY, MODALS & SANDBOX MONITOR
    # =====================================================================

    @staticmethod
    def get_sanitize_html_js() -> str:
        """Fournit le moteur JS d'assainissement HTML autonome pour la neutralisation des injections XSS."""
        return """
      window.echoSanitizeHTML = (html) => {
          if (typeof html !== 'string') return '';
          const doc = new DOMParser().parseFromString(html, 'text/html');
          const allowedTags = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'BR', 'P', 'SPAN', 'UL', 'OL', 'LI', 'CODE', 'PRE', 'DIV', 'A', 'HR', 'SMALL', 'BLOCKQUOTE']);
          const clean = (node) => {
              const children = Array.from(node.childNodes);
              for (const child of children) {
                  if (child.nodeType === Node.ELEMENT_NODE) {
                      if (child.tagName === 'SCRIPT' || child.tagName === 'STYLE' || child.tagName === 'IFRAME' || child.tagName === 'OBJECT' || child.tagName === 'EMBED') {
                          child.remove();
                          continue;
                      }
                      if (!allowedTags.has(child.tagName)) {
                          const textNode = document.createTextNode(child.textContent);
                          node.replaceChild(textNode, child);
                      } else {
                          const attrs = Array.from(child.attributes);
                          for (const attr of attrs) {
                              const name = attr.name.toLowerCase();
                              const val = (attr.value || '').toLowerCase().replace(/[\\s\\x00-\\x1f\\x7f-\\x9f]/g, '');
                              if (name.startsWith('on') || val.startsWith('javascript:') || val.startsWith('data:text/html') || val.startsWith('vbscript:')) {
                                  child.removeAttribute(attr.name);
                              }
                          }
                          if (child.tagName === 'A') {
                              child.target = '_blank';
                              child.rel = 'noopener noreferrer';
                          }
                          clean(child);
                      }
                  }
              }
          };
          clean(doc.body);
          return doc.body.innerHTML;
      };
      """

    @staticmethod
    def get_custom_modals_js() -> str:
        """Fournit le code JS autonome des modales natives asynchrones d'ECHO (Confirm & Prompt)."""
        return EchoUI.get_sanitize_html_js() + """
      window.echoCustomConfirm = (msg, arg2, arg3) => {
          const callback = typeof arg2 === 'function' ? arg2 : arg3;
          const timeoutSeconds = typeof arg2 === 'number' ? arg2 : 0;

          const isDark = document.documentElement.classList.contains('dark') || window.matchMedia('(prefers-color-scheme: dark)').matches;
          const bgColor = isDark ? '#1e1e2e' : '#ffffff';
          const borderColor = isDark ? '#444' : '#ddd';
          const textColor = isDark ? '#cdd6f4' : '#333';
          const accentColor = '#89b4fa';

          const overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:flex-start; justify-content:center; padding-top:10dvh; overflow-y:auto; font-family:"Segoe UI",system-ui,sans-serif;';
          const originalOverflow = document.body.style.overflow;
          document.body.style.overflow = 'hidden';
          const dialog = document.createElement('div');
          dialog.style.cssText = 'background:' + bgColor + '; border:1px solid ' + borderColor + '; padding:16px; border-radius:8px; text-align:left; box-shadow:0 10px 40px rgba(0,0,0,0.5); width:90%; max-width:450px; box-sizing:border-box; color:' + textColor + '; max-height:85vh; overflow-y:auto;';
          dialog.innerHTML = '<div style="margin-bottom:20px; font-size:14px; line-height:1.5;">' + window.echoSanitizeHTML(msg) + '</div>';
          const btnContainer = document.createElement('div');
          btnContainer.style.cssText = 'display:flex; justify-content:center; gap:10px; flex-wrap:wrap;';
          const btnCancel = document.createElement('button');
          btnCancel.textContent = 'Annuler';
          btnCancel.style.cssText = 'min-height:44px; display:inline-flex; align-items:center; justify-content:center; padding:0 16px; border-radius:4px; border:1px solid ' + borderColor + '; background:transparent; color:' + textColor + '; cursor:pointer; font-size:14px; flex: 1 1 45%;';
          const btnOk = document.createElement('button');
          btnOk.textContent = 'Confirmer';
          btnOk.style.cssText = 'min-height:44px; display:inline-flex; align-items:center; justify-content:center; padding:0 16px; border-radius:4px; border:none; background:' + accentColor + '; color:#1e1e2e; cursor:pointer; font-weight:600; font-size:14px; flex: 1 1 45%;';

          let timeRemaining = timeoutSeconds;
          let timerInterval = null;

          const cleanupAndResolve = (val) => {
              if (timerInterval) clearInterval(timerInterval);
              document.body.style.overflow = originalOverflow;
              overlay.remove();
              callback && callback(val);
          };

          const updateTimer = () => {
              const m = Math.floor(timeRemaining / 60);
              const s = timeRemaining % 60;
              btnCancel.textContent = 'Annuler (' + m + ':' + s.toString().padStart(2, '0') + ')';
          };

          if (timeoutSeconds > 0) {
              updateTimer();
              timerInterval = setInterval(() => {
                  timeRemaining--;
                  if (timeRemaining <= 0) {
                      cleanupAndResolve(false);
                  } else {
                      updateTimer();
                  }
              }, 1000);
          }

          btnCancel.onclick = () => cleanupAndResolve(false);
          btnOk.onclick = () => cleanupAndResolve(true);

          btnContainer.appendChild(btnCancel);
          btnContainer.appendChild(btnOk);
          dialog.appendChild(btnContainer);
          overlay.appendChild(dialog);
          document.body.appendChild(overlay);
      };

      window.echoCustomPrompt = (msg, timeoutSeconds, options, callback) => {
              const isDark = document.documentElement.classList.contains('dark') || window.matchMedia('(prefers-color-scheme: dark)').matches;
              const bgColor = isDark ? '#1e1e2e' : '#ffffff';
              const borderColor = isDark ? '#444' : '#ddd';
              const textColor = isDark ? '#cdd6f4' : '#333';
              const accentColor = '#89b4fa';

              const overlay = document.createElement('div');
              overlay.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.5); z-index:999999; display:flex; align-items:flex-start; justify-content:center; padding-top:10dvh; overflow-y:auto; font-family:"Segoe UI",system-ui,sans-serif;';
              const originalOverflow = document.body.style.overflow;
              document.body.style.overflow = 'hidden';
              const dialog = document.createElement('div');
              dialog.style.cssText = 'background:' + bgColor + '; border:1px solid ' + borderColor + '; padding:16px; border-radius:8px; text-align:left; box-shadow:0 10px 40px rgba(0,0,0,0.5); width:90%; max-width:500px; box-sizing:border-box; color:' + textColor + '; max-height:85vh; overflow-y:auto;';
              dialog.innerHTML = '<div style="margin-bottom:15px; font-size:14px; line-height:1.5; text-align:left;">' + window.echoSanitizeHTML(msg) + '</div>';

              const inputField = document.createElement('input');
              inputField.type = 'text';
              inputField.style.cssText = 'width:100%; box-sizing:border-box; padding:12px; margin-bottom:20px; border-radius:4px; border:1px solid ' + borderColor + '; background:rgba(0,0,0,0.2); color:' + textColor + '; outline:none; font-family:monospace; font-size:16px !important;';

              if (options && options.length > 0) {
                  const pillsContainer = document.createElement('div');
                  pillsContainer.style.cssText = 'display:flex; flex-wrap:wrap; gap:8px; margin-bottom:15px; justify-content:flex-start;';
                  options.forEach(opt => {
                      const pill = document.createElement('button');
                      pill.textContent = opt;
                      pill.style.cssText = 'min-height:44px; display:inline-flex; align-items:center; justify-content:center; padding:0 16px; border-radius:22px; border:1px solid ' + accentColor + '; background:rgba(137,180,250,0.1); color:' + accentColor + '; cursor:pointer; font-size:14px; transition:all 0.2s; white-space:nowrap; margin-bottom:4px;';
                      pill.onmouseover = () => { pill.style.background = accentColor; pill.style.color = '#1e1e2e'; };
                      pill.onmouseout = () => { pill.style.background = 'rgba(137,180,250,0.1)'; pill.style.color = accentColor; };
                      pill.onclick = () => {
                          inputField.value = opt;
                          cleanupAndResolve(opt);
                      };
                      pillsContainer.appendChild(pill);
                  });
                  dialog.appendChild(pillsContainer);
              }

              const btnContainer = document.createElement('div');
              btnContainer.style.cssText = 'display:flex; justify-content:space-between; gap:10px; flex-wrap:wrap;';

              const btnCancel = document.createElement('button');
              btnCancel.textContent = 'Annuler';
              btnCancel.style.cssText = 'min-height:44px; display:inline-flex; align-items:center; justify-content:center; padding:0 16px; border-radius:4px; border:1px solid ' + borderColor + '; background:transparent; color:' + textColor + '; cursor:pointer; font-size:14px; flex: 1 1 45%;';

              const btnOk = document.createElement('button');
              btnOk.textContent = 'Soumettre';
              btnOk.style.cssText = 'min-height:44px; display:inline-flex; align-items:center; justify-content:center; padding:0 16px; border-radius:4px; border:none; background:' + accentColor + '; color:#1e1e2e; cursor:pointer; font-weight:600; font-size:14px; flex: 1 1 45%;';

              let timeRemaining = timeoutSeconds;
              let timerInterval = null;

              const updateTimer = () => {
                  const m = Math.floor(timeRemaining / 60);
                  const s = timeRemaining % 60;
                  btnCancel.textContent = 'Annuler (' + m + ':' + s.toString().padStart(2, '0') + ')';
              };

              const cleanupAndResolve = (val) => {
                  if(timerInterval) clearInterval(timerInterval);
                  document.body.style.overflow = originalOverflow;
                  overlay.remove();
                  callback && callback(val);
              };

              if (timeoutSeconds > 0) {
                  updateTimer();
                  timerInterval = setInterval(() => {
                      timeRemaining--;
                      if (timeRemaining <= 0) {
                          cleanupAndResolve(null);
                      } else {
                          updateTimer();
                      }
                  }, 1000);
              }

          btnCancel.onclick = () => cleanupAndResolve(null);
          btnOk.onclick = () => cleanupAndResolve(inputField.value);
          inputField.onkeydown = (e) => { if(e.key === 'Enter') cleanupAndResolve(inputField.value); };

          dialog.appendChild(inputField);
          btnContainer.appendChild(btnCancel);
          btnContainer.appendChild(btnOk);
          dialog.appendChild(btnContainer);
          overlay.appendChild(dialog);
          document.body.appendChild(overlay);
          inputField.focus();
      };
      """

    @staticmethod
    def get_floating_monitor_js() -> str:
        """Fournit le code JS autonome de la fenêtre flottante ECHO Monitor (multiplexée par window_id).
        Hérite désormais nativement de EchoFloatingWindow pour bénéficier des fenêtres interactives.
        L'iframe est construite via le DOM (propriété srcdoc) : aucun échappement HTML manuel requis.
        allow-same-origin est volontairement exclu : une iframe srcdoc hériterait sinon de l'origine
        d'Open WebUI (accès localStorage/token de session depuis le code de la Sandbox)."""
        return EchoUI.get_floating_window_class_js() + """
      window._echoUIPayloads = window._echoUIPayloads || {};
      if (!window._echoUIAggregatorBound) {
          window._echoUIAggregatorBound = true;
          window.addEventListener('message', (event) => {
              if (event.data && event.data.echoUIPayload) {
                  const { window_id, payload } = event.data.echoUIPayload;
                  window._echoUIPayloads[window_id] = payload;
              }
              if (event.data && event.data.echoSignal === 'abort') {
                  window._echoUIPayloads['_echo_signal'] = 'abort';
              }
              if (event.data && event.data.echoPing) {
                  const wid = event.data.echoPing;
                  if (!window._echoUIPayloads['_pings']) window._echoUIPayloads['_pings'] = {};
                  window._echoUIPayloads['_pings'][wid] = Date.now();
              }
          });
      }
      window._echoGetAggregatedPayload = () => window._echoUIPayloads;

      window.echoCreateFloatingMonitor = (window_id, title, htmlContent, width, height) => {
          const hudId = 'echo-sandbox-monitor-hud-' + window_id;
          
          let monitor = window['echo_monitor_obj_' + window_id];
          if (!document.getElementById(hudId) || !monitor) {
              monitor = new window.EchoFloatingWindow({
                  id: hudId,
                  title: title,
                  icon: '🚀',
                  width: width || '500px',
                  height: height || '400px'
              });
              window['echo_monitor_obj_' + window_id] = monitor;
              
              monitor.onClose = () => {
                  delete window['echo_monitor_obj_' + window_id];
              };
              
              monitor.render();
              monitor.attachBaseEvents();
          } else {
              const titleEl = document.querySelector(`#${hudId}-header div`);
              if (titleEl) titleEl.textContent = title;
          }
          
          const body = document.getElementById(hudId + '-body');
          if (body) {
              const existingResizer = document.getElementById(hudId + '-resizer');
              body.innerHTML = '';
              const iframe = document.createElement('iframe');
              iframe.setAttribute('sandbox', 'allow-scripts allow-forms allow-modals allow-popups');
              iframe.style.cssText = 'width:100%; height:100%; border:none; display:block; flex:1;';
              const heartbeat = `<script>
                  setInterval(function(){
                      window.parent.postMessage({ echoPing: '${window_id}' }, '*');
                  }, 1000);
              </script>`;
              iframe.srcdoc = heartbeat + htmlContent;
              body.appendChild(iframe);
              if (existingResizer) body.appendChild(existingResizer);
          }
          return true;
      };
      """

    # =====================================================================
    # ECHO COGNITIVE MONITOR — HUD Sub-Agent Visualization
    # =====================================================================

    @staticmethod
    def _generate_agent_monitor_js(
        threads_json: str,
        chat_id: str,
        message_id: str = "",
        model_id: str = "echo_orchestrator",
    ) -> str:
        """Génère le script JS complet du HUD Cognitive Monitor.
        Injection via __event_call__(type: 'execute', data: code: ...).

        Affiche les threads cognitifs (delegates, experts, conseils) sous forme
        d'onglets verticaux avec arbre d'appels expand/collapse.
        3 contrôles : refresh manuel, auto-refresh slider 2-15s, réduire."""

        return (
            "(function() {\n"
            "  const HUD_ID = 'echo-cognitive-monitor';\n"
            + EchoUI.get_mobile_guard_js('echo-cognitive-monitor') + "\n"
            + EchoUI.get_hud_geometry_js() + "\n"
            + EchoUI.get_hud_rpc_bridge_js() + "\n"
            "  const CID = '" + chat_id + "';\n"
            "  const MID = '" + message_id + "';\n"
            "  const MODEL_ID = '" + model_id + "';\n"
            "  const STATE_KEY = 'echo_cogmon_' + CID;\n"
            "\n"
            "  var existing = document.getElementById(HUD_ID);\n"
            "  if (existing) existing.remove();\n"
            "\n"
            "  var threads = " + threads_json + ";\n"
            "\n"
            "  var activeThreadIdx = 0;\n"
            "  var expandedNodes = {};\n"
            "  var autoRefreshTimer = null;\n"
            "  var isMinimized = false;\n"
            "  var geoCtrl = null;\n"
            "\n"
            "  var saved = {};\n"
            "  try { saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch(e) {}\n"
            "  var autoInterval = saved.interval || 5;\n"
            "  var autoEnabled = saved.autoOn || false;\n"
            "  // Forcé à false au lancement pour éviter le bug de la fenêtre vide après restauration\n"
            "  isMinimized = false;\n"
            "  if (saved.activeIdx !== undefined) activeThreadIdx = saved.activeIdx;\n"
            "  if (activeThreadIdx >= threads.length) activeThreadIdx = Math.max(0, threads.length - 1);\n"
            "  if (saved.expanded) try { expandedNodes = JSON.parse(saved.expanded); } catch(e) {}\n"
            "\n"
            "  var rpcBridge = window.echoCreateHudRpcBridge({\n"
            "    hudId: HUD_ID,\n"
            "    actionId: 'agent_monitor_action',\n"
            "    resolveVar: 'echoAgentResolve',\n"
            "    sendFn: 'sendAgentAction',\n"
            "    readyFn: '_echoAgentOnReady',\n"
            "    cid: CID,\n"
            "    mid: MID,\n"
            "    modelId: MODEL_ID,\n"
            "    onRouteClose: function() { if (autoRefreshTimer) clearInterval(autoRefreshTimer); }\n"
            "  });\n"
            "\n"
            "  var isDark = document.documentElement.classList.contains('dark') ||\n"
            "               window.matchMedia('(prefers-color-scheme: dark)').matches;\n"
            "  var C = {\n"
            "    bg:       isDark ? '#1a1b2e' : '#ffffff',\n"
            "    headerBg: isDark ? 'rgba(26,27,46,0.97)' : 'rgba(245,245,250,0.97)',\n"
            "    sidebarBg:isDark ? '#151626' : '#f5f5fa',\n"
            "    text:     isDark ? '#e2e8f0' : '#1e293b',\n"
            "    textMuted:isDark ? '#94a3b8' : '#64748b',\n"
            "    border:   isDark ? '#2d3748' : '#e2e8f0',\n"
            "    accent:   '#38bdf8',\n"
            "    hoverBg:  isDark ? 'rgba(56,189,248,0.08)' : 'rgba(56,189,248,0.06)',\n"
            "    success:  '#10b981',\n"
            "    error:    '#ef4444',\n"
            "    warning:  '#f59e0b',\n"
            "    cyan:     '#38bdf8',\n"
            "  };\n"
            "\n"
            "  function esc(s) { return (s||'').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }\n"
            "  function trunc(s, n) { s = s || ''; return s.length > n ? s.substring(0, n) + '\\u2026' : s; }\n"
            "  function fmtTime(ts) {\n"
            "    if (!ts) return '';\n"
            "    var d = new Date(ts * 1000);\n"
            "    var p = function(n){return ('0'+n).slice(-2);};\n"
            "    return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());\n"
            "  }\n"
            "  function saveState() {\n"
            "    var hud = document.getElementById(HUD_ID);\n"
            "    if (!hud) return;\n"
            "    var nb = geoCtrl ? geoCtrl.normalBounds : { x: '60px', y: '60px', w: '720px', h: '500px' };\n"
            "    localStorage.setItem(STATE_KEY, JSON.stringify({\n"
            "      x: nb.x, y: nb.y,\n"
            "      w: nb.w, h: nb.h,\n"
            "      interval: autoInterval, autoOn: autoEnabled, min: isMinimized,\n"
            "      activeIdx: activeThreadIdx, expanded: JSON.stringify(expandedNodes)\n"
            "    }));\n"
            "  }\n"
            "\n"
            "  // =============== SIDEBAR ONGLETS VERTICAUX ===============\n"
            "  function renderSidebar() {\n"
            "    var sb = document.getElementById(HUD_ID + '-sidebar');\n"
            "    if (!sb) return;\n"
            "    sb.innerHTML = '';\n"
            "    threads.forEach(function(t, i) {\n"
            "      var isActive = (i === activeThreadIdx);\n"
            "      var tab = document.createElement('div');\n"
            "      tab.style.cssText = 'padding:8px 10px; cursor:pointer; border-left:3px solid ' + (isActive ? t.color : 'transparent') + ';"
            " background:' + (isActive ? C.hoverBg : 'transparent') + '; transition:all 0.15s; margin:2px 0;';\n"
            "      tab.innerHTML = '<div style=\"font-size:16px; text-align:center;\">' + t.icon + '</div>'\n"
            "        + '<div style=\"font-size:10px; color:' + t.color + '; text-align:center; font-family:monospace;"
            " overflow:hidden; text-overflow:ellipsis; white-space:nowrap;\">' + t.sid.substring(0, 10) + '</div>'\n"
            "        + '<div style=\"font-size:9px; color:' + C.textMuted + '; text-align:center;\">' + esc(t.label) + '</div>'\n"
            "        + '<div style=\"font-size:9px; color:' + C.textMuted + '; text-align:center; margin-top:2px;"
            " background:rgba(255,255,255,0.05); border-radius:8px; padding:1px 4px;\">' + t.steps_count + '</div>';\n"
            "      tab.onmouseenter = function() { if (!isActive) tab.style.background = C.hoverBg; };\n"
            "      tab.onmouseleave = function() { if (!isActive) tab.style.background = 'transparent'; };\n"
            "      tab.onclick = function() { activeThreadIdx = i; renderSidebar(); renderTree(); saveState(); };\n"
            "      sb.appendChild(tab);\n"
            "    });\n"
            "  }\n"
            "\n"
            "  // =============== ARBRE D'APPELS ===============\n"
            "  function renderTree() {\n"
            "    var tree = document.getElementById(HUD_ID + '-tree');\n"
            "    if (!tree || !threads.length) { if(tree) tree.innerHTML = '<div style=\"padding:20px; color:' + C.textMuted + ';\">Aucun thread.</div>'; return; }\n"
            "    var t = threads[activeThreadIdx];\n"
            "    var html = '<div style=\"padding:12px 16px; border-bottom:1px solid ' + C.border + '; display:flex; align-items:center; gap:8px;\">'\n"
            "      + '<span style=\"font-size:18px;\">' + t.icon + '</span>'\n"
            "      + '<div><div style=\"font-weight:600; font-size:13px; color:' + t.color + ';\">' + esc(t.label) + '</div>'\n"
            "      + '<div style=\"font-size:11px; color:' + C.textMuted + '; font-family:monospace;\">' + t.sid + ' \\u00b7 ' + t.steps_count + ' \\u00e9tapes \\u00b7 ' + fmtTime(t.updated_at) + '</div></div></div>';\n"
            "\n"
            "    html += '<div id=\"' + HUD_ID + '-tree-scroll\" style=\"padding:8px 12px; overflow-y:auto; flex:1;\">';\n"
            "\n"
            "    if (!t.nodes || t.nodes.length === 0) {\n"
            "      html += '<div style=\"color:' + C.textMuted + '; font-style:italic; padding:16px;\">Thread vide.</div>';\n"
            "    } else {\n"
            "      t.nodes.forEach(function(node, ni) {\n"
            "        var nodeId = t.sid + '_' + ni;\n"
            "        var isExpanded = !!expandedNodes[nodeId];\n"
            "        var icon = '', label = '', detail = '', color = C.text, indent = 0;\n"
            "\n"
            "        if (node.type === 'text') {\n"
            "          if (node.role === 'user' && ni === 0) { icon = '\\ud83d\\udccb'; label = 'T\\u00e2che'; color = C.accent; }\n"
            "          else if (node.role === 'model') {\n"
            "            icon = '\\ud83d\\udcac';\n"
            "            label = node.expert_alias ? node.expert_alias : 'R\\u00e9ponse';\n"
            "            color = node.expert_alias ? '#a78bfa' : C.success;\n"
            "          } else { icon = '\\ud83d\\udcad'; label = node.role === 'user' ? 'User' : 'Model'; color = C.textMuted; }\n"
            "          detail = esc(node.content || '');\n"
            "        } else if (node.type === 'worker_branch') {\n"
            "          icon = '\\ud83d\\udc77'; label = 'Worker'; color = C.cyan;\n"
            "          detail = esc(node.content || '');\n"
            "        } else if (node.type === 'functionCall') {\n"
            "          icon = '\\ud83d\\udd27'; label = node.fn_name || '?'; color = C.cyan; indent = 1;\n"
            "          var args = node.fn_args || {};\n"
            "          var argParts = [];\n"
            "          for (var k in args) { if (args.hasOwnProperty(k)) argParts.push(k + ': ' + esc(trunc(args[k], 80))); }\n"
            "          detail = argParts.join(' \\u00b7 ');\n"
            "        } else if (node.type === 'functionResponse') {\n"
            "          var isOk = (node.status === 'ok' || node.status === 'success' || node.status === true);\n"
            "          icon = isOk ? '\\u2705' : '\\u274c'; label = node.fn_name || '?'; indent = 2;\n"
            "          color = isOk ? C.success : C.error;\n"
            "          detail = esc(trunc(node.content || '', 200));\n"
            "        } else if (node.type === 'escalation') {\n"
            "          icon = '\\ud83d\\ude80'; label = 'Escalade cognitive'; color = C.warning;\n"
            "          detail = esc(node.content || '');\n"
            "        } else if (node.type === 'question') {\n"
            "          icon = '\\u2753'; label = 'Question en attente'; color = C.warning;\n"
            "          detail = esc(node.content || '');\n"
            "        } else {\n"
            "          icon = '\\u00b7'; label = node.type || '?'; detail = '';\n"
            "        }\n"
            "\n"
            "        if (node.indent_override !== undefined) indent = node.indent_override;\n"
            "        var marginLeft = indent * 20;\n"
            "        var connector = indent > 0 ? '<span style=\"color:' + C.border + '; margin-right:4px;\">' + (indent > 1 ? '\\u2514\\u2500' : '\\u251c\\u2500\\u2500') + '</span>' : '';\n"
            "        var expandable = detail.length > 60;\n"
            "        var displayDetail = isExpanded ? detail : trunc(detail, 60);\n"
            "        var ts = node.timestamp ? '<span style=\"font-size:9px; color:' + C.textMuted + '; margin-left:auto; flex-shrink:0;\">' + fmtTime(node.timestamp) + '</span>' : '';\n"
            "\n"
            "        html += '<div data-nodeid=\"' + nodeId + '\" style=\"display:flex; align-items:flex-start; gap:6px; padding:4px 6px; margin-left:' + marginLeft + 'px;'\n"
            "          + ' border-radius:6px; cursor:' + (expandable ? 'pointer' : 'default') + '; transition:background 0.12s;\"'\n"
            "          + ' onmouseenter=\"this.style.background=\\'' + C.hoverBg + '\\';\"'\n"
            "          + ' onmouseleave=\"this.style.background=\\'transparent\\';\"'\n"
            "          + '>'\n"
            "          + connector\n"
            "          + '<span style=\"flex-shrink:0;\">' + icon + '</span>'\n"
            "          + '<span style=\"font-size:12px; font-weight:600; color:' + color + '; flex-shrink:0;\">' + esc(label) + '</span>'\n"
            "          + '<span style=\"font-size:11px; color:' + C.textMuted + '; overflow:hidden; word-break:break-word;\">' + displayDetail\n"
            "          + (expandable && !isExpanded ? ' <span style=\"color:' + C.accent + '; font-size:10px;\">\\u25b8</span>' : '')\n"
            "          + '</span>'\n"
            "          + ts\n"
            "          + '</div>';\n"
            "      });\n"
            "    }\n"
            "    html += '</div>';\n"
            "    tree.innerHTML = html;\n"
            "\n"
            "    // Attach click handlers for expand/collapse\n"
            "    tree.querySelectorAll('[data-nodeid]').forEach(function(el) {\n"
            "      el.onclick = function() {\n"
            "        var nid = el.getAttribute('data-nodeid');\n"
            "        if (expandedNodes[nid]) delete expandedNodes[nid];\n"
            "        else expandedNodes[nid] = true;\n"
            "        renderTree();\n"
            "        saveState();\n"
            "      };\n"
            "    });\n"
            "  }\n"
            "\n"
            "  // =============== CONSTRUCTION DU HUD ===============\n"
            "  var hud = document.createElement('div');\n"
            "  hud.id = HUD_ID;\n"
            "  hud.setAttribute('data-chat-id', CID);\n"
            "  hud.style.cssText = 'position:fixed; z-index:10001; display:flex; flex-direction:column;'\n"
            "    + ' background:' + C.bg + '; border:1px solid ' + C.border + '; border-radius:12px;'\n"
            "    + ' box-shadow:0 20px 60px rgba(0,0,0,0.4); font-family:Segoe UI,system-ui,sans-serif;'\n"
            "    + ' color:' + C.text + '; overflow:hidden; resize:both; min-width:500px; min-height:200px;';\n"
            "\n"
            "  // --- HEADER ---\n"
            "  var header = document.createElement('div');\n"
            "  header.id = HUD_ID + '-header';\n"
            "  header.style.cssText = 'display:flex; align-items:center; padding:8px 14px; gap:10px;'\n"
            "    + ' background:' + C.headerBg + '; border-bottom:1px solid ' + C.border + '; cursor:move;'\n"
            "    + ' user-select:none; flex-shrink:0; min-height:42px;';\n"
            "  header.innerHTML = '<span style=\"font-size:16px;\">\\ud83e\\udde0</span>'\n"
            "    + '<span style=\"font-weight:600; font-size:13px; flex:1;\">Cognitive Monitor</span>'\n"
            "    + '<button id=\"' + HUD_ID + '-refresh\" title=\"Rafra\\u00eechir\" style=\"background:none; border:none; color:' + C.text + '; cursor:pointer; font-size:14px;\">\\ud83d\\udd04</button>'\n"
            "    + '<button id=\"' + HUD_ID + '-auto-toggle\" title=\"Auto-refresh\" style=\"background:none; border:1px solid ' + C.border + '; color:' + C.textMuted + '; cursor:pointer; font-size:11px; padding:2px 6px; border-radius:4px;\">\\u25b6</button>'\n"
            "    + '<input id=\"' + HUD_ID + '-auto-slider\" type=\"range\" min=\"2\" max=\"15\" value=\"' + autoInterval + '\" title=\"Intervalle auto-refresh\" style=\"width:60px; accent-color:' + C.accent + '; cursor:pointer;\" />'\n"
            "    + '<span id=\"' + HUD_ID + '-auto-label\" style=\"font-size:10px; color:' + C.textMuted + '; min-width:22px;\">' + autoInterval + 's</span>'\n"
            "    + '<button id=\"' + HUD_ID + '-minimize\" title=\"R\\u00e9duire\" style=\"background:none; border:none; color:' + C.textMuted + '; cursor:pointer; font-size:16px;\">\\u2014</button>'\n"
            "    + '<button id=\"' + HUD_ID + '-close\" title=\"Fermer\" style=\"background:none; border:none; color:' + C.error + '; cursor:pointer; font-size:18px;\">\\u00d7</button>';\n"
            "  hud.appendChild(header);\n"
            "\n"
            "  // --- BODY ---\n"
            "  var body = document.createElement('div');\n"
            "  body.id = HUD_ID + '-body';\n"
            "  body.style.cssText = 'display:' + (isMinimized ? 'none' : 'flex') + '; flex:1; overflow:hidden;';\n"
            "\n"
            "  var sidebar = document.createElement('div');\n"
            "  sidebar.id = HUD_ID + '-sidebar';\n"
            "  sidebar.style.cssText = 'width:80px; background:' + C.sidebarBg + '; border-right:1px solid ' + C.border + ';'\n"
            "    + ' overflow-y:auto; flex-shrink:0; scrollbar-width:thin;';\n"
            "\n"
            "  var treePanel = document.createElement('div');\n"
            "  treePanel.id = HUD_ID + '-tree';\n"
            "  treePanel.style.cssText = 'flex:1; overflow-y:auto; display:flex; flex-direction:column; scrollbar-width:thin;';\n"
            "\n"
            "  body.appendChild(sidebar);\n"
            "  body.appendChild(treePanel);\n"
            "  hud.appendChild(body);\n"
            "\n"
            "  // --- STATUS BAR ---\n"
            "  var statusBar = document.createElement('div');\n"
            "  statusBar.id = HUD_ID + '-status';\n"
            "  statusBar.style.cssText = 'display:' + (isMinimized ? 'none' : 'flex') + '; align-items:center; padding:4px 14px;'\n"
            "    + ' background:' + C.sidebarBg + '; border-top:1px solid ' + C.border + '; font-size:11px;'\n"
            "    + ' color:' + C.textMuted + '; font-family:monospace; flex-shrink:0; gap:12px;';\n"
            "  var totalSteps = threads.reduce(function(s, t) { return s + (t.steps_count || 0); }, 0);\n"
            "  var lastUpdate = threads.length ? fmtTime(Math.max.apply(null, threads.map(function(t) { return t.updated_at || 0; }))) : '';\n"
            "  statusBar.innerHTML = '<span>\\ud83d\\udcca ' + threads.length + ' thread' + (threads.length > 1 ? 's' : '') + '</span>'\n"
            "    + '<span>|</span>'\n"
            "    + '<span>' + totalSteps + ' \\u00e9tapes</span>'\n"
            "    + '<span>|</span>'\n"
            "    + '<span>' + lastUpdate + '</span>'\n"
            "    + '<span style=\"flex:1;\"></span>'\n"
            "    + '<span id=\"' + HUD_ID + '-auto-status\" style=\"color:' + (autoEnabled ? C.success : C.textMuted) + ';\">' + (autoEnabled ? '\\u25cf Auto' : '\\u25cb Manuel') + '</span>';\n"
            "  hud.appendChild(statusBar);\n"
            "\n"
            "  document.body.appendChild(hud);\n"
            "\n"
            "  // =============== ÉVÉNEMENTS ===============\n"
            "  geoCtrl = window.echoBindHudGeometry(hud, header, {\n"
            "    defaultW: '720px',\n"
            "    defaultH: '500px',\n"
            "    defaultX: '60px',\n"
            "    defaultY: '60px',\n"
            "    defaultTx: 'none',\n"
            "    minW: '500px',\n"
            "    minH: '200px',\n"
            "    minimizedH: '42px',\n"
            "    minimizedMinH: '42px',\n"
            "    initialBounds: saved,\n"
            "    onSave: function() { saveState(); },\n"
            "    onMinimize: function(isMin) {\n"
            "      isMinimized = isMin;\n"
            "      body.style.display = isMin ? 'none' : 'flex';\n"
            "      statusBar.style.display = isMin ? 'none' : 'flex';\n"
            "    }\n"
            "  });\n"
            "\n"
            "  // Refresh\n"
            "  document.getElementById(HUD_ID + '-refresh').onclick = function() {\n"
            "    window.sendAgentAction({action: 'refresh'});\n"
            "  };\n"
            "\n"
            "  // Auto-refresh\n"
            "  var toggleBtn = document.getElementById(HUD_ID + '-auto-toggle');\n"
            "  var slider = document.getElementById(HUD_ID + '-auto-slider');\n"
            "  var autoLabel = document.getElementById(HUD_ID + '-auto-label');\n"
            "  var autoStatusEl = document.getElementById(HUD_ID + '-auto-status');\n"
            "\n"
            "  function updateAutoState() {\n"
            "    toggleBtn.textContent = autoEnabled ? '\\u23f8' : '\\u25b6';\n"
            "    toggleBtn.style.borderColor = autoEnabled ? C.success : C.border;\n"
            "    toggleBtn.style.color = autoEnabled ? C.success : C.textMuted;\n"
            "    if (autoStatusEl) {\n"
            "      autoStatusEl.textContent = autoEnabled ? '\\u25cf Auto ' + autoInterval + 's' : '\\u25cb Manuel';\n"
            "      autoStatusEl.style.color = autoEnabled ? C.success : C.textMuted;\n"
            "    }\n"
            "    if (autoRefreshTimer) { clearInterval(autoRefreshTimer); autoRefreshTimer = null; }\n"
            "    if (autoEnabled) {\n"
            "      autoRefreshTimer = setInterval(function() {\n"
            "        window.sendAgentAction({action: 'refresh'});\n"
            "      }, autoInterval * 1000);\n"
            "    }\n"
            "    saveState();\n"
            "  }\n"
            "\n"
            "  toggleBtn.onclick = function() { autoEnabled = !autoEnabled; updateAutoState(); };\n"
            "  slider.oninput = function() {\n"
            "    autoInterval = parseInt(slider.value);\n"
            "    autoLabel.textContent = autoInterval + 's';\n"
            "    if (autoEnabled) updateAutoState();\n"
            "    saveState();\n"
            "  };\n"
            "\n"
            "  // Minimize\n"
            "  document.getElementById(HUD_ID + '-minimize').onclick = function() {\n"
            "    geoCtrl.toggleMinimize();\n"
            "  };\n"
            "\n"
            "  document.getElementById(HUD_ID + '-close').onclick = function() {\n"
            "    if (rpcBridge) rpcBridge.cleanup();\n"
            "    if (autoRefreshTimer) clearInterval(autoRefreshTimer);\n"
            "    hud.remove();\n"
            "    window.sendAgentAction({action: 'close'});\n"
            "  };\n"
            "\n"
            "  // =============== STATUS BAR UPDATE ===============\n"
            "  function updateStatusBar() {\n"
            "    var sb = document.getElementById(HUD_ID + '-status');\n"
            "    if (!sb) return;\n"
            "    var totalSteps = threads.reduce(function(s, t) { return s + (t.steps_count || 0); }, 0);\n"
            "    var lastUpdate = threads.length ? fmtTime(Math.max.apply(null, threads.map(function(t) { return t.updated_at || 0; }))) : '';\n"
            "    sb.innerHTML = '<span>\\ud83d\\udcca ' + threads.length + ' thread' + (threads.length > 1 ? 's' : '') + '</span>'\n"
            "      + '<span>|</span>'\n"
            "      + '<span>' + totalSteps + ' \\u00e9tapes</span>'\n"
            "      + '<span>|</span>'\n"
            "      + '<span>' + lastUpdate + '</span>'\n"
            "      + '<span style=\"flex:1;\"></span>'\n"
            "      + '<span id=\"' + HUD_ID + '-auto-status\" style=\"color:' + (autoEnabled ? C.success : C.textMuted) + ';\">' + (autoEnabled ? '\\u25cf Auto ' + autoInterval + 's' : '\\u25cb Manuel') + '</span>';\n"
            "    autoStatusEl = document.getElementById(HUD_ID + '-auto-status');\n"
            "  }\n"
            "\n"
            "  // =============== GLOBAL API — Mise à jour live ===============\n"
            "  window.echoMonitorUpdate = function(newThreads) {\n"
            "    threads = newThreads;\n"
            "    if (activeThreadIdx >= threads.length) activeThreadIdx = Math.max(0, threads.length - 1);\n"
            "    renderSidebar();\n"
            "    renderTree();\n"
            "    updateStatusBar();\n"
            "    var scrollArea = document.getElementById(HUD_ID + '-tree-scroll');\n"
            "    if (scrollArea) scrollArea.scrollTop = scrollArea.scrollHeight;\n"
            "  };\n"
            "\n"
            "  // =============== INIT ===============\n"
            "  renderSidebar();\n"
            "  renderTree();\n"
            "  updateAutoState();\n"
            "  var initialScroll = document.getElementById(HUD_ID + '-tree-scroll');\n"
            "  if (initialScroll) initialScroll.scrollTop = initialScroll.scrollHeight;\n"
            "})();\n")

    # =====================================================================
    # ECHO IDENTITY VAULT — HUD Visualization
    # =====================================================================

    @staticmethod
    def _generate_identity_vault_js(
            accounts_json: str,
            schemas_json: str = "{}",
            chat_id: str = "",
            message_id: str = "",
            model_id: str = "echo_orchestrator") -> str:
        """Génère le script JS complet du HUD ECHO Identity Vault avec schémas dynamiques."""

        return (
            "(function() {\n"
            "  const HUD_ID = 'echo-vault-identity';\n"
            "  const CID = '" + chat_id + "';\n"
            "  const MID = '" + message_id + "';\n"
            "  const MODEL_ID = '" + model_id + "';\n"
            + EchoUI.get_mobile_guard_js('echo-vault-identity') + "\n"
            + EchoUI.get_floating_window_class_js() + "\n"
            + EchoUI.get_hud_rpc_bridge_js() + "\n"
            + EchoUI.get_sanitize_html_js() + "\n"
            "  if (document.getElementById(HUD_ID)) document.getElementById(HUD_ID).remove();\n"
            "  const rpcBridge = window.echoCreateHudRpcBridge({\n"
            "    hudId: HUD_ID,\n"
            "    actionId: 'echo_identity_vault_action',\n"
            "    resolveVar: 'echoVaultResolve',\n"
            "    sendFn: 'sendVaultAction',\n"
            "    readyFn: '_echoVaultOnReady',\n"
            "    cid: CID,\n"
            "    mid: MID,\n"
            "    modelId: MODEL_ID\n"
            "  });\n"
            "  let accounts = JSON.parse('" + accounts_json.replace("\\", "\\\\").replace("'", "\\'") + "');\n"
            "  let schemas = JSON.parse('" + schemas_json.replace("\\", "\\\\").replace("'", "\\'") + "');\n"
            "  const isDark = document.documentElement.classList.contains('dark') || document.documentElement.classList.contains('oled-dark');\n"
            "  \n"
            "  const vaultConfirm = function(msg, callback) {\n"
            "    const overlay = document.createElement('div');\n"
            "    overlay.style.cssText = 'position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,0.5); z-index:20000; display:flex; align-items:center; justify-content:center;';\n"
            "    const dialog = document.createElement('div');\n"
            "    dialog.style.cssText = 'background:' + (isDark ? '#262626' : '#f9f9f9') + '; border:1px solid ' + (isDark ? '#404040' : '#e5e5e5') + '; padding:16px; border-radius:8px; text-align:left; box-shadow:0 10px 40px rgba(0,0,0,0.5); width:90%; max-width:450px; box-sizing:border-box; color:' + (isDark ? '#ececec' : '#171717') + '; font-family:system-ui,sans-serif; max-height:85vh; overflow-y:auto;';\n"
            "    const cleanMsg = window.echoSanitizeHTML ? window.echoSanitizeHTML(msg) : msg;\n"
            "    dialog.innerHTML = '<div style=\"margin-bottom:20px; font-size:14px; line-height:1.5;\">' + cleanMsg + '</div>';\n"
            "    const btnContainer = document.createElement('div');\n"
            "    btnContainer.style.cssText = 'display:flex; justify-content:center; gap:10px; flex-wrap:wrap;';\n"
            "    const btnCancel = document.createElement('button');\n"
            "    btnCancel.textContent = 'Annuler';\n"
            "    btnCancel.style.cssText = 'padding:6px 14px; border-radius:4px; border:1px solid ' + (isDark ? '#404040' : '#e5e5e5') + '; background:transparent; color:' + (isDark ? '#ececec' : '#171717') + '; cursor:pointer; font-size:13px;';\n"
            "    const btnOk = document.createElement('button');\n"
            "    btnOk.textContent = 'Confirmer';\n"
            "    btnOk.style.cssText = 'padding:6px 14px; border-radius:4px; border:none; background:#89b4fa; color:#1e1e2e; cursor:pointer; font-weight:600; font-size:13px;';\n"
            "    btnCancel.onclick = function() { overlay.remove(); if(callback) callback(false); };\n"
            "    btnOk.onclick = function() { overlay.remove(); if(callback) callback(true); };\n"
            "    btnContainer.appendChild(btnCancel);\n"
            "    btnContainer.appendChild(btnOk);\n"
            "    dialog.appendChild(btnContainer);\n"
            "    overlay.appendChild(dialog);\n"
            "    document.body.appendChild(overlay);\n"
            "  };\n"
            "  const vaultAlert = function(msg) {\n"
            "    vaultConfirm(msg, function(){});\n"
            "  };\n"
            "  \n"
            "  const css = `\n"
            "    .vault-table { width: 100%; border-collapse: collapse; font-size: 13px; }\n"
            "    .vault-table th, .vault-table td { text-align: left; padding: 8px; border-bottom: 1px solid ${isDark ? '#404040' : '#e5e5e5'}; }\n"
            "    .vault-table th { color: ${isDark ? '#a3a3a3' : '#6b7280'}; font-weight: 500; }\n"
            "    .vault-badge { padding: 2px 6px; border-radius: 4px; font-size: 11px; font-weight: bold; }\n"
            "    .vault-badge.RO { background: rgba(34, 197, 94, 0.2); color: #22c55e; }\n"
            "    .vault-badge.RW { background: rgba(239, 68, 68, 0.2); color: #ef4444; }\n"
            "    .vault-btn-del { color: #ef4444; cursor: pointer; background: none; border: none; font-size: 14px; }\n"
            "    .vault-btn-del:hover { color: #b91c1c; }\n"
            "    .vault-form { display: flex; flex-direction: column; gap: 8px; background: ${isDark ? '#1a1a1a' : '#ffffff'}; padding: 12px; border: 1px solid ${isDark ? '#404040' : '#e5e5e5'}; border-radius: 6px; }\n"
            "    .vault-form input, .vault-form select { width: 100%; padding: 6px 8px; background: ${isDark ? '#262626' : '#f3f4f6'}; border: 1px solid ${isDark ? '#404040' : '#d1d5db'}; color: inherit; border-radius: 4px; box-sizing: border-box; font-size: 13px; }\n"
            "    .vault-form select { appearance: auto; }\n"
            "    .vault-btn { background: #3b82f6; color: white; border: none; padding: 8px; border-radius: 4px; cursor: pointer; font-weight: bold; font-size: 13px; transition: 0.2s; }\n"
            "    .vault-btn:hover { background: #2563eb; }\n"
            "  `;\n"
            "  \n"
            "  const style = document.createElement('style');\n"
            "  style.textContent = css;\n"
            "  document.head.appendChild(style);\n"
            "  \n"
            "  const bodyHtml = `\n"
            "    <div id=\"${HUD_ID}-content\" style=\"flex: 1; min-height: 0; padding: 16px; display: flex; flex-direction: column; gap: 16px; overflow-y: auto; background: ${isDark ? '#262626' : '#f9f9f9'}; color: ${isDark ? '#ececec' : '#171717'};\">\n"
            "      <div id=\"${HUD_ID}-list\"></div>\n"
            "      <div class=\"vault-form\">\n"
            "        <div id=\"vault-form-title\" style=\"font-weight:bold; margin-bottom:4px;\">➕ Configurer un service</div>\n"
            "        <div style=\"display:flex; flex-direction:column; gap:8px;\">\n"
            "          <select id=\"vault-input-service\" style=\"flex:1;\"></select>\n"
            "          <input type=\"text\" id=\"vault-input-account\" placeholder=\"Nom du Secret (ex: LINKEDIN_COOKIE)\" autocomplete=\"off\" spellcheck=\"false\" />\n"
            "        </div>\n"
            "        <div id=\"vault-dynamic-fields\" style=\"display:flex; flex-direction:column; gap:8px;\"></div>\n"
            "        <div style=\"display:flex; gap:8px;\">\n"
            "          <button class=\"vault-btn\" id=\"vault-btn-cancel\" style=\"flex:0 0 auto; background: #6b7280;\">Annuler</button>\n"
            "          <button class=\"vault-btn\" id=\"vault-btn-add\" style=\"flex:1;\">Enregistrer</button>\n"
            "        </div>\n"
            "      </div>\n"
            "    </div>\n"
            "  `;\n"
            "  \n"
            "  const monitor = new window.EchoFloatingWindow({\n"
            "      id: HUD_ID,\n"
            "      title: 'ECHO Identity Vault',\n"
            "      icon: '🔐',\n"
            "      width: '500px',\n"
            "      height: '80vh',\n"
            "      bodyHtml: bodyHtml,\n"
            "      onClose: () => { if (rpcBridge) rpcBridge.cleanup(); window.sendVaultAction({action: 'close'}); }\n"
            "  });\n"
            "  monitor.render();\n"
            "  const vaultEl = document.getElementById(HUD_ID);\n"
            "  if (vaultEl && CID) vaultEl.setAttribute('data-chat-id', CID);\n"
            "  monitor.attachBaseEvents();\n"
            "  \n"
            "  // Populate Select Service & Dynamic Fields\n"
            "  const serviceSelect = document.getElementById('vault-input-service');\n"
            "  const dynamicFieldsContainer = document.getElementById('vault-dynamic-fields');\n"
            "  \n"
            "  const categories = {};\n"
            "  const uncategorized = [];\n"
            "  Object.keys(schemas).forEach(key => {\n"
            "    const cat = schemas[key].category;\n"
            "    if (cat) {\n"
            "      if (!categories[cat]) categories[cat] = [];\n"
            "      categories[cat].push({key, name: schemas[key].name || key});\n"
            "    } else {\n"
            "      uncategorized.push({key, name: schemas[key].name || key});\n"
            "    }\n"
            "  });\n"
            "  const catLabels = {\n"
            "      'mcp_native': 'Modules Natifs (MCP)',\n"
            "      'mcp_resident': 'Serveurs Résidents (MCP)',\n"
            "      'mcp_ephemeral': 'Serveurs Éphémères (MCP)',\n"
            "      'mcp_remote': 'Serveurs Distants (MCP)',\n"
            "      'orchestration': 'Orchestration & Workflow'\n"
            "  };\n"
            "  Object.keys(categories).forEach(cat => {\n"
            "    const optgroup = document.createElement('optgroup');\n"
            "    optgroup.label = catLabels[cat] || cat;\n"
            "    categories[cat].forEach(item => {\n"
            "      const opt = document.createElement('option');\n"
            "      opt.value = item.key;\n"
            "      opt.textContent = item.name;\n"
            "      optgroup.appendChild(opt);\n"
            "    });\n"
            "    serviceSelect.appendChild(optgroup);\n"
            "  });\n"
            "  if (uncategorized.length > 0) {\n"
            "    const optgroup = document.createElement('optgroup');\n"
            "    optgroup.label = 'Autres';\n"
            "    uncategorized.forEach(item => {\n"
            "      const opt = document.createElement('option');\n"
            "      opt.value = item.key;\n"
            "      opt.textContent = item.name;\n"
            "      optgroup.appendChild(opt);\n"
            "    });\n"
            "    serviceSelect.appendChild(optgroup);\n"
            "  }\n"
            "  \n"
            "  function renderDynamicFields() {\n"
            "    const serviceKey = serviceSelect.value;\n"
            "    dynamicFieldsContainer.innerHTML = '';\n"
            "    if(serviceKey && schemas[serviceKey] && schemas[serviceKey].fields) {\n"
            "      schemas[serviceKey].fields.forEach(f => {\n"
            "        let helpIcon = '';\n"
            "        if(f.help) {\n"
            "          helpIcon = `<span title=\"${f.help.replace(/\"/g, '&quot;')}\" style=\"cursor:help; margin-left:4px; font-size:12px;\" onclick=\"alert(this.getAttribute('title'))\">ℹ️</span>`;\n"
            "        }\n"
            "        let inputHtml = '';\n"
            "        if (f.type === 'select') {\n"
            "          inputHtml = `<select class=\"vault-dynamic-input\" data-key=\"${f.id}\">`;\n"
            "          if(f.options) {\n"
            "            f.options.forEach(opt => {\n"
            "              const val = opt.value !== undefined ? opt.value : opt;\n"
            "              const lbl = opt.label !== undefined ? opt.label : opt;\n"
            "              inputHtml += `<option value=\"${val}\">${lbl}</option>`;\n"
            "            });\n"
            "          }\n"
            "          inputHtml += `</select>`;\n"
            "        } else if (f.type === 'password') {\n"
            "          inputHtml = `\n"
            "            <div style=\"position:relative; display:flex; align-items:center;\">\n"
            "              <input type=\"password\" class=\"vault-dynamic-input\" data-key=\"${f.id}\" placeholder=\"${f.placeholder || ''}\" autocomplete=\"new-password\" spellcheck=\"false\" style=\"width:100%; padding-right:24px; box-sizing:border-box;\" />\n"
            "              <span onclick=\"const i=this.previousElementSibling; if(i.type==='password'){i.type='text'; this.style.opacity='1';} else {i.type='password'; this.style.opacity='0.5';}\" style=\"position:absolute; right:8px; cursor:pointer; opacity:0.5; font-size:14px; user-select:none;\" title=\"Afficher/Masquer\">👁️</span>\n"
            "            </div>\n"
            "          `;\n"
            "        } else {\n"
            "          inputHtml = `<input type=\"${f.type || 'text'}\" class=\"vault-dynamic-input\" data-key=\"${f.id}\" placeholder=\"${f.placeholder || ''}\" autocomplete=\"off\" spellcheck=\"false\" />`;\n"
            "        }\n"
            "        const fieldHtml = `\n"
            "          <div style=\"display:flex; flex-direction:column; gap:2px;\">\n"
            "            <label style=\"font-size:11px; font-weight:bold; color: ${isDark ? '#a3a3a3' : '#6b7280'};\">${f.label} ${helpIcon}</label>\n"
            "            ${inputHtml}\n"
            "          </div>\n"
            "        `;\n"
            "        dynamicFieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);\n"
            "      });\n"
            "    }\n"
            "  }\n"
            "  function resetForm() {\n"
            "    const accountInput = document.getElementById('vault-input-account');\n"
            "    accountInput.disabled = false;\n"
            "    accountInput.value = '';\n"
            "    document.querySelectorAll('.vault-dynamic-input').forEach(input => input.value = '');\n"
            "    const addBtn = document.getElementById('vault-btn-add');\n"
            "    addBtn.textContent = 'Enregistrer';\n"
            "    addBtn.style.background = '#3b82f6';\n"
            "    document.getElementById('vault-form-title').textContent = '➕ Configurer un service';\n"
            "  }\n"
            "  serviceSelect.addEventListener('change', function() { renderDynamicFields(); resetForm(); });\n"
            "  if(Object.keys(schemas).length > 0) renderDynamicFields();\n"
            "  \n"
            "  // Render List\n"
            "  function renderList() {\n"
            "    const listDiv = document.getElementById(HUD_ID + '-list');\n"
            "    if(accounts.length === 0) {\n"
            "      listDiv.innerHTML = '<div style=\"text-align:center; padding:16px; opacity:0.6;\"><i>Aucun identifiant enregistré.</i></div>';\n"
            "      return;\n"
            "    }\n"
            "    let html = '<table class=\"vault-table\"><thead><tr><th>Service</th><th>Nom</th><th width=\"30\"></th></tr></thead><tbody>';\n"
            "    accounts.forEach(acc => {\n"
            "      html += `<tr>\n"
            "        <td>${acc.service}</td>\n"
            "        <td><strong>${acc.account_id || ''}</strong></td>\n"
            "        <td style=\"text-align:right;\">\n"
            "          <button class=\"vault-btn-del\" style=\"color:#3b82f6; margin-right:8px;\" data-service=\"${acc.service}\" data-account=\"${acc.account_id || ''}\" onclick=\"window.echoVaultEdit(this.getAttribute('data-service'), this.getAttribute('data-account'))\" title=\"Écraser (Modifier)\"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M17 3a2.828 2.828 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z'/></svg></button>\n"
            "          <button class=\"vault-btn-del\" data-service=\"${acc.service}\" data-account=\"${acc.account_id || 'default'}\" onclick=\"window.echoVaultDelete(this.getAttribute('data-service'), this.getAttribute('data-account'))\" title=\"Supprimer\"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='3 6 5 6 21 6'/><path d='M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2'/></svg></button>\n"
            "        </td>\n"
            "      </tr>`;\n"
            "    });\n"
            "    html += '</tbody></table>';\n"
            "    listDiv.innerHTML = html;\n"
            "  }\n"
            "  renderList();\n"
            "  \n"
            "  // Events\n"
            "  \n"
            "  document.getElementById('vault-btn-cancel').onclick = function() {\n"
            "    resetForm();\n"
            "  };\n"
            "  \n"
            "  document.getElementById('vault-btn-add').onclick = function() {\n"
            "    const service = serviceSelect.value;\n"
            "    \n"
            "    if(!service) { vaultAlert('Veuillez sélectionner un Service.'); return; }\n"
            "    \n"
            "    const account_id = document.getElementById('vault-input-account').value.trim();\n"
            "    if(!account_id) { vaultAlert('Veuillez saisir un Nom de Secret.'); return; }\n"
            "    \n"
            "    const credObj = {};\n"
            "    let missingField = false;\n"
            "    document.querySelectorAll('.vault-dynamic-input').forEach(input => {\n"
            "      if(!input.value.trim()) missingField = true;\n"
            "      credObj[input.dataset.key] = input.value.trim();\n"
            "    });\n"
            "    \n"
            "    const processForm = function() {\n"
            "      const credStr = JSON.stringify(credObj);\n"
            "      document.querySelectorAll('.vault-dynamic-input').forEach(input => input.value = '');\n"
            "      resetForm();\n"
            "      window.sendVaultAction({action: 'add_account', service: service, account_id: account_id, credentials: credStr});\n"
            "    };\n"
            "    \n"
            "    if(missingField) {\n"
            "      vaultConfirm('Certains champs sont vides. Voulez-vous continuer ?', function(agreed) {\n"
            "        if (agreed) processForm();\n"
            "      });\n"
            "    } else {\n"
            "      processForm();\n"
            "    }\n"
            "  };\n"
            "  \n"
            "  window.echoVaultDelete = function(service, alias) {\n"
            "    vaultConfirm('Supprimer les identifiants pour ' + service + ' ?', function(agreed) {\n"
            "      if(agreed) window.sendVaultAction({action: 'delete_account', service: service, account_id: alias});\n"
            "    });\n"
            "  };\n"
            "  \n"
            "  window.echoVaultEdit = function(service, alias) {\n"
            "    serviceSelect.value = service;\n"
            "    if(serviceSelect.value !== service) {\n"
            "      const opt = document.createElement('option');\n"
            "      opt.value = service; opt.textContent = service;\n"
            "      serviceSelect.appendChild(opt);\n"
            "      serviceSelect.value = service;\n"
            "    }\n"
            "    renderDynamicFields();\n"
            "    const accountInput = document.getElementById('vault-input-account');\n"
            "    accountInput.value = alias;\n"
            "    accountInput.disabled = true;\n"
            "    const addBtn = document.getElementById('vault-btn-add');\n"
            "    addBtn.textContent = 'Écraser les secrets';\n"
            "    addBtn.style.background = '#f59e0b';\n"
            "    document.getElementById('vault-form-title').textContent = '✏️ Modifier le service';\n"
            "  };\n"
            "  \n"
            "  window.echoVaultUpdate = function(newAccountsJson) {\n"
            "    accounts = JSON.parse(newAccountsJson);\n"
            "    renderList();\n"
            "  }\n"
            "})();\n"
        )
