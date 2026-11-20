"""
title: ECHO UI Rendering Engine
author: Wilfried BARNAVON
version: 6.14.2.2
description: Composant système interne : ECHO UI Rendering Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.14.2.2: Purge du code mort EchoFloatingHUD et ajout du pont postMessage echo-asset:// et du fast-path blob: dans image_viewer et get_media_export_js.
# 6.6.16.1: Suppression de get_floating_monitor_js suite à la migration de echo_monitor vers EchoCanvasTab.svelte.
# 6.6.9.2: Purge du reliquat mort get_esm_loader_js.
# 6.6.9.1: Purge des ponts JS obsolètes (get_webgpu_probe_js, get_edge_embedding_bridge_js, get_event_dispatcher_js, get_mobile_guard_js, safe_deploy, get_context_gauge_renderer_js) et du doublon CustomEvent dans deploy_context_gauge.
# 6.6.2.2: Purge de monitor_ECHO et _generate_webplayer_js (remplacés par l'onglet natif EchoBrowserTab).


import sys
import orjson as std_json
from typing import Optional, Any, List, Dict
from fastapi.responses import HTMLResponse

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")

from echo_constants import (  # noqa: E402
    ECHO_GLOBAL_TENANT_PROJECT_ID,
    ECHO_MAX_CONTEXT_SIZE,
)


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
        "Export": "<path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='17 8 12 3 7 8'/><line x1='12' y1='3' x2='12' y2='15'/>",
        "Check": "<polyline points='20 6 9 17 4 12'/>",
        "Copy": "<rect x='9' y='9' width='13' height='13' rx='2' ry='2'/><path d='M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'/>",
        "Present": "<path d='M6 8l4 4-4 4M18 8l-4 4 4 4M12 4v16'/>",
        "CornerUpLeft": "<polyline points='9 14 4 9 9 4'/><path d='M20 20v-7a4 4 0 0 0-4-4H4'/>",
        "RotateCcw": "<polyline points='1 4 1 10 7 10'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/>",
        "SkipBack": "<polygon points='19 20 9 12 19 4 19 20'/><line x1='5' y1='19' x2='5' y2='5'/>",
        "SkipForward": "<polygon points='5 4 15 12 5 20 5 4'/><line x1='19' y1='5' x2='19' y2='19'/>",
        "Play": "<polygon points='5 3 19 12 5 21 5 3'/>",
        "Rewind": "<polygon points='11 19 2 12 11 5 11 19'/><polygon points='22 19 13 12 22 5 22 19'/>",
        "Pause": "<rect x='6' y='4' width='4' height='16'/><rect x='14' y='4' width='4' height='16'/>",
        "Crop": "<path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/>",
        "Cpu": "<rect x='4' y='4' width='16' height='16' rx='2' ry='2'/><rect x='9' y='9' width='6' height='6'/><line x1='9' y1='1' x2='9' y2='4'/><line x1='15' y1='1' x2='15' y2='4'/><line x1='9' y1='20' x2='9' y2='23'/><line x1='15' y1='20' x2='15' y2='23'/><line x1='20' y1='9' x2='23' y2='9'/><line x1='20' y1='14' x2='23' y2='14'/><line x1='1' y1='9' x2='4' y2='9'/><line x1='1' y1='14' x2='4' y2='14'/>",
        "Shrink": "<path d='M4 14h6v6m10-10h-6V4m0 6 7-7M3 21l7-7'/>",
        "Lock": "<rect x='3' y='11' width='18' height='11' rx='2' ry='2'/><path d='M7 11V7a5 5 0 0 1 10 0v4'/>",
        "Unlock": "<rect x='3' y='11' width='18' height='11' rx='2' ry='2'/><path d='M7 11V7a5 5 0 0 1 9.9-1'/>"
    }

    @staticmethod
    def get_icon(name: str, size: int = 16, color: str = "currentColor", stroke_width: int = 2, css_class: str = "") -> str:
        """Générateur SVG universel."""
        path = EchoRichUI.ECHO_ICONS.get(name, "")
        cls = f" class='{css_class}'" if css_class else ""
        return f"<svg width='{size}' height='{size}' viewBox='0 0 24 24' fill='none' stroke='{color}' stroke-width='{stroke_width}' stroke-linecap='round' stroke-linejoin='round'{cls}>{path}</svg>"

    @staticmethod
    def _get_boilerplate(content: str, title: str = "ECHO Visual") -> str:
        """Encapsulation HTML standard avec détection de thème hybride (Open WebUI Native)."""
        max_icon_j = std_json.dumps(EchoRichUI.get_icon("Maximize", size=14)).decode("utf-8")
        shrink_icon_j = std_json.dumps(EchoRichUI.get_icon("Shrink", size=14)).decode("utf-8")
        return f"""
    <!DOCTYPE html>
    <html lang="fr" class="dark">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>{title}</title>
      <style>
        :root {{
          --echo-bg: #ffffff;
          --echo-text: #171717;
          --echo-hud-bg: #ffffff;
          --echo-hud-border: #e5e5e5;
          --echo-hud-text: #525252;
          --echo-btn-bg: #f3f4f6;
          --echo-btn-border: #d1d5db;
          --echo-btn-text: #171717;
          --echo-accent: #3b82f6;
        }}
        html.dark {{
          --echo-bg: #171717;
          --echo-text: #ececec;
          --echo-hud-bg: #171717;
          --echo-hud-border: #2e2e2e;
          --echo-hud-text: #a3a3a3;
          --echo-btn-bg: #262626;
          --echo-btn-border: #404040;
          --echo-btn-text: #ececec;
        }}
        html.oled-dark {{
          --echo-bg: #000000;
          --echo-text: #ffffff;
          --echo-hud-bg: #000000;
          --echo-hud-border: #262626;
          --echo-hud-text: #a3a3a3;
          --echo-btn-bg: #141414;
          --echo-btn-border: #262626;
          --echo-btn-text: #ffffff;
        }}
        html {{
          --bg-primary: var(--echo-bg);
          --bg-secondary: var(--echo-hud-bg);
          --text-primary: var(--echo-text);
          --text-secondary: var(--echo-hud-text);
          --border: var(--echo-hud-border);
          --accent: var(--echo-accent);
        }}
        html, body {{
          margin: 0; padding: 0; background: var(--echo-bg); color: var(--echo-text);
          font-family: ui-sans-serif, system-ui, -apple-system, sans-serif; overflow: hidden; transition: background 0.15s, color 0.15s;
        }}
        .echo-container {{ width: 100%; display: flex; flex-direction: column; }}
        #echo-iv-root {{
          display: flex;
          flex-direction: column;
          width: 100%;
          border: 1px solid var(--echo-hud-border);
          border-radius: 10px;
          overflow: hidden;
          background: var(--echo-bg);
          box-sizing: border-box;
        }}
        html.echo-is-fullscreen,
        html.echo-is-fullscreen body,
        html.echo-is-fullscreen .echo-container,
        html.echo-is-fullscreen #echo-iv-root {{
          width: 100vw !important;
          height: 100dvh !important;
          max-height: 100dvh !important;
          border-radius: 0 !important;
          border: none !important;
          margin: 0 !important;
        }}
        html.echo-is-fullscreen #canvas-area {{
          flex: 1 1 auto !important;
          height: calc(100dvh - 46px) !important;
          min-height: 0 !important;
        }}
        html.echo-is-fullscreen #visual-stage,
        html.echo-is-fullscreen #visual-target,
        html.echo-is-fullscreen #visual-target-container,
        html.echo-is-fullscreen #celestial-map {{
          height: 100% !important;
          min-height: 0 !important;
        }}
        #hud-bar {{
          margin-bottom: 0 !important;
          border: none !important;
          border-bottom: 1px solid var(--echo-hud-border) !important;
          border-radius: 0 !important;
          padding: 8px 12px !important;
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          flex-wrap: wrap;
          background: var(--echo-bg);
          z-index: 100;
          color: var(--echo-hud-text);
          font-size: 12px;
        }}
        .echo-iv-title-group {{
          display: flex;
          align-items: center;
          gap: 8px;
          min-width: 0;
          flex: 1;
        }}
        .echo-iv-title {{
          font-weight: 600;
          font-size: 13px;
          color: var(--echo-text);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }}
        .echo-iv-dims {{
          font-size: 11px;
          color: var(--echo-hud-text);
          font-family: 'JetBrains Mono', monospace;
          white-space: nowrap;
          padding: 2px 6px;
          border-radius: 4px;
          background: var(--echo-btn-bg);
          border: 1px solid var(--echo-hud-border);
        }}
        .echo-iv-actions {{
          display: flex;
          align-items: center;
          gap: 6px;
          flex-wrap: wrap;
        }}
        .echo-iv-btn {{
          display: inline-flex;
          align-items: center;
          justify-content: center;
          gap: 5px;
          height: 28px;
          padding: 0 9px;
          border-radius: 6px;
          border: 1px solid var(--echo-btn-border);
          background: var(--echo-btn-bg);
          color: var(--echo-text);
          font-size: 11px;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.15s ease;
          user-select: none;
        }}
        .echo-iv-btn:hover {{
          border-color: var(--echo-accent);
          color: var(--echo-accent);
        }}
        .echo-iv-btn.is-active {{
          border-color: var(--echo-accent);
          background: rgba(59, 130, 246, 0.14);
          color: var(--echo-accent);
        }}
        .echo-iv-btn:disabled {{
          opacity: 0.55;
          cursor: default;
        }}
        #canvas-area {{
          width: 100%;
          min-height: 80px;
          position: relative;
          overflow: hidden;
          display: flex;
          align-items: center;
          justify-content: center;
          background-color: var(--echo-bg);
          box-sizing: border-box;
          padding: 8px;
        }}
        #echo-rv-lock-shield {{
          position: absolute;
          inset: 0;
          z-index: 50;
          background: transparent;
          cursor: default;
        }}
        .btn {{
          background: var(--echo-btn-bg); border: 1px solid var(--echo-btn-border); color: var(--echo-btn-text);
          padding: 4px 12px; border-radius: 6px; cursor: pointer; font-size: 11px; transition: all 0.2s; font-weight: 500;
        }}
        .btn:hover {{ filter: brightness(1.2); border-color: var(--echo-accent); }}

        @media (prefers-color-scheme: dark) {{
          html:not(.light) {{
            --echo-bg: #171717;
            --echo-text: #ececec;
          }}
        }}
      </style>
      <script>
      var lastHeight = 0;
      var reportTimeout = null;
      var disableAutoResize = false;

      function reportHeight() {{
        if (disableAutoResize) return;
        const container = document.querySelector('.echo-container');
        if (!container && !document.body) return;
        const rawH = container ? container.getBoundingClientRect().height : document.body.offsetHeight;
        const h = Math.max(Math.ceil(rawH), 100);
        if (Math.abs(h - lastHeight) > 2) {{
            lastHeight = h;
            parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
        }}
      }}

      window.echoBindFrameFullscreen = function(opts) {{
        opts = opts || {{}};
        const btnEl = opts.btnEl || document.getElementById('echo-iv-btn-fs');
        const maxIcon = opts.maxIcon || {max_icon_j};
        const shrinkIcon = opts.shrinkIcon || {shrink_icon_j};
        const onChange = opts.onChange || null;
        let isFs = document.documentElement.classList.contains('echo-is-fullscreen');

        function applyFsState(nextFs) {{
          const changed = (isFs !== !!nextFs);
          isFs = !!nextFs;
          if (isFs && reportTimeout) {{
            clearTimeout(reportTimeout);
            reportTimeout = null;
          }}
          document.documentElement.classList.toggle('echo-is-fullscreen', isFs);
          disableAutoResize = isFs;
          if (btnEl) {{
            btnEl.classList.toggle('is-active', isFs);
            btnEl.innerHTML = (isFs ? shrinkIcon : maxIcon) + ' <span>' + (isFs ? 'Quitter' : 'Plein écran') + '</span>';
            btnEl.title = isFs ? 'Quitter le mode plein écran (Échap)' : 'Afficher en plein écran maximisé';
          }}
          if (changed) {{
            if (typeof onChange === 'function') onChange(isFs);
            try {{ window.dispatchEvent(new Event('resize')); }} catch (e) {{}}
          }}
          if (!isFs && typeof reportHeight === 'function') {{
            lastHeight = 0;
            setTimeout(reportHeight, 50);
          }}
        }}

        function toggleFs() {{
          const targetFs = !isFs;
          applyFsState(targetFs);
          if (targetFs) {{
            const rootEl = document.documentElement;
            const reqFs = rootEl.requestFullscreen || rootEl.webkitRequestFullscreen;
            if (reqFs) {{
              try {{
                Promise.resolve(reqFs.call(rootEl)).catch(() => {{
                  try {{ parent.postMessage({{ type: 'echo:iframe:fullscreen', active: true }}, '*'); }} catch (e) {{}}
                }});
                return;
              }} catch (e) {{}}
            }}
            try {{ parent.postMessage({{ type: 'echo:iframe:fullscreen', active: true }}, '*'); }} catch (e) {{}}
          }} else {{
            if (document.fullscreenElement || document.webkitFullscreenElement) {{
              const exitFs = document.exitFullscreen || document.webkitExitFullscreen;
              if (exitFs) {{
                try {{ Promise.resolve(exitFs.call(document)).catch(() => {{}}); }} catch (e) {{}}
              }}
            }}
            try {{ parent.postMessage({{ type: 'echo:iframe:fullscreen', active: false }}, '*'); }} catch (e) {{}}
          }}
        }}

        if (btnEl && !btnEl.dataset.echoFsBound) {{
          btnEl.dataset.echoFsBound = '1';
          btnEl.onclick = function(e) {{
            e.stopPropagation();
            toggleFs();
          }};
          window.addEventListener('message', (ev) => {{
            if (ev.data && ev.data.type === 'echo:iframe:fullscreen-state') {{
              applyFsState(!!ev.data.active);
            }}
          }});
          document.addEventListener('fullscreenchange', () => {{
            const nativeFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
            if (nativeFs && !isFs) {{
              applyFsState(true);
            }} else if (!nativeFs && isFs) {{
              try {{ parent.postMessage({{ type: 'echo:iframe:fullscreen', active: false }}, '*'); }} catch (e) {{}}
              applyFsState(false);
            }}
          }});
          window.addEventListener('keydown', (ke) => {{
            if (ke.key === 'Escape' && isFs) {{
              ke.stopPropagation();
              if (document.fullscreenElement && document.exitFullscreen) {{
                try {{ document.exitFullscreen().catch(() => {{}}); }} catch (e) {{}}
              }}
              try {{ parent.postMessage({{ type: 'echo:iframe:fullscreen', active: false }}, '*'); }} catch (e) {{}}
              applyFsState(false);
            }}
          }}, true);
        }}

        return {{
          isFullscreen: () => isFs,
          setFullscreen: applyFsState,
          toggle: toggleFs
        }};
      }};
      </script>
    </head>
    <body>
      <div class="echo-container">
        {content}
      </div>
    <script>
      if (window.echoBindFrameFullscreen && document.getElementById('echo-iv-btn-fs')) {{
        window.echoBindFrameFullscreen();
      }}

      function applyThemeClass(theme, customColors) {{
        const root = document.documentElement;
        root.classList.remove('light', 'dark', 'oled-dark');
        if (theme === 'oled-dark') {{
            root.classList.add('dark', 'oled-dark');
        }} else if (theme === 'light') {{
            root.classList.add('light');
        }} else {{
            root.classList.add('dark');
        }}
        if (customColors && typeof customColors === 'object') {{
            if (customColors.bgColor) {{
                root.style.setProperty('--echo-bg', customColors.bgColor);
                root.style.setProperty('--echo-hud-bg', customColors.bgColor);
            }}
            if (customColors.textColor) {{
                root.style.setProperty('--echo-text', customColors.textColor);
                root.style.setProperty('--echo-btn-text', customColors.textColor);
            }}
            if (customColors.borderColor) {{
                root.style.setProperty('--echo-hud-border', customColors.borderColor);
            }}
        }}
        if (typeof reportHeight === 'function') reportHeight();
      }}

      function syncWithParent() {{
        try {{
            if (parent && parent.document && parent.document.documentElement) {{
                const pDoc = parent.document;
                const isDark = pDoc.documentElement.classList.contains('dark');
                const isOled = pDoc.documentElement.classList.contains('oled-dark') || pDoc.documentElement.style.getPropertyValue('--color-gray-900') === '#000000';
                const refEl = pDoc.getElementById('chat-container') || pDoc.querySelector('main') || pDoc.body || pDoc.documentElement;
                let bgColor = '';
                let textColor = '';
                if (refEl && parent.getComputedStyle) {{
                    const cs = parent.getComputedStyle(refEl);
                    if (cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent') {{
                        bgColor = cs.backgroundColor;
                    }}
                    if (cs.color) textColor = cs.color;
                }}
                applyThemeClass(isOled ? 'oled-dark' : (isDark ? 'dark' : 'light'), {{ bgColor: bgColor, textColor: textColor }});
                return true;
            }}
        }} catch(e) {{ }}
        try {{
            parent.postMessage({{ type: 'echo:iframe:init' }}, '*');
        }} catch(e) {{ }}
        return false;
      }}

      window.addEventListener('message', (event) => {{
        if (event.data && event.data.type === 'theme-update') {{
            applyThemeClass(event.data.theme, {{
                bgColor: event.data.bgColor || '',
                textColor: event.data.textColor || '',
                borderColor: event.data.borderColor || ''
            }});
        }}
      }});

      const darkMQ = window.matchMedia('(prefers-color-scheme: dark)');
      darkMQ.addEventListener('change', (e) => {{
        let hasOverride = false;
        try {{ hasOverride = !!localStorage.getItem('theme-override'); }} catch(err) {{ }}
        if (!hasOverride) {{
            applyThemeClass(e.matches ? 'dark' : 'light');
        }}
      }});

      function debouncedReport() {{
        clearTimeout(reportTimeout);
        reportTimeout = setTimeout(reportHeight, 150);
      }}

      syncWithParent();

      window.addEventListener('load', () => {{
        syncWithParent();
        reportHeight();
      }});

      if (window.ResizeObserver) {{
          const targetEl = document.querySelector('.echo-container') || document.body;
          const ro = new ResizeObserver(() => {{
            if (!disableAutoResize) debouncedReport();
          }});
          ro.observe(targetEl);
      }}

      setTimeout(reportHeight, 500);
    </script>
    </body>
    </html>
    """


class EchoUI(EchoRichUI):
    """Moteur de pilotage HUD pour ECHO."""

    @staticmethod
    def get_media_export_js() -> str:
        """Génère les moteurs factorisés window.echoTriggerDownload et window.echoExportImage."""
        check_svg = EchoRichUI.get_icon("Check", size=16, color="#4ade80")
        return f"""
        window.echoTriggerDownload = function(blob, filename) {{
            if (!blob) return false;
            if ((window.EchoWM && window.EchoWM.isReplaying) || document.hidden) return false;
            const safeName = String(filename || 'download').split('/').pop() || 'download';
            const url = URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = safeName;
            document.body.appendChild(a);
            a.click();
            a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
            return true;
        }};

        window.echoExportImage = async function(opts) {{
            if (!opts || !opts.imgEl) return false;
            const imgEl = opts.imgEl;
            const mode = opts.mode || 'copy';
            const btnEl = opts.btnEl || null;
            const natW = imgEl.naturalWidth || imgEl.width || 0;
            const natH = imgEl.naturalHeight || imgEl.height || 0;
            if (!natW || !natH) return false;

            const flashButton = (btn) => {{
                if (!btn) return;
                if (!btn.dataset.echoOrigHtml) btn.dataset.echoOrigHtml = btn.innerHTML;
                if (btn.dataset.echoOrigColor === undefined) btn.dataset.echoOrigColor = btn.style.color || '';
                if (btn._echoFlashTimer) clearTimeout(btn._echoFlashTimer);
                btn.innerHTML = "{check_svg}";
                btn.style.color = '#4ade80';
                btn._echoFlashTimer = setTimeout(() => {{
                    btn.innerHTML = btn.dataset.echoOrigHtml;
                    btn.style.color = btn.dataset.echoOrigColor;
                    btn._echoFlashTimer = null;
                }}, 1000);
            }};

            const showFallback = (url, fbMode) => {{
                const actionLabel = (fbMode === 'save')
                    ? "Mode Restreint : Faites Clic droit -> Enregistrer l'image sous..."
                    : "Mode Restreint : Faites Clic droit -> Copier l'image";
                const over = document.createElement('div');
                over.style.cssText = 'position:fixed; inset:0; background:rgba(0,0,0,0.85); z-index:20000; display:flex; flex-direction:column; align-items:center; justify-content:center; color:white; backdrop-filter:blur(5px); padding:20px;';
                over.innerHTML = `
                    <p style="background:#4ade80; color:black; padding:8px 16px; border-radius:20px; font-weight:bold; margin-bottom:20px; text-align:center;">${{actionLabel}}</p>
                    <img src="${{url}}" style="max-width:90%; max-height:70%; border:2px solid #555; border-radius:8px; box-shadow:0 0 50px rgba(0,0,0,0.5);" />
                    <p style="margin-top:20px; color:#aaa; cursor:pointer; font-size:14px;">[ Cliquez n'importe où pour fermer ]</p>
                `;
                over.onclick = () => over.remove();
                document.body.appendChild(over);
            }};

            const parseDataUri = (src) => {{
                if (!src || !src.startsWith('data:')) return null;
                const commaIdx = src.indexOf(',');
                if (commaIdx === -1) return null;
                const meta = src.substring(5, commaIdx);
                const isB64 = meta.endsWith(';base64');
                const mime = (isB64 ? meta.slice(0, -7) : meta) || 'image/png';
                if (!isB64) return null;
                const b64 = src.substring(commaIdx + 1);
                const binStr = atob(b64);
                const bytes = new Uint8Array(binStr.length);
                for (let i = 0; i < binStr.length; i++) bytes[i] = binStr.charCodeAt(i);
                return {{ blob: new Blob([bytes], {{ type: mime }}), mime: mime }};
            }};

            const triggerDownload = (blob, name) => {{
                window.echoTriggerDownload(blob, name);
            }};

            const extFromMime = (mime) => {{
                const m = (mime || '').toLowerCase();
                const map = {{
                    'image/png': '.png', 'image/jpeg': '.jpg', 'image/jpg': '.jpg',
                    'image/webp': '.webp', 'image/gif': '.gif', 'image/svg+xml': '.svg',
                    'image/bmp': '.bmp', 'image/x-icon': '.ico', 'image/avif': '.avif'
                }};
                return map[m] || '.png';
            }};

            let isCropped = false;
            let sx = 0, sy = 0, sw = natW, sh = natH;
            const cropBox = opts.cropBoxEl || null;
            const canvasContainer = opts.canvasContainerEl || null;
            if (opts.isCropping && cropBox && canvasContainer && cropBox.style.display !== 'none') {{
                const rect = cropBox.getBoundingClientRect();
                const cRect = canvasContainer.getBoundingClientRect();
                if (cRect.width > 0 && cRect.height > 0) {{
                    const scaleX = natW / cRect.width, scaleY = natH / cRect.height;
                    sx = Math.max(0, (rect.left - cRect.left) * scaleX);
                    sy = Math.max(0, (rect.top - cRect.top) * scaleY);
                    sw = Math.min(rect.width * scaleX, natW - sx);
                    sh = Math.min(rect.height * scaleY, natH - sy);
                    if (sw > 5 && sh > 5) isCropped = true;
                }}
            }}

            const buildCanvas = () => {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                if (isCropped) {{
                    canvas.width = Math.round(sw);
                    canvas.height = Math.round(sh);
                    ctx.imageSmoothingEnabled = true;
                    ctx.imageSmoothingQuality = 'high';
                    ctx.drawImage(imgEl, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height);
                }} else {{
                    canvas.width = natW;
                    canvas.height = natH;
                    ctx.imageSmoothingEnabled = true;
                    ctx.imageSmoothingQuality = 'high';
                    ctx.drawImage(imgEl, 0, 0);
                }}
                return canvas;
            }};

            const src = imgEl.src || '';

            if (mode === 'save') {{
                if (!isCropped) {{
                    const parsed = parseDataUri(src);
                    if (parsed) {{
                        const ext = extFromMime(parsed.mime);
                        let dlName = opts.filename || ('ECHO_IMAGE_' + Date.now() + ext);
                        if (dlName.indexOf('.') === -1) dlName += ext;
                        triggerDownload(parsed.blob, dlName);
                        flashButton(btnEl);
                        return true;
                    }}
                    if (src.startsWith('blob:')) {{
                        try {{
                            const resp = await fetch(src);
                            const b = await resp.blob();
                            const ext = extFromMime(b.type);
                            let dlName = opts.filename || ('ECHO_IMAGE_' + Date.now() + ext);
                            if (dlName.indexOf('.') === -1) dlName += ext;
                            triggerDownload(b, dlName);
                            flashButton(btnEl);
                            return true;
                        }} catch (e) {{}}
                    }}
                    if (src.startsWith('http://') || src.startsWith('https://')) {{
                        try {{
                            const resp = await fetch(src, {{ mode: 'cors', credentials: 'omit' }});
                            if (resp.ok) {{
                                const b = await resp.blob();
                                const ext = extFromMime(b.type);
                                let dlName = opts.filename || ('ECHO_IMAGE_' + Date.now() + ext);
                                if (dlName.indexOf('.') === -1) dlName += ext;
                                triggerDownload(b, dlName);
                                flashButton(btnEl);
                                return true;
                            }}
                        }} catch (e) {{}}
                    }}
                }}
                let canvas;
                try {{
                    canvas = buildCanvas();
                }} catch (err) {{
                    showFallback(src, 'save');
                    return false;
                }}
                let dlName = opts.filename || ('ECHO_REPLAY_' + (isCropped ? 'Crop' : 'Full') + '_' + Date.now() + '.png');
                if (dlName.indexOf('.') === -1) dlName += '.png';
                return new Promise((resolve) => {{
                    try {{
                        canvas.toBlob((blob) => {{
                            if (!blob) {{ showFallback(src, 'save'); resolve(false); return; }}
                            triggerDownload(blob, dlName);
                            flashButton(btnEl);
                            resolve(true);
                        }}, 'image/png');
                    }} catch (err) {{
                        showFallback(src, 'save');
                        resolve(false);
                    }}
                }});
            }}

            // Mode 'copy' (Presse-papier W3C ClipboardItem image/png)
            if (!navigator.clipboard || !window.ClipboardItem) {{
                try {{
                    const c = buildCanvas();
                    showFallback(c.toDataURL('image/png'), 'copy');
                }} catch (err) {{
                    showFallback(src, 'copy');
                }}
                return false;
            }}

            if (!isCropped && src.startsWith('blob:')) {{
                try {{
                    const resp = await fetch(src);
                    const b = await resp.blob();
                    if (b && b.type === 'image/png') {{
                        await navigator.clipboard.write([new ClipboardItem({{ 'image/png': b }})]);
                        flashButton(btnEl);
                        return true;
                    }}
                }} catch (e) {{}}
            }}

            if (!isCropped && src.startsWith('data:image/png;base64,')) {{
                const parsed = parseDataUri(src);
                if (parsed) {{
                    try {{
                        await navigator.clipboard.write([new ClipboardItem({{ 'image/png': parsed.blob }})]);
                        flashButton(btnEl);
                        return true;
                    }} catch (err) {{
                        showFallback(src, 'copy');
                        return false;
                    }}
                }}
            }}

            let canvas;
            try {{
                canvas = buildCanvas();
            }} catch (err) {{
                showFallback(src, 'copy');
                return false;
            }}
            return new Promise((resolve) => {{
                try {{
                    canvas.toBlob(async (blob) => {{
                        if (!blob) {{ showFallback(src, 'copy'); resolve(false); return; }}
                        try {{
                            await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                            flashButton(btnEl);
                            resolve(true);
                        }} catch (err) {{
                            try {{
                                showFallback(canvas.toDataURL('image/png'), 'copy');
                            }} catch (e2) {{
                                showFallback(src, 'copy');
                            }}
                            resolve(false);
                        }}
                    }}, 'image/png');
                }} catch (err) {{
                    showFallback(src, 'copy');
                    resolve(false);
                }}
            }});
        }};
        """

    @staticmethod
    def calculate_context_delta(
        current: Optional[Dict[str, Any]],
        previous: Optional[Dict[str, Any]],
        max_context_size: int = ECHO_MAX_CONTEXT_SIZE
    ) -> Dict[str, Any]:
        """Calcule le différentiel unitaire de tokens et l'impact capacitaire entre deux tours."""
        cur = current or {}
        prev = previous or {}

        c_prompt = int(cur.get("promptTokenCount", 0))
        c_candidates = int(cur.get("candidatesTokenCount", 0))
        c_cache = int(cur.get("cachedContentTokenCount", 0))
        c_total = int(cur.get("totalTokenCount", 0))

        p_prompt = int(prev.get("promptTokenCount", 0))
        p_candidates = int(prev.get("candidatesTokenCount", 0))
        p_cache = int(prev.get("cachedContentTokenCount", 0))
        p_total = int(prev.get("totalTokenCount", 0))

        is_first_turn = bool(prev.get("is_first_turn")) or (p_total == 0 and c_total > 0 and bool(prev))
        has_prev = p_total > 0 or is_first_turn

        delta_total = (c_total - p_total) if has_prev else 0
        delta_prompt = (c_prompt - p_prompt) if has_prev else 0
        delta_candidates = (c_candidates - p_candidates) if has_prev else 0
        delta_cache = (c_cache - p_cache) if has_prev else 0

        max_size = max(1, int(max_context_size or ECHO_MAX_CONTEXT_SIZE))
        capacity_lost_pct = (delta_total / max_size) * 100.0 if has_prev else 0.0

        if not is_first_turn and has_prev and p_prompt > 0:
            growth_pct = (delta_prompt / p_prompt) * 100.0
        elif not is_first_turn and has_prev and p_total > 0:
            growth_pct = (delta_total / p_total) * 100.0
        else:
            growth_pct = 0.0

        p_q5 = prev.get("quota_5h_fraction")
        c_q5 = cur.get("quota_5h_fraction")
        delta_q5 = round((float(c_q5) - float(p_q5)) * 100.0, 1) if (p_q5 is not None and c_q5 is not None) else None

        p_qw = prev.get("quota_weekly_fraction")
        c_qw = cur.get("quota_weekly_fraction")
        delta_qw = round((float(c_qw) - float(p_qw)) * 100.0, 1) if (p_qw is not None and c_qw is not None) else None

        return {
            "has_delta": has_prev,
            "delta_total": delta_total,
            "delta_prompt": delta_prompt,
            "delta_candidates": delta_candidates,
            "delta_cache": delta_cache,
            "capacity_lost_pct": round(capacity_lost_pct, 3),
            "growth_pct": round(growth_pct, 1),
            "delta_quota_5h": delta_q5,
            "delta_quota_weekly": delta_qw,
            "is_first_turn": is_first_turn,
        }

    @staticmethod
    async def deploy_context_gauge(
            events: Any,
            plan_name: str = "",
            credits_val: str = "N/A",
            quota_str: str = "",
            c_t: int = 0,
            active_p_t: int = 0,
            g_t: int = 0,
            max_t: int = ECHO_MAX_CONTEXT_SIZE,
            cache_pct: float = 0.0,
            prompt_pct: float = 0.0,
            gen_pct: float = 0.0,
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
            quota_rpm_lim: str = "N/A",
            quota_5h_fraction: Optional[float] = None,
            quota_5h_reset: str = "N/A",
            quota_5h_disabled: bool = False,
            quota_weekly_fraction: Optional[float] = None,
            quota_weekly_reset: str = "N/A",
            delta: Optional[Dict[str, Any]] = None,
            compaction_count: int = 0,
            compacted_turns: int = 0,
            is_pending: bool = False,
            chat_id: Optional[str] = None):
        """Déploie ou met à jour le HUD contextuel ECHO de façon unifiée et non-bloquante."""
        payload = {
            "chat_id": chat_id or (getattr(events, "chat_id", None) if events else None),
            "tenant_id": ECHO_GLOBAL_TENANT_PROJECT_ID,
            "plan_name": plan_name,
            "credits_val": credits_val,
            "quota_str": quota_str,
            "cachedContentTokenCount": c_t,
            "promptTokenCount": (c_t + active_p_t),
            "candidatesTokenCount": g_t,
            "totalTokenCount": (c_t + active_p_t + g_t),
            "max_tokens": max_t,
            "user_email": user_email,
            "user_tier": user_tier,
            "project_id": project_id,
            "auth_sources": auth_sources or [],
            "quota_fraction": quota_fraction,
            "quota_reset": quota_reset,
            "quota_type": quota_type,
            "quota_model": quota_model,
            "quota_rpd_rem": quota_rpd_rem,
            "quota_rpd_lim": quota_rpd_lim,
            "quota_rpm_rem": quota_rpm_rem,
            "quota_rpm_lim": quota_rpm_lim,
            "quota_5h_fraction": quota_5h_fraction,
            "quota_5h_reset": quota_5h_reset,
            "quota_5h_disabled": quota_5h_disabled,
            "quota_weekly_fraction": quota_weekly_fraction,
            "quota_weekly_reset": quota_weekly_reset,
            "delta": delta or {},
            "compaction_count": compaction_count,
            "compacted_turns": compacted_turns,
            "is_pending": is_pending,
        }

        # Diffusion SSE immédiate via EchoWindowManager
        try:
            from echo_window_manager import get_window_manager
            uid = getattr(events, "user_id", None) or "system"
            await get_window_manager().broadcast(uid, {
                "type": "echo_context_gauge",
                "chat_id": payload.get("chat_id"),
                "payload": payload,
            })
        except Exception:
            pass

    @classmethod
    def image_viewer(
            cls,
            img_url: str,
            title: str = "Aperçu Image",
            filename: str = "",
            is_remote_fallback: bool = False,
            gallery: Optional[List[Dict[str, Any]]] = None) -> HTMLResponse:
        """Génère une Iframe inline responsive et auto-adaptative pour l'affichage d'images (locales B64 ou distantes) avec outils de copie, d'export et galerie multi-essais."""
        img_url_j = std_json.dumps(img_url or "").decode("utf-8")
        fname_j = std_json.dumps(filename or "").decode("utf-8")
        gallery_j = std_json.dumps(gallery or []).decode("utf-8")
        safe_title = (
            (title or "Aperçu Image")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        copy_icon = cls.get_icon("Copy", size=15)
        export_icon = cls.get_icon("Export", size=15)
        max_icon = cls.get_icon("Maximize", size=14)
        media_export_js = cls.get_media_export_js()
        fallback_badge = (
            '<span id="echo-iv-badge-mode" style="font-size:10px; padding:2px 7px; border-radius:999px; '
            'background:rgba(245,158,11,0.15); color:#f59e0b; border:1px solid rgba(245,158,11,0.3); '
            'white-space:nowrap;">Lien direct</span>'
            if is_remote_fallback
            else ""
        )
        has_gallery = bool(gallery and len(gallery) > 1)
        gallery_bar_html = (
            '<div id="echo-iv-gallery-bar" style="display:flex; align-items:center; gap:6px; padding:6px 12px; '
            'border-bottom:1px solid var(--echo-hud-border); background:var(--echo-bg); flex-wrap:wrap;"></div>'
            if has_gallery
            else ""
        )

        content = f"""
    <style>
      #echo-iv-img {{
        display: block;
        width: 100%;
        max-width: 100%;
        height: auto;
        object-fit: contain;
        border-radius: 4px;
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.18);
        transform-origin: center center;
        user-select: none;
        -webkit-user-drag: none;
      }}
    </style>
    <div id="echo-iv-root">
      <div id="hud-bar">
        <div class="echo-iv-title-group">
          <span style="font-size:15px;">🖼️</span>
          <span class="echo-iv-title" title="{safe_title}">{safe_title}</span>
          <span id="echo-iv-dims" class="echo-iv-dims" style="display:none;"></span>
          {fallback_badge}
        </div>
        <div class="echo-iv-actions">
          <button id="echo-iv-btn-fit" class="echo-iv-btn" title="Basculer Ajuster au cadre / Taille réelle (1:1)">
            {max_icon} <span id="echo-iv-fit-label">1:1</span>
          </button>
          <button id="echo-iv-btn-fs" class="echo-iv-btn" title="Afficher en plein écran maximisé">
            {max_icon} <span>Plein écran</span>
          </button>
          <button id="echo-iv-btn-copy" class="echo-iv-btn" title="Copier l'image dans le presse-papier (Qualité native)">
            {copy_icon} <span>Copier</span>
          </button>
          <button id="echo-iv-btn-export" class="echo-iv-btn" title="Télécharger l'image originale sur le PC">
            {export_icon} <span>Exporter</span>
          </button>
        </div>
      </div>
      {gallery_bar_html}
      <div id="canvas-area">
        <img id="echo-iv-img" alt="{safe_title}" />
        <div id="echo-iv-error" style="display:none; flex-direction:column; align-items:center; gap:8px; padding:24px; text-align:center; color:var(--echo-hud-text);">
          <span style="font-size:24px;">⚠️</span>
          <span style="font-size:13px; font-weight:600; color:var(--echo-text);">Impossible de charger l'image distante dans le cadre.</span>
          <a id="echo-iv-ext-link" target="_blank" rel="noopener noreferrer" style="font-size:12px; color:var(--echo-accent); text-decoration:underline;">Ouvrir le lien dans un nouvel onglet</a>
        </div>
      </div>
    </div>
    <script>
      (function() {{
        {media_export_js}
        let IMG_SRC = {img_url_j};
        let FILENAME = {fname_j};
        const GALLERY = {gallery_j};
        const img = document.getElementById('echo-iv-img');
        const area = document.getElementById('canvas-area');
        const dimsEl = document.getElementById('echo-iv-dims');
        const errEl = document.getElementById('echo-iv-error');
        const extLink = document.getElementById('echo-iv-ext-link');
        const btnFit = document.getElementById('echo-iv-btn-fit');
        const fitLabel = document.getElementById('echo-iv-fit-label');
        const btnCopy = document.getElementById('echo-iv-btn-copy');
        const btnExport = document.getElementById('echo-iv-btn-export');
        const galleryBar = document.getElementById('echo-iv-gallery-bar');

        const _echoAssetUrlCache = new Map();
        const _echoAssetPending = new Map();
        let _echoAssetSeq = 0;
        let _loadSeq = 0;

        window.addEventListener('message', function(ev) {{
          const data = ev && ev.data;
          if (!data || typeof data !== 'object' || !data.reqId) return;
          const entry = _echoAssetPending.get(data.reqId);
          if (!entry) return;
          if (data.type === 'echo:asset-blob') {{
            clearTimeout(entry.timer);
            _echoAssetPending.delete(data.reqId);
            if (data.blob) {{
              try {{
                const blobUrl = URL.createObjectURL(data.blob);
                _echoAssetUrlCache.set(entry.rawSrc, blobUrl);
                entry.resolve(blobUrl);
              }} catch (err) {{
                entry.reject(err);
              }}
            }} else {{
              entry.reject(new Error('Blob echo-asset manquant'));
            }}
          }} else if (data.type === 'echo:asset-error') {{
            clearTimeout(entry.timer);
            _echoAssetPending.delete(data.reqId);
            entry.reject(new Error(data.error || 'Erreur chargement asset'));
          }}
        }});

        window.addEventListener('pagehide', function() {{
          _echoAssetUrlCache.forEach(function(url) {{
            try {{ URL.revokeObjectURL(url); }} catch (e) {{}}
          }});
          _echoAssetUrlCache.clear();
        }});

        function resolveEchoAssetUrl(rawSrc) {{
          if (!rawSrc || !rawSrc.startsWith('echo-asset://')) {{
            return Promise.resolve(rawSrc);
          }}
          if (_echoAssetUrlCache.has(rawSrc)) {{
            return Promise.resolve(_echoAssetUrlCache.get(rawSrc));
          }}
          return new Promise(function(resolve, reject) {{
            const reqId = 'ea_' + (++_echoAssetSeq) + '_' + Date.now();
            const timer = setTimeout(function() {{
              _echoAssetPending.delete(reqId);
              reject(new Error('Timeout echo-asset'));
            }}, 15000);
            _echoAssetPending.set(reqId, {{ resolve: resolve, reject: reject, timer: timer, rawSrc: rawSrc }});
            try {{
              window.parent.postMessage({{ type: 'echo:fetch-asset', reqId: reqId, asset: rawSrc.slice(13) }}, '*');
            }} catch (err) {{
              clearTimeout(timer);
              _echoAssetPending.delete(reqId);
              reject(err);
            }}
          }});
        }}

        function applyImageSource(rawSrc) {{
          const seq = ++_loadSeq;
          img.style.display = 'block';
          errEl.style.display = 'none';
          resolveEchoAssetUrl(rawSrc).then(function(resolved) {{
            if (seq !== _loadSeq) return;
            img.src = resolved;
          }}).catch(function() {{
            if (seq !== _loadSeq) return;
            if (typeof img.onerror === 'function') img.onerror();
          }});
        }}

        let fitMode = true;
        let zoom = 1.0;
        let panX = 0, panY = 0;
        let isDragging = false, startX = 0, startY = 0;
        let isTouchDragging = false, touchStartX = 0, touchStartY = 0;
        let isPinching = false, pinchStartDist = 0, pinchStartZoom = 1.0;
        let pinchCenterStart = {{ x: 0, y: 0 }}, pinchPanStart = {{ x: 0, y: 0 }};
        let lastTapTime = 0, tapStartPos = {{ x: 0, y: 0 }};

        function updateLayout() {{
          const isFullscreen = document.documentElement.classList.contains('echo-is-fullscreen');
          const natW = img.naturalWidth || 0;
          const natH = img.naturalHeight || 0;
          if (!natW || !natH) return;

          const areaW = Math.max(100, (area.clientWidth || document.body.clientWidth || 640) - 16);

          if (fitMode) {{
            area.style.touchAction = '';
            img.style.touchAction = '';
            zoom = 1.0;
            panX = 0;
            panY = 0;
            area.style.cursor = 'zoom-in';

            if (isFullscreen) {{
              disableAutoResize = true;
              const hudH = (document.getElementById('hud-bar') ? document.getElementById('hud-bar').offsetHeight : 46) + (galleryBar ? galleryBar.offsetHeight : 0);
              const availH = Math.max(120, (window.innerHeight || 800) - hudH);
              area.style.height = availH + 'px';
              img.style.width = '100%';
              img.style.height = '100%';
              img.style.maxWidth = '100%';
              img.style.maxHeight = '100%';
              img.style.objectFit = 'contain';
              img.style.transform = 'none';
              fitLabel.textContent = '1:1';
            }} else {{
              disableAutoResize = false;
              img.style.width = '100%';
              img.style.maxWidth = '100%';
              img.style.height = 'auto';
              img.style.maxHeight = 'none';
              img.style.objectFit = 'contain';
              img.style.transform = 'none';
              fitLabel.textContent = '1:1';

              const scaledH = Math.round(areaW * (natH / natW));
              const targetH = Math.max(60, scaledH + 16);
              const prevH = area.style.height;
              area.style.height = targetH + 'px';

              if (prevH !== area.style.height && typeof reportHeight === 'function') {{
                setTimeout(reportHeight, 30);
              }}
            }}
          }} else {{
            area.style.touchAction = 'none';
            img.style.touchAction = 'none';
            disableAutoResize = true;
            area.style.cursor = isDragging ? 'grabbing' : 'grab';
            if (isFullscreen) {{
              const hudH = (document.getElementById('hud-bar') ? document.getElementById('hud-bar').offsetHeight : 46) + (galleryBar ? galleryBar.offsetHeight : 0);
              area.style.height = Math.max(120, (window.innerHeight || 800) - hudH) + 'px';
            }}
            img.style.width = natW + 'px';
            img.style.maxWidth = 'none';
            img.style.height = natH + 'px';
            img.style.maxHeight = 'none';
            img.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`;
            fitLabel.textContent = 'Ajuster';
          }}
        }}

        img.onload = function() {{
          const natW = img.naturalWidth || 0;
          const natH = img.naturalHeight || 0;
          if (natW && natH) {{
            dimsEl.textContent = natW + ' × ' + natH + ' px';
            dimsEl.style.display = 'inline-block';
          }}
          updateLayout();
        }};

        img.onerror = function() {{
          img.style.display = 'none';
          errEl.style.display = 'flex';
          if (extLink && (IMG_SRC.startsWith('http://') || IMG_SRC.startsWith('https://'))) {{
            extLink.href = IMG_SRC;
          }} else if (extLink) {{
            extLink.style.display = 'none';
          }}
          area.style.height = '180px';
          if (typeof reportHeight === 'function') setTimeout(reportHeight, 30);
        }};

        function toggleFitMode() {{
          fitMode = !fitMode;
          if (!fitMode) {{
            zoom = 1.0;
            panX = 0;
            panY = 0;
          }}
          updateLayout();
        }}

        btnFit.onclick = toggleFitMode;
        img.ondblclick = toggleFitMode;

        area.addEventListener('wheel', function(e) {{
          if (!e.ctrlKey && fitMode) return;
          e.preventDefault();
          const natW = img.naturalWidth || 0;
          const natH = img.naturalHeight || 0;
          if (fitMode && natW && natH) {{
            const areaW = Math.max(100, (area.clientWidth || document.body.clientWidth || 640) - 16);
            zoom = areaW / natW;
            panX = 0;
            panY = 0;
            fitMode = false;
          }}
          const factor = e.deltaY < 0 ? 1.12 : 0.89;
          zoom = Math.max(0.1, Math.min(10.0, zoom * factor));
          updateLayout();
        }}, {{ passive: false }});

        area.addEventListener('mousedown', function(e) {{
          if (fitMode || e.button !== 0) return;
          e.preventDefault();
          isDragging = true;
          startX = e.clientX - panX;
          startY = e.clientY - panY;
          area.style.cursor = 'grabbing';
        }});

        window.addEventListener('mousemove', function(e) {{
          if (!isDragging) return;
          panX = e.clientX - startX;
          panY = e.clientY - startY;
          img.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`;
        }});

        window.addEventListener('mouseup', function() {{
          if (isDragging) {{
            isDragging = false;
            if (!fitMode) area.style.cursor = 'grab';
          }}
        }});

        function getTouchDist(t1, t2) {{
          return Math.hypot(t1.clientX - t2.clientX, t1.clientY - t2.clientY);
        }}

        function getTouchCenter(t1, t2) {{
          return {{
            x: (t1.clientX + t2.clientX) / 2,
            y: (t1.clientY + t2.clientY) / 2
          }};
        }}

        area.addEventListener('touchstart', function(e) {{
          if (fitMode) {{
            if (e.touches.length === 1) {{
              const now = Date.now();
              const t = e.touches[0];
              const dist = Math.hypot(t.clientX - tapStartPos.x, t.clientY - tapStartPos.y);
              if (now - lastTapTime < 300 && dist < 12) {{
                e.preventDefault();
                toggleFitMode();
                lastTapTime = 0;
                return;
              }}
              lastTapTime = now;
              tapStartPos = {{ x: t.clientX, y: t.clientY }};
            }}
            return;
          }}
          e.preventDefault();
          if (e.touches.length === 1) {{
            isTouchDragging = true;
            isPinching = false;
            touchStartX = e.touches[0].clientX - panX;
            touchStartY = e.touches[0].clientY - panY;
            const now = Date.now();
            const t = e.touches[0];
            const dist = Math.hypot(t.clientX - tapStartPos.x, t.clientY - tapStartPos.y);
            if (now - lastTapTime < 300 && dist < 12) {{
              toggleFitMode();
              lastTapTime = 0;
              return;
            }}
            lastTapTime = now;
            tapStartPos = {{ x: t.clientX, y: t.clientY }};
          }} else if (e.touches.length >= 2) {{
            isTouchDragging = false;
            isPinching = true;
            pinchStartDist = getTouchDist(e.touches[0], e.touches[1]);
            pinchStartZoom = zoom;
            pinchPanStart = {{ x: panX, y: panY }};
            pinchCenterStart = getTouchCenter(e.touches[0], e.touches[1]);
          }}
        }}, {{ passive: false }});

        window.addEventListener('touchmove', function(e) {{
          if (fitMode || (!isTouchDragging && !isPinching)) return;
          e.preventDefault();
          if (e.touches.length === 1 && isTouchDragging) {{
            panX = e.touches[0].clientX - touchStartX;
            panY = e.touches[0].clientY - touchStartY;
            img.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`;
          }} else if (e.touches.length >= 2 && isPinching && pinchStartDist > 0) {{
            const curDist = getTouchDist(e.touches[0], e.touches[1]);
            const factor = curDist / pinchStartDist;
            const newZoom = Math.max(0.1, Math.min(10.0, pinchStartZoom * factor));
            const curCenter = getTouchCenter(e.touches[0], e.touches[1]);
            const rect = area.getBoundingClientRect();
            const ax = rect.left + rect.width / 2;
            const ay = rect.top + rect.height / 2;
            panX = curCenter.x - ax - (newZoom / pinchStartZoom) * (pinchCenterStart.x - ax - pinchPanStart.x);
            panY = curCenter.y - ay - (newZoom / pinchStartZoom) * (pinchCenterStart.y - ay - pinchPanStart.y);
            zoom = newZoom;
            img.style.transform = `translate(${{panX}}px, ${{panY}}px) scale(${{zoom}})`;
          }}
        }}, {{ passive: false }});

        function onTouchEnd(e) {{
          if (fitMode) return;
          if (e.touches.length === 1) {{
            isPinching = false;
            isTouchDragging = true;
            touchStartX = e.touches[0].clientX - panX;
            touchStartY = e.touches[0].clientY - panY;
          }} else if (e.touches.length === 0) {{
            isPinching = false;
            isTouchDragging = false;
            pinchStartDist = 0;
          }}
        }}
        window.addEventListener('touchend', onTouchEnd, {{ passive: false }});
        window.addEventListener('touchcancel', onTouchEnd, {{ passive: false }});

        btnCopy.onclick = function(e) {{
          e.stopPropagation();
          if (window.echoExportImage) {{
            window.echoExportImage({{ imgEl: img, mode: 'copy', btnEl: btnCopy, filename: FILENAME }});
          }}
        }};

        btnExport.onclick = function(e) {{
          e.stopPropagation();
          if (window.echoExportImage) {{
            window.echoExportImage({{ imgEl: img, mode: 'save', btnEl: btnExport, filename: FILENAME }});
          }}
        }};

        let lastW = area.clientWidth || 0;
        if (window.ResizeObserver) {{
          new ResizeObserver(function(entries) {{
            const w = entries[0] && entries[0].contentRect ? Math.round(entries[0].contentRect.width) : 0;
            if (w > 0 && Math.abs(w - lastW) > 4) {{
              lastW = w;
              if (fitMode) updateLayout();
            }}
          }}).observe(area);
        }}

        if (galleryBar && Array.isArray(GALLERY) && GALLERY.length > 1) {{
          let activeIdx = GALLERY.findIndex(function(g) {{ return g && g.best; }});
          if (activeIdx < 0) activeIdx = GALLERY.length - 1;
          function selectGalleryItem(idx) {{
            const item = GALLERY[idx];
            if (!item) return;
            activeIdx = idx;
            IMG_SRC = item.img_url || item.data_uri || IMG_SRC;
            FILENAME = item.filename || FILENAME;
            applyImageSource(IMG_SRC);
            const tabs = galleryBar.querySelectorAll('.echo-iv-tab-btn');
            for (let i = 0; i < tabs.length; i++) {{
              tabs[i].classList.toggle('is-active', i === idx);
            }}
          }}
          for (let i = 0; i < GALLERY.length; i++) {{
            const gItem = GALLERY[i];
            const tabBtn = document.createElement('button');
            tabBtn.className = 'echo-iv-btn echo-iv-tab-btn' + (i === activeIdx ? ' is-active' : '');
            const badge = gItem.best ? ' ★' : '';
            tabBtn.textContent = (gItem.label || ('Essai ' + (i + 1))) + badge;
            if (gItem.analysis) tabBtn.title = gItem.analysis;
            tabBtn.onclick = (function(index) {{
              return function(e) {{
                e.stopPropagation();
                selectGalleryItem(index);
              }};
            }})(i);
            galleryBar.appendChild(tabBtn);
          }}
          selectGalleryItem(activeIdx);
        }} else {{
          applyImageSource(IMG_SRC);
        }}

        window.addEventListener('resize', updateLayout);
      }})();
    </script>
    """
        html = cls._get_boilerplate(content, title)
        return HTMLResponse(
            content=html, headers={"Content-Disposition": "inline"}
        )

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
    def build_rich_view_html(
            cls,
            moteur: str,
            payload: str,
            title: str = "ECHO Rendu Visuel",
            cdn_timeout_ms: int = 30000) -> str:
        """Construit le document HTML autonome pour un moteur de visualisation riche."""
        from echo_visuals import VisualEngine
        cfg = VisualEngine.get_config(
            moteur, payload, cdn_timeout_ms=cdn_timeout_ms)

        safe_title = (
            (title or "ECHO Rendu Visuel")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
        safe_moteur = (moteur or "mermaid").upper()
        copy_icon = cls.get_icon("Copy", size=15)
        export_icon = cls.get_icon("Export", size=15)
        max_icon = cls.get_icon("Maximize", size=14)
        lock_icon = cls.get_icon("Lock", size=14)
        unlock_icon = cls.get_icon("Unlock", size=14)
        media_export_js = cls.get_media_export_js()
        moteur_j = std_json.dumps(moteur or "visual").decode("utf-8")
        lock_icon_j = std_json.dumps(lock_icon).decode("utf-8")
        unlock_icon_j = std_json.dumps(unlock_icon).decode("utf-8")

        styles_list = [s for s in cfg.get("scripts", []) if s.endswith('.css')]
        scripts_list = [
            s for s in cfg.get(
                "scripts",
                []) if not s.endswith('.css')]

        html_to_image_cdn = (
            "https://cdn.jsdelivr.net/npm/html-to-image@1.11.11/dist/html-to-image.js"
        )
        if html_to_image_cdn not in scripts_list:
            scripts_list.append(html_to_image_cdn)

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
    <script>
      (function() {{
        try {{
          const origGetContext = HTMLCanvasElement.prototype.getContext;
          HTMLCanvasElement.prototype.getContext = function(type, attribs) {{
            if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {{
              attribs = Object.assign({{}}, attribs, {{ preserveDrawingBuffer: true }});
            }}
            return origGetContext.call(this, type, attribs);
          }};
        }} catch(e) {{}}
      }})();
    </script>
    {scripts_html}

    <div id="echo-iv-root">
      <div id="hud-bar">
        <div class="echo-iv-title-group">
          <span style="font-size:15px;">📊</span>
          <span class="echo-iv-title" title="{safe_title}">{safe_title}</span>
          <span class="echo-iv-dims">{safe_moteur}</span>
        </div>
        <div class="echo-iv-actions">
          <button id="echo-rv-btn-lock" class="echo-iv-btn" title="Activer/Désactiver l'interaction souris et clavier dans le schéma">
            {lock_icon} <span id="echo-rv-lock-label">Verrouillé</span>
          </button>
          <button id="echo-iv-btn-fit" class="echo-iv-btn" title="Basculer Ajuster au cadre / Taille réelle (1:1)">
            {max_icon} <span id="echo-iv-fit-label">1:1</span>
          </button>
          <button id="echo-iv-btn-fs" class="echo-iv-btn" title="Afficher en plein écran maximisé">
            {max_icon} <span>Plein écran</span>
          </button>
          <button id="echo-iv-btn-copy" class="echo-iv-btn" title="Copier la vue en cours en PNG dans le presse-papier">
            {copy_icon} <span>Copier</span>
          </button>
          <button id="echo-iv-btn-export" class="echo-iv-btn" title="Télécharger la vue en cours en PNG">
            {export_icon} <span>Exporter</span>
          </button>
        </div>
      </div>
      <div id="canvas-area" style="padding:0; display:block; position:relative;">
        <div id="echo-rv-lock-shield" title="Interaction verrouillée (cliquez sur Verrouillé pour interagir)"></div>
        <div id="visual-stage" style="width:100%; height:100%; pointer-events:none; transform-origin:0 0;">
          {cfg.get('container', '<div id="visual-target"></div>')}
        </div>
      </div>
    </div>

    <script>
      (function() {{
        const recordErr = (msg) => {{
          if (!document.body) return;
          const prev = document.body.getAttribute('data-echo-visual-error') || '';
          const clean = String(msg || '').trim();
          if (clean && !prev.includes(clean)) {{
            document.body.setAttribute('data-echo-visual-error', (prev ? prev + ' | ' : '') + clean);
          }}
        }};
        const origErr = console.error;
        console.error = function(...args) {{
          recordErr(args.map(a => (a && a.message) ? a.message : String(a)).join(' '));
          origErr.apply(console, args);
        }};
        window.addEventListener('error', (e) => recordErr(e.message || e.error));
        window.addEventListener('unhandledrejection', (e) => recordErr(e.reason));
      }})();

      {media_export_js}

      (function() {{
        const area = document.getElementById('canvas-area');
        const shield = document.getElementById('echo-rv-lock-shield');
        const stage = document.getElementById('visual-stage');
        const btnLock = document.getElementById('echo-rv-btn-lock');
        const btnFit = document.getElementById('echo-iv-btn-fit');
        const fitLabel = document.getElementById('echo-iv-fit-label');
        const btnCopy = document.getElementById('echo-iv-btn-copy');
        const btnExport = document.getElementById('echo-iv-btn-export');
        const MOTEUR = {moteur_j};
        const lockIcon = {lock_icon_j};
        const unlockIcon = {unlock_icon_j};

        let interactiveMode = false;
        function setInteractiveMode(enabled) {{
          interactiveMode = !!enabled;
          if (!interactiveMode && stage && document.activeElement && stage.contains(document.activeElement)) {{
            try {{
              document.activeElement.blur();
            }} catch(err) {{}}
          }}
          if (shield) shield.style.display = interactiveMode ? 'none' : 'block';
          if (stage) stage.style.pointerEvents = interactiveMode ? 'auto' : 'none';
          if (btnLock) {{
            btnLock.classList.toggle('is-active', interactiveMode);
            btnLock.innerHTML = (interactiveMode ? unlockIcon : lockIcon) +
              ' <span id="echo-rv-lock-label">' + (interactiveMode ? 'Interactif' : 'Verrouillé') + '</span>';
            btnLock.title = interactiveMode
              ? "Interaction active (cliquez pour verrouiller)"
              : "Interaction verrouillée (cliquez pour interagir)";
          }}
        }}

        if (btnLock) {{
          btnLock.onclick = function(e) {{
            e.stopPropagation();
            setInteractiveMode(!interactiveMode);
          }};
        }}

        const blockKeyEvent = function(e) {{
          if (!interactiveMode && e.key !== 'Escape') {{
            e.stopPropagation();
            e.preventDefault();
          }}
        }};
        window.addEventListener('keydown', blockKeyEvent, true);
        window.addEventListener('keyup', blockKeyEvent, true);
        window.addEventListener('keypress', blockKeyEvent, true);

        let isFit11 = false;
        if (btnFit) {{
          btnFit.onclick = function(e) {{
            e.stopPropagation();
            isFit11 = !isFit11;
            btnFit.classList.toggle('is-active', isFit11);
            if (fitLabel) fitLabel.textContent = isFit11 ? 'Ajuster' : '1:1';
            if (stage) {{
              if (isFit11) {{
                stage.style.transform = 'scale(1.25)';
                stage.style.transformOrigin = 'top left';
              }} else {{
                stage.style.transform = 'none';
                stage.style.transformOrigin = '0 0';
              }}
            }}
            try {{
              window.dispatchEvent(new Event('resize'));
            }} catch(err) {{}}
          }};
        }}

        async function exportCurrentView(mode, btnEl) {{
          if (!window.htmlToImage || !window.htmlToImage.toPng || !area) return;
          try {{
            let bg = '#171717';
            try {{
              const cs = window.getComputedStyle(area);
              if (cs && cs.backgroundColor && cs.backgroundColor !== 'rgba(0, 0, 0, 0)' && cs.backgroundColor !== 'transparent') {{
                bg = cs.backgroundColor;
              }} else if (document.body) {{
                const bcs = window.getComputedStyle(document.body);
                if (bcs && bcs.backgroundColor && bcs.backgroundColor !== 'rgba(0, 0, 0, 0)' && bcs.backgroundColor !== 'transparent') {{
                  bg = bcs.backgroundColor;
                }}
              }}
            }} catch(e) {{}}

            const dataUrl = await window.htmlToImage.toPng(area, {{
              pixelRatio: Math.min(window.devicePixelRatio || 1, 2),
              skipFonts: true,
              backgroundColor: bg
            }});

            const tmpImg = new Image();
            tmpImg.onload = function() {{
              if (window.echoExportImage) {{
                window.echoExportImage({{
                  imgEl: tmpImg,
                  mode: mode,
                  btnEl: btnEl,
                  filename: 'ECHO_' + (MOTEUR || 'VISUAL').toUpperCase() + '_' + Date.now() + '.png'
                }});
              }}
            }};
            tmpImg.src = dataUrl;
          }} catch(err) {{
            console.error('Erreur exportCurrentView:', err);
          }}
        }}

        if (btnCopy) {{
          btnCopy.onclick = function(e) {{
            e.stopPropagation();
            exportCurrentView('copy', btnCopy);
          }};
        }}
        if (btnExport) {{
          btnExport.onclick = function(e) {{
            e.stopPropagation();
            exportCurrentView('save', btnExport);
          }};
        }}
      }})();

      {cfg.get('init', '')}
    </script>
    """
        return cls._get_boilerplate(content, title)

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
    # ECHO DOM SECURITY, MODALS & SANDBOX MONITOR
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
