"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 5.99
description: Composant système interne : ECHO UI Rendering Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.99: Factorisation complète du Codex et du WebPlayer via la classe unifiée EchoFloatingWindow. Intégration du Clamping et du Mobile Guard natifs.
# 5.98: Support du Trigger Asynchrone JS via _echoCodexTarget pour forcer l'ouverture du Codex sur un fichier spécifique.
# 5.97: Architecture - Factorisation du HUD ECHO Identity Vault via la classe unifiée EchoFloatingWindow. Maintien de l'architecture spécifique pour le Cognitive Monitor et le WebPlayer.
# 5.96: Architecture - Factorisation des fenêtres flottantes via la classe unifiée EchoFloatingWindow. L'ECHO Monitor devient le Sandbox Monitor natif.
# 5.95: Fix Monitor - Extraction de echoCreateFloatingMonitor dans get_floating_monitor_js() (non injecté auparavant), iframe construite via DOM (srcdoc natif), retrait de allow-same-origin.
# 5.93: Implémentation du Pattern Data Island pour le rendu des composants ECHO Sandbox Monitor via iframe sécurisée.
# 5.88: Codex - Remplacement des icônes d'import/export par des SVG (Upload/Download).
# 5.86: Fix - Correction d'une erreur de syntaxe f-string dans le JS injecté du Lazy Loading.
# 5.85: Refonte majeure (Codex) : Implémentation du Lazy Loading avec requêtage asynchrone (load_directory) et purge mémoire dynamique.
# 5.77: Factorisation de l'arbre (treeMap) pour tous les espaces (main/sandbox) avec tri descendant par date (mtime).
# 5.76: Rendu asymétrique de l'arborescence Codex (liste plate pour le main, arbre pour la sandbox).
# 5.75: Support du paramètre timeoutSeconds dans echoCustomConfirm pour annulation automatique avec rétrocompatibilité.
# (EchoUI.get_custom_modals_js) avec implémentation de boutons interactifs
# (pills) pour les options de prompt.


import sys
import orjson as std_json
from typing import Optional, Any, List, Dict
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")

