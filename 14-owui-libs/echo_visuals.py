"""
title: ECHO Visuals Engine Configuration
author: Wilfried BARNAVON
version: 6.0
description: 6.0: Correction critique de l'affichage Bio/SVG/A-Frame et stabilisation du redimensionnement iFrame.
"""

class VisualEngine:
    @staticmethod
    def get_config(moteur: str, payload: str, cdn_timeout_ms: int = 5000) -> dict:
        """
        Registre universel des moteurs de rendu ECHO.
        Version 5.9 : Correction critique de la syntaxe (échappement des accolades).
        """
        moteur = moteur.lower()
        max_retries = max(1, cdn_timeout_ms // 100)
        
        configs = {
            # --- 1. MINDMAPS (MARKMAP) ---
            "markmap": {
                "scripts": ["https://cdn.jsdelivr.net/npm/d3@7", "https://cdn.jsdelivr.net/npm/markmap-view", "https://cdn.jsdelivr.net/npm/markmap-lib"],
                "container": f'''
                    <style>
                        .markmap-node {{ font-size: 14px; fill: #1e293b !important; font-weight: 500; }}
                        .markmap-link {{ stroke: #94a3b8 !important; stroke-width: 1.5px; }}
                        .markmap-node-circle {{ fill: #0ea5e9 !important; stroke: #0284c7 !important; }}
                        #visual-target {{ width: 100%; min-height: 500px; background: #ffffff; display: block; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <svg id="visual-target"></svg>
                ''',
                "init": f"""
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (!target) return;
                        
                        if (window.markmap && typeof window.markmap.Transformer !== 'undefined') {{
                            target.innerHTML = ''; 
                            const {{ Transformer, Markmap, loadCSS, loadJS }} = window.markmap;
                            const {{ root, features }} = new Transformer().transform(rawData);
                            const {{ styles, scripts }} = new Transformer().getUsedAssets(features);
                            if (styles) loadCSS(styles);
                            if (scripts) loadJS(scripts, {{ getMarkmap: () => window.markmap }});
                            Markmap.create(target, null, root);
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 2. DIAGRAMMES (MERMAID) ---
            "mermaid": {
                "scripts": ["https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" class="mermaid" style="width:100%; height:auto; min-height:400px; background:#ffffff; display:flex; justify-content:center;"></div>',
                "init": f"""
                    const uid = 'mermaid-' + Date.now();
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (!target) return;
                        
                        if (typeof mermaid !== 'undefined') {{
                            target.innerHTML = ''; 
                            mermaid.initialize({{ startOnLoad: false, theme: 'default', securityLevel: 'loose' }});
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            mermaid.render(uid, rawData).then(({{svg}}) => {{ 
                                target.innerHTML = svg; 
                                const svgEl = target.querySelector('svg');
                                if(svgEl) {{ svgEl.style.maxWidth = '100%'; svgEl.style.height = 'auto'; }}
                            }}).catch(err => {{ console.error("Mermaid Error:", err); }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 3. GANTT (SYNTAXE MERMAID) ---
            "gantt": {
                "scripts": ["https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" class="mermaid" style="width:100%; height:auto; min-height:400px; background:#ffffff; display:flex; justify-content:center; overflow-x:auto;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof mermaid !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            target.innerHTML = ''; 
                            mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'loose' }});
                            const b64 = document.getElementById('visual-payload').textContent;
                            let rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            if (!rawData.includes('gantt')) rawData = 'gantt\\n' + rawData;
                            mermaid.render('gantt-svg-' + Date.now(), rawData).then(({{svg}}) => {{ target.innerHTML = svg; }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 4. RENDU CRAYONNÉ (SKETCH) ---
            "sketch": {
                "scripts": ["https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js", "https://cdn.jsdelivr.net/npm/roughjs@4.5.2/bundled/rough.js"],
                "container": f'''
                    <style>
                        @import url('https://fonts.googleapis.com/css2?family=Caveat:wght@600&display=swap');
                        #visual-target-container {{ width: 100%; height: auto; min-height: 400px; display: flex; justify-content: center; align-items: center; background: #ffffff; padding: 20px; }}
                        #mermaid-hidden {{ display: none; position: absolute; }}
                        #rough-target {{ width: 100%; height: 100%; overflow: visible; font-family: 'Caveat', cursive; font-size: 18px; fill: #1e293b; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="mermaid-hidden"></div>
                    <div id="visual-target-container"><svg id="rough-target"></svg></div>
                ''',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof mermaid !== 'undefined' && typeof rough !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const hiddenTarget = document.getElementById('mermaid-hidden');
                            const roughTarget = document.getElementById('rough-target');
                            roughTarget.innerHTML = '';
                            mermaid.initialize({{ startOnLoad: false, theme: 'neutral', securityLevel: 'loose', fontFamily: 'Caveat' }});
                            mermaid.render('mermaid-sketch-' + Date.now(), rawData).then(({{svg}}) => {{ 
                                hiddenTarget.innerHTML = svg; 
                                const originalSvg = hiddenTarget.querySelector('svg');
                                if (!originalSvg) return;
                                roughTarget.setAttribute('viewBox', originalSvg.getAttribute('viewBox') || '0 0 800 600');
                                const rc = rough.svg(roughTarget);
                                originalSvg.querySelectorAll('rect').forEach(rect => {{
                                    const x = parseFloat(rect.getAttribute('x') || 0), y = parseFloat(rect.getAttribute('y') || 0);
                                    const w = parseFloat(rect.getAttribute('width') || 0), h = parseFloat(rect.getAttribute('height') || 0);
                                    if (w > 0 && h > 0) roughTarget.appendChild(rc.rectangle(x, y, w, h, {{ stroke: '#475569', strokeWidth: 2, roughness: 1.5 }}));
                                }});
                                originalSvg.querySelectorAll('path').forEach(path => {{
                                    const d = path.getAttribute('d');
                                    if (d) roughTarget.appendChild(rc.path(d, {{ stroke: '#64748b', strokeWidth: 1.5, roughness: 1.2 }}));
                                }});
                                originalSvg.querySelectorAll('text').forEach(text => {{
                                    const clone = text.cloneNode(true);
                                    clone.setAttribute('fill', '#1e293b');
                                    roughTarget.appendChild(clone);
                                }});
                            }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 5. GRAPHIQUES (ECHARTS) ---
            "echarts": {
                "scripts": ["https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:500px; background:#ffffff;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (!target) return;
                        
                        if (typeof echarts !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const chart = echarts.init(target);
                            chart.setOption(JSON.parse(rawData));
                            window.addEventListener('resize', () => chart.resize());
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 6. STATISTIQUES (VEGA) ---
            "vega": {
                "scripts": ["https://cdn.jsdelivr.net/npm/vega@5", "https://cdn.jsdelivr.net/npm/vega-lite@5", "https://cdn.jsdelivr.net/npm/vega-embed@6"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:400px; background:#ffffff;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof vegaEmbed !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            target.innerHTML = '';
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            vegaEmbed('#visual-target', JSON.parse(rawData), {{ theme: 'light', actions: false }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 7. RÉSEAUX (CYTOSCAPE) ---
            "cytoscape": {
                "scripts": ["https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.28.1/cytoscape.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:500px; background:#ffffff;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof cytoscape !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            if (!target) return;
                            target.innerHTML = '';
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            try {{
                                cytoscape({{ container: target, elements: JSON.parse(rawData), 
                                    style: [{{ selector: 'node', style: {{ 'background-color': '#0ea5e9', 'label': 'data(id)', 'color': '#1e293b', 'font-size': '12px' }}}}, 
                                            {{ selector: 'edge', style: {{ 'width': 2, 'line-color': '#cbd5e1', 'target-arrow-color': '#cbd5e1', 'target-arrow-shape': 'triangle', 'curve-style': 'bezier' }}}}],
                                    layout: {{ name: 'cose', padding: 10 }} 
                                }});
                            }} catch(e) {{ console.error("Cytoscape Error:", e); }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 8. CHRONOLOGIE (TIMELINE) ---
            "timeline": {
                "scripts": ["https://cdn.knightlab.com/libs/timeline3/latest/css/timeline.css", "https://cdn.knightlab.com/libs/timeline3/latest/js/timeline-min.js"],
                "container": f'''
                    <style>
                        #visual-target {{ width: 100%; height: 600px; border: 1px solid #e2e8f0; border-radius: 8px; overflow: hidden; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="visual-target"></div>
                ''',
                "init": f"""
                    disableAutoResize = true; 
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof TL !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            target.innerHTML = '';
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            try {{
                                new TL.Timeline('visual-target', JSON.parse(rawData), {{ theme: 'light', height: 600 }});
                                setTimeout(() => {{ 
                                    disableAutoResize = false; 
                                    if (typeof reportHeight === 'function') reportHeight(); 
                                }}, 2000);
                            }} catch(e) {{ console.error("Timeline Error:", e); }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 9. PROCESSUS (BPMN) ---
            "bpmn": {
                "scripts": ["https://cdn.jsdelivr.net/npm/bpmn-js@17/dist/bpmn-viewer.production.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:500px; background:#ffffff;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof BpmnJS !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            target.innerHTML = '';
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const viewer = new BpmnJS({{ container: '#visual-target' }});
                            viewer.importXML(rawData).then(() => {{ viewer.get('canvas').zoom('fit-viewport'); }}).catch(e => console.error(e));
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 10. 3D/VR (AFRAME) ---
            "aframe": {
                "scripts": ["https://cdn.jsdelivr.net/npm/aframe@1.6.0/dist/aframe-master.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; overflow:hidden; background:#000; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:12px;">Chargement de la scène 3D...</div>',
                "init": f"""
                    disableAutoResize = true; 
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (typeof AFRAME !== 'undefined') {{
                            try {{
                                const b64 = document.getElementById('visual-payload').textContent.trim();
                                let rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                                
                                if (rawData.includes('<a-scene') && !rawData.includes('embedded')) {{
                                    rawData = rawData.replace('<a-scene', '<a-scene embedded');
                                }}
                                
                                target.innerHTML = rawData;
                                setTimeout(() => {{ 
                                    disableAutoResize = false;
                                    if (typeof reportHeight === 'function') reportHeight(); 
                                }}, 1500);
                            }} catch(e) {{
                                console.error("A-Frame Render Error:", e);
                                target.innerHTML = "⚠️ Erreur A-Frame : " + e.message;
                            }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }} else {{
                            target.innerHTML = "⚠️ Échec du chargement de A-Frame (CDN Timeout).";
                        }}
                    }};
                    run();
                """
            },

            # --- 11. SIGNAUX (WAVEDROM) ---
            "wavedrom": {
                "scripts": ["https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/wavedrom.min.js", "https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/skins/default.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target-wrapper" style="width:100%; display:flex; justify-content:center; background:#ffffff; overflow:auto;"><div id="visual-target" style="padding:20px;"><script type="WaveDrom"></script></div></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof WaveDrom !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const scriptTag = document.querySelector('#visual-target script');
                            if(scriptTag) {{ scriptTag.text = rawData; WaveDrom.ProcessAll(); }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100);
                        }}
                    }};
                    run();
                """
            },

            # --- 12. CHIMIE (CHEM) ---
            "chem": {
                "scripts": ["https://unpkg.com/smiles-drawer@2.0.1/dist/smiles-drawer.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div style="width:100%; min-height:400px; display:flex; justify-content:center; align-items:center; background:#ffffff;"><canvas id="visual-target" width="600" height="400" style="max-width:100%; height:auto;"></canvas></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof SmilesDrawer !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const options = {{ width: 600, height: 400, theme: 'light' }};
                            const drawer = new SmilesDrawer.Drawer(options);
                            SmilesDrawer.parse(rawData, (tree) => {{ 
                                drawer.draw(tree, 'visual-target', 'light', false); 
                            }}, (err) => {{ console.error("SmilesDrawer Error:", err); }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100); 
                        }}
                    }};
                    run();
                """
            },

            # --- 13. SCIENCE (PLOTLY) ---
            "science": {
                "scripts": ["https://cdn.plot.ly/plotly-2.33.0.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:500px; background:#ffffff;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof Plotly !== 'undefined') {{
                            const target = document.getElementById('visual-target');
                            target.innerHTML = '';
                            const b64 = document.getElementById('visual-payload').textContent;
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                            const data = JSON.parse(rawData);
                            Plotly.newPlot('visual-target', data.data || data, data.layout || {{ template: 'plotly' }}, {{ responsive: true }});
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100); 
                        }}
                    }};
                    run();
                """
            },

            # --- 14. BIOLOGIE (3DMOL) ---
            "bio": {
                "scripts": ["https://cdn.jsdelivr.net/npm/jquery@3.7.1/dist/jquery.min.js", "https://cdn.jsdelivr.net/npm/3dmol@2.4.2/build/3Dmol-min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; position:relative; background:#ffffff; border:1px solid #e2e8f0; border-radius:8px; display:flex; align-items:center; justify-content:center; color:#64748b; font-size:12px;">Chargement du modèle moléculaire...</div>',
                "init": f"""
                    disableAutoResize = true; 
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (typeof $3Dmol !== 'undefined' && typeof jQuery !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent.trim();
                            try {{
                                const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0))).trim();
                                target.innerHTML = '';
                                const viewer = $3Dmol.createViewer(target, {{ backgroundColor: 'white' }});
                                
                                // Détection intelligente : ID PDB (4 chars) ou PDB:ID or full data
                                const cleanData = rawData.replace(/^PDB:/i, '').trim();
                                if (cleanData.length === 4 && !cleanData.includes('\\n')) {{
                                    $3Dmol.download("pdb:" + cleanData, viewer, {{}}, () => {{ 
                                        viewer.setStyle({{}}, {{ cartoon: {{ color: 'spectrum' }} }}); 
                                        viewer.zoomTo(); 
                                        viewer.render(); 
                                        disableAutoResize = false;
                                        if (typeof reportHeight === 'function') reportHeight();
                                    }});
                                }} else {{
                                    viewer.addModel(rawData, "pdb"); 
                                    viewer.setStyle({{}}, {{ cartoon: {{ color: 'spectrum' }} }}); 
                                    viewer.zoomTo(); 
                                    viewer.render();
                                    disableAutoResize = false;
                                    if (typeof reportHeight === 'function') reportHeight();
                                }}
                            }} catch(e) {{ 
                                console.error("3Dmol Error:", e);
                                target.innerHTML = "⚠️ Erreur de rendu 3Dmol : " + e.message;
                            }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100); 
                        }} else {{
                            target.innerHTML = "⚠️ Échec du chargement des librairies 3Dmol (CDN Timeout).";
                        }}
                    }};
                    run();
                """
            },

            # --- 15. ASTRONOMIE (CELESTIAL) ---
            "astro": {
                "scripts": [
                    "https://cdn.jsdelivr.net/npm/d3@3/d3.min.js", 
                    "https://cdn.jsdelivr.net/npm/d3-geo-projection@0.2/d3.geo.projection.min.js",
                    "https://cdn.jsdelivr.net/npm/d3-celestial@0.7.35/celestial.min.js",
                    "https://cdn.jsdelivr.net/npm/d3-celestial@0.7.35/celestial.css"
                ],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="celestial-map" style="width:100%; height:500px; background:#000000;"></div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        if (typeof Celestial !== 'undefined') {{
                            const b64 = document.getElementById('visual-payload').textContent.trim();
                            try {{
                                const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                                const config = JSON.parse(rawData);
                                Celestial.display({{
                                    container: "celestial-map",
                                    projection: config.projection || "orthographic",
                                    transform: config.transform || "equatorial",
                                    datapath: "https://cdn.jsdelivr.net/gh/ofrohn/d3-celestial/data/",
                                    stars: {{ show: true, limit: 6 }},
                                    constellations: {{ show: true, names: true }}
                                }});
                            }} catch(e) {{ console.error("Celestial JSON Error:", e); }}
                        }} else if (retries < maxRetries) {{
                            retries++; setTimeout(run, 100); 
                        }}
                    }};
                    run();
                """
            },

            # --- 16. DIRECT SVG ---
            "svg": {
                "scripts": [],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; min-height:400px; display:flex; align-items:center; justify-content:center; background:#ffffff; color:#64748b; font-size:12px;">Chargement du SVG...</div>',
                "init": f"""
                    let retries = 0;
                    const maxRetries = {max_retries};
                    const run = () => {{
                        const target = document.getElementById('visual-target');
                        if (!target && retries < maxRetries) {{
                            retries++; setTimeout(run, 100); return;
                        }}
                        
                        try {{
                            const b64 = document.getElementById('visual-payload').textContent.trim();
                            const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0))).trim();
                            
                            if (rawData.toLowerCase().includes('<svg')) {{
                                // Extraction propre si le modèle a ajouté du texte autour
                                const svgMatch = rawData.match(/<svg[\\s\\S]*<\\/svg>/i);
                                target.innerHTML = svgMatch ? svgMatch[0] : rawData;
                                const svgEl = target.querySelector('svg');
                                if (svgEl) {{
                                    svgEl.style.maxWidth = '100%';
                                    svgEl.style.height = 'auto';
                                    svgEl.style.display = 'block';
                                    if (!svgEl.getAttribute('preserveAspectRatio')) {{
                                        svgEl.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                                    }}
                                }}
                            }} else {{
                                const parser = new DOMParser();
                                const doc = parser.parseFromString(rawData, "image/svg+xml");
                                const svgEl = doc.querySelector('svg');
                                if (svgEl && !doc.querySelector('parsererror')) {{
                                    target.innerHTML = '';
                                    target.appendChild(svgEl);
                                    svgEl.style.maxWidth = '100%';
                                    svgEl.style.height = 'auto';
                                }} else {{
                                    target.innerHTML = "⚠️ Code SVG mal formé ou non détecté.";
                                }}
                            }}
                            if (typeof reportHeight === 'function') reportHeight();
                        }} catch(e) {{
                            console.error("SVG Render Error:", e);
                            target.innerHTML = "⚠️ Erreur SVG : " + e.message;
                        }}
                    }};
                    run();
                """
            }
        }
        
        return configs.get(moteur, configs["mermaid"])
