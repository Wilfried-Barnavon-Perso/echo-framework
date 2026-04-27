"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 3.9
description: 3.9: Nettoyage syntaxique et optimisation de l'émission HUD.
"""

from fastapi.responses import HTMLResponse
import sys
import os
import hashlib
from typing import Optional, Any, List, Dict

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")

class EchoRichUI:
  """Usine de rendu de composants visuels riches pour ECHO."""
  
  @staticmethod
  def _get_boilerplate(content: str, title: str = "ECHO Visual") -> str:
    """Encapsulation HTML standard avec style ECHO."""
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="UTF-8">
      <title>{title}</title>
      <style>
        body {{ margin: 0; padding: 0; background: #1a1a1b; color: #e2e8f0; font-family: sans-serif; overflow: hidden; }}
        .echo-container {{ width: 100vw; height: 100vh; display: flex; flex-direction: column; }}
        #hud-bar {{ background: #0f172a; padding: 8px 16px; border-bottom: 1px solid #334155; display: flex; align-items: center; justify-content: space-between; z-index: 100; }}
        .btn {{ background: #334155; border: none; color: white; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 12px; transition: background 0.2s; }}
        .btn:hover {{ background: #475569; }}
      </style>
    </head>
    <body>
      <div class="echo-container">
        {content}
      </div>
    </body>
    </html>
    """

  @classmethod
  def image_viewer(cls, img_url: str, title: str = "Aperçu Image") -> HTMLResponse:
    """Génère un HTMLResponse pour visualiser une image avec outils ECHO (Zoom/Pan/Crop)."""
    content = f"""
    <div id="hud-bar">
      <span style="font-weight:bold; font-size:13px; color:#00d4ff;">👁️ ECHO Vision Explorer</span>
      <div style="display:flex; gap:8px;">
        <button class="btn" onclick="resetView()">Réinitialiser</button>
        <button class="btn" id="crop-btn" onclick="toggleCrop()">Outil Crop (Désactivé)</button>
        <span id="coords" style="font-size:11px; opacity:0.7; align-self:center; display:none;"></span>
      </div>
    </div>
    <div id="canvas-area" style="flex:1; overflow:hidden; position:relative; cursor:grab; display:flex; justify-content:center; align-items:center;">
      <img id="main-image" src="{img_url}" style="max-width:100%; max-height:100%; user-select:none; transition: transform 0.1s ease-out;" draggable="false">
      <div id="crop-box" style="border: 2px dashed #00d4ff; background: rgba(0,212,255,0.1); position: absolute; display: none; pointer-events: none;"></div>
    </div>
    <script>
      const img = document.getElementById('main-image');
      const container = document.getElementById('canvas-area');
      const crop = document.getElementById('crop-box');
      const coords = document.getElementById('coords');
      const cropBtn = document.getElementById('crop-btn');
      
      let scale = 1, startX, startY, scrollLeft, scrollTop, isPanning = false, isDragging = false, selOn = false;

      function toggleCrop() {{
        selOn = !selOn;
        cropBtn.textContent = selOn ? 'Outil Crop (Activé)' : 'Outil Crop (Désactivé)';
        cropBtn.style.background = selOn ? '#0369a1' : '#334155';
        container.style.cursor = selOn ? 'crosshair' : 'grab';
        if (!selOn) crop.style.display = 'none';
      }}

      function resetView() {{
        scale = 1; img.style.transform = `scale(${{scale}})`;
        container.scrollLeft = 0; container.scrollTop = 0;
      }}

      container.onwheel = (e) => {{
        e.preventDefault();
        const delta = e.deltaY > 0 ? 0.9 : 1.1;
        scale *= delta;
        scale = Math.min(Math.max(0.5, scale), 10);
        img.style.transform = `scale(${{scale}})`;
      }};

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
    from echo_visuals import VisualEngine
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
      auth_sources: Optional[list] = None,
      quota_amount: str = "N/A",
      quota_fraction: float = 1.0,
      quota_reset: str = "N/A",
      quota_type: str = "UNKNOWN"
  ):
    """Déploie le HUD ECHO flottant avec tooltips en dessous (Front-display)."""
    
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
                      document.querySelector('nav div.flex.items-center.w-full.pl-1\\\\.5.pr-1');
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
      
      var iconHtml = `
        <div class="echo-tooltip">
          <svg width="20" height="20" viewBox="0 0 20 20" style="cursor:help;">
            <circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" />
            <circle cx="10" cy="10" r="8" fill="none" stroke="{q_color}" stroke-width="2" 
                    stroke-dasharray="{dash_array}" stroke-dashoffset="{dash_offset}" 
                    transform="rotate(-90 10 10)" stroke-linecap="round" style="transition: stroke-dashoffset 1s ease-in-out;" />
            <path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" />
          </svg>
          <div class="tooltip-box">
            <div class="tooltip-title">AUTHENTIFICATION GEMINI</div>
            <div class="tooltip-row"><span>🔐 Sources:</span> <span>{auth_list}</span></div>
            <div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div>
            <div class="tooltip-row"><span>🏗️ Projet:</span> <span>{project_id or 'N/A'}</span></div>
            <div class="tooltip-row"><span>🏆 Tier:</span> <span style="color:#10b981;font-weight:bold;">{user_tier or 'N/A'}</span></div>
            <div style="margin-top:8px; padding-top:8px; border-top:1px dashed rgba(255,255,255,0.2);">
                <div class="tooltip-row"><span>📊 Quota Restant:</span> <b>{quota_amount} ({quota_fraction*100:.1f}%)</b></div>
                <div class="tooltip-row"><span>🕒 Reset:</span> <span>{quota_reset}</span></div>
                <div class="tooltip-row"><span>🏷️ Ressource:</span> <span style="font-size:9px;opacity:0.8;">{quota_type}</span></div>
            </div>
          </div>
        </div>
      `;
      
      var barHtml = `
        <div class="echo-tooltip" style="min-width:180px;">
          <div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;border:1px solid rgba(255,255,255,0.1);">
            <div style="width:{cache_pct}%;background:linear-gradient(90deg, #7c3aed, #8b5cf6);" title="Cache"></div>
            <div style="width:{prompt_pct}%;background:linear-gradient(90deg, #059669, #10b981);" title="Prompt"></div>
            <div style="width:{gen_pct}%;background:linear-gradient(90deg, #d97706, #f59e0b);" title="Gen"></div>
          </div>
          <div class="tooltip-box" style="width:240px;">
            <div class="tooltip-title">CONSOMMATION CONTEXTUELLE</div>
            <div class="tooltip-row"><span><span class="dot" style="background:#8b5cf6;"></span>Cache:</span> <span>{c_t} ({cache_pct:.3f}%)</span></div>
            <div class="tooltip-row"><span><span class="dot" style="background:#10b981;"></span>Prompt:</span> <span>{active_p_t} ({prompt_pct:.3f}%)</span></div>
            <div class="tooltip-row"><span><span class="dot" style="background:#f59e0b;"></span>Génération:</span> <span>{g_t} ({gen_pct:.3f}%)</span></div>
            <div class="tooltip-row" style="margin-top:6px;border-top:1px solid rgba(255,255,255,0.1);padding-top:4px;font-weight:bold;">
              <span>Total:</span> <span>{total_t} / {max_t} ({total_pct:.3f}%)</span>
            </div>
          </div>
        </div>
      `;
      
      hud.innerHTML = iconHtml + barHtml;
      hudWrapper.appendChild(hud);
      var nav = container.closest('nav');
      if (nav) nav.appendChild(hudWrapper);
    }})();
    """
    await events.emit("execute", {"code": js_code})
