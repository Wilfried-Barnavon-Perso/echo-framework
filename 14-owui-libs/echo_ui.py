"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 3.0
description: 3.0: Refonte complète du HUD (Centrage Navbar, Tooltips Identité & Tokens).
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
      <script>
        // --- POLYFILL STORAGE ECHO (Fix Sandbox OWUI) ---
        (function() {{
          function createMockStorage() {{
            let storage = {{}};
            return {{
              getItem: (key) => key in storage ? storage[key] : null,
              setItem: (key, value) => storage[key] = value || '',
              removeItem: (key) => delete storage[key],
              clear: () => storage = {{}},
              key: (i) => Object.keys(storage)[i] || null,
              get length() {{ return Object.keys(storage).length; }}
            }};
          }}
          try {{
            const test = window.localStorage;
          }} catch (e) {{
            console.warn("ECHO: LocalStorage inaccessible (Sandbox). Activation du Polyfill Mémoire.");
            Object.defineProperty(window, 'localStorage', {{ value: createMockStorage() }});
            Object.defineProperty(window, 'sessionStorage', {{ value: createMockStorage() }});
          }}
        }})();
      </script>
    </head>
    <body>
      {content}
      <script>
        // --- SUTURE VISUELLE ECHO (OWUI COMPAT) ---
        let lastHeight = 0;
        let resizeTimeout;

        function reportHeight() {{
          const h = document.documentElement.scrollHeight || document.body.scrollHeight;
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
  def generate_rich_view(cls, moteur: str, payload: str, title: str = "ECHO Rendu Visuel", cdn_timeout_ms: int = 30000) -> HTMLResponse:
    """Usine de rendu universelle (Rendu Visuel)."""
    cfg = VisualEngine.get_config(moteur, payload, cdn_timeout_ms=cdn_timeout_ms)
    
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
  async def deploy_context_gauge(
      events: Any, 
      plan_name: str, 
      credits_val: str, 
      quota_str: str, 
      c_t: int, 
      active_p_t: int, 
      g_t: int, 
      max_t: int, 
      cache_pct: float, 
      prompt_pct: float, 
      gen_pct: float,
      user_email: Optional[str] = None,
      user_tier: Optional[str] = None,
      project_id: Optional[str] = None,
      auth_sources: Optional[list] = None
  ):
    """Déploie le HUD ECHO centré avec tooltips avancés (Identité API & Consommation)."""
    
    auth_list = ", ".join(auth_sources) if auth_sources else "N/A"
    total_t = c_t + active_p_t + g_t
    
    js_code = f"""
    (function() {{
      var navbar = document.querySelector('nav div.flex.items-center.w-full.pl-1.5.pr-1');
      if (!navbar) return;
      
      var hud = document.getElementById('echo-nav-context-hud');
      if (hud) hud.remove();
      
      // --- STYLES TOOLTIP ECHO ---
      var styleId = 'echo-hud-styles';
      if (!document.getElementById(styleId)) {{
        var style = document.createElement('style');
        style.id = styleId;
        style.innerHTML = `
          .echo-tooltip {{
            position: relative;
            display: flex;
            align-items: center;
          }}
          .echo-tooltip .tooltip-box {{
            visibility: hidden;
            width: 240px;
            background: rgba(15, 23, 42, 0.95);
            backdrop-filter: blur(8px);
            color: #f8fafc;
            text-align: left;
            border-radius: 8px;
            padding: 12px;
            position: absolute;
            z-index: 100;
            top: 150%;
            left: 50%;
            transform: translateX(-50%);
            opacity: 0;
            transition: opacity 0.3s, transform 0.3s;
            border: 1px solid rgba(0, 212, 255, 0.3);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.5), 0 0 15px rgba(0, 212, 255, 0.1);
            font-size: 11px;
            line-height: 1.5;
            pointer-events: none;
          }}
          .echo-tooltip:hover .tooltip-box {{
            visibility: visible;
            opacity: 1;
            transform: translateX(-50%) translateY(-5px);
          }}
          .tooltip-title {{
            color: #00d4ff;
            font-weight: bold;
            margin-bottom: 8px;
            border-bottom: 1px solid rgba(0, 212, 255, 0.2);
            padding-bottom: 4px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
          }}
          .tooltip-row {{ display: flex; justify-content: space-between; margin-bottom: 4px; }}
          .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; margin-right: 6px; }}
        `;
        document.head.appendChild(style);
      }}

      hud = document.createElement('div');
      hud.id = 'echo-nav-context-hud';
      // Centrage Absolu entre Sélecteur et Menus
      hud.style.cssText = 'position:absolute;left:50%;transform:translateX(-50%);display:flex;align-items:center;justify-content:center;width:auto;max-width:30%;z-index:40;gap:12px;';
      
      var iconHtml = `
        <div class="echo-tooltip">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20" fill="currentColor" style="width:16px;height:16px;color:#00d4ff;cursor:help;opacity:0.8;">
            <path fill-rule="evenodd" d="M10 1a4.5 4.5 0 00-4.5 4.5V9H5a2 2 0 00-2 2v6a2 2 0 002 2h10a2 2 0 002-2v-6a2 2 0 00-2-2h-.5V5.5A4.5 4.5 0 0010 1zm3 8V5.5a3 3 0 10-6 0V9h6z" clip-rule="evenodd" />
          </svg>
          <div class="tooltip-box">
            <div class="tooltip-title">INFO GEMINI CODE ASSIST</div>
            <div class="tooltip-row"><span>🔐 Sources:</span> <span>{auth_list}</span></div>
            <div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div>
            <div class="tooltip-row"><span>🏗️ Projet:</span> <span>{project_id or 'N/A'}</span></div>
            <div class="tooltip-row"><span>🏆 Tier:</span> <span style="color:#10b981;font-weight:bold;">{user_tier or 'N/A'}</span></div>
          </div>
        </div>
      `;
      
      var barHtml = `
        <div class="echo-tooltip" style="flex-grow:1;min-width:150px;">
          <div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;border:1px solid rgba(255,255,255,0.1);">
            <div style="width:{cache_pct}%;background:linear-gradient(90deg, #7c3aed, #8b5cf6);" title="Cache"></div>
            <div style="width:{prompt_pct}%;background:linear-gradient(90deg, #059669, #10b981);" title="Prompt"></div>
            <div style="width:{gen_pct}%;background:linear-gradient(90deg, #d97706, #f59e0b);" title="Gen"></div>
          </div>
          <div class="tooltip-box" style="width:200px;">
            <div class="tooltip-title">CONSOMMATION CONTEXTUELLE</div>
            <div class="tooltip-row"><span><span class="dot" style="background:#8b5cf6;"></span>Cache:</span> <span>{c_t}</span></div>
            <div class="tooltip-row"><span><span class="dot" style="background:#10b981;"></span>Prompt:</span> <span>{active_p_t}</span></div>
            <div class="tooltip-row"><span><span class="dot" style="background:#f59e0b;"></span>Génération:</span> <span>{g_t}</span></div>
            <div class="tooltip-row" style="margin-top:6px;border-top:1px solid rgba(255,255,255,0.1);padding-top:4px;font-weight:bold;">
              <span>Total:</span> <span>{total_t} / {max_t}</span>
            </div>
          </div>
        </div>
      `;
      
      hud.innerHTML = iconHtml + barHtml;
      navbar.appendChild(hud);
    }})();
    """
    await events.emit("execute", {"code": js_code})
