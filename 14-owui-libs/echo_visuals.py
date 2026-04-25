"""
title: ECHO Visuals Engine Configuration
author: Wilfried BARNAVON
version: 3.9
description: 3.9: Ré-implémentation du moteur SMILES (chem) et ajout de science, bio, astro.
"""

class VisualEngine:
    @staticmethod
    def get_config(moteur: str, payload: str, cdn_timeout_ms: int = 5000) -> dict:
        """
        Registre universel des moteurs de rendu ECHO.
        Payload reçu en Base64 pour garantir l'intégrité des caractères spéciaux.
        """
        moteur = moteur.lower()
        
        # Calcul du nombre max de boucles de vérification (1 boucle = 100ms)
        max_retries = max(1, cdn_timeout_ms // 100)
        
        configs = {
            # --- VAGUES 1 & 2 : FONDAMENTAUX ---
            "markmap": {
                "scripts": ["https://cdn.jsdelivr.net/npm/d3@7", "https://cdn.jsdelivr.net/npm/markmap-view", "https://cdn.jsdelivr.net/npm/markmap-lib"],
                "container": f'''
                    <style>
                        .markmap-node {{ font-size: 14px; fill: #f8fafc !important; font-weight: 500; }}
                        .markmap-link {{ stroke: #475569 !important; stroke-width: 1.5px; }}
                        .markmap-node-circle {{ fill: #10b981 !important; stroke: #064e3b !important; }}
                        #visual-target {{ width: 100%; height: 600px; min-height: 400px; background: #1a1a1b; display: block; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <svg id="visual-target"></svg>
                ''',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
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
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" class="mermaid" style="width:100%; height:auto; min-height:300px;"></div>',
                "init": """
                    mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose' });
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const target = document.getElementById('visual-target');
                    mermaid.render('mermaid-svg-' + Date.now(), rawData).then(({svg}) => { 
                        target.innerHTML = svg; 
                        const svgEl = target.querySelector('svg');
                        if(svgEl) { svgEl.style.maxWidth = '100%'; svgEl.style.height = 'auto'; }
                    });
                """
            },
            "sketch": {
                "scripts": [
                    "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js",
                    "https://cdn.jsdelivr.net/npm/roughjs@4.5.2/bundled/rough.js"
                ],
                "container": f'''
                    <style>
                        @import url('https://fonts.googleapis.com/css2?family=Caveat:wght@600&display=swap');
                        #visual-target-container {{ width: 100%; height: auto; min-height: 400px; display: flex; justify-content: center; align-items: center; background: #1a1a1b; padding: 20px; }}
                        #mermaid-hidden {{ display: none; position: absolute; }}
                        #rough-target {{ width: 100%; height: 100%; overflow: visible; font-family: 'Caveat', cursive; font-size: 18px; fill: #e2e8f0; }}
                        #rough-target text {{ font-family: 'Caveat', cursive; fill: #e2e8f0; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="mermaid-hidden"></div>
                    <div id="visual-target-container">
                        <svg id="rough-target"></svg>
                    </div>
                ''',
                "init": """
                    mermaid.initialize({ startOnLoad: false, theme: 'dark', securityLevel: 'loose', fontFamily: 'Caveat' });
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const hiddenTarget = document.getElementById('mermaid-hidden');
                    const roughTarget = document.getElementById('rough-target');
                    
                    mermaid.render('mermaid-svg-' + Date.now(), rawData).then(({svg}) => { 
                        hiddenTarget.innerHTML = svg; 
                        const originalSvg = hiddenTarget.querySelector('svg');
                        if (!originalSvg) return;

                        if (originalSvg.getAttribute('viewBox')) {
                            roughTarget.setAttribute('viewBox', originalSvg.getAttribute('viewBox'));
                        } else if (originalSvg.getAttribute('width') && originalSvg.getAttribute('height')) {
                            roughTarget.setAttribute('viewBox', `0 0 ${{originalSvg.getAttribute('width').replace('px','')}} ${{originalSvg.getAttribute('height').replace('px','')}}`);
                        }
                        
                        const rc = rough.svg(roughTarget);
                        const roughOptions = {
                            roughness: 1.5,
                            bowing: 1,
                            stroke: '#94a3b8',
                            strokeWidth: 2,
                            fill: 'rgba(51, 65, 85, 0.4)',
                            fillStyle: 'hachure',
                            hachureGap: 5,
                            hachureAngle: 60
                        };

                        originalSvg.querySelectorAll('rect').forEach(rect => {
                            const x = parseFloat(rect.getAttribute('x') || 0);
                            const y = parseFloat(rect.getAttribute('y') || 0);
                            const w = parseFloat(rect.getAttribute('width') || 0);
                            const h = parseFloat(rect.getAttribute('height') || 0);
                            if (w > 0 && h > 0 && rect.getAttribute('class') !== 'background') {
                                const fill = rect.style.fill || rect.getAttribute('fill');
                                const stroke = rect.style.stroke || rect.getAttribute('stroke');
                                let opts = { ...roughOptions };
                                if (fill && fill !== 'none' && fill !== 'transparent') opts.fill = fill;
                                if (stroke && stroke !== 'none') opts.stroke = stroke;
                                roughTarget.appendChild(rc.rectangle(x, y, w, h, opts));
                            }
                        });

                        originalSvg.querySelectorAll('circle').forEach(circle => {
                            const cx = parseFloat(circle.getAttribute('cx') || 0);
                            const cy = parseFloat(circle.getAttribute('cy') || 0);
                            const r = parseFloat(circle.getAttribute('r') || 0);
                            if (r > 0) roughTarget.appendChild(rc.circle(cx, cy, r * 2, roughOptions));
                        });

                        originalSvg.querySelectorAll('polygon').forEach(poly => {
                            const pointsStr = poly.getAttribute('points');
                            if (pointsStr) {
                                const points = pointsStr.trim().split(/\\s+|,/).map(Number);
                                const pts = [];
                                for(let i=0; i<points.length; i+=2) {
                                    if(!isNaN(points[i]) && !isNaN(points[i+1])) pts.push([points[i], points[i+1]]);
                                }
                                if(pts.length > 2) roughTarget.appendChild(rc.polygon(pts, roughOptions));
                            }
                        });

                        originalSvg.querySelectorAll('path').forEach(path => {
                            const d = path.getAttribute('d');
                            const cls = path.getAttribute('class') || "";
                            if (d) {
                                let opts = { stroke: '#cbd5e1', strokeWidth: 1.5, roughness: 1.2 };
                                if (cls.includes('arrowheadPath')) {
                                    opts.fill = '#cbd5e1';
                                    opts.fillStyle = 'solid';
                                }
                                roughTarget.appendChild(rc.path(d, opts));
                            }
                        });

                        originalSvg.querySelectorAll('text').forEach(text => {
                            const clone = text.cloneNode(true);
                            clone.removeAttribute('style');
                            clone.setAttribute('font-family', 'Caveat, cursive');
                            clone.setAttribute('font-size', '18px');
                            clone.setAttribute('fill', '#f8fafc');
                            clone.setAttribute('font-weight', '600');
                            roughTarget.appendChild(clone);
                        });
                        hiddenTarget.innerHTML = '';
                    });
                """
            },
            "echarts": {
                "scripts": ["https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:500px;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const chart = echarts.init(document.getElementById('visual-target'), 'dark');
                    chart.setOption(JSON.parse(rawData));
                    window.addEventListener('resize', () => chart.resize());
                """
            },
            "vega": {
                "scripts": ["https://cdn.jsdelivr.net/npm/vega@5", "https://cdn.jsdelivr.net/npm/vega-lite@5", "https://cdn.jsdelivr.net/npm/vega-embed@6"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:auto; min-height:400px;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    vegaEmbed('#visual-target', JSON.parse(rawData), { theme: 'dark', actions: false });
                """
            },
            "timeline": {
                "scripts": ["https://cdn.knightlab.com/libs/timeline3/latest/css/timeline.css", "https://cdn.knightlab.com/libs/timeline3/latest/js/timeline-min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    new TL.Timeline('visual-target', JSON.parse(rawData), { theme: 'dark', height: 600 });
                """
            },

            # --- VAGUE 3 : MÉTIER & LOGIQUE ---
            "bpmn": {
                "scripts": ["https://cdn.jsdelivr.net/npm/bpmn-js@17/dist/bpmn-viewer.production.min.js"],
                "container": f'''
                    <style>
                        #visual-target {{ width: 100%; height: 600px; background: #1a1a1b; }}
                        /* Inversion propre pour rendre le BPMN (noir) visible en blanc */
                        .bjs-container {{ filter: invert(0.93) hue-rotate(180deg) brightness(1.2); }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="visual-target"></div>
                ''',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const viewer = new BpmnJS({ container: '#visual-target' });
                    viewer.importXML(rawData).then(() => { viewer.get('canvas').zoom('fit-viewport'); });
                """
            },
            "gantt": {
                "scripts": ["https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.css", "https://cdn.jsdelivr.net/npm/frappe-gantt/dist/frappe-gantt.min.js"],
                "container": f'''
                    <style>
                        .gantt .grid-header {{ fill: #1a1a1b; stroke: #334155; }}
                        .gantt .grid-row {{ fill: #1a1a1b; }}
                        .gantt .grid-row:nth-child(even) {{ fill: #1e1e1f; }}
                        .gantt .tick {{ stroke: #334155; }}
                        .gantt .lower-text, .gantt .upper-text {{ fill: #e2e8f0; font-weight: bold; }}
                        .gantt .bar {{ fill: #10b981; }}
                        .gantt .bar-label {{ fill: #fff; font-weight: bold; }}
                        #visual-target {{ width: 100%; overflow: auto; background: #1a1a1b; padding: 10px; }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="visual-target"></div>
                ''',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    let retries = 0;
                    const maxRetries = """ + str(max_retries) + """;
                    const run = () => {
                        if (typeof Gantt !== 'undefined') {
                            new Gantt("#visual-target", JSON.parse(rawData), { view_mode: 'Day', language: 'fr' });
                        } else if (retries < maxRetries) { 
                            retries++;
                            setTimeout(run, 100); 
                        } else {
                            document.getElementById('visual-target').innerHTML = "<p style='color:#ef4444; padding:20px;'>Erreur : Timeout (" + (""" + str(cdn_timeout_ms) + """/1000) + "s). Impossible de charger la librairie Frappe Gantt depuis le CDN.</p>";
                        }
                    };
                    run();
                """
            },

            # --- VAGUE 4 : 3D & PLANS ---
            "aframe": {
                "scripts": ["https://aframe.io/releases/1.6.0/aframe.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    document.getElementById('visual-target').innerHTML = rawData;
                """
            },

            # --- VAGUE 5 : GÉO & TOPOLOGIE ---
            "leaflet": {
                "scripts": ["https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css", "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:500px;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const data = JSON.parse(rawData);
                    const map = L.map('visual-target').setView([data.lat || 48.85, data.lon || 2.35], data.zoom || 13);
                    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
                        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
                        subdomains: 'abcd',
                        maxZoom: 20
                    }).addTo(map);
                    if(data.features) L.geoJSON(data).addTo(map);
                """
            },
            "cytoscape": {
                "scripts": ["https://cdn.jsdelivr.net/npm/cytoscape@3.28.1/dist/cytoscape.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; background:#1a1a1b;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    cytoscape({ container: document.getElementById('visual-target'), elements: JSON.parse(rawData), 
                        style: [{ selector: 'node', style: { 'background-color': '#0ea5e9', 'label': 'data(id)', 'color': '#fff' }}, 
                                { selector: 'edge', style: { 'width': 3, 'line-color': '#334155', 'target-arrow-color': '#334155', 'target-arrow-shape': 'triangle' }}],
                        layout: { name: 'cose' } 
                    });
                """
            },

            # --- VAGUE 7 : INGÉNIERIE & ÉLECTRONIQUE ---
            "wavedrom": {
                "scripts": ["https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/wavedrom.min.js", "https://cdnjs.cloudflare.com/ajax/libs/wavedrom/3.5.0/skins/default.js"],
                "container": f'''
                    <style>
                        #visual-target {{ overflow: auto; background: #1a1a1b; min-height: 200px; padding: 20px; }}
                        /* Inversion pour WaveDrom */
                        #visual-target svg {{ filter: invert(0.9) hue-rotate(180deg) brightness(1.1); }}
                    </style>
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div id="visual-target"><script type="WaveDrom"></script></div>
                ''',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const scriptTag = document.querySelector('#visual-target script');
                    scriptTag.text = rawData;
                    WaveDrom.ProcessAll();
                """
            },

            # --- VAGUE 8 : ECHO SCIENTIFIC SUITE ---
            "chem": {
                "scripts": ["https://unpkg.com/smiles-drawer@2.0.1/dist/smiles-drawer.min.js"],
                "container": f'''
                    <div id="visual-payload" style="display:none;">{payload}</div>
                    <div style="width:100%; height:500px; display:flex; justify-content:center; align-items:center; background:#1a1a1b;">
                        <canvas id="visual-target" style="max-width:100%; max-height:100%;"></canvas>
                    </div>
                ''',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    let retries = 0;
                    const maxRetries = """ + str(max_retries) + """;
                    const run = () => {
                        if (typeof SmilesDrawer !== 'undefined') {
                            const options = { width: 600, height: 600, theme: 'dark' };
                            const drawer = new SmilesDrawer.Drawer(options);
                            SmilesDrawer.parse(rawData, (tree) => {
                                drawer.draw(tree, 'visual-target', 'dark', false);
                            }, (err) => { console.error('SmilesDrawer Error:', err); });
                        } else if (retries < maxRetries) { 
                            retries++;
                            setTimeout(run, 100); 
                        }
                    };
                    run();
                """
            },
            "science": {
                "scripts": ["https://cdn.plot.ly/plotly-2.33.0.min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:500px; background:#1a1a1b;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    let retries = 0;
                    const maxRetries = """ + str(max_retries) + """;
                    const run = () => {
                        if (typeof Plotly !== 'undefined') {
                            const data = JSON.parse(rawData);
                            const config = { responsive: true, displaylogo: false };
                            const layout = data.layout || { template: 'plotly_dark', paper_bgcolor: '#1a1a1b', plot_bgcolor: '#1a1a1b' };
                            Plotly.newPlot('visual-target', data.data || data, layout, config);
                        } else if (retries < maxRetries) { 
                            retries++;
                            setTimeout(run, 100); 
                        }
                    };
                    run();
                """
            },
            "bio": {
                "scripts": ["https://3Dmol.org/build/3Dmol-min.js"],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; position:relative; background:#1a1a1b;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    let retries = 0;
                    const maxRetries = """ + str(max_retries) + """;
                    const run = () => {
                        if (typeof $3Dmol !== 'undefined') {
                            const viewer = $3Dmol.createViewer("visual-target", { backgroundColor: '#1a1a1b' });
                            const input = rawData.trim();
                            if (input.length === 4 && !input.includes('\\n')) {
                                $3Dmol.download("pdb:" + input, viewer, {}, function() {
                                    viewer.setStyle({}, { cartoon: { color: 'spectrum' } });
                                    viewer.zoomTo();
                                    viewer.render();
                                });
                            } else {
                                viewer.addModel(input, "pdb");
                                viewer.setStyle({}, { cartoon: { color: 'spectrum' } });
                                viewer.zoomTo();
                                viewer.render();
                            }
                        } else if (retries < maxRetries) { 
                            retries++;
                            setTimeout(run, 100); 
                        }
                    };
                    run();
                """
            },
            "astro": {
                "scripts": [
                    "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.css",
                    "https://aladin.cds.unistra.fr/AladinLite/api/v3/latest/aladin.js"
                ],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; background:#1a1a1b;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    let retries = 0;
                    const maxRetries = """ + str(max_retries) + """;
                    const run = () => {
                        if (typeof A !== 'undefined' && typeof A.aladin === 'function') {
                            try {
                                const rawTarget = (data.target || '').trim();
                                // Nettoyage de la cible : autorise les noms composés (ex: Alpha Centauri A) jusqu'à 5 mots
                                const cleanTarget = (rawTarget.length > 0 && rawTarget.length < 60 && rawTarget.split(/\s+/).length < 6) ? rawTarget : 'Orion';
                                
                                // Délai de sécurité pour l'initialisation du contexte WebGL
                                setTimeout(() => {
                                    try {
                                        A.aladin('#visual-target', {
                                            target: cleanTarget,
                                            fov: data.fov || 2,
                                            survey: data.survey || 'P/DSS2/color'
                                        });
                                    } catch (inner) {
                                        console.error('ECHO Astro: Initialisation native échouée, repli Orion.', inner);
                                        A.aladin('#visual-target', { target: 'Orion', fov: 5 });
                                    }
                                }, 300);
                            } catch (e) {
                                console.error('ECHO Astro: Init Error:', e);
                            }
                        } else if (retries < maxRetries) { 
                            retries++;
                            setTimeout(run, 100); 
                        }
                    };
                    run();
                """
            },
            "svg": {
                "scripts": [],
                "container": f'<div id="visual-payload" style="display:none;">{payload}</div><div id="visual-target" style="width:100%; height:600px; display:flex; align-items:center; justify-content:center; background:#1a1a1b;"></div>',
                "init": """
                    const b64 = document.getElementById('visual-payload').textContent;
                    const rawData = new TextDecoder('utf-8').decode(Uint8Array.from(atob(b64), c => c.charCodeAt(0)));
                    const target = document.getElementById('visual-target');
                    target.innerHTML = rawData;
                    const svg = target.querySelector('svg');
                    if(svg) {
                        svg.style.width = '100%';
                        svg.style.height = '100%';
                        svg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
                    }
                """
            }
        }
        
        return configs.get(moteur, configs["mermaid"])