from echo_constants import ECHO_GLOBAL_TENANT_PROJECT_ID


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
        "Crop": "<path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/>"
    }

    @staticmethod
    def get_icon(name: str, size: int = 16, color: str = "currentColor", stroke_width: int = 2, css_class: str = "") -> str:
        """Générateur SVG universel."""
        path = EchoRichUI.ECHO_ICONS.get(name, "")
        cls = f" class='{css_class}'" if css_class else ""
        return f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='{stroke_width}' stroke-linecap='round' stroke-linejoin='round'{cls}>{path}</svg>"

    @staticmethod
    def get_floating_window_class_js() -> str:
        """
        # ==============================================================================
        # TEMPLATE STANDARD : ECHO FLOATING WINDOW (Moteur JS)
        # Génère la classe ES6 `EchoFloatingWindow` utilisée par tous les HUDs flottants
        # pour gérer nativement le rendu Glassmorphism, le Drag, Resize et les contrôles.
        # ==============================================================================
        """
        return """
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
                    this.oldState = {};
                }
                
                render() {
                    let hud = document.getElementById(this.id);
                    if (hud) hud.remove();
                    
                    hud = document.createElement('div');
                    hud.id = this.id;
                    hud.style.cssText = `position:fixed; left:${this.posX}px; top:${this.posY}px; width:${this.width}; height:${this.height}; z-index:10000; background:rgba(12,12,12,0.98); backdrop-filter:blur(25px); border:1px solid #333; border-radius:12px; box-shadow:0 25px 70px rgba(0,0,0,0.9); color:white; font-family:system-ui,sans-serif; display:flex; flex-direction:column; overflow:hidden; min-width:${this.minWidth}; min-height:${this.minHeight};`;
                    
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

                    const styleId = this.id + '-mobile-style';
                    if (!document.getElementById(styleId)) {
                        const styleEl = document.createElement('style');
                        styleEl.id = styleId;
                        styleEl.innerHTML = `
                            @media (max-width: 768px) {
                                #${this.id} { position: fixed !important; top: 0 !important; left: 0 !important; width: 100vw !important; height: 100dvh !important; max-width: 100vw !important; max-height: 100dvh !important; min-width: 0 !important; min-height: 0 !important; border-radius: 0 !important; z-index: 10005 !important; transform: none !important; }
                                #${this.id}-resizer { display: none !important; }
                                #${this.id}-header { cursor: default !important; }
                            }
                        `;
                        document.head.appendChild(styleEl);
                    }
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
                    let isDragging = false, startX, startY, initialLeft, initialTop;
                    
          

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

        create: function(data) {
          const old = document.getElementById(HUD_ID); if(old) old.remove();

          const playerWindow = new window.EchoFloatingWindow({
              id: HUD_ID,
              title: '{icon} Navigateur',
              width: '50vw',
              height: '80vh',
              minWidth: '200px',
              minHeight: '100px',
              onClose: () => { 
                  if (typeof this.saveState === 'function') this.saveState();
              },
              customHeader: `
                <input id="${HUD_ID}-url" type="text" placeholder="URL du navigateur..." style="flex:1; background:rgba(0,0,0,0.4); border:1px solid #333; border-radius:6px; color:#00d4ff; font-size:11px; padding:6px 12px; outline:none; font-family:monospace; margin-right:10px;" readonly />
              `
          });
          playerWindow.render();
          playerWindow.attachBaseEvents();

          this.hud = document.getElementById(HUD_ID);
          const playerContainer = document.getElementById(HUD_ID + '-body');
          playerContainer.innerHTML = `
            <div id="${HUD_ID}-area" style="flex:1; position:relative; background:#000; overflow:hidden; cursor:crosshair;">
              <div id="${HUD_ID}-matrix" style="position:absolute; top:0; left:0; transform-origin: 0 0; will-change: transform;">
                <img id="${HUD_ID}-img" style="display:block; user-select:none; pointer-events:none; width:100%; height:100%; max-width:none !important;" draggable="false" />
                <div id="${HUD_ID}-hitboxes" style="position:absolute; inset:0; pointer-events:none;"></div>
              </div>
            </div>
          `;
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
            quota_fraction: float = 1.0,
            quota_reset: str = "N/A",
            quota_type: str = "UNKNOWN",
            quota_model: str = "",
            quota_rpd_rem: str = "N/A",
            quota_rpd_lim: str = "N/A",
            quota_rpm_rem: str = "N/A",
            quota_rpm_lim: str = "N/A"):
        """Déploie le HUD ECHO flottant avec tooltips en dessous."""
        auth_list = ", ".join(auth_sources) if auth_sources else "N/A"
        total_t = c_t + active_p_t + g_t
        q_color = "#10b981"
        if quota_fraction < 0.2:
            q_color = "#ef4444"
        elif quota_fraction < 0.5:
            q_color = "#f59e0b"
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
      var iconHtml = `<div class="echo-tooltip"><svg width="20" height="20" viewBox="0 0 20 20"><circle cx="10" cy="10" r="8" fill="none" stroke="rgba(255,255,255,0.1)" stroke-width="2" /><circle cx="10" cy="10" r="8" fill="none" stroke="{q_color}" stroke-width="2" stroke-dasharray="{dash_array}" stroke-dashoffset="{dash_offset}" transform="rotate(-90 10 10)" stroke-linecap="round" /><path d="M10 6a2.5 2.5 0 00-2.5 2.5V10h5V8.5A2.5 2.5 0 0010 6zm3.5 4H6.5a1 1 0 00-1 1v4a1 1 0 001 1h7a1 1 0 001 1h7a1 1 0 001-1v-4a1 1 0 00-1-1z" fill="white" opacity="0.9" /></svg><div class="tooltip-box" style="width:300px;"><div class="tooltip-title">AUTHENTIFICATION</div><div class="tooltip-row"><span>🔐 Source:</span> <span>{auth_list}</span></div><div class="tooltip-row"><span>👤 Compte:</span> <span>{user_email or 'N/A'}</span></div><div class="tooltip-row"><span>🏗️ Projet Perso:</span> <span>{project_id or 'N/A'}</span></div><div class="tooltip-row"><span>🌐 Tenant:</span> <span style="color:#10b981;">{ECHO_GLOBAL_TENANT_PROJECT_ID}</span></div><div class="tooltip-title" style="margin-top:8px;border-top:1px solid rgba(0,212,255,0.2);padding-top:6px;">QUOTAS</div><div class="tooltip-row"><span>💳 Crédits:</span> <b style="color:#10b981;">{credits_val}</b></div><div class="tooltip-row"><span>🤖 Modèle CA:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_model or "—"}</span></div><div class="tooltip-row"><span>📊 Quota:</span> <b style="color:{q_color};">{quota_fraction * 100:.1f}%</b></div><div class="tooltip-row"><span>📅 Req/jour:</span> <span>{"N/A" if quota_rpd_rem == "N/A" else f"{quota_rpd_rem} / {quota_rpd_lim}"}</span></div><div class="tooltip-row"><span>⚡ Req/min:</span> <span>{"N/A" if quota_rpm_rem == "N/A" else f"{quota_rpm_rem} / {quota_rpm_lim}"}</span></div><div class="tooltip-row"><span>🔄 Reset:</span> <span>{"—" if quota_reset == "N/A" else quota_reset}</span></div><div class="tooltip-row"><span>🏷️ Type:</span> <span style="color:#a3a3a3;font-size:10px;">{quota_type}</span></div></div></div>`;
      var barHtml = `<div class="echo-tooltip" style="min-width:180px;"><div style="display:flex;width:100%;height:6px;background:rgba(255,255,255,0.05);border-radius:3px;overflow:hidden;"><div style="width:{cache_pct}%;background:#8b5cf6;"></div><div style="width:{prompt_pct}%;background:#10b981;"></div><div style="width:{gen_pct}%;background:#f59e0b;"></div></div><div class="tooltip-box" style="width:240px;"><div class="tooltip-title">CONTEXTE</div><div class="tooltip-row"><span>Cache:</span> <span>{c_t}</span></div><div class="tooltip-row"><span>Prompt:</span> <span>{active_p_t}</span></div><div class="tooltip-row"><span>Génération:</span> <span>{g_t}</span></div><div class="tooltip-row" style="font-weight:bold;margin-top:4px;"><span>Total:</span> <span>{total_t} / {max_t}</span></div></div></div>`;
      hud.innerHTML = iconHtml + barHtml;
      hudWrapper.appendChild(hud);
      document.body.appendChild(hudWrapper);
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
    # ECHO CODEX — HUD Monaco Editor
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
              iframe.srcdoc = htmlContent;
              body.appendChild(iframe);
              if (existingResizer) body.appendChild(existingResizer);
          }
          return true;
      };
      """

    @staticmethod
    def _generate_codex_js(
            files_json: str,
            quick_actions_json: str,
            workspaces_json: str,
            current_workspace: str,
            chat_id: str) -> str:
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
      const workspaces = {workspaces_json};
      let currentWorkspace = '{current_workspace}';
      // Mapping langage Monaco → extension (pour rename)
      const LANG_TO_EXT = {{
        python:'.py', javascript:'.js', typescript:'.ts', c:'.c', cpp:'.cpp',
        java:'.java', go:'.go', rust:'.rs', ruby:'.rb', php:'.php', swift:'.swift',
        kotlin:'.kt', csharp:'.cs', vb:'.vb', shell:'.sh', powershell:'.ps1',
        bat:'.bat', html:'.html', css:'.css', json:'.json', xml:'.xml',
        yaml:'.yaml', toml:'.toml', ini:'.ini', markdown:'.md', plaintext:'.txt',
        sql:'.sql', r:'.r', lua:'.lua', perl:'.pl', dockerfile:'.dockerfile',
      }};
      let targetFileOverride = null;
      if (window._echoCodexTarget) {{
          currentWorkspace = window._echoCodexTarget.workspace || currentWorkspace;
          targetFileOverride = window._echoCodexTarget.file;
          setTimeout(() => {{
              if (window.sendCodexAction) {{
                  window.sendCodexAction({{
                      action: "switch_workspace", 
                      workspace: currentWorkspace, 
                      current_file: targetFileOverride
                  }});
              }}
          }}, 100);
          window._echoCodexTarget = null;
      }}
      let currentFile = targetFileOverride || (files.length > 0 ? files[0].filename : null);
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
      const PREVIEW_LANGS = ['markdown', 'html', 'css', 'xml', 'pdf'];
      let markedLoaded = false;
      let previewDebounceTimer = null;

      // --- COMMUNICATION PROXY (Race Condition Guard) ---
      window.sendCodexAction = function(payload) {{
        if (typeof window.echoCodexResolve === 'function') {{
          const resolveFn = window.echoCodexResolve;
          window.echoCodexResolve = null; // Verrouille immédiatement
          resolveFn(payload);
        }} else {{
          // Retry dans 50ms pour les actions vitales, ignore les pings
          if (payload && payload.action !== 'ping') {{
             setTimeout(() => window.sendCodexAction(payload), 50);
          }}
        }}
      }};

      // --- Restore position ---
      let savedState = {{}};
      try {{ savedState = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}'); }} catch(e) {{}}
      if (savedState.previewOpen !== undefined) previewOpen = savedState.previewOpen;
      if (savedState.editorRatio) editorRatio = savedState.editorRatio;
      if (savedState.previewRatio) previewRatio = savedState.previewRatio;
      let sidebarWidth = savedState.sidebarWidth || 150;

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

      // --- Custom Dialogs ---
      {EchoUI.get_custom_modals_js()}

      // --- HUD Container (Migrated to EchoFloatingWindow) ---
      const codexWindow = new window.EchoFloatingWindow({{
          id: CODEX_ID,
          title: '📝 ECHO Codex',
          width: savedState.w || '900px',
          height: savedState.h || '600px',
          minWidth: '600px',
          minHeight: '400px',
          onClose: () => {{ 
              if (typeof saveState === 'function') saveState();
              window.sendCodexAction({{action:'close'}}); 
          }},
          customHeader: `
            <select id="${{CODEX_ID}}-lang" style="background:transparent; border:1px solid ${{borderColor}}; color:${{textColor}}; padding:2px 6px; border-radius:4px; font-size:12px; margin-right:auto;"></select>
            <button id="${{CODEX_ID}}-import" title="Importer (PC → Codex)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='17 8 12 3 7 8'/><line x1='12' y1='3' x2='12' y2='15'/></svg></button>
            <button id="${{CODEX_ID}}-export" title="Exporter (Codex → PC)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='7 10 12 15 17 10'/><line x1='12' y1='15' x2='12' y2='3'/></svg></button>
            <button id="${{CODEX_ID}}-copy" title="Copier" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='9' y='9' width='13' height='13' rx='2' ry='2'/><path d='M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'/></svg></button>
            <button id="${{CODEX_ID}}-refresh" title="Actualiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='23 4 23 10 17 10'/><polyline points='1 20 1 14 7 14'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/></svg></button>
            <button id="${{CODEX_ID}}-save" title="Sauvegarder (Ctrl+S)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1; opacity:0.3; transition:opacity 0.2s;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z'/><polyline points='17 21 17 13 7 13 7 21'/><polyline points='7 3 7 8 15 8'/></svg></button>
            <button id="${{CODEX_ID}}-preview-toggle" title="Prévisualisation" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px; opacity:0.4; margin-right: 10px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/></svg></button>
          `
      }});
      codexWindow.render();
      codexWindow.attachBaseEvents();
      
      const hud = document.getElementById(CODEX_ID);
      if (savedState.x) hud.style.left = savedState.x;
      if (savedState.y) hud.style.top = savedState.y;

      const codexMainArea = document.createElement('div');
      codexMainArea.id = CODEX_ID + '-main';
      codexMainArea.style.cssText = `flex:1; display:flex; flex-direction:row; overflow:hidden;`;
      
      const statusBar = document.createElement('div');
      statusBar.id = CODEX_ID + '-status';
      statusBar.style.cssText = `height:24px; background:${{panelBg}}; border-top:1px solid ${{borderColor}}; display:flex; align-items:center; padding:0 10px; font-size:11px; color:${{textColor}}; justify-content:space-between;`;
      statusBar.innerHTML = `<span id="${{CODEX_ID}}-status-text">Prêt</span><span><span id="${{CODEX_ID}}-lines">0 lignes</span> • <span id="${{CODEX_ID}}-tokens">~0 tok</span></span>`;

      const aiPanel = document.createElement('div');
      aiPanel.id = CODEX_ID + '-ai-panel';
      aiPanel.style.cssText = `display:none; flex-direction:column; height:200px; border-top:1px solid ${{borderColor}}; background:${{bgColor}};`;
      aiPanel.innerHTML = `
        <div style="padding:4px 10px; background:${{panelBg}}; font-size:11px; color:${{accentColor}}; border-bottom:1px solid ${{borderColor}}; display:flex; align-items:center;">
          <span>🤖 AI Assistant (ECHO Codex)</span>
          <button id="${{CODEX_ID}}-ai-close" style="margin-left:auto; background:none; border:none; color:${{textColor}}; cursor:pointer;">X</button>
        </div>
        <div id="${{CODEX_ID}}-ai-chat" style="flex:1; overflow-y:auto; padding:10px; font-size:13px; color:${{textColor}}; font-family:sans-serif; display:flex; flex-direction:column; gap:8px;"></div>
        <div style="display:flex; padding:5px; border-top:1px solid ${{borderColor}}; background:${{panelBg}};">
          <input id="${{CODEX_ID}}-ai-input" type="text" placeholder="Demander à l'IA d'analyser/modifier ce fichier..." style="flex:1; background:${{bgColor}}; border:1px solid ${{borderColor}}; color:${{textColor}}; padding:6px 10px; border-radius:4px; outline:none; font-size:12px;">
          <button id="${{CODEX_ID}}-ai-send" style="background:${{accentColor}}; border:none; color:${{isDark ? '#000' : '#fff'}}; padding:0 15px; margin-left:5px; border-radius:4px; font-weight:bold; cursor:pointer; font-size:12px;">Envoyer</button>
        </div>
      `;

      const diffBar = document.createElement('div');
      diffBar.id = CODEX_ID + '-diff-bar';
      diffBar.style.cssText = `display:none; height:32px; background:${{isDark ? '#1a2b3c' : '#e0f7fa'}}; border-top:1px solid ${{borderColor}}; align-items:center; padding:0 15px; font-size:12px; color:${{textColor}}; justify-content:space-between;`;
      diffBar.innerHTML = `
        <span>🔍 Modifications en attente (<span id="${{CODEX_ID}}-diff-count">0</span>)</span>
        <div style="display:flex; gap:8px;">
          <button id="${{CODEX_ID}}-diff-accept" style="background:#2ecc71; border:none; color:#000; padding:4px 12px; border-radius:4px; cursor:pointer; font-weight:bold;">Accepter</button>
          <button id="${{CODEX_ID}}-diff-reject" style="background:#e74c3c; border:none; color:#fff; padding:4px 12px; border-radius:4px; cursor:pointer; font-weight:bold;">Rejeter</button>
        </div>
      `;

      const codexContainer = document.getElementById(CODEX_ID + '-body');
      codexContainer.appendChild(statusBar);
      codexContainer.appendChild(aiPanel);
      codexContainer.appendChild(diffBar);
      codexContainer.appendChild(codexMainArea);

      // ===== FILE TREE =====
      function renderFileTree() {{
        const sb = document.getElementById(CODEX_ID + '-sidebar');
        sb.innerHTML = '';

        if (!document.getElementById('codex-spin-style')) {{
            const style = document.createElement('style');
            style.id = 'codex-spin-style';
            style.textContent = '@keyframes codex-spin {{ 100% {{ transform: rotate(360deg); }} }}';
            document.head.appendChild(style);
        }}

        // --- 1. Workspace Switcher ---
        const wsContainer = document.createElement('div');
        wsContainer.style.cssText = `display:flex; align-items:center; background:${{headerBg}}; border-bottom:1px solid ${{borderColor}}; flex-shrink:0;`;

        const wsSelect = document.createElement('select');
        wsSelect.id = CODEX_ID + '-workspace';
        wsSelect.style.cssText = `flex:1; padding:6px; background:transparent; border:none; color:${{textColor}}; font-size:12px; font-weight:bold; outline:none; cursor:pointer;`;
        
        Object.entries(workspaces).forEach(([key, label]) => {{
          const opt = document.createElement('option');
          opt.value = key;
          opt.textContent = '📦 ' + label;
          if (key === currentWorkspace) opt.selected = true;
          wsSelect.appendChild(opt);
        }});
        
        const wsSpinner = document.createElement('div');
        wsSpinner.id = CODEX_ID + '-ws-spinner';
        wsSpinner.style.cssText = `display:none; width:14px; height:14px; margin-right:8px; border:2px solid ${{textColor}}; border-top-color:transparent; border-radius:50%; animation:codex-spin 1s linear infinite;`;

        wsSelect.onchange = () => {{
          wsSelect.disabled = true;
          wsSpinner.style.display = 'block';
          window.sendCodexAction({{action:'switch_workspace', workspace:wsSelect.value}});
        }};
        
        wsContainer.appendChild(wsSelect);
        wsContainer.appendChild(wsSpinner);
        sb.appendChild(wsContainer);

        // Afficher la Timeline Git (historique) pour TOUS les espaces (main et sandbox)
        const statusBar = document.getElementById(CODEX_ID + '-status');
        if (statusBar) statusBar.style.display = 'flex';

        // --- 2 & 3. Render Files ---
        const treeContainer = document.createElement('div');
        treeContainer.style.cssText = 'overflow-y:auto; flex:1; padding-bottom:6px;';
        // Mode Universel : Arborescence dynamique (Lazy Loading)
        const treeMap = {{ '': {{ isDir: true, children: {{}}, mtime: 0, isLoaded: true }} }};
        
        function injectFilesToTree(fileList) {{
          fileList.forEach(f => {{
            const parts = f.filename.split('/');
            let currentPath = '';
            let parentNode = treeMap[''];
            
            if (f.mtime && f.mtime > parentNode.mtime) parentNode.mtime = f.mtime;

            for (let i = 0; i < parts.length; i++) {{
              const part = parts[i];
              currentPath = currentPath ? currentPath + '/' + part : part;
              const isLast = (i === parts.length - 1);
              
              if (!parentNode.children[part]) {{
                parentNode.children[part] = {{
                  name: part,
                  path: currentPath,
                  isDir: isLast ? (f.type === 'directory') : true,
                  file: isLast && f.type === 'file' ? f : null,
                  children: {{}},
                  mtime: f.mtime || 0,
                  isLoaded: false
                }};
              }} else {{
                if (f.mtime && f.mtime > parentNode.children[part].mtime) {{
                  parentNode.children[part].mtime = f.mtime;
                }}
                if (!isLast) {{
                  parentNode.children[part].isDir = true;
                }}
              }}
              parentNode = parentNode.children[part];
            }}
          }});
        }}

        injectFilesToTree(files);

        window.echoCodexAppendNodes = function(targetDir, newFiles) {{
           injectFilesToTree(newFiles);
           
           const parts = targetDir.split('/');
           let node = treeMap[''];
           if (targetDir !== "") {{
               for (const part of parts) {{
                   if (node.children[part]) node = node.children[part];
                   else return;
               }}
           }}
           
           const containerId = 'codex-dir-' + encodeURIComponent(targetDir);
           const childrenContainer = document.getElementById(containerId);
           if (childrenContainer) {{
               childrenContainer.innerHTML = '';
               node.isLoaded = true;
               
               const summary = childrenContainer.previousElementSibling;
               if (summary) {{
                   const span = summary.querySelector('.lazy-loading-span');
                   if (span) span.remove();
               }}
               
               renderNode(node, childrenContainer, targetDir === "" ? 0 : parts.length);
           }}
        }};

        function renderNode(node, container, level) {{
          Object.values(node.children).sort((a,b) => {{
            if(a.isDir && !b.isDir) return -1;
            if(!a.isDir && b.isDir) return 1;
            return (b.mtime || 0) - (a.mtime || 0);
          }}).forEach(child => {{
              if (child.isDir) {{
                const details = document.createElement('details');
                // N'ouvre le dossier que si le fichier actif s'y trouve
                details.open = currentFile && currentFile.startsWith(child.path + '/');
                const summary = document.createElement('summary');
                summary.style.cssText = `padding:4px 10px; padding-left:${{10 + level * 10}}px; cursor:pointer; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:flex; align-items:center; user-select:none; font-weight:600; color:${{isDark ? '#cba6f7' : '#8839ef'}};`;
                summary.innerHTML = `<span style="margin-right:4px;">📁</span> <span style="flex:1; overflow:hidden; text-overflow:ellipsis;">${{child.name}}</span>`;

                // Folder actions
                const actionGroup = document.createElement('div');
                actionGroup.style.cssText = 'margin-left:auto; display:flex; gap:4px;';
                
                const renBtn = document.createElement('span');
                renBtn.innerHTML = '✏️';
                renBtn.title = 'Renommer le dossier ' + child.path;
                renBtn.style.cssText = `opacity:0; color:${{isDark ? '#f9e2af' : '#df8e1d'}}; cursor:pointer; font-size:12px; padding:0 4px; transition:opacity 0.15s;`;
                renBtn.onclick = (e) => {{
                  e.preventDefault();
                  const input = document.createElement('input');
                  input.type = 'text';
                  input.value = child.name;
                  input.style.cssText = `flex:1; background:rgba(0,0,0,0.4); border:1px solid ${{isDark ? '#cba6f7' : '#8839ef'}}; color:inherit; font-family:inherit; font-size:inherit; padding:1px 4px; outline:none; border-radius:3px; margin-right:8px;`;
                  input.onclick = (ev) => ev.preventDefault();
                  const finalize = () => {{
                    if (input.parentNode && !input.disabled) {{
                      const newName = input.value.trim();
                      if (newName && newName !== child.name) {{
                        input.disabled = true;
                        const parentPath = child.path.substring(0, child.path.lastIndexOf('/') + 1);
                        window.sendCodexAction({{action:'rename_file', old_name:child.path, new_name: parentPath + newName, current_file:currentFile}});
                        summary.innerHTML = `<span style="margin-right:4px;">📁</span> <span style="flex:1; overflow:hidden; text-overflow:ellipsis;">${{newName}}</span>`;
                        summary.appendChild(actionGroup);
                      }} else {{
                        summary.innerHTML = `<span style="margin-right:4px;">📁</span> <span style="flex:1; overflow:hidden; text-overflow:ellipsis;">${{child.name}}</span>`;
                        summary.appendChild(actionGroup);
                      }}
                    }}
                  }};
                  input.onblur = finalize;
                  input.onkeydown = (ev) => {{
                    if (ev.key === 'Enter') {{ ev.preventDefault(); finalize(); }}
                    if (ev.key === 'Escape') {{ 
                      summary.innerHTML = `<span style="margin-right:4px;">📁</span> <span style="flex:1; overflow:hidden; text-overflow:ellipsis;">${{child.name}}</span>`;
                      summary.appendChild(actionGroup);
                    }}
                  }};
                  summary.innerHTML = `<span style="margin-right:4px;">📁</span>`;
                  summary.appendChild(input);
                  input.focus();
                  input.select();
                }};
                
                const delBtn = document.createElement('span');
                delBtn.innerHTML = '🗑️';
                delBtn.title = 'Supprimer le dossier ' + child.path;
                delBtn.style.cssText = `opacity:0; color:#f38ba8; cursor:pointer; font-size:12px; padding:0 4px; transition:opacity 0.15s;`;
                delBtn.onclick = (e) => {{
                  e.preventDefault();
                  window.echoCustomConfirm('Supprimer le dossier ' + child.path + ' ?', (agreed) => {{
                    if (agreed) window.sendCodexAction({{action:'delete_file', filename:child.path, current_file:currentFile}});
                  }});
                }};

                summary.onmouseenter = () => {{ renBtn.style.opacity = '1'; delBtn.style.opacity = '1'; }};
                summary.onmouseleave = () => {{ renBtn.style.opacity = '0'; delBtn.style.opacity = '0'; }};
                actionGroup.appendChild(renBtn);
                actionGroup.appendChild(delBtn);
                summary.appendChild(actionGroup);

                details.appendChild(summary);
                const childrenContainer = document.createElement('div');
                childrenContainer.id = 'codex-dir-' + encodeURIComponent(child.path);

                details.ontoggle = (e) => {{
                    if (details.open) {{
                        if (!child.isLoaded) {{
                            child.isLoaded = true;
                            const loadSpan = document.createElement('span');
                            loadSpan.className = 'lazy-loading-span';
                            loadSpan.style.cssText = `display:inline-block; width:10px; height:10px; margin-left:8px; border:2px solid ${{isDark ? '#cba6f7' : '#8839ef'}}; border-top-color:transparent; border-radius:50%; animation:codex-spin 0.8s linear infinite;`;
                            summary.appendChild(loadSpan);
                            window.sendCodexAction({{action: 'load_directory', path: child.path}});
                        }}
                    }} else {{
                        // Purge DOM et JS pour libérer la RAM
                        child.isLoaded = false;
                        child.children = {{}}; 
                        childrenContainer.innerHTML = '';
                    }}
                }};

                // Si le dossier doit être ouvert par défaut (focus fichier)
                if (details.open && !child.isLoaded) {{
                    child.isLoaded = true;
                            const loadSpan = document.createElement('span');
                            loadSpan.className = 'lazy-loading-span';
                            loadSpan.style.cssText = `display:inline-block; width:10px; height:10px; margin-left:8px; border:2px solid ${{isDark ? '#cba6f7' : '#8839ef'}}; border-top-color:transparent; border-radius:50%; animation:codex-spin 0.8s linear infinite;`;
                            summary.appendChild(loadSpan);
                    window.sendCodexAction({{action: 'load_directory', path: child.path}});
                }}

                if (child.isLoaded) {{
                    renderNode(child, childrenContainer, level + 1);
                }}
                
                details.appendChild(childrenContainer);
                container.appendChild(details);
              }} else {{
                // Fichier
                const f = child.file;
                const item = document.createElement('div');
                const isActive = f.filename === currentFile;
                item.style.cssText = `padding:4px 10px; padding-left:${{10 + level * 10}}px; cursor:pointer; font-size:12px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:flex; align-items:center; background:${{isActive ? hoverBg : 'transparent'}}; border-left:${{isActive ? '3px solid ' + accentColor : '3px solid transparent'}};`;
                const nameSpan = document.createElement('span');
                nameSpan.style.cssText = 'flex:1; overflow:hidden; text-overflow:ellipsis;';
                nameSpan.innerHTML = `<span style="margin-right:4px;">${{isActive ? '📝' : '📄'}}</span> ${{((modified && isActive) ? '● ' : '') + child.name}}`;
                nameSpan.title = f.filename + ' (' + f.lang + ', ' + f.lines + ' lines)';
                nameSpan.onclick = () => switchFile(f.filename);
                item.appendChild(nameSpan);

                // Boutons d'action (fichier arboré)
                const actionGroup = document.createElement('div');
                actionGroup.style.cssText = 'margin-left:auto; display:flex; gap:4px; align-items:center;';
                
                const renBtn = document.createElement('span');
                renBtn.innerHTML = '✏️';
                renBtn.title = 'Renommer ' + f.filename;
                renBtn.style.cssText = `opacity:0; color:${{isDark ? '#f9e2af' : '#df8e1d'}}; cursor:pointer; font-size:12px; padding:0 4px; transition:opacity 0.15s;`;
                renBtn.onclick = (e) => {{
                  e.stopPropagation();
                  const input = document.createElement('input');
                  input.type = 'text';
                  input.value = child.name;
                  input.style.cssText = `flex:1; background:rgba(0,0,0,0.4); border:1px solid ${{accentColor}}; color:inherit; font-family:inherit; font-size:inherit; padding:1px 4px; outline:none; border-radius:3px; margin-right:8px;`;
                  input.onclick = (ev) => ev.stopPropagation();
                  const finalize = () => {{
                    if (input.parentNode && !input.disabled) {{
                      const newName = input.value.trim();
                      if (newName && newName !== child.name) {{
                        input.disabled = true;
                        const parentPath = f.filename.substring(0, f.filename.lastIndexOf('/') + 1);
                        window.sendCodexAction({{action:'rename_file', old_name:f.filename, new_name: parentPath + newName, current_file:currentFile}});
                        nameSpan.innerHTML = `<span style="margin-right:4px;">${{isActive ? '📝' : '📄'}}</span> ${{((modified && isActive) ? '● ' : '') + newName}}`;
                      }} else {{
                        nameSpan.innerHTML = `<span style="margin-right:4px;">${{isActive ? '📝' : '📄'}}</span> ${{((modified && isActive) ? '● ' : '') + child.name}}`;
                      }}
                    }}
                  }};
                  input.onblur = finalize;
                  input.onkeydown = (ev) => {{
                    if (ev.key === 'Enter') {{ ev.preventDefault(); finalize(); }}
                    if (ev.key === 'Escape') {{ 
                      nameSpan.innerHTML = `<span style="margin-right:4px;">${{isActive ? '📝' : '📄'}}</span> ${{((modified && isActive) ? '● ' : '') + child.name}}`;
                    }}
                  }};
                  nameSpan.innerHTML = `<span style="margin-right:4px;">${{isActive ? '📝' : '📄'}}</span>`;
                  nameSpan.appendChild(input);
                  input.focus();
                  input.select();
                }};

                const delBtn = document.createElement('span');
                delBtn.innerHTML = '×';
                delBtn.title = 'Supprimer ' + f.filename;
                delBtn.style.cssText = `opacity:0; color:#f38ba8; cursor:pointer; font-size:14px; font-weight:bold; padding:0 4px; transition:opacity 0.15s;`;
                delBtn.onclick = (e) => {{
                  e.stopPropagation();
                  window.echoCustomConfirm('Supprimer ' + f.filename + ' ?', (agreed) => {{
                    if (agreed) window.sendCodexAction({{action:'delete_file', filename:f.filename, current_file:currentFile}});
                  }});
                }};
                
                item.onmouseenter = () => {{ renBtn.style.opacity = '1'; delBtn.style.opacity = '1'; }};
                item.onmouseleave = () => {{ renBtn.style.opacity = '0'; delBtn.style.opacity = '0'; }};
                actionGroup.appendChild(renBtn);
                actionGroup.appendChild(delBtn);
                item.appendChild(actionGroup);
                container.appendChild(item);
              }}
            }});
          }}
          renderNode(treeMap[''], treeContainer, 0);

        sb.appendChild(treeContainer);

        // + Créer
        const newBtn = document.createElement('div');
        newBtn.style.cssText = `padding:8px 10px; cursor:pointer; font-size:12px; color:${{accentColor}}; border-top:1px solid ${{borderColor}}; margin-top:auto; font-weight:bold; flex-shrink:0;`;
        newBtn.textContent = '+ Créer';
        newBtn.onclick = () => {{
          newBtn.textContent = '';
          const input = document.createElement('input');
          input.type = 'text';
          input.placeholder = 'ex: src/main.py';
          input.style.cssText = `width:100%; background:rgba(0,0,0,0.2); border:1px solid ${{borderColor}}; color:${{textColor}}; padding:4px 6px; border-radius:4px; font-size:12px; outline:none; font-family:monospace;`;

          const submitFile = () => {{
            const name = input.value.trim();
            if (name) window.sendCodexAction({{action:'new_file', filename:name}});
            else renderFileTree();
          }};

          input.onkeydown = (e) => {{
            if (e.key === 'Enter') submitFile();
            if (e.key === 'Escape') renderFileTree();
          }};
          input.onblur = () => submitFile();

          newBtn.appendChild(input);
          input.focus();
          newBtn.onclick = null;
        }};
        sb.appendChild(newBtn);

        // Reset
        const resetBtn = document.createElement('div');
        resetBtn.style.cssText = `padding:8px 10px; cursor:pointer; font-size:12px; color:#f38ba8; border-top:1px dashed ${{borderColor}}; font-weight:bold; flex-shrink:0;`;
        resetBtn.textContent = '🗑️ Reset complet';
        resetBtn.onclick = () => {{
          window.echoCustomConfirm(`⚠️ Vider intégralement le workspace "{current_workspace}" ? Irréversible.`, (agreed) => {{
            if (agreed) {{
              window.sendCodexAction({{action:'reset'}});
            }}
          }});
        }};
        sb.appendChild(resetBtn);
      }}

      function switchFile(filename) {{
        if (modified && currentFile) {{
          window.echoCustomConfirm('Modifications non sauvegardées. Continuer ?', (agreed) => {{
            if (agreed) {{
              modified = false;
              switchFile(filename);
            }}
          }});
          return;
        }}
        currentFile = filename;
        modified = false;
        renderFileTree();
        updateStatus(filename + ' \u2022 chargement...');
        // Demander le contenu au backend Python
        window.sendCodexAction({{action:'load_file', filename:filename}});
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
        window.sendCodexAction({{
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

      // Splitter drag logic
      let isDraggingSplit = false;
      let isDraggingSidebarSplit = false;
      document.getElementById(CODEX_ID + '-splitter').onmousedown = (e) => {{
        e.preventDefault();
        isDraggingSplit = true;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        // Bloquer les events sur l'iframe du preview (sinon elle capture les mousemove)
        document.getElementById(CODEX_ID + '-preview').style.pointerEvents = 'none';
        editorWrap.style.pointerEvents = 'none';
      }};
      document.getElementById(CODEX_ID + '-sidebar-splitter').onmousedown = (e) => {{
        e.preventDefault();
        isDraggingSidebarSplit = true;
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
        document.getElementById(CODEX_ID + '-preview').style.pointerEvents = 'none';
        editorWrap.style.pointerEvents = 'none';
      }};
      document.addEventListener('mousemove', (e) => {{
        if (isDraggingSidebarSplit) {{
          const hudRect = hud.getBoundingClientRect();
          let newW = e.clientX - hudRect.left;
          newW = Math.max(50, Math.min(newW, hudRect.width / 2));
          sidebarWidth = newW;
          document.getElementById(CODEX_ID + '-sidebar').style.width = newW + 'px';
        }}
        if (isDraggingSplit) {{
          const hudRect = hud.getBoundingClientRect();
          const sidebarW = document.getElementById(CODEX_ID + '-sidebar').offsetWidth;
          const avail = hudRect.width - sidebarW - 10;
          let edW = e.clientX - (hudRect.left + sidebarW + 5);
          edW = Math.max(200, Math.min(edW, avail - 200));
          const prevW = avail - edW;
          editorRatio = (edW / avail) * 100;
          previewRatio = (prevW / avail) * 100;
          editorWrap.style.flex = `${{editorRatio}} 1 0%`;
          document.getElementById(CODEX_ID + '-preview').style.flex = `${{previewRatio}} 1 0%`;
        }}
      }});
      document.addEventListener('mouseup', () => {{
        if (isDraggingSplit || isDraggingSidebarSplit) {{
          isDraggingSplit = false;
          isDraggingSidebarSplit = false;
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
          accessibilitySupport: 'off'
        }});
        editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {{
          doSave();
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
            const baseName = currentFile.replace(/\\.[^.]+$/, '');
            const newExt = LANG_TO_EXT[newLang];
            const newFilename = baseName + newExt;
            if (newFilename !== currentFile) {{
              window.echoCustomConfirm('Renommer ' + currentFile + ' \u2192 ' + newFilename + ' ?', (agreed) => {{
                if (agreed) {{
                  window.sendCodexAction({{action:'rename_file', old_name:currentFile, new_name:newFilename, current_file:currentFile}});
                }}
              }});
            }}
          }}
          // Mise à jour preview
          updatePreviewButton();
          if (previewOpen) updatePreview();
        }};

        // Demande de chargement initial asynchrone (Pull)
        const checkReady = setInterval(() => {{
          if (typeof window.echoCodexResolve === 'function') {{
            clearInterval(checkReady);
            if (currentFile) {{
              updateStatus(currentFile + ' \u2022 chargement...');
              window.sendCodexAction({{action:'load_file', filename: currentFile}});
            }}
          }}
        }}, 50);
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

      // ===== PING HEARTBEAT =====
      setInterval(() => {{
        if (!document.hidden) {{
            window.sendCodexAction({{action: 'ping', current_file: currentFile}});
          }}
      }}, 5000);

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
            "  const HUD_ID = 'echo-cognitive-monitor';\n" + EchoUI.get_mobile_guard_js('echo-cognitive-monitor') + "\n"
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
            "  // Forcé à false au lancement pour éviter le bug de la fenêtre vide après restauration\n"
            "  isMinimized = false;\n"
            "  if (saved.activeIdx !== undefined) activeThreadIdx = saved.activeIdx;\n"
            "  if (activeThreadIdx >= threads.length) activeThreadIdx = Math.max(0, threads.length - 1);\n"
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
            "    var p = function(n){return ('0'+n).slice(-2);};\n"
            "    return d.getFullYear() + '-' + p(d.getMonth()+1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());\n"
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
            "  codexMainArea.appendChild(sidebar);\n"
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
            "    var scrollArea = document.getElementById(HUD_ID + '-tree-scroll');\n"
            "    if (scrollArea) scrollArea.scrollTop = scrollArea.scrollHeight;\n"
            "  };\n"
            "\n"
            "  // =============== INIT ===============\n"
            "  clampHud();\n"
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
            schemas_json: str = "{}") -> str:
        """Génère le script JS complet du HUD ECHO Identity Vault avec schémas dynamiques."""

        return (
            "(function() {\n"
            "  const HUD_ID = 'echo-vault-identity';\n"
            + EchoUI.get_mobile_guard_js('echo-vault-identity') + "\n"
            + EchoUI.get_sanitize_html_js() + "\n"
            "  if (document.getElementById(HUD_ID)) document.getElementById(HUD_ID).remove();\n"
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
            "      onClose: () => { if(window.echoVaultResolve) window.echoVaultResolve({action: 'close'}); }\n"
            "  });\n"
            "  monitor.render();\n"
            "  monitor.attachBaseEvents();\n"
            "  \n"
            "  // Populate Select Service & Dynamic Fields\n"
            "  const serviceSelect = document.getElementById('vault-input-service');\n"
            "  const dynamicFieldsContainer = document.getElementById('vault-dynamic-fields');\n"
            "  \n"
            "  Object.keys(schemas).forEach(key => {\n"
            "    const opt = document.createElement('option');\n"
            "    opt.value = key;\n"
            "    opt.textContent = schemas[key].name || key;\n"
            "    serviceSelect.appendChild(opt);\n"
            "  });\n"
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
            "      if(window.echoVaultResolve) window.echoVaultResolve({action: 'add_account', service: service, account_id: account_id, credentials: credStr});\n"
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
            "      if(agreed && window.echoVaultResolve) window.echoVaultResolve({action: 'delete_account', service: service, account_id: alias});\n"
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
