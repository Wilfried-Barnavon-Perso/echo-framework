"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 5.27
description: 5.16: UI Moderne - Icône globe, minimisation HUD corrigée (min-height fix) et Équilibre Souverain Pro. 5.17: Ajout show_image_js (injection JS sans HTMLResponse).
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
  def _generate_webplayer_js(b64: str, mime: str, metadata: list, current_url: str, hud_id: str, state_key: str, direct_url: str = None, icon: str = "👁️") -> str:
    """Génère le moteur de pilotage ECHO WEBPLAYER (v5.20 Équilibre Souverain Pro)."""
    meta_j = std_json.dumps(metadata).decode('utf-8')
    b64_j = std_json.dumps(b64).decode('utf-8')
    url_j = std_json.dumps(current_url).decode('utf-8')
    mime_j = std_json.dumps(mime).decode('utf-8')
    direct_url_j = std_json.dumps(direct_url or "").decode('utf-8')

    return f"""
  (function() {{
    const HUD_ID = '{hud_id}';
    const STATE_KEY = '{state_key}';
    const ENGINE_KEY = 'echoWebPlayer_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_');

    const payload = {{
      b64: {b64_j}, mime: {mime_j}, metadata: {meta_j},
      url: {url_j},
      directUrl: {direct_url_j}
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
          
          if (fromResize) {{
            this.imgScale = this.hud.offsetWidth / img.naturalWidth;
          }}

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

          new ResizeObserver(entries => {{
            if (this.isDragging) return;
            for (let entry of entries) {{
                if (entry.contentRect.width > 0) this.syncLayout(true);
            }}
          }}).observe(this.hud);
        }},

        create: function(data) {{
          const old = document.getElementById(HUD_ID); if(old) old.remove();
          this.hud = document.createElement('div');
          this.hud.id = HUD_ID;
          this.hud.style.cssText = 'position:fixed; z-index:10000; background:rgba(12,12,12,0.98); backdrop-filter:blur(25px); border:1px solid #333; border-radius:12px; box-shadow:0 25px 70px rgba(0,0,0,0.9); color:white; font-family:sans-serif; display:flex; flex-direction:column; overflow:hidden; resize:both; min-width:200px; min-height:100px;';
          
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
          img.src = data.directUrl || ("data:" + data.mime + ";base64," + data.b64);
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
    await events.call("execute", {"code": js_code})

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
        `;
        document.head.appendChild(style);
      }}
      hudWrapper = document.createElement('div');
      hudWrapper.id = 'echo-nav-context-hud-wrapper';
      hudWrapper.style.cssText = 'position:absolute;left:50%;top:22px;transform:translateX(-50%);width:auto;min-width:300px;display:flex;justify-content:center;align-items:center;z-index:60;pointer-events:none;';
      var hud = document.createElement('div');
      hud.id = 'echo-nav-context-hud';
      hud.style.cssText = 'display:flex;align-items:center;justify-content:center;gap:12px;pointer-events:auto;background:rgba(0,0,0,0.2);padding:4px 12px;border-radius:20px;backdrop-filter:blur(4px);';
      var iconHtml = `<div class="echo-tooltip"><svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" /><circle cx="10" cy="10" r="8" fill="none" stroke="{q_color}" stroke-width="2" stroke-dasharray="{dash_array}" stroke-dashoffset="{dash_offset}" transform="rotate(-90 10 10)" stroke-linecap="round" /><path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" /></svg><div class="tooltip-box" style="width:300px;"><div class="tooltip-title">AUTHENTIFICATION</div><div class="tooltip-row"><span>🔐 Source:</span> <span>{auth_list}</span></div><div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div><div class="tooltip-row"><span>🏗️ Projet:</span> <span>{project_id or 'N/A'}</span></div><div class="tooltip-title" style="margin-top:8px;border-top:1px solid rgba(0,212,255,0.2);padding-top:6px;">QUOTAS</div><div class="tooltip-row"><span>💳 Crédits:</span> <b style="color:#10b981;">{credits_val}</b></div><div class="tooltip-row"><span>🤖 Modèle CA:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_model or "—"}</span></div><div class="tooltip-row"><span>📊 Quota:</span> <b style="color:{q_color};">{quota_fraction*100:.1f}%</b></div><div class="tooltip-row"><span>📅 Req/jour:</span> <span>{"N/A" if quota_rpd_rem == "N/A" else f"{quota_rpd_rem} / {quota_rpd_lim}"}</span></div><div class="tooltip-row"><span>⚡ Req/min:</span> <span>{"N/A" if quota_rpm_rem == "N/A" else f"{quota_rpm_rem} / {quota_rpm_lim}"}</span></div><div class="tooltip-row"><span>🔄 Reset:</span> <span>{"—" if quota_reset == "N/A" else quota_reset}</span></div><div class="tooltip-row"><span>🏷️ Type:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_type}</span></div></div></div>`;
      var barHtml = `<div class="echo-tooltip" style="min-width:180px;"><div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;"><div style="width:{cache_pct}%;background:#8b5cf6;"></div><div style="width:{prompt_pct}%;background:#10b981;"></div><div style="width:{gen_pct}%;background:#f59e0b;"></div></div><div class="tooltip-box" style="width:240px;"><div class="tooltip-title">CONTEXTE</div><div class="tooltip-row"><span>Cache:</span> <span>{c_t}</span></div><div class="tooltip-row"><span>Prompt:</span> <span>{active_p_t}</span></div><div class="tooltip-row"><span>Génération:</span> <span>{g_t}</span></div><div class="tooltip-row" style="font-weight:bold;margin-top:4px;"><span>Total:</span> <span>{total_t} / {max_t}</span></div></div></div>`;
      hud.innerHTML = iconHtml + barHtml;
      hudWrapper.appendChild(hud);
      var nav = container.closest('nav');
      if (nav) nav.appendChild(hudWrapper);
    }})();
    """
    await events.emit("execute", {"code": js_code})

  @staticmethod
  def show_image_js(img_url: str, title: str = "Aperçu Image") -> str:
    """Réutilise le moteur WebPlayer (HUD navigateur) pour afficher une image.
    Utiliser via events.call('execute', {'code': ...}).
    N'utilise pas HTMLResponse — retour 100% propre, sans pollution du contexte Gemini."""
    return EchoUI._generate_webplayer_js(
        b64="", mime="", metadata=[],
        current_url=title,
        hud_id="echo-img-viewer",
        state_key="echo_img_viewer_state",
        direct_url=img_url
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
      const CID = '{chat_id}';
      const STATE_KEY = 'echo_codex_' + CID;
      const MONACO_CDN = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';

      let existingHud = document.getElementById(CODEX_ID);
      if (existingHud) {{ existingHud.remove(); }}

      // --- State ---
      let files = {files_json};
      const quickActions = {quick_actions_json};
      let currentFile = files.length > 0 ? files[0].filename : null;
      let editor = null;
      let diffEditor = null;
      let isDiffMode = false;
      let isHistoryMode = false;
      let historyContent = null;
      let lastInstruction = '';
      let lastModel = 'MODEL_FLASH';
      let modified = false;

      // --- Restore position ---
      let savedState = {{}};
      try {{ savedState = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}'); }} catch(e) {{}}

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
      editorWrap.style.cssText = 'flex:1; overflow:hidden; position:relative;';

      body.appendChild(sidebar);
      body.appendChild(editorWrap);
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
          }}));
        }} catch(e) {{}}
      }}

      // Ctrl+S
      editorWrap.onkeydown = (e) => {{
        if ((e.ctrlKey || e.metaKey) && e.key === 's') {{
          e.preventDefault();
          if (currentFile && editor) {{
            window.echoCodexResolve({{action:'save', filename:currentFile, content:editor.getValue(),
              language:files.find(f=>f.filename===currentFile)?.lang||'plaintext'}});
            modified = false;
            renderFileTree();
          }}
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
          editor.onDidChangeModelContent(() => {{ modified = true; renderFileTree(); }});
          modified = false;
          // Mettre à jour le sélecteur de langage
          const langSelect = document.getElementById(CODEX_ID + '-lang');
          if (langSelect) langSelect.value = lang;
          updateStatus(filename + ' \u2022 charg\u00e9');
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
        editor.onDidChangeModelContent(() => {{ modified = true; renderFileTree(); }});

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
          if (editor) monaco.editor.setModelLanguage(editor.getModel(), langSelect.value);
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

