"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 5.36
description: 5.36: Fix Python SyntaxError (échappement des f-strings pour editorRatio/previewRatio).
             5.35: Redimensionnement proportionnel (ratio 33/67) pour le preview Codex.
             5.34: Fix redimensionnement du preview Codex (min-width:0 sur flex item) empêchant la perte du bouton PDF et l'ascenseur horizontal.
             5.33: Bascule de monitor_ECHO vers events.emit pour compatibilité universelle avec les Outils.
             5.16: UI Moderne - Icône globe, minimisation HUD corrigée (min-height fix) et Équilibre Souverain Pro. 5.17: Ajout show_image_js (injection JS sans HTMLResponse).
             5.18: Tooltip AUTHENTIFICATION refondu : section QUOTAS détaillée (Crédits, Quota modèle, Reset, Type).
             5.19: Nouveaux paramètres quota (quota_model, RPD, RPM) dans la signature et le tooltip.
             5.20: Refonte show_image_js — réutilise le moteur WebPlayer (HUD navigateur) avec
             paramètres direct_url et icon. Icône par défaut 👁️ (image), 🌐 pour le navigateur.
             5.21: Migration map_viewer() — Remplacement embed Google Maps par carte interactive
             Leaflet.js + tuiles OSM. Signature enrichie (lat, lon, zoom, lieux). Marqueur custom
             ECHO, popup élégante multi-résultats, CTRL+molette, contrôles stylisés.
             5.22: Retour embed Google Maps (googleMaps grounding natif Gemini) —
             suppression Leaflet/OSM. Signature simplifiée (query, title uniquement).
             5.23: Ajout _generate_codex_js() — Moteur HUD Monaco Codex.
             File tree, mini-chat AI, quick actions, diff view, upload/download,
             navigation historique ◀ ▶, détection thème dark/light.
             5.24: Spinner AI, refresh post-diff via load_file, sélecteur modèle
             (Flash/Pro/Lite), bouton × suppression par fichier.
             5.28: Preview Panel WYSIWYG — Panneau latéral droit déployable (toggle 🤖).
             Rendu temps réel debounced pour Markdown (marked.js), HTML (iframe srcdoc),
             CSS (iframe + template), SVG (DOM inline). Splitter draggable.
             Ajout _generate_agent_monitor_js() — HUD Cognitive Monitor.
             Visualisation arborescente des agents cognitifs, onglets verticaux,
             refresh manuel + auto-refresh slider 2-15s, clampHud viewport.
             5.32: Rendu multi-agentique : fusion chronologique (alias d'experts) 
             et arborescence de superviseur (worker_branch et indent_override).
"""

from fastapi.responses import HTMLResponse
import sys
import os
import hashlib
import orjson as std_json
from typing import Optional, Any, List, Dict

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")

class EchoRichUI:
  """Usine de rendu de composants visuels riches pour ECHO."""

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
  def get_mobile_guard_js(hud_id: str, block_execution: bool = False, error_msg: str = "Incompatible sur mobile.") -> str:
      """Génère le garde-fou JS centralisé pour l'adaptation ou le blocage sur mobile."""
      return f"""
      const isMobile = window.matchMedia('(max-width: 768px)').matches || /Mobi|Android/i.test(navigator.userAgent);
      if (isMobile) {{
          {f"window.parent.postMessage({{ type: 'toast', message: `{error_msg}`, level: 'warning' }}, '*'); return;" if block_execution else f"const styleId = '{hud_id}-mobile-style'; if (!document.getElementById(styleId)) {{ const styleEl = document.createElement('style'); styleEl.id = styleId; styleEl.innerHTML = `#{hud_id} {{ position: fixed !important; inset: 0 !important; width: 100vw !important; height: 100dvh !important; max-width: none !important; max-height: none !important; min-width: 0 !important; min-height: 0 !important; border-radius: 0 !important; z-index: 10005 !important; transform: none !important; }} #{hud_id}-resizer, .cp {{ display: none !important; }}`; document.head.appendChild(styleEl); }}"}
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
  def _generate_webplayer_js(b64: str, mime: str, metadata: list, current_url: str, hud_id: str, state_key: str, icon: str = "👁️") -> str:
    """Génère le moteur de pilotage ECHO WEBPLAYER (v5.20 Équilibre Souverain Pro)."""
    meta_j = std_json.dumps(metadata).decode('utf-8')
    b64_j = std_json.dumps(b64).decode('utf-8')
    url_j = std_json.dumps(current_url).decode('utf-8')
    mime_j = std_json.dumps(mime).decode('utf-8')

    return f"""
  (function() {{
    const HUD_ID = '{hud_id}';
    {EchoUI.get_mobile_guard_js(hud_id, block_execution=True, error_msg="Le WebPlayer est indisponible sur mobile.")}
    const STATE_KEY = '{state_key}';
    const ENGINE_KEY = 'echoWebPlayer_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_');

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
              <input id="${{HUD_ID}}-url" type="text" style="flex:1; background:rgba(0,0,0,0.4); border:1px solid #333; border-radius:6px; color:#00d4ff; font-size:11px; padding:6px 12px; outline:none; font-family:monospace;" readonly />
              <div style="display:flex; gap:8px;">
                <button id="${{HUD_ID}}-btn-zoom" title="Maximiser (Ajuster)" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px;">⛶</button>
                <button id="${{HUD_ID}}-btn-reset" title="Taille réelle (1:1)" style="background:none; border:none; color:#777; cursor:pointer; font-size:11px; font-weight:bold;">1:1</button>
                <button id="${{HUD_ID}}-btn-min" title="Minimiser" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px;">—</button>
                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ef4444; cursor:pointer; font-size:18px;">×</button>
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
            this.ratio = img.naturalHeight / img.naturalWidth;
            matrix.style.width = img.naturalWidth + "px";
            matrix.style.height = img.naturalHeight + "px";

            if (!localStorage.getItem(STATE_KEY)) {{
                this.imgScale = this.getInitialScale(img.naturalWidth, img.naturalHeight);
                this.posX = (window.innerWidth - (img.naturalWidth * this.imgScale)) / 2;
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
        }}
      }};
    }}
    window[ENGINE_KEY].update(payload);
  }})();
    """

  @staticmethod
  async def monitor_ECHO(events: Any, b64: str, metadata: List[Dict] = None, hud_id: str = "echo-webplayer", state_key: str = "echo_webplayer_state", current_url: str = ""):
    """Déploie le moniteur visuel interactif (HUD) haute performance."""
    js_code = EchoUI._generate_webplayer_js(b64, "image/png", metadata or [], current_url, hud_id, state_key, icon="🌐")
    await events.emit("execute", {"code": js_code})

  @staticmethod
  async def deploy_context_gauge(
      events: Any, plan_name: str, credits_val: str, quota_str: str,
      c_t: int, active_p_t: int, g_t: int, max_t: int,
      cache_pct: float, prompt_pct: float, gen_pct: float,
      user_email: Optional[str] = None, user_tier: Optional[str] = None,
      project_id: Optional[str] = None, auth_sources: Optional[list] = None,
      quota_amount: str = "N/A", quota_fraction: float = 1.0,
      quota_reset: str = "N/A", quota_type: str = "UNKNOWN",
      quota_model: str = "", quota_rpd_rem: str = "N/A", quota_rpd_lim: str = "N/A",
      quota_rpm_rem: str = "N/A", quota_rpm_lim: str = "N/A"
  ):
    """Déploie le HUD ECHO flottant avec tooltips en dessous."""
    auth_list = ", ".join(auth_sources) if auth_sources else "N/A"
    total_t = c_t + active_p_t + g_t
    total_pct = (total_t / max_t) * 100 if max_t > 0 else 0
    q_color = "#10b981"
    if quota_fraction < 0.2: q_color = "#ef4444"
    elif quota_fraction < 0.5: q_color = "#f59e0b"
    dash_array = 2 * 3.14159 * 8
    dash_offset = dash_array * (1 - quota_fraction)

    js_code = f"""
    (function() {{
      var container = document.querySelector('nav div.flex.items-center.w-full.max-w-full') ||
                      document.querySelector('nav div.flex.items-center.w-full.pl-1\\\\.5.pr-1') ||
                      document.querySelector('header nav');
      if (!container) return;
      var hudWrapper = document.getElementById('echo-nav-context-hud-wrapper');
      if (hudWrapper) hudWrapper.remove();
      var styleId = 'echo-hud-styles';
      if (!document.getElementById(styleId)) {{
        var style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
          .echo-tooltip {{ position: relative; display: flex; align-items: center; }}
          .echo-tooltip .tooltip-box {{
            visibility: hidden; width: 260px; background: rgba(15, 23, 42, 0.98);
            backdrop-filter: blur(12px); color: #f8fafc; text-align: left;
            border-radius: 8px; padding: 12px; position: absolute; z-index: 9999;
            top: 120%; left: 50%; transform: translateX(-50%); opacity: 0;
            transition: opacity 0.3s, transform 0.3s; border: 1px solid rgba(0, 212, 255, 0.4);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5); font-size: 11px; pointer-events: none;
          }}
          .echo-tooltip:hover .tooltip-box {{ visibility: visible; opacity: 1; transform: translateX(-50%) translateY(-5px); }}
          .tooltip-title {{ color: #00d4ff; font-weight: bold; margin-bottom: 8px; border-bottom: 1px solid rgba(0, 212, 255, 0.2); padding-bottom: 4px; text-transform: uppercase; }}
          .tooltip-row {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
          .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
          @media (max-width: 768px) {{ .tooltip-box {{ width: 90vw !important; left: 50% !important; transform: translateX(-50%) translateY(-5px) !important; white-space: normal !important; }} }}
        `;
        document.head.appendChild(style);
      }}
      hudWrapper = document.createElement('div');
      hudWrapper.id = 'echo-nav-context-hud-wrapper';
      hudWrapper.style.cssText = 'position:fixed;left:50%;top:22px;transform:translateX(-50%);width:auto;min-width:300px;display:flex;justify-content:center;align-items:center;z-index:9999;pointer-events:none;';
      var hud = document.createElement('div');
      hud.id = 'echo-nav-context-hud';
      hud.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:12px;pointer-events:auto;background:rgba(0,0,0,0.2);padding:4px 12px;border-radius:20px;backdrop-filter:blur(4px);';
      var iconHtml = `<div class="echo-tooltip"><svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" /><circle cx="10" cy="10" r="8" fill="none" stroke="{q_color}" stroke-width="2" stroke-dasharray="{dash_array}" stroke-dashoffset="{dash_offset}" transform="rotate(-90 10 10)" stroke-linecap="round" /><path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" /></svg><div class="tooltip-box" style="width:300px;"><div class="tooltip-title">AUTHENTIFICATION</div><div class="tooltip-row"><span>🔐 Source:</span> <span>{auth_list}</span></div><div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div><div class="tooltip-row"><span>🏗️ Projet:</span> <span>{project_id or 'N/A'}</span></div><div class="tooltip-title" style="margin-top:8px;border-top:1px solid rgba(0,212,255,0.2);padding-top:6px;">QUOTAS</div><div class="tooltip-row"><span>💳 Crédits:</span> <b style="color:#10b981;">{credits_val}</b></div><div class="tooltip-row"><span>🤖 Modèle CA:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_model or "—"}</span></div><div class="tooltip-row"><span>📊 Quota:</span> <b style="color:{q_color};">{quota_fraction*100:.1f}%</b></div><div class="tooltip-row"><span>📅 Req/jour:</span> <span>{"N/A" if quota_rpd_rem == "N/A" else f"{quota_rpd_rem} / {quota_rpd_lim}"}</span></div><div class="tooltip-row"><span>⚡ Req/min:</span> <span>{"N/A" if quota_rpm_rem == "N/A" else f"{quota_rpm_rem} / {quota_rpm_lim}"}</span></div><div class="tooltip-row"><span>🔄 Reset:</span> <span>{"—" if quota_reset == "N/A" else quota_reset}</span></div><div class="tooltip-row"><span>🏷️ Type:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_type}</span></div></div></div>`;
      var barHtml = `<div class="echo-tooltip" style="min-width:180px;"><div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;"><div style="width:{cache_pct}%;background:#8b5cf6;"></div><div style="width:{prompt_pct}%;background:#10b981;"></div><div style="width:{gen_pct}%;background:#f59e0b;"></div></div><div class="tooltip-box" style="width:240px;"><div class="tooltip-title">CONTEXTE</div><div class="tooltip-row"><span>Cache:</span> <span>{c_t}</span></div><div class="tooltip-row"><span>Prompt:</span> <span>{active_p_t}</span></div><div class="tooltip-row"><span>Génération:</span> <span>{g_t}</span></div><div class="tooltip-row" style="font-weight:bold;margin-top:4px;"><span>Total:</span> <span>{total_t} / {max_t}</span></div></div></div>`;
      hud.innerHTML = iconHtml + barHtml;
      hudWrapper.appendChild(hud);
      document.body.appendChild(hudWrapper);
    }})();
    """
    await events.emit("execute", {"code": js_code})
  @staticmethod
  def show_image_js(img_url: str, title: str = "Aperçu Image") -> str:
    """Réutilise le moteur WebPlayer (HUD navigateur) pour afficher une image.
    Utiliser via events.call('execute', {'code': ...}).
    N'utilise pas HTMLResponse — retour 100% propre, sans pollution du contexte Gemini."""
    return EchoUI._generate_webplayer_js(
      b64="", mime="image/png", metadata=[], current_url=title, 
      hud_id="echo-preview", state_key="echo_preview_state", icon="🖼️"
    )

  @classmethod
  def image_viewer(cls, img_url: str, title: str = "Aperçu Image") -> HTMLResponse:
    """Maintenu pour les Actions OWUI. Pour les Tools, utiliser show_image_js() + events.call."""
    content = f"""
    <div id="hud-bar"><span style="font-weight:bold;">👁️ ECHO Vision Explorer</span></div>
    <div id="canvas-area" style="width:100%; height:600px; overflow:hidden; position:relative; background:#f1f5f9; display:flex; align-items:center; justify-content:center;">
      <img src="{img_url}" style="max-width:100%; max-height:100%;">
    </div>
    """
    html = cls._get_boilerplate(content, title)
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

  @classmethod
  def player_ui(cls, session_id: str, total_steps: int) -> HTMLResponse:
    content = f"<div style='padding:20px;'>Interface Replay v5.136 active via Action.</div>"
    html = cls._get_boilerplate(content, "ECHO Navigation Replay")
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})


  @classmethod
  def map_viewer(cls, query: str, title: str = "Localisation") -> HTMLResponse:
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
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

  @classmethod
  def generate_rich_view(cls, moteur: str, payload: str, title: str = "ECHO Rendu Visuel", cdn_timeout_ms: int = 30000) -> tuple:
    """Usine de rendu universelle (Restauration intégrale v5.121)."""
    from echo_visuals import VisualEngine
    cfg = VisualEngine.get_config(moteur, payload, cdn_timeout_ms=cdn_timeout_ms)

    styles_list = [s for s in cfg.get("scripts", []) if s.endswith('.css')]
    scripts_list = [s for s in cfg.get("scripts", []) if not s.endswith('.css')]

    styles_html = "\n".join([f'<link rel="stylesheet" href="{s}">' for s in styles_list])
    scripts_html = "\n".join([f'<script src="{s}"></script>' for s in scripts_list])

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
    response = HTMLResponse(content=html, headers={"Content-Disposition": "inline"})
    return response, {"status": "success", "message": f"Visualisation {moteur} générée."}

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

    var printStyle = document.createElement('style');
    printStyle.id = STYLE_ID;
    printStyle.textContent =
        '@media print {{\\n' +
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

    window.print();
}});
"""

  # =====================================================================
  # ECHO CODEX — HUD Monaco Editor
  # =====================================================================

  @staticmethod
  def _generate_codex_js(files_json: str, quick_actions_json: str, chat_id: str) -> str:
    """Génère le script JS complet du HUD Monaco Codex.
    Injection via __event_call__({type: 'execute', data: {code: ...}})."""
    return f"""
    (function() {{
      const CODEX_ID = 'echo-codex-hud';
      {EchoUI.get_mobile_guard_js("echo-codex-hud", block_execution=True, error_msg="Le Codex ECHO requiert un navigateur de bureau.")}
      const CID = '{chat_id}';
      const STATE_KEY = 'echo_codex_' + CID;
      const MONACO_CDN = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';

      let existingHud = document.getElementById(CODEX_ID);
      if (existingHud) {{ existingHud.remove(); }}

      // --- State ---
      let files = {files_json};
      const quickActions = {quick_actions_json};
      // Mapping langage Monaco → extension (pour rename)
      const LANG_TO_EXT = {{
        python:'.py', javascript:'.js', typescript:'.ts', c:'.c', cpp:'.cpp',
        java:'.java', go:'.go', rust:'.rs', ruby:'.rb', php:'.php', swift:'.swift',
        kotlin:'.kt', csharp:'.cs', vb:'.vb', shell:'.sh', powershell:'.ps1',
        bat:'.bat', html:'.html', css:'.css', json:'.json', xml:'.xml',
        yaml:'.yaml', toml:'.toml', ini:'.ini', markdown:'.md', plaintext:'.txt',
        sql:'.sql', r:'.r', lua:'.lua', perl:'.pl', dockerfile:'.dockerfile',
      }};
      let currentFile = files.length > 0 ? files[0].filename : null;
      let editor = null;
      let diffEditor = null;
      let isDiffMode = false;
      let isHistoryMode = false;
      let historyContent = null;
      let lastInstruction = '';
      let lastModel = 'MODEL_FLASH';
      let modified = false;
      let previewOpen = false;
      let editorRatio = 33;
      let previewRatio = 67;
      const PREVIEW_LANGS = ['markdown', 'html', 'css', 'xml'];
      let markedLoaded = false;
      let previewDebounceTimer = null;

      // --- Restore position ---
      let savedState = {{}};
      try {{ savedState = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}'); }} catch(e) {{}}
      if (savedState.previewOpen !== undefined) previewOpen = savedState.previewOpen;
      if (savedState.editorRatio) editorRatio = savedState.editorRatio;
      if (savedState.previewRatio) previewRatio = savedState.previewRatio;

      // --- Theme Detection ---
      const isDark = document.documentElement.classList.contains('dark') ||
                     window.matchMedia('(prefers-color-scheme: dark)').matches;
      const theme = isDark ? 'vs-dark' : 'vs';
      const bgColor = isDark ? '#1e1e2e' : '#ffffff';
      const borderColor = isDark ? '#444' : '#ddd';
      const textColor = isDark ? '#cdd6f4' : '#333';
      const headerBg = isDark ? 'rgba(30,30,46,0.95)' : 'rgba(245,245,245,0.95)';
      const sidebarBg = isDark ? '#181825' : '#f0f0f0';
      const statusBg = isDark ? '#11111b' : '#e8e8e8';
      const accentColor = '#89b4fa';
      const hoverBg = isDark ? 'rgba(137,180,250,0.1)' : 'rgba(0,0,0,0.05)';
      const historyBg = isDark ? 'rgba(250,179,135,0.15)' : 'rgba(255,200,100,0.2)';

      // --- Custom Scrollbars ---
      if (!document.getElementById(CODEX_ID + '-scrollbars')) {{
        const scrollStyle = document.createElement('style');
        scrollStyle.id = CODEX_ID + '-scrollbars';
        scrollStyle.textContent = `
          #${{CODEX_ID}} *::-webkit-scrollbar {{ width: 10px; height: 10px; }}
          #${{CODEX_ID}} *::-webkit-scrollbar-track {{ background: ${{isDark ? 'rgba(0,0,0,0.2)' : 'rgba(0,0,0,0.05)'}}; border-radius: 4px; }}
          #${{CODEX_ID}} *::-webkit-scrollbar-thumb {{ background: ${{isDark ? '#555' : '#ccc'}}; border-radius: 4px; }}
          #${{CODEX_ID}} *::-webkit-scrollbar-thumb:hover {{ background: ${{accentColor}}; }}
          #${{CODEX_ID}} *::-webkit-scrollbar-corner {{ background: transparent; }}
        `;
        document.head.appendChild(scrollStyle);
      }}

      // --- HUD Container ---
      const hud = document.createElement('div');
      hud.id = CODEX_ID;
      hud.style.cssText = `position:fixed; z-index:10001; display:flex; flex-direction:column;
        background:${{bgColor}}; border:1px solid ${{borderColor}}; border-radius:12px;
        box-shadow:0 20px 60px rgba(0,0,0,0.4); font-family:'Segoe UI',system-ui,sans-serif;
        color:${{textColor}}; overflow:hidden; resize:both; min-width:600px; min-height:400px;
        width:${{savedState.w || '900px'}}; height:${{savedState.h || '600px'}};
        top:${{savedState.y || '60px'}}; left:${{savedState.x || '50%'}};
        ${{savedState.x ? '' : 'transform:translateX(-50%);'}}`;

      // --- HEADER (draggable) ---
      const header = document.createElement('div');
      header.style.cssText = `display:flex; align-items:center; padding:8px 12px; gap:8px;
        background:${{headerBg}}; border-bottom:1px solid ${{borderColor}}; cursor:move;
        user-select:none; flex-shrink:0;`;
      header.innerHTML = `
        <span style="font-weight:600; font-size:14px;">📝 ECHO Codex</span>
        <span style="flex:1;"></span>
        <select id="${{CODEX_ID}}-lang" style="background:transparent; border:1px solid ${{borderColor}};
          color:${{textColor}}; padding:2px 6px; border-radius:4px; font-size:12px;"></select>
        <button id="${{CODEX_ID}}-import" title="Importer (PC → Codex)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px;">📂</button>
        <button id="${{CODEX_ID}}-export" title="Exporter (Codex → PC)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px;">💾</button>
        <button id="${{CODEX_ID}}-copy" title="Copier" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg></button>
        <button id="${{CODEX_ID}}-refresh" title="Actualiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg></button>
        <button id="${{CODEX_ID}}-save" title="Sauvegarder (Ctrl+S)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1; opacity:0.3; transition:opacity 0.2s;"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg></button>
        <button id="${{CODEX_ID}}-preview-toggle" title="Prévisualisation" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px; opacity:0.4;">👁️</button>
        <button id="${{CODEX_ID}}-minimize" title="Minimiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px;">—</button>
        <button id="${{CODEX_ID}}-close" title="Fermer" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:18px;">×</button>`;
      hud.appendChild(header);

      // --- BODY (sidebar + editor) ---
      const body = document.createElement('div');
      body.style.cssText = 'display:flex; flex:1; overflow:hidden;';

      // Sidebar (file tree)
      const sidebar = document.createElement('div');
      sidebar.id = CODEX_ID + '-sidebar';
      sidebar.style.cssText = `width:150px; background:${{sidebarBg}}; border-right:1px solid ${{borderColor}};
        overflow-y:auto; flex-shrink:0; display:flex; flex-direction:column; padding:6px 0;`;

      // Editor container
      const editorWrap = document.createElement('div');
      editorWrap.id = CODEX_ID + '-editor';
      editorWrap.style.cssText = `flex:${{previewOpen ? editorRatio : 100}} 1 0%; overflow:hidden; position:relative; min-width:0;`;

      // Splitter (entre éditeur et preview)
      const splitter = document.createElement('div');
      splitter.id = CODEX_ID + '-splitter';
      splitter.style.cssText = `width:5px; cursor:col-resize; background:${{borderColor}}; flex-shrink:0; display:none; transition:background 0.15s;`;
      splitter.onmouseenter = () => splitter.style.background = accentColor;
      splitter.onmouseleave = () => splitter.style.background = borderColor;

      // Preview panel (panneau latéral droit)
      const previewPanel = document.createElement('div');
      previewPanel.id = CODEX_ID + '-preview';
      previewPanel.style.cssText = `flex:${{previewRatio}} 1 0%; min-width:0; display:none; flex-direction:column; overflow:hidden; background:${{bgColor}};`;
      previewPanel.innerHTML = `
        <div style="padding:6px 10px; font-size:11px; color:${{isDark ? '#a6adc8' : '#888'}}; border-bottom:1px solid ${{borderColor}}; user-select:none; flex-shrink:0; display:flex; align-items:center; gap:8px;">
          <button id="${{CODEX_ID}}-preview-print" title="Print / PDF" style="background:none; border:none; color:${{textColor}}; cursor:pointer; padding:0; display:none; align-items:center; opacity:0.8; transition:opacity 0.2s;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.8">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 18 15 15"/></svg>
          </button>
          <span id="${{CODEX_ID}}-preview-label" style="flex:1;">Preview</span>
        </div>
        <div id="${{CODEX_ID}}-preview-content" style="flex:1; padding:12px; overflow:auto; font-size:14px; line-height:1.6; min-width:0;"></div>
      `;

      body.appendChild(sidebar);
      body.appendChild(editorWrap);
      body.appendChild(splitter);
      body.appendChild(previewPanel);
      hud.appendChild(body);

      // --- AI PANEL (mini-chat + quick actions + model selector) ---
      const aiPanel = document.createElement('div');
      aiPanel.style.cssText = `display:flex; flex-direction:column; gap:6px; padding:8px 12px;
        border-top:1px solid ${{borderColor}}; flex-shrink:0;`;
      aiPanel.innerHTML = `
        <div style="display:flex; gap:6px;">
          <input id="${{CODEX_ID}}-ai-input" type="text" placeholder="Instruction instantan\u00e9e pour l'IA..."
            style="flex:1; background:transparent; border:1px solid ${{borderColor}}; color:${{textColor}};
            padding:6px 10px; border-radius:6px; font-size:13px; outline:none;"
          />
          <select id="${{CODEX_ID}}-model" title="Mod\u00e8le AI" style="background:transparent; border:1px solid ${{borderColor}};
            color:${{textColor}}; padding:2px 6px; border-radius:4px; font-size:11px; max-width:90px;">
            <option value="MODEL_FLASH" selected>Flash</option>
            <option value="MODEL_PRO">Pro</option>
            <option value="MODEL_LITE">Lite</option>
          </select>
          <button id="${{CODEX_ID}}-ai-send" style="background:${{accentColor}}; border:none; color:#1e1e2e;
            padding:6px 14px; border-radius:6px; font-size:13px; cursor:pointer; font-weight:600;">Envoyer</button>
        </div>
        <div id="${{CODEX_ID}}-quick" style="display:flex; gap:4px; flex-wrap:wrap;"></div>`;
      hud.appendChild(aiPanel);

      // Tracker le choix utilisateur sur le dropdown modèle
      const modelSelect = document.getElementById(CODEX_ID + '-model');
      if (modelSelect) modelSelect.onchange = () => {{ lastModel = modelSelect.value; }};

      // --- MICRO-SPINNER (sur le bouton cliqué) ---
      let spinnerTarget = null;
      let spinnerOriginal = '';
      const spinStyle = document.createElement('style');
      spinStyle.textContent = '@keyframes echoCodexSpin {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}';
      document.head.appendChild(spinStyle);
      function showButtonSpinner(btn) {{
        if (!btn) return;
        spinnerTarget = btn;
        spinnerOriginal = btn.innerHTML;
        btn.disabled = true;
        btn.innerHTML = `<span style="display:inline-block; width:14px; height:14px; border:2px solid ${{borderColor}};
          border-top-color:${{accentColor}}; border-radius:50%; animation:echoCodexSpin 0.7s linear infinite;"></span>`;
      }}
      function hideButtonSpinner() {{
        if (spinnerTarget) {{
          spinnerTarget.innerHTML = spinnerOriginal;
          spinnerTarget.disabled = false;
          spinnerTarget = null;
          spinnerOriginal = '';
        }}
      }}

      // --- STATUS BAR (historique ◀ ▶) ---
      const statusBar = document.createElement('div');
      statusBar.id = CODEX_ID + '-status';
      statusBar.style.cssText = `display:flex; align-items:center; padding:4px 12px; gap:8px;
        background:${{statusBg}}; border-top:1px solid ${{borderColor}}; font-size:11px;
        font-family:monospace; flex-shrink:0; min-height:28px;`;
      statusBar.innerHTML = `
        <button id="${{CODEX_ID}}-hist-prev" title="Version pr\u00e9c\u00e9dente" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px;">◀</button>
        <span id="${{CODEX_ID}}-status-text" style="flex:1; color:${{isDark ? '#a6adc8' : '#666'}};">Pr\u00eat</span>
        <button id="${{CODEX_ID}}-hist-next" title="Version suivante" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px;">▶</button>
        <div id="${{CODEX_ID}}-hist-actions" style="display:none; gap:6px;">
          <button id="${{CODEX_ID}}-hist-pin" style="background:none; border:1px solid ${{borderColor}}; color:${{textColor}}; cursor:pointer; padding:1px 8px; border-radius:4px; font-size:11px;">📌 Revenir au pr\u00e9sent</button>
          <button id="${{CODEX_ID}}-hist-restore" style="background:none; border:1px solid ${{borderColor}}; color:${{textColor}}; cursor:pointer; padding:1px 8px; border-radius:4px; font-size:11px;">⤴️ Restaurer</button>
        </div>`;
      hud.appendChild(statusBar);

      // --- DIFF ACTIONS (hidden by default) ---
      const diffBar = document.createElement('div');
      diffBar.id = CODEX_ID + '-diff-bar';
      diffBar.style.cssText = `display:none; justify-content:center; gap:12px; padding:8px;
        border-top:1px solid ${{borderColor}}; flex-shrink:0;`;
      diffBar.innerHTML = `
        <button id="${{CODEX_ID}}-diff-accept" style="background:#a6e3a1; border:none; color:#1e1e2e;
          padding:6px 20px; border-radius:6px; font-size:13px; cursor:pointer; font-weight:600;">✅ Accepter</button>
        <button id="${{CODEX_ID}}-diff-reject" style="background:#f38ba8; border:none; color:#1e1e2e;
          padding:6px 20px; border-radius:6px; font-size:13px; cursor:pointer; font-weight:600;">❌ Rejeter</button>`;
      hud.appendChild(diffBar);

      document.body.appendChild(hud);

      // ===== FILE TREE =====
      function renderFileTree() {{
        const sb = document.getElementById(CODEX_ID + '-sidebar');
        sb.innerHTML = '';
        files.forEach(f => {{
          const item = document.createElement('div');
          const isActive = f.filename === currentFile;
          item.style.cssText = `padding:4px 10px; cursor:pointer; font-size:12px; white-space:nowrap;
            overflow:hidden; text-overflow:ellipsis; display:flex; align-items:center;
            background:${{isActive ? hoverBg : 'transparent'}};
            border-left:${{isActive ? '3px solid ' + accentColor : '3px solid transparent'}};`;
          const nameSpan = document.createElement('span');
          nameSpan.style.cssText = 'flex:1; overflow:hidden; text-overflow:ellipsis;';
          nameSpan.textContent = (modified && isActive ? '● ' : '') + f.filename;
          nameSpan.title = f.filename + ' (' + f.lang + ', ' + f.lines + ' lines)';
          nameSpan.onclick = () => switchFile(f.filename);
          item.appendChild(nameSpan);
          // Bouton supprimer
          const delBtn = document.createElement('span');
          delBtn.textContent = '×';
          delBtn.title = 'Supprimer ' + f.filename;
          delBtn.style.cssText = `opacity:0; color:#f38ba8; cursor:pointer; font-size:14px;
            font-weight:bold; padding:0 4px; transition:opacity 0.15s;`;
          item.onmouseenter = () => delBtn.style.opacity = '1';
          item.onmouseleave = () => delBtn.style.opacity = '0';
          delBtn.onclick = (e) => {{
            e.stopPropagation();
            if (confirm('Supprimer ' + f.filename + ' ?')) {{
              window.echoCodexResolve({{action:'delete_file', filename:f.filename}});
            }}
          }};
          item.appendChild(delBtn);
          sb.appendChild(item);
        }});
        // + Créer
        const newBtn = document.createElement('div');
        newBtn.style.cssText = `padding:6px 10px; cursor:pointer; font-size:12px; color:${{accentColor}};`;
        newBtn.textContent = '+ Cr\u00e9er';
        newBtn.onclick = () => {{
          const name = prompt('Nom du fichier (ex: main.py)');
          if (name) window.echoCodexResolve({{action:'new_file', filename:name}});
        }};
        sb.appendChild(newBtn);
        // Reset
        const resetBtn = document.createElement('div');
        resetBtn.style.cssText = `padding:6px 10px; cursor:pointer; font-size:12px; color:#f38ba8; margin-top:auto;`;
        resetBtn.textContent = '🗑 Reset';
        resetBtn.onclick = () => {{
          if (confirm('⚠️ Supprimer tout le d\u00e9p\u00f4t Codex de cette conversation ? Irr\u00e9versible.')) {{
            window.echoCodexResolve({{action:'reset'}});
          }}
        }};
        sb.appendChild(resetBtn);
      }}

      function switchFile(filename) {{
        if (modified && currentFile) {{
          if (!confirm('Modifications non sauvegard\u00e9es. Continuer ?')) return;
        }}
        currentFile = filename;
        modified = false;
        renderFileTree();
        updateStatus(filename + ' \u2022 chargement...');
        // Demander le contenu au backend Python
        window.echoCodexResolve({{action:'load_file', filename:filename}});
      }}

      // ===== QUICK ACTIONS =====
      const quickDiv = document.getElementById(CODEX_ID + '-quick');
      Object.entries(quickActions).forEach(([key, instruction]) => {{
        const btn = document.createElement('button');
        btn.textContent = key.charAt(0).toUpperCase() + key.slice(1);
        btn.style.cssText = `background:transparent; border:1px solid ${{borderColor}}; color:${{textColor}};
          padding:2px 10px; border-radius:4px; font-size:11px; cursor:pointer;`;
        btn.onmouseenter = () => btn.style.background = hoverBg;
        btn.onmouseleave = () => btn.style.background = 'transparent';
        btn.onclick = () => sendAiEdit(instruction, btn);
        quickDiv.appendChild(btn);
      }});

      // ===== AI EDIT =====
      function sendAiEdit(instruction, triggerBtn) {{
        if (!currentFile || !editor) return;
        const selection = editor.getModel().getValueInRange(editor.getSelection());
        lastInstruction = instruction;
        showButtonSpinner(triggerBtn || document.getElementById(CODEX_ID + '-ai-send'));
        const modelSelect = document.getElementById(CODEX_ID + '-model');
        window.echoCodexResolve({{
          action: 'ai_edit',
          instruction: instruction,
          content: editor.getValue(),
          selection: selection || null,
          filename: currentFile,
          language: files.find(f => f.filename === currentFile)?.lang || 'plaintext',
          model: modelSelect ? modelSelect.value : 'MODEL_FLASH',
        }});
      }}

      document.getElementById(CODEX_ID + '-ai-send').onclick = () => {{
        const input = document.getElementById(CODEX_ID + '-ai-input');
        const sendBtn = document.getElementById(CODEX_ID + '-ai-send');
        if (input.value.trim()) {{ sendAiEdit(input.value.trim(), sendBtn); input.value = ''; }}
      }};
      document.getElementById(CODEX_ID + '-ai-input').onkeydown = (e) => {{
        if (e.key === 'Enter') document.getElementById(CODEX_ID + '-ai-send').click();
      }};

      // ===== STATUS =====
      function updateStatus(text) {{
        const el = document.getElementById(CODEX_ID + '-status-text');
        if (el) el.textContent = text;
      }}

      // ===== DRAG (clampé aux limites du viewport) =====
      let isDragging = false, dragX, dragY;
      header.onmousedown = (e) => {{
        if (e.target.tagName === 'BUTTON' || e.target.tagName === 'SELECT') return;
        isDragging = true;
        dragX = e.clientX - hud.offsetLeft;
        dragY = e.clientY - hud.offsetTop;
        hud.style.transform = 'none';
      }};
      document.onmousemove = (e) => {{
        if (!isDragging) return;
        const maxX = window.innerWidth - hud.offsetWidth;
        const maxY = window.innerHeight - 44; // Header height (~44px) toujours visible
        let newX = Math.max(0, Math.min(e.clientX - dragX, maxX));
        let newY = Math.max(0, Math.min(e.clientY - dragY, maxY));
        hud.style.left = newX + 'px';
        hud.style.top = newY + 'px';
      }};
      document.onmouseup = () => {{
        isDragging = false;
        saveState();
      }};

      // ===== HEADER BUTTONS =====
      document.getElementById(CODEX_ID + '-close').onclick = () => {{
        saveState();
        hud.remove();
        window.echoCodexResolve({{action:'close'}});
      }};
      let isMinimized = false;
      document.getElementById(CODEX_ID + '-minimize').onclick = () => {{
        isMinimized = !isMinimized;
        // Collapse : on masque tout sauf le header, et on fixe la taille
        body.style.display = isMinimized ? 'none' : 'flex';
        aiPanel.style.display = isMinimized ? 'none' : 'flex';
        statusBar.style.display = isMinimized ? 'none' : 'flex';
        if (!isMinimized && isDiffMode) diffBar.style.display = 'flex';
        else diffBar.style.display = 'none';
        hud.style.resize = isMinimized ? 'none' : 'both';
        hud.style.height = isMinimized ? 'auto' : (savedState.h || '600px');
        hud.style.minHeight = isMinimized ? '0' : '400px';
      }};

      // Import (PC → Codex)
      document.getElementById(CODEX_ID + '-import').onclick = () => {{
        const inp = document.createElement('input');
        inp.type = 'file';
        inp.multiple = true;
        inp.onchange = async () => {{
          for (const file of inp.files) {{
            const text = await file.text();
            window.echoCodexResolve({{action:'upload', filename:file.name, content:text}});
          }}
        }};
        inp.click();
      }};

      // Export (Codex → PC)
      document.getElementById(CODEX_ID + '-export').onclick = () => {{
        if (currentFile) window.echoCodexResolve({{action:'download', filename:currentFile}});
      }};

      // Copier dans le presse-papier (fallback HTTP via execCommand)
      document.getElementById(CODEX_ID + '-copy').onclick = () => {{
        if (!editor) return;
        const text = editor.getModel().getValueInRange(editor.getSelection()) || editor.getValue();
        const showOk = () => {{
          const btn = document.getElementById(CODEX_ID + '-copy');
          const orig = btn.innerHTML;
          btn.textContent = '\u2714';
          setTimeout(() => btn.innerHTML = orig, 1200);
        }};
        if (navigator.clipboard && window.isSecureContext) {{
          navigator.clipboard.writeText(text).then(showOk);
        }} else {{
          const ta = document.createElement('textarea');
          ta.value = text;
          ta.style.cssText = 'position:fixed;left:-9999px;';
          document.body.appendChild(ta);
          ta.select();
          document.execCommand('copy');
          document.body.removeChild(ta);
          showOk();
        }}
      }};

      // History ◀ ▶
      document.getElementById(CODEX_ID + '-hist-prev').onclick = () => {{
        if (currentFile) window.echoCodexResolve({{action:'history_prev', filename:currentFile}});
      }};
      document.getElementById(CODEX_ID + '-hist-next').onclick = () => {{
        if (currentFile) window.echoCodexResolve({{action:'history_next', filename:currentFile}});
      }};

      // Refresh 🔄
      document.getElementById(CODEX_ID + '-refresh').onclick = () => {{
        window.echoCodexResolve({{action:'refresh', filename:currentFile || ''}});
      }};
      document.getElementById(CODEX_ID + '-hist-pin').onclick = () => {{
        if (currentFile) window.echoCodexResolve({{action:'history_exit', filename:currentFile}});
      }};
      document.getElementById(CODEX_ID + '-hist-restore').onclick = () => {{
        if (currentFile && historyContent !== null) {{
          window.echoCodexResolve({{action:'history_restore', filename:currentFile, content:historyContent, source_hash:document.getElementById(CODEX_ID+'-status-text').dataset.hash||''}});
        }}
      }};

      // ===== DIFF ACCEPT/REJECT =====
      document.getElementById(CODEX_ID + '-diff-accept').onclick = () => {{
        if (diffEditor) {{
          const content = diffEditor.getModifiedEditor().getValue();
          window.echoCodexResolve({{action:'accept_diff', filename:currentFile, content:content, instruction:lastInstruction}});
        }}
      }};
      document.getElementById(CODEX_ID + '-diff-reject').onclick = () => {{
        window.echoCodexResolve({{action:'reject_diff'}});
      }};

      // ===== SAVE STATE =====
      function saveState() {{
        try {{
          localStorage.setItem(STATE_KEY, JSON.stringify({{
            x: hud.style.left, y: hud.style.top,
            w: hud.style.width, h: hud.style.height,
            previewOpen: previewOpen,
            editorRatio: editorRatio,
            previewRatio: previewRatio,
          }}));
        }} catch(e) {{}}
      }}

      // Ctrl+S
      function doSave() {{
        if (currentFile && editor) {{
          window.echoCodexResolve({{action:'save', filename:currentFile, content:editor.getValue(),
            language:files.find(f=>f.filename===currentFile)?.lang||'plaintext'}});
          modified = false;
          renderFileTree();
          updateSaveButton();
        }}
      }}
      // Mise à jour visuelle du bouton save
      function updateSaveButton() {{
        const btn = document.getElementById(CODEX_ID + '-save');
        if (!btn) return;
        btn.style.opacity = modified ? '1' : '0.3';
        btn.style.color = modified ? accentColor : textColor;
      }}
      document.getElementById(CODEX_ID + '-save').onclick = () => doSave();
      editorWrap.onkeydown = (e) => {{
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {{
          e.preventDefault();
          doSave();
        }}
      }};

      // ===== GLOBAL API (callable from Python) =====

      window.echoCodexNotify = (type, msg) => {{
        hideButtonSpinner();
        if (type === 'saved') {{ updateStatus('\ud83d\udcbe Sauvegard\u00e9 \u2022 ' + msg); modified = false; renderFileTree(); }}
        else if (type === 'committed') {{
          updateStatus('\u2705 Commit\u00e9 \u2022 ' + msg); exitDiffMode();
          modified = false; renderFileTree();
          // Reload géré côté Python (push echoCodexSetContent)
        }}
        else if (type === 'restored') {{ updateStatus('\u2934\ufe0f Restaur\u00e9 \u2022 ' + msg); exitHistoryMode(); }}
        else {{ updateStatus(msg); }}
      }};

      // Repositionner le dropdown sur le modèle effectif (après cascade)
      window.echoCodexSetModel = (modelKey) => {{
        lastModel = modelKey;
        const sel = document.getElementById(CODEX_ID + '-model');
        if (!sel) return;
        for (let i = 0; i < sel.options.length; i++) {{
          if (sel.options[i].value === modelKey) {{
            sel.selectedIndex = i;
            return;
          }}
        }}
      }};

      window.echoCodexShowDiff = (modifiedContent) => {{
        hideButtonSpinner();
        isDiffMode = true;
        // Masquer le preview pendant le diff
        document.getElementById(CODEX_ID + '-preview').style.display = 'none';
        document.getElementById(CODEX_ID + '-splitter').style.display = 'none';
        editorWrap.innerHTML = '';
        diffEditor = monaco.editor.createDiffEditor(editorWrap, {{
          theme: theme, readOnly: false, renderSideBySide: true,
          automaticLayout: true, minimap: {{enabled: false}},
        }});
        const originalModel = monaco.editor.createModel(editor ? editor.getValue() : '', files.find(f=>f.filename===currentFile)?.lang||'plaintext');
        const modifiedModel = monaco.editor.createModel(modifiedContent, files.find(f=>f.filename===currentFile)?.lang||'plaintext');
        diffEditor.setModel({{ original: originalModel, modified: modifiedModel }});
        document.getElementById(CODEX_ID + '-diff-bar').style.display = 'flex';
        aiPanel.style.display = 'none';
      }};

      window.echoCodexRevertDiff = () => {{
        hideButtonSpinner();
        exitDiffMode();
        // Reload géré côté Python (push echoCodexSetContent)
      }};

      function exitDiffMode() {{
        if (!isDiffMode) return;
        isDiffMode = false;
        diffEditor = null;
        document.getElementById(CODEX_ID + '-diff-bar').style.display = 'none';
        aiPanel.style.display = 'flex';
        initEditor();
        // Restaurer la sélection modèle après réaffichage du panel
        window.echoCodexSetModel(lastModel);
        // Restaurer le preview si ouvert
        if (previewOpen && getPreviewLang()) {{
          document.getElementById(CODEX_ID + '-preview').style.display = 'flex';
          document.getElementById(CODEX_ID + '-splitter').style.display = 'block';
          setTimeout(updatePreview, 100);
        }}
      }}

      window.echoCodexRefreshTree = (newFiles) => {{
        files = newFiles;
        renderFileTree();
      }};

      window.echoCodexDownload = (name, content) => {{
        const blob = new Blob([content], {{type: 'text/plain'}});
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);
      }};

      window.echoCodexReset = () => {{ hud.remove(); }};

      // Chargement de contenu depuis le backend Python
      window.echoCodexSetContent = (content, filename) => {{
        if (editor) {{
          const lang = files.find(f => f.filename === filename)?.lang || 'plaintext';
          const model = monaco.editor.createModel(content, lang);
          editor.setModel(model);
          editor.onDidChangeModelContent(() => {{
            modified = true; renderFileTree(); updateSaveButton();
            if (previewOpen) {{
              clearTimeout(previewDebounceTimer);
              previewDebounceTimer = setTimeout(updatePreview, 400);
            }}
          }});
          modified = false;
          // Mettre à jour le sélecteur de langage
          const langSelect = document.getElementById(CODEX_ID + '-lang');
          if (langSelect) langSelect.value = lang;
          updateStatus(filename + ' \u2022 charg\u00e9');
          // Preview : mise à jour du bouton et du rendu
          updatePreviewButton();
          if (previewOpen && !PREVIEW_LANGS.includes(lang)) {{
            togglePreview(false);
          }} else if (previewOpen) {{
            updatePreview();
          }}
        }}
      }};

      window.echoCodexLoadVersion = (content, info, idx, total) => {{
        isHistoryMode = true;
        historyContent = content;
        if (editor) {{
          editor.setValue(content);
          editor.updateOptions({{readOnly: true}});
        }}
        editorWrap.style.background = historyBg;
        const statusText = document.getElementById(CODEX_ID + '-status-text');
        statusText.textContent = `(${{idx+1}}/${{total}}) ${{info.hash}} "${{info.message}}"`;
        statusText.dataset.hash = info.hash;
        document.getElementById(CODEX_ID + '-hist-actions').style.display = 'flex';
      }};

      window.echoCodexExitHistory = () => {{ exitHistoryMode(); }};

      function exitHistoryMode() {{
        isHistoryMode = false;
        historyContent = null;
        if (editor) editor.updateOptions({{readOnly: false}});
        editorWrap.style.background = 'transparent';
        document.getElementById(CODEX_ID + '-hist-actions').style.display = 'none';
        updateStatus(currentFile ? currentFile + ' \u2022 HEAD' : 'Pr\u00eat');
      }}

      // ===== PREVIEW PANEL =====

      // Styles prose pour le rendu Markdown
      if (!document.getElementById(CODEX_ID + '-prose-styles')) {{
        const _ps = document.createElement('style');
        _ps.id = CODEX_ID + '-prose-styles';
        _ps.textContent = `
          .echo-codex-prose {{ color: ${{textColor}}; line-height: 1.7; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; word-wrap: break-word; }}
          .echo-codex-prose h1 {{ font-size: 1.8em; margin: 0.8em 0 0.4em; border-bottom: 1px solid ${{borderColor}}; padding-bottom: 0.3em; }}
          .echo-codex-prose h2 {{ font-size: 1.5em; margin: 0.7em 0 0.3em; }}
          .echo-codex-prose h3 {{ font-size: 1.25em; margin: 0.6em 0 0.3em; }}
          .echo-codex-prose h4 {{ font-size: 1.1em; margin: 0.5em 0 0.2em; }}
          .echo-codex-prose p {{ margin: 0.5em 0; }}
          .echo-codex-prose code {{ background: rgba(127,127,127,0.15); padding: 2px 5px; border-radius: 3px; font-size: 0.9em; font-family: 'Cascadia Code', 'Fira Code', monospace; }}
          .echo-codex-prose pre {{ background: ${{isDark ? 'rgba(0,0,0,0.4)' : 'rgba(0,0,0,0.06)'}}; padding: 12px; border-radius: 6px; overflow-x: auto; }}
          .echo-codex-prose pre code {{ background: none; padding: 0; }}
          .echo-codex-prose blockquote {{ border-left: 3px solid ${{accentColor}}; padding-left: 12px; margin-left: 0; opacity: 0.85; font-style: italic; }}
          .echo-codex-prose table {{ border-collapse: collapse; width: 100%; margin: 0.5em 0; }}
          .echo-codex-prose th, .echo-codex-prose td {{ border: 1px solid ${{borderColor}}; padding: 6px 10px; text-align: left; }}
          .echo-codex-prose th {{ background: ${{isDark ? 'rgba(255,255,255,0.05)' : 'rgba(0,0,0,0.04)'}}; font-weight: 600; }}
          .echo-codex-prose img {{ max-width: 100%; border-radius: 4px; }}
          .echo-codex-prose a {{ color: ${{accentColor}}; text-decoration: none; }}
          .echo-codex-prose a:hover {{ text-decoration: underline; }}
          .echo-codex-prose ul, .echo-codex-prose ol {{ padding-left: 1.5em; }}
          .echo-codex-prose li {{ margin: 0.25em 0; }}
          .echo-codex-prose hr {{ border: none; border-top: 1px solid ${{borderColor}}; margin: 1em 0; }}
        `;
        document.head.appendChild(_ps);
      }}

      function getPreviewLang() {{
        if (!currentFile) return null;
        const lang = files.find(f => f.filename === currentFile)?.lang || 'plaintext';
        return PREVIEW_LANGS.includes(lang) ? lang : null;
      }}

      function updatePreviewButton() {{
        const btn = document.getElementById(CODEX_ID + '-preview-toggle');
        if (!btn) return;
        const lang = getPreviewLang();
        if (lang) {{
          btn.disabled = false;
          btn.style.opacity = previewOpen ? '1' : '0.6';
          btn.style.color = previewOpen ? accentColor : textColor;
          btn.title = 'Pr\u00e9visualisation ' + lang.toUpperCase();
        }} else {{
          btn.disabled = true;
          btn.style.opacity = '0.25';
          btn.style.color = textColor;
          btn.title = 'Pr\u00e9visualisation indisponible';
        }}
      }}

      function togglePreview(forceState) {{
        const panel = document.getElementById(CODEX_ID + '-preview');
        const split = document.getElementById(CODEX_ID + '-splitter');
        if (!panel || !split) return;

        previewOpen = forceState !== undefined ? forceState : !previewOpen;

        if (previewOpen && !getPreviewLang()) {{
          previewOpen = false;
          return;
        }}

        panel.style.display = previewOpen ? 'flex' : 'none';
        split.style.display = previewOpen ? 'block' : 'none';
        if (previewOpen) {{
          editorWrap.style.flex = `${{editorRatio}} 1 0%`;
          panel.style.flex = `${{previewRatio}} 1 0%`;
        }} else {{
          editorWrap.style.flex = `1 1 0%`;
        }}

        updatePreviewButton();
        saveState();

        if (previewOpen) updatePreview();
      }}

      function updatePreview() {{
        const lang = getPreviewLang();
        if (!lang || !previewOpen) return;

        const content = editor ? editor.getValue() : '';
        const container = document.getElementById(CODEX_ID + '-preview-content');
        const label = document.getElementById(CODEX_ID + '-preview-label');
        const printBtn = document.getElementById(CODEX_ID + '-preview-print');
        if (!container) return;

        if (printBtn) printBtn.style.display = (lang === 'markdown' || lang === 'html') ? 'flex' : 'none';

        if (lang === 'markdown') {{
          if (label) label.textContent = 'Markdown Preview';
          renderMarkdown(content, container);
        }} else if (lang === 'html') {{
          if (label) label.textContent = 'HTML Preview';
          renderHTML(content, container);
        }} else if (lang === 'css') {{
          if (label) label.textContent = 'CSS Preview';
          renderCSS(content, container);
        }} else if (lang === 'xml') {{
          if (label) label.textContent = 'SVG Preview';
          renderSVG(content, container);
        }}
      }}

      function renderMarkdown(content, container) {{
        container.style.padding = '12px';
        const oldIframe = container.querySelector('iframe');
        if (oldIframe) oldIframe.remove();
        if (window.marked) {{
          container.innerHTML = '<div class="echo-codex-prose">' + window.marked.parse(content) + '</div>';
        }} else if (!markedLoaded) {{
          container.innerHTML = '<div style="color:' + (isDark ? '#a6adc8' : '#888') + '; padding:20px;">Chargement marked.js...</div>';
          loadMarked(() => renderMarkdown(content, container));
        }} else {{
          container.innerHTML = '<div style="color:#f38ba8; padding:12px;">\u274c marked.js non disponible</div>';
        }}
      }}

      function renderHTML(content, container) {{
        container.style.padding = '0';
        let iframe = container.querySelector('iframe');
        if (!iframe) {{
          container.innerHTML = '';
          iframe = document.createElement('iframe');
          iframe.sandbox = 'allow-scripts';
          iframe.style.cssText = 'width:100%; height:100%; border:none; background:white;';
          container.appendChild(iframe);
        }}
        iframe.srcdoc = content;
      }}

      function renderCSS(content, container) {{
        container.style.padding = '0';
        let iframe = container.querySelector('iframe');
        if (!iframe) {{
          container.innerHTML = '';
          iframe = document.createElement('iframe');
          iframe.sandbox = 'allow-scripts';
          iframe.style.cssText = 'width:100%; height:100%; border:none; background:white;';
          container.appendChild(iframe);
        }}
        iframe.srcdoc = '<!DOCTYPE html><html><head><style>' + content + '</style></head><body>' +
          '<h1>Heading 1</h1><h2>Heading 2</h2><h3>Heading 3</h3>' +
          '<p>Paragraph with <strong>bold</strong>, <em>italic</em>, and <a href="#">link</a>.</p>' +
          '<ul><li>Item 1</li><li>Item 2</li><li>Item 3</li></ul>' +
          '<blockquote>Blockquote example</blockquote>' +
          '<pre><code>code {{ display: block; }}</code></pre>' +
          '<table><tr><th>Header</th><th>Header</th></tr><tr><td>Cell</td><td>Cell</td></tr></table>' +
          '<button>Button</button> <input type="text" placeholder="Input" />' +
          '<div class="demo">Demo div</div></body></html>';
      }}

      function renderSVG(content, container) {{
        container.style.padding = '12px';
        const oldIframe = container.querySelector('iframe');
        if (oldIframe) oldIframe.remove();
        const cleaned = content.replace(/<script[\s\S]*?<\/script>/gi, '');
        container.innerHTML = '<div style="display:flex; align-items:center; justify-content:center; min-height:200px;">' + cleaned + '</div>';
        const svg = container.querySelector('svg');
        if (svg) {{
          svg.style.maxWidth = '100%';
          svg.style.height = 'auto';
        }}
      }}

      function loadMarked(callback) {{
        if (markedLoaded) {{ if (window.marked) callback(); return; }}
        // import() ESM natif : bypasse totalement l'AMD loader de Monaco
        // qui intercepte les script tags UMD via define/require
        import('https://cdn.jsdelivr.net/npm/marked@15/+esm')
          .then(m => {{
            window.marked = m;
            markedLoaded = true;
            callback();
          }})
          .catch(err => {{
            markedLoaded = true;
            const c = document.getElementById(CODEX_ID + '-preview-content');
            if (c) c.innerHTML = '<div style="color:#f38ba8; padding:12px;">\u274c marked.js: ' + err.message + '</div>';
          }});
      }}

      // Toggle preview
      document.getElementById(CODEX_ID + '-preview-toggle').onclick = () => {{
        togglePreview();
      }};

      // Print Preview
      document.getElementById(CODEX_ID + '-preview-print').onclick = () => {{
        const isolationFn = function() {{
          {EchoUI.get_print_isolation_js('#echo-codex-hud-preview-content')}
        }};
        isolationFn();
      }};

      // Splitter drag logic
      let isDraggingSplit = false;
      document.getElementById(CODEX_ID + '-splitter').onmousedown = (e) => {{
        e.preventDefault();
        isDraggingSplit = true;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        // Bloquer les events sur l'iframe du preview (sinon elle capture les mousemove)
        document.getElementById(CODEX_ID + '-preview').style.pointerEvents = 'none';
        editorWrap.style.pointerEvents = 'none';
      }};
      document.addEventListener('mousemove', (e) => {{
        if (!isDraggingSplit) return;
        const hudRect = hud.getBoundingClientRect();
        const sidebarW = document.getElementById(CODEX_ID + '-sidebar').offsetWidth;
        const avail = hudRect.width - sidebarW - 5;
        let edW = e.clientX - (hudRect.left + sidebarW);
        edW = Math.max(200, Math.min(edW, avail - 200));
        const prevW = avail - edW;
        editorRatio = (edW / avail) * 100;
        previewRatio = (prevW / avail) * 100;
        editorWrap.style.flex = `${{editorRatio}} 1 0%`;
        document.getElementById(CODEX_ID + '-preview').style.flex = `${{previewRatio}} 1 0%`;
      }});
      document.addEventListener('mouseup', () => {{
        if (isDraggingSplit) {{
          isDraggingSplit = false;
          document.body.style.cursor = '';
          document.body.style.userSelect = '';
          document.getElementById(CODEX_ID + '-preview').style.pointerEvents = '';
          editorWrap.style.pointerEvents = '';
          saveState();
        }}
      }});

      // Restaurer l'état du preview
      if (previewOpen) {{
        const _pp = document.getElementById(CODEX_ID + '-preview');
        const _sp = document.getElementById(CODEX_ID + '-splitter');
        if (_pp) _pp.style.display = 'flex';
        if (_sp) _sp.style.display = 'block';
        editorWrap.style.flex = `${{editorRatio}} 1 0%`;
        if (_pp) _pp.style.flex = `${{previewRatio}} 1 0%`;
      }}
      updatePreviewButton();

      // ===== MONACO LOADER =====
      function initEditor() {{
        editorWrap.innerHTML = '';
        const lang = files.find(f => f.filename === currentFile)?.lang || 'plaintext';
        editor = monaco.editor.create(editorWrap, {{
          value: '', language: lang, theme: theme,
          automaticLayout: true, minimap: {{enabled: true}},
          fontSize: 13, lineNumbers: 'on', wordWrap: 'on',
          scrollBeyondLastLine: false, renderWhitespace: 'selection',
        }});
        editor.onDidChangeModelContent(() => {{
          modified = true; renderFileTree(); updateSaveButton();
          if (previewOpen) {{
            clearTimeout(previewDebounceTimer);
            previewDebounceTimer = setTimeout(updatePreview, 400);
          }}
        }});

        // Populate lang selector
        const langSelect = document.getElementById(CODEX_ID + '-lang');
        langSelect.innerHTML = '';
        const langs = monaco.languages.getLanguages();
        langs.sort((a,b) => a.id.localeCompare(b.id));
        langs.forEach(l => {{
          const opt = document.createElement('option');
          opt.value = l.id;
          opt.textContent = l.id;
          if (l.id === lang) opt.selected = true;
          langSelect.appendChild(opt);
        }});
        langSelect.onchange = () => {{
          const newLang = langSelect.value;
          if (editor) monaco.editor.setModelLanguage(editor.getModel(), newLang);
          // Proposer le rename si l'extension correspond à un langage connu
          if (currentFile && LANG_TO_EXT[newLang]) {{
            const baseName = currentFile.replace(/\.[^.]+$/, '');
            const newExt = LANG_TO_EXT[newLang];
            const newFilename = baseName + newExt;
            if (newFilename !== currentFile) {{
              if (confirm('Renommer ' + currentFile + ' \u2192 ' + newFilename + ' ?')) {{
                window.echoCodexResolve({{action:'rename_file', old_name:currentFile, new_name:newFilename}});
              }}
            }}
          }}
          // Mise à jour preview
          updatePreviewButton();
          if (previewOpen) updatePreview();
        }};
      }}

      function loadMonaco() {{
        if (window.monaco) {{ initEditor(); renderFileTree(); return; }}
        const loaderScript = document.createElement('script');
        loaderScript.src = MONACO_CDN + '/vs/loader.js';
        loaderScript.onload = () => {{
          require.config({{ paths: {{ vs: MONACO_CDN + '/vs' }} }});
          require(['vs/editor/editor.main'], () => {{
            initEditor();
            renderFileTree();
          }});
        }};
        document.head.appendChild(loaderScript);
      }}

      loadMonaco();
    }})();
    """

  # =====================================================================
  # ECHO COGNITIVE MONITOR — HUD Sub-Agent Visualization
  # =====================================================================

  @staticmethod
  def _generate_agent_monitor_js(threads_json: str, chat_id: str) -> str:
    """Génère le script JS complet du HUD Cognitive Monitor.
    Injection via __event_call__(type: 'execute', data: code: ...).
    
    Affiche les threads cognitifs (delegates, experts, conseils) sous forme
    d'onglets verticaux avec arbre d'appels expand/collapse.
    3 contrôles : refresh manuel, auto-refresh slider 2-15s, réduire."""

    return (
      "(function() {\n"
      "  const HUD_ID = 'echo-cognitive-monitor';\n"
      + EchoUI.get_mobile_guard_js('echo-cognitive-monitor', block_execution=True, error_msg="Agent Monitor requiert un navigateur de bureau.") + "\n"
      "  const CID = '" + chat_id + "';\n"
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
      "\n"
      "  var saved = {};\n"
      "  try { saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{}'); } catch(e) {}\n"
      "  var posX = saved.x || 60;\n"
      "  var posY = saved.y || 60;\n"
      "  var hudW = saved.w || '720px';\n"
      "  var hudH = saved.h || '500px';\n"
      "  var autoInterval = saved.interval || 5;\n"
      "  var autoEnabled = saved.autoOn || false;\n"
      "  isMinimized = saved.min || false;\n"
      "  if (saved.activeIdx !== undefined) activeThreadIdx = saved.activeIdx;\n"
      "  if (saved.expanded) try { expandedNodes = JSON.parse(saved.expanded); } catch(e) {}\n"
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
      "    return d.toLocaleTimeString('fr-FR', {hour:'2-digit', minute:'2-digit', second:'2-digit'});\n"
      "  }\n"
      "  function saveState() {\n"
      "    var hud = document.getElementById(HUD_ID);\n"
      "    if (!hud) return;\n"
      "    localStorage.setItem(STATE_KEY, JSON.stringify({\n"
      "      x: posX, y: posY,\n"
      "      w: hud.style.width, h: hud.style.height,\n"
      "      interval: autoInterval, autoOn: autoEnabled, min: isMinimized,\n"
      "      activeIdx: activeThreadIdx, expanded: JSON.stringify(expandedNodes)\n"
      "    }));\n"
      "  }\n"
      "  function clampHud() {\n"
      "    var hud = document.getElementById(HUD_ID);\n"
      "    if (!hud) return;\n"
      "    var vw = window.innerWidth, vh = window.innerHeight;\n"
      "    var w = hud.offsetWidth, h = hud.offsetHeight;\n"
      "    if (posX < 0) posX = 0;\n"
      "    if (posY < 0) posY = 0;\n"
      "    if (posX + w > vw) posX = Math.max(0, vw - w);\n"
      "    if (posY + h > vh) posY = Math.max(0, vh - h);\n"
      "    hud.style.left = posX + 'px';\n"
      "    hud.style.top = posY + 'px';\n"
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
      "    html += '<div style=\"padding:8px 12px; overflow-y:auto; flex:1;\">';\n"
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
      "  hud.style.cssText = 'position:fixed; z-index:10001; display:flex; flex-direction:column;'\n"
      "    + ' background:' + C.bg + '; border:1px solid ' + C.border + '; border-radius:12px;'\n"
      "    + ' box-shadow:0 20px 60px rgba(0,0,0,0.4); font-family:Segoe UI,system-ui,sans-serif;'\n"
      "    + ' color:' + C.text + '; overflow:hidden; resize:both; min-width:500px; min-height:200px;'\n"
      "    + ' width:' + hudW + '; height:' + hudH + '; left:' + posX + 'px; top:' + posY + 'px;';\n"
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
      "\n"
      "  // Draggable\n"
      "  header.onmousedown = function(e) {\n"
      "    if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;\n"
      "    e.preventDefault();\n"
      "    var ox = e.clientX, oy = e.clientY;\n"
      "    function move(me) {\n"
      "      posX += (me.clientX - ox); posY += (me.clientY - oy);\n"
      "      ox = me.clientX; oy = me.clientY;\n"
      "      clampHud();\n"
      "    }\n"
      "    function up() {\n"
      "      document.removeEventListener('mousemove', move);\n"
      "      document.removeEventListener('mouseup', up);\n"
      "      saveState();\n"
      "    }\n"
      "    document.addEventListener('mousemove', move);\n"
      "    document.addEventListener('mouseup', up);\n"
      "  };\n"
      "\n"
      "  window.addEventListener('resize', clampHud);\n"
      "\n"
      "  // Refresh\n"
      "  document.getElementById(HUD_ID + '-refresh').onclick = function() {\n"
      "    if (window.echoAgentResolve) {\n"
      "      window.echoAgentResolve({action: 'refresh'});\n"
      "    } else {\n"
      "      var btn = document.getElementById(HUD_ID + '-refresh');\n"
      "      if (btn) { btn.textContent = '\\u23f3'; setTimeout(function() { if (btn) btn.textContent = '\\ud83d\\udd04'; }, 1000); }\n"
      "    }\n"
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
      "        if (window.echoAgentResolve) window.echoAgentResolve({action: 'refresh'});\n"
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
      "    isMinimized = !isMinimized;\n"
      "    body.style.display = isMinimized ? 'none' : 'flex';\n"
      "    statusBar.style.display = isMinimized ? 'none' : 'flex';\n"
      "    hud.style.minHeight = isMinimized ? '42px' : '200px';\n"
      "    hud.style.height = isMinimized ? '42px' : hudH;\n"
      "    hud.style.resize = isMinimized ? 'none' : 'both';\n"
      "    saveState();\n"
      "  };\n"
      "\n"
      "  // Close — resolve pour sortir de la boucle Python\n"
      "  document.getElementById(HUD_ID + '-close').onclick = function() {\n"
      "    if (autoRefreshTimer) clearInterval(autoRefreshTimer);\n"
      "    hud.remove();\n"
      "    if (window.echoAgentResolve) window.echoAgentResolve({action: 'close'});\n"
      "  };\n"
      "\n"
      "  // Resize persistence\n"
      "  new ResizeObserver(function() {\n"
      "    hudW = hud.style.width;\n"
      "    hudH = hud.style.height;\n"
      "    saveState();\n"
      "  }).observe(hud);\n"
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
      "  };\n"
      "\n"
      "  // =============== INIT ===============\n"
      "  clampHud();\n"
      "  renderSidebar();\n"
      "  renderTree();\n"
      "  updateAutoState();\n"
      "})();\n"
    )

