"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 1.7
description: 1.7: Stabilisation accrue de la Suture Visuelle (Debounce 250ms).
"""

from fastapi.responses import HTMLResponse
import sys
import os
from typing import Optional, Any

# Importations ECHO
sys.path.append("/app/backend/echo_libs")
from echo_visuals import VisualEngine

class EchoRichUI:
  @staticmethod
  def _get_boilerplate(content: str, title: str = "ECHO Visualizer") -> str:
    """Encapsule le contenu avec Suture d'Auto-dimensionnement universelle."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>{title}</title>
      <style>
        body {{
          background: #1a1a1b;
          color: #e2e8f0;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
          margin: 0;
          padding: 0;
          overflow: hidden;
        }}
        ::-webkit-scrollbar {{ width: 6px; height: 6px; }}
        ::-webkit-scrollbar-track {{ background: #1a1a1b; }}
        ::-webkit-scrollbar-thumb {{ background: #334155; border-radius: 10px; }}
        ::-webkit-scrollbar-thumb:hover {{ background: #475569; }}
      </style>
    </head>
    <body>
      {content}
      <script>
        // --- SUTURE VISUELLE ECHO (OWUI COMPAT) ---
        let lastHeight = 0;
        let resizeTimeout;

        function reportHeight() {{
          const h = document.documentElement.scrollHeight || document.body.scrollHeight;
          // On évite les boucles infinies en vérifiant si la hauteur a réellement changé (> 2px)
          if (Math.abs(h - lastHeight) > 2) {{
            lastHeight = h;
            parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
          }}
        }}

        window.addEventListener('load', () => {{
          setTimeout(reportHeight, 100);
        }});

        const observer = new ResizeObserver(entries => {{
          clearTimeout(resizeTimeout);
          // Délai augmenté à 250ms pour plus de stabilité
          resizeTimeout = setTimeout(reportHeight, 250);
        }});
        observer.observe(document.body);
      </script>
    </body>
    </html>
    """

  @classmethod
  def player_ui(cls, session_id: str, total_steps: int) -> HTMLResponse:
    """Rendu du WebPlayer pour le Replay de navigation."""
    content = f"""
    <div id="player-container" style="width:100%; height:600px; position:relative; background:#000;">
      <div id="hud-bar" style="position:absolute; top:10px; left:10px; z-index:100; display:flex; gap:10px;">
        <button onclick="prev()" style="background:#334155; color:white; border:none; padding:5px 15px; border-radius:4px; cursor:pointer;">◀️</button>
        <span id="step-info" style="color:white; align-self:center; font-family:monospace; font-size:12px;">Step 1 / {total_steps}</span>
        <button onclick="next()" style="background:#334155; color:white; border:none; padding:5px 15px; border-radius:4px; cursor:pointer;">▶️</button>
        <button id="sel-btn" onclick="toggleSel()" style="background:#065f46; color:white; border:none; padding:5px 10px; border-radius:4px; cursor:pointer;">Mode Sélection (S)</button>
      </div>
      <div id="img-wrapper" style="width:100%; height:100%; overflow:hidden; cursor:grab; display:flex; align-items:center; justify-content:center;">
        <img id="screenshot" src="/api/v1/files/U_{session_id}_T_1.png" style="max-width:none; transition: transform 0.1s ease-out; transform-origin: center;">
      </div>
      <div id="crop-zone" style="position:absolute; border:2px dashed #10b981; background:rgba(16,185,129,0.1); pointer-events:none; display:none; z-index:50;"></div>
      <div id="coords" style="position:absolute; bottom:10px; right:10px; background:rgba(0,0,0,0.7); color:#10b981; padding:2px 8px; border-radius:4px; font-family:monospace; font-size:11px; display:none; z-index:110;"></div>
    </div>
    <script>
      let current = 1;
      const total = {total_steps};
      const img = document.getElementById('screenshot');
      const container = document.getElementById('img-wrapper');
      const crop = document.getElementById('crop-zone');
      const coords = document.getElementById('coords');
      
      let scale = 1;
      let isPanning = false;
      let isDragging = false;
      let selOn = false;
      let startX, startY, scrollLeft, scrollTop;

      function update() {{
        img.src = `/api/v1/files/U_{session_id}_T_${{current}}.png`;
        document.getElementById('step-info').textContent = `Step ${{current}} / ${{total}}`;
      }}
      function next() {{ if(current < total) {{ current++; update(); }} }}
      function prev() {{ if(current > 1) {{ current--; update(); }} }}
      
      function toggleSel() {{
        selOn = !selOn;
        document.getElementById('sel-btn').style.background = selOn ? '#059669' : '#064e3b';
        container.style.cursor = selOn ? 'crosshair' : 'grab';
        if(!selOn) {{ crop.style.display = 'none'; coords.style.display = 'none'; }}
      }}

      function resetAll() {{ scale = 1; img.style.transform = `scale(1)`; container.scrollLeft = 0; container.scrollTop = 0; }}

      window.addEventListener('wheel', (e) => {{
        if (e.ctrlKey) {{
          e.preventDefault();
          const delta = e.deltaY > 0 ? 0.9 : 1.1;
          scale = Math.min(Math.max(0.1, scale * delta), 10);
          img.style.transform = `scale(${{scale}})`;
        }}
      }}, {{ passive: false }});

      container.onmousedown = (e) => {{
        if (e.target.closest('#hud-bar')) return;
        if (selOn) {{
          isDragging = true;
          const rect = container.getBoundingClientRect();
          startX = e.clientX - rect.left + container.scrollLeft;
          startY = e.clientY - rect.top + container.scrollTop;
          crop.style.display = 'block';
          crop.style.width = '0';
          crop.style.height = '0';
          crop.style.left = startX + 'px';
          crop.style.top = startY + 'px';
        }} else {{
          isPanning = true;
          container.style.cursor = 'grabbing';
          startX = e.pageX - container.offsetLeft;
          startY = e.pageY - container.offsetTop;
          scrollLeft = container.scrollLeft;
          scrollTop = container.scrollTop;
        }}
      }};

      window.onmousemove = (e) => {{
        if (isDragging && selOn) {{
          const rect = container.getBoundingClientRect();
          const currentX = e.clientX - rect.left + container.scrollLeft;
          const currentY = e.clientY - rect.top + container.scrollTop;
          
          const left = Math.min(startX, currentX);
          const top = Math.min(startY, currentY);
          const width = Math.abs(currentX - startX);
          const height = Math.abs(currentY - startY);
          
          crop.style.left = left + 'px';
          crop.style.top = top + 'px';
          crop.style.width = width + 'px';
          crop.style.height = height + 'px';
          
          const iRect = img.getBoundingClientRect();
          const rx = img.naturalWidth / iRect.width;
          const ry = img.naturalHeight / iRect.height;
          coords.style.display = 'block';
          coords.textContent = `${Math.round(width*rx)}x${Math.round(height*ry)}px`;
        }} else if (isPanning) {{
          e.preventDefault();
          const x = e.pageX - container.offsetLeft;
          const y = e.pageY - container.offsetTop;
          const walkX = (x - startX);
          const walkY = (y - startY);
          container.scrollLeft = scrollLeft - walkX;
          container.scrollTop = scrollTop - walkY;
        }}
      }};

      window.onmouseup = () => {{
        isDragging = false;
        isPanning = false;
        if (!selOn) container.style.cursor = 'grab';
      }};
    </script>
    """
    html = cls._get_boilerplate(content, title)
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

  @classmethod
  def map_viewer(cls, query: str, title: str = "Localisation") -> HTMLResponse:
    """Génère un HTMLResponse pour l'embed Google Maps en mode Cinéma (85vh)."""
    from urllib.parse import quote
    safe_query = quote(query.strip())
    content = f"""
    <div style="width: 100%; height: 85vh; min-height: 600px; background: #1a1a1b; border-radius: 8px; overflow: hidden; border: 1px solid #334155;">
      <iframe width="100%" height="100%" frameborder="0" style="border:0; display: block;" 
        src="https://www.google.com/maps?q={safe_query}&output=embed" allowfullscreen loading="lazy">
      </iframe>
    </div>
    """
    html = cls._get_boilerplate(content, title)
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

  @classmethod
  def generate_rich_view(cls, moteur: str, payload: str, title: str = "ECHO Rendu Visuel") -> HTMLResponse:
    """Usine de rendu universelle (Rendu Visuel). Le redimensionnement est géré par le boilerplate."""
    cfg = VisualEngine.get_config(moteur, payload)
    
    # Gestion des styles CSS et JS
    styles_list = [s for s in cfg["scripts"] if s.endswith('.css')]
    scripts_list = [s for s in cfg["scripts"] if not s.endswith('.css')]
    
    styles_html = "\n".join([f'<link rel="stylesheet" href="{s}">' for s in styles_list])
    scripts_html = "\n".join([f'<script src="{s}"></script>' for s in scripts_list])

    content = f"""
    <style>
      #gbav-target {{ display: block; width: 100%; height: 100vh; }}
    </style>
    {styles_html}
    {cfg["container"]}
    {scripts_html}
    
    <script>
      try {{
        {cfg["init"]}
      }} catch (e) {{
        document.body.innerHTML = `<div style="color:#ef4444; padding:20px;">
          <h3>⚠️ Erreur de Rendu Rendu Visuel ({moteur})</h3>
          <pre>${{e.message}}</pre>
        </div>`;
      }}
    </script>
    """
    html = cls._get_boilerplate(content, title)
    return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

class EchoUI(EchoRichUI):
  """Moteur de pilotage HUD pour ECHO."""
  
  @staticmethod
  def _generate_webplayer_js(b64: str, mime: str, metadata: list, current_url: str, hud_id: str, state_key: str) -> str:
    """Génère le moteur de pilotage ECHO WEBPLAYER."""
    import orjson as std_json
    meta_json = std_json.dumps(metadata).decode('utf-8')
    
    return f"""
  (function() {{
    const HUD_ID = '{hud_id}';
    const STATE_KEY = '{state_key}';
    const ENGINE_KEY = 'echoWebPlayer_' + HUD_ID.replace(/[^a-zA-Z0-9]/g, '_');
    
    const payload = {{ 
      b64: "{b64}", mime: "{mime}", metadata: {meta_json}, 
      url: "{current_url}"
    }};

    if (!window[ENGINE_KEY]) {{
      window[ENGINE_KEY] = {{
        hud: null, ratio: 1.0, posX: 0, posY: 0, 
        imgScale: 1.0, imgX: 0, imgY: 0,
        isDragging: false, startMouseX: 0, startMouseY: 0,

        getBestSize: function(ratio, percent = 0.5) {{
          const vw = window.innerWidth, vh = window.innerHeight;
          let w = Math.sqrt(percent * vw * vh / ratio);
          let h = w * ratio;
          if (w > vw * 0.95) {{ w = vw * 0.95; h = w * ratio; }}
          if (h > (vh * 0.95 - 40)) {{ h = vh * 0.95 - 40; w = h / ratio; }}
          return {{ w, h }};
        }},

        clampHud: function() {{
          if (!this.hud) return;
          const vw = window.innerWidth, vh = window.innerHeight;
          const rect = this.hud.getBoundingClientRect();
          const marginW = 15, marginH = 15;
          if (this.posX < marginW) this.posX = marginW;
          if (this.posY < marginH) this.posY = marginH;
          if (this.posX + rect.width > vw - marginW) this.posX = vw - marginW - rect.width;
          if (this.posY + rect.height > vh - marginH) this.posY = vh - marginH - rect.height;
          this.hud.style.transform = "translate3d(" + this.posX + "px, " + this.posY + "px, 0px)";
        }},

        saveState: function() {{
          if (!this.hud) return;
          const area = document.getElementById(HUD_ID + "-area");
          const isM = area && area.style.display === 'none';
          localStorage.setItem(STATE_KEY, JSON.stringify({{
            w: this.hud.offsetWidth, x: this.posX, y: this.posY, m: isM
          }}));
        }},

        attachEvents: function() {{
          if (!this.hud) return;
          const matrix = document.getElementById(HUD_ID + "-matrix");
          const area = document.getElementById(HUD_ID + "-area");
          const header = document.getElementById(HUD_ID + "-header");

          area.addEventListener('wheel', (e) => {{
            if (e.ctrlKey) {{
              e.preventDefault();
              const delta = e.deltaY > 0 ? 0.9 : 1.1;
              this.imgScale = Math.min(Math.max(0.1, this.imgScale * delta), 15);
              matrix.style.transform = "scale(" + this.imgScale + ") translate3d(" + this.imgX + "px, " + this.imgY + "px, 0px)";
            }}
          }}, {{ passive: false }});

          area.onmousedown = (e) => {{
            if (e.button === 1 || (e.button === 0 && e.altKey)) {{
              e.preventDefault();
              this.isDragging = true;
              this.startMouseX = e.clientX; this.startMouseY = e.clientY;
              area.style.cursor = 'grabbing';
            }}
          }};

          window.addEventListener('mousemove', (e) => {{
            if (this.isDragging) {{
              this.imgX += (e.clientX - this.startMouseX) / this.imgScale;
              this.imgY += (e.clientY - this.startMouseY) / this.imgScale;
              this.startMouseX = e.clientX; this.startMouseY = e.clientY;
              matrix.style.transform = "scale(" + this.imgScale + ") translate3d(" + this.imgX + "px, " + this.imgY + "px, 0px)";
            }}
          }});

          window.addEventListener('mouseup', () => {{
            this.isDragging = false;
            if (area) area.style.cursor = 'crosshair';
          }});

          header.onmousedown = (e) => {{
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'BUTTON') return;
            e.preventDefault();
            let ox = e.clientX, oy = e.clientY;
            document.onmousemove = (me) => {{
              this.posX += (me.clientX - ox); this.posY += (me.clientY - oy);
              ox = me.clientX; oy = me.clientY;
              this.clampHud();
            }};
            document.onmouseup = () => {{ document.onmousemove = null; this.saveState(); }};
          }};

          document.getElementById(HUD_ID + "-btn-help").onclick = () => {{
            const help = document.getElementById(HUD_ID + "-help-overlay");
            help.style.display = help.style.display === 'none' ? 'flex' : 'none';
          }};

          document.getElementById(HUD_ID + "-btn-zoom").onclick = () => {{
            const area = document.getElementById(HUD_ID + "-area");
            const img = document.getElementById(HUD_ID + "-img");
            if (area && img && img.naturalWidth) {{
              const areaRect = area.getBoundingClientRect();
              const scaleW = areaRect.width / img.naturalWidth;
              const scaleH = areaRect.height / img.naturalHeight;
              this.imgScale = Math.min(scaleW, scaleH);
              this.imgX = 0; this.imgY = 0;
              matrix.style.transform = "scale(" + this.imgScale + ") translate3d(0px, 0px, 0px)";
            }}
          }};

          document.getElementById(HUD_ID + "-btn-reset").onclick = () => {{
            const size = this.getBestSize(this.ratio, 0.5);
            this.hud.style.width = size.w + "px";
            this.hud.style.height = (size.h + 40) + "px";
            this.posX = (window.innerWidth - size.w) / 2;
            this.posY = (window.innerHeight - (size.h + 40)) / 2;
            this.clampHud();
            this.saveState();
          }};

          document.getElementById(HUD_ID + "-btn-min").onclick = (e) => {{
            e.stopPropagation();
            const a = document.getElementById(HUD_ID + "-area");
            const isHiding = a.style.display !== 'none';
            a.style.display = isHiding ? 'none' : 'block';
            this.hud.style.height = isHiding ? 'auto' : (this.hud.offsetWidth * this.ratio + 40) + 'px';
            this.saveState();
          }};

          document.getElementById(HUD_ID + "-btn-close").onclick = () => {{ this.hud.remove(); }};
        }},

        create: function(data) {{
          const old = document.getElementById(HUD_ID); if(old) old.remove();
          this.hud = document.createElement('div');
          this.hud.id = HUD_ID;
          this.hud.style.cssText = 'position:fixed; top:0px; left:0px; z-index:10000; background:rgba(20,20,20,0.98); backdrop-filter:blur(15px); border:1px solid #444; border-radius:10px; box-shadow:0 15px 60px rgba(0,0,0,0.8); color:white; font-family:sans-serif; display:flex; flex-direction:column; min-width:300px; box-sizing: border-box;';
          this.hud.innerHTML = `
            <div id="${HUD_ID}-header" style="padding:8px 12px; background:rgba(0,0,0,0.5); display:flex; align-items:center; gap:8px; border-bottom:1px solid #333; cursor:move; user-select:none;">
              <span style="font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px; background:#10b981; color:black;">🤖 AUTO</span>
              <input id="${HUD_ID}-url" type="text" style="flex:1; background:rgba(255,255,255,0.05); border:1px solid #444; border-radius:4px; color:#aaa; font-size:11px; padding:4px 8px; outline:none;" readonly />
              <div style="display:flex; gap:10px;">
                <button id="${HUD_ID}-btn-help">?</button>
                <button id="${HUD_ID}-btn-zoom">🔍</button>
                <button id="${HUD_ID}-btn-reset">🔄</button>
                <button id="${HUD_ID}-btn-min">_</button>
                <button id="${HUD_ID}-btn-close" style="color:#ff4444;">×</button>
              </div>
            </div>
            <div id="${HUD_ID}-area" style="flex:1; position:relative; background:black; overflow:hidden; cursor:crosshair;">
              <div id="${HUD_ID}-matrix" style="width:100%; height:100%; transform-origin: 0 0; will-change: transform;">
                <img id="${HUD_ID}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" draggable="false" />
                <div id="${HUD_ID}-hitboxes" style="position:absolute; inset:0; pointer-events:none;"></div>
              </div>
              <div id="${HUD_ID}-help-overlay" style="position:absolute; inset:0; background:rgba(0,0,0,0.85); display:none; flex-direction:column; align-items:center; justify-content:center; z-index:100;">
                <button onclick="this.parentElement.style.display='none'">Compris</button>
              </div>
            </div>
          `;
          document.body.appendChild(this.hud);
          this.attachEvents();
        }},

        update: function(data) {{
          if (!this.hud) this.create(data);
          const img = document.getElementById(HUD_ID + "-img");
          document.getElementById(HUD_ID + "-url").value = data.url;
          img.onload = () => {{
            this.ratio = img.naturalHeight / img.naturalWidth;
            const size = this.getBestSize(this.ratio, 0.5);
            this.hud.style.width = size.w + "px";
            this.hud.style.height = (size.h + 40) + "px";
            this.clampHud();
          }};
          img.src = "data:" + data.mime + ";base64," + data.b64;
        }}
      }};
    }}
    window[ENGINE_KEY].update(payload);
  }})();
"""

  @staticmethod
  async def monitor_ECHO(events: Any, b64: str, metadata: list, current_url: str, hud_id: str = "echo-webplayer", title: str = "ECHO WEBPLAYER", state_key: str = "echo_webplayer_state"):
    """Interface unifiée WEBPLAYER."""
    js_code = EchoUI._generate_webplayer_js(b64, "image/png", metadata, current_url, hud_id, state_key)
    await events.call("execute", {"code": js_code})

  @staticmethod
  async def deploy_context_gauge(events: Any, plan_name: str, credits_val: str, quota_str: str, c_t: int, active_p_t: int, g_t: int, max_t: int, cache_pct: float, prompt_pct: float, gen_pct: float):
    """Déploie la jauge de contexte dans le HUD."""
    js_code = f"""
    (function() {{
      var navContainer = document.querySelector('nav div.flex.items-center.w-full.max-w-full');
      if (!navContainer) return;
      var hud = document.getElementById('echo-nav-context-hud');
      if (hud) hud.remove();
      hud = document.createElement('div');
      hud.id = 'echo-nav-context-hud';
      hud.style.cssText = 'display:flex;align-items:center;margin:0 12px;flex-grow:8;width:66%;min-width:350px;';
      hud.innerHTML = '<div style="display:flex;width:100%;height:8px;background:rgba(128,128,128,0.2);border-radius:4px;overflow:hidden;">' +
        '<div style="width:{cache_pct}%;background:#8b5cf6;"></div>' +
        '<div style="width:{prompt_pct}%;background:#10b981;"></div>' +
        '<div style="width:{gen_pct}%;background:#f59e0b;"></div></div>';
      navContainer.appendChild(hud);
    }})();
    """
    await events.emit("execute", {"code": js_code})
