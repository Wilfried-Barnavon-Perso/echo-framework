"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 5.16
description: 5.16: UI Moderne - Icône globe, minimisation HUD corrigée (min-height fix) et Équilibre Souverain Pro.
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
  def _generate_webplayer_js(b64: str, mime: str, metadata: list, current_url: str, hud_id: str, state_key: str) -> str:
    """Génère le moteur de pilotage ECHO WEBPLAYER (v5.15 Équilibre Souverain Pro)."""
    meta_j = std_json.dumps(metadata).decode('utf-8')
    b64_j = std_json.dumps(b64).decode('utf-8')
    url_j = std_json.dumps(current_url).decode('utf-8')
    mime_j = std_json.dumps(mime).decode('utf-8')

    return f"""
  (function() {{
    const HUD_ID = '{hud_id}';
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
              <span style="font-size:14px; padding:3px 8px; border-radius:8px; background:rgba(0,212,255,0.1); color:#00d4ff;">🌐</span>
              <input id="${{HUD_ID}}-url" type="text" style="flex:1; background:rgba(0,0,0,0.4); border:1px solid #333; border-radius:6px; color:#00d4ff; font-size:11px; padding:6px 12px; outline:none; font-family:monospace;" readonly />
              <div style="display:flex; gap:8px;">
                <button id="${{HUD_ID}}-btn-zoom" title="Maximiser (Ajuster)" style="background:none; border:none; color:#777; cursor:pointer; font-size:16px;">⛶</button>
                <button id="${{HUD_ID}}-btn-reset" title="Taille rÃ©elle (1:1)" style="background:none; border:none; color:#777; cursor:pointer; font-size:11px; font-weight:bold;">1:1</button>
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
    js_code = EchoUI._generate_webplayer_js(b64, "image/png", metadata or [], current_url, hud_id, state_key)
    await events.call("execute", {"code": js_code})

  @staticmethod
  async def deploy_context_gauge(
      events: Any, plan_name: str, credits_val: str, quota_str: str,
      c_t: int, active_p_t: int, g_t: int, max_t: int,
      cache_pct: float, prompt_pct: float, gen_pct: float,
      user_email: Optional[str] = None, user_tier: Optional[str] = None,
      project_id: Optional[str] = None, auth_sources: Optional[list] = None,
      quota_amount: str = "N/A", quota_fraction: float = 1.0,
      quota_reset: str = "N/A", quota_type: str = "UNKNOWN"
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
      var iconHtml = `<div class="echo-tooltip"><svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" /><circle cx="10" cy="10" r="8" fill="none" stroke="{q_color}" stroke-width="2" stroke-dasharray="{dash_array}" stroke-dashoffset="{dash_offset}" transform="rotate(-90 10 10)" stroke-linecap="round" /><path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" /></svg><div class="tooltip-box"><div class="tooltip-title">AUTHENTIFICATION</div><div class="tooltip-row"><span>🔐 Source:</span> <span>{auth_list}</span></div><div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div><div class="tooltip-row"><span>🏗️ Projet:</span> <span>{project_id or 'N/A'}</span></div><div class="tooltip-row"><span>📊 Quota:</span> <b>{quota_amount} ({quota_fraction*100:.1f}%)</b></div></div></div>`;
      var barHtml = `<div class="echo-tooltip" style="min-width:180px;"><div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;"><div style="width:{cache_pct}%;background:#8b5cf6;"></div><div style="width:{prompt_pct}%;background:#10b981;"></div><div style="width:{gen_pct}%;background:#f59e0b;"></div></div><div class="tooltip-box" style="width:240px;"><div class="tooltip-title">CONTEXTE</div><div class="tooltip-row"><span>Cache:</span> <span>{c_t}</span></div><div class="tooltip-row"><span>Prompt:</span> <span>{active_p_t}</span></div><div class="tooltip-row"><span>Génération:</span> <span>{g_t}</span></div><div class="tooltip-row" style="font-weight:bold;margin-top:4px;"><span>Total:</span> <span>{total_t} / {max_t}</span></div></div></div>`;
      hud.innerHTML = iconHtml + barHtml;
      hudWrapper.appendChild(hud);
      var nav = container.closest('nav');
      if (nav) nav.appendChild(hudWrapper);
    }})();
    """
    await events.emit("execute", {"code": js_code})

  @classmethod
  def image_viewer(cls, img_url: str, title: str = "Aperçu Image") -> HTMLResponse:
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
