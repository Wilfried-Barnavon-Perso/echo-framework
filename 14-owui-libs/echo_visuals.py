"""
title: ECHO Visuals Engine Configuration
author: Wilfried BARNAVON
version: 1.6
description: 1.6: Finalisation de l'Omniscience Visuelle - Déploiement complet des 13 moteurs (Vagues 1 à 7).
"""

class VisualEngine:
    @staticmethod
    def get_config(moteur: str, payload: str) -> dict:
        """
        Registre universel des moteurs de rendu ECHO.
        """
        moteur = moteur.lower()
        safe_payload = payload.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        
        configs = {
            # --- VAGUES 1 & 2 : FONDAMENTAUX ---
            "markmap": {
                "scripts": ["https://cdn.jsdelivr.net/npm/d3@7", "https://cdn.jsdelivr.net/npm/markmap-view", "https://cdn.jsdelivr.net/npm/markmap-lib"],
                "container": f'<template id="visual-payload">{safe_payload}</template><svg id="visual-target" style="width:100%; height:100vh;"></svg>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    const { Transformer, Markmap, loadCSS, loadJS } = window.markmap;
                    const { root, features } = new Transformer().transform(rawData);
                    const { styles, scripts } = new Transformer().getUsedAssets(features);
                    if (styles) loadCSS(styles);
                    if (scripts) loadJS(scripts, { getMarkmap: () => window.markmap });
                    Markmap.create('#visual-target', null, root);
                """
            },
            "mermaid": {
                "scripts": ["https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" class="mermaid" style="width:100%; height:100%;"></div>',
                "init": """
                    mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    mermaid.render('mermaid-svg', rawData).then(({svg}) => { document.getElementById('visual-target').innerHTML = svg; });
                """
            },
            "echarts": {
                "scripts": ["https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="width:100%; height:100vh;"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    const chart = echarts.init(document.getElementById('visual-target'), 'dark');
                    chart.setOption(JSON.parse(rawData));
                    window.addEventListener('resize', () => chart.resize());
                """
            },

            # --- VAGUE 3 : MÉTIER & LOGIQUE ---
            "bpmn": {
                "scripts": ["https://cdn.jsdelivr.net/npm/bpmn-js@17/dist/bpmn-viewer.production.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="width:100%; height:100vh;"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    const viewer = new BpmnJS({ container: '#visual-target' });
                    viewer.importXML(rawData).then(() => { viewer.get('canvas').zoom('fit-viewport'); });
                """
            },
            "gantt": {
                "scripts": ["https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.css", "https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    new Gantt("#visual-target", JSON.parse(rawData), { view_mode: 'Day', language: 'fr' });
                """
            },

            # --- VAGUE 4 : 3D & PLANS ---
            "aframe": {
                "scripts": ["https://aframe.io/releases/1.6.0/aframe.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="width:100%; height:100vh;"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    document.getElementById('visual-target').innerHTML = rawData;
                """
            },

            # --- VAGUE 5 : GÉO & TOPOLOGIE ---
            "leaflet": {
                "scripts": ["https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css", "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="width:100%; height:100vh;"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    const data = JSON.parse(rawData);
                    const map = L.map('visual-target').setView([data.lat || 48.85, data.lon || 2.35], data.zoom || 13);
                    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png').addTo(map);
                    if(data.features) L.geoJSON(data).addTo(map);
                """
            },
            "cytoscape": {
                "scripts": ["https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="width:100%; height:100vh; background:#1a1a1b;"></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    cytoscape({ container: document.getElementById('visual-target'), elements: JSON.parse(rawData), 
                        style: [{ selector: 'node', style: { 'background-color': '#0ea5e9', 'label': 'data(id)', 'color': '#fff' }}, 
                                { selector: 'edge', style: { 'width': 3, 'line-color': '#334155', 'target-arrow-color': '#334155', 'target-arrow-shape': 'triangle' }}],
                        layout: { name: 'cose' } 
                    });
                """
            },

            # --- VAGUE 6 : BIOLOGIE & CHIMIE ---
            "smiles": {
                "scripts": ["https://unpkg.com/smiles-drawer@2.0.1/dist/smiles-drawer.min.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><canvas id="visual-target" style="width:100%; height:100vh; background:#fff;"></canvas>',
                "init": """
                    const smiles = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim();
                    SmiDrawer.apply({ theme: 'dark' }, '#visual-target', 'light').draw(smiles);
                """
            },

            # --- VAGUE 7 : INGÉNIERIE & ÉLECTRONIQUE ---
            "wavedrom": {
                "scripts": ["https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/wavedrom.min.js", "https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/skins/default.js"],
                "container": f'<template id="visual-payload">{safe_payload}</template><div id="visual-target" style="overflow:auto; background:#fff;"><script type="WaveDrom"></script></div>',
                "init": """
                    const rawData = document.getElementById('visual-payload').innerHTML.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
                    const scriptTag = document.querySelector('#visual-target script');
                    scriptTag.text = rawData;
                    WaveDrom.ProcessAll();
                """
            }
        }
        
        return configs.get(moteur, configs["mermaid"])
