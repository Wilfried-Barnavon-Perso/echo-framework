"""
title: ECHO Codex UI Rendering Engine
author: Wilfried BARNAVON
version: 5.228.6.2
description: Composant système interne : Moteur de rendu HUD Monaco pour ECHO Codex.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.228.6.2: Extraction et factorisation du moteur HUD Monaco Codex depuis echo_ui.py vers echo_codex_ui.py (EchoCodexUI héritant de EchoUI).


import sys

# Importations ECHO Standard
if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_ui import EchoUI


class EchoCodexUI(EchoUI):
    """Moteur de pilotage HUD Monaco Editor pour ECHO Codex, héritant des primitives EchoUI."""

    @classmethod
    def _generate_codex_js(
            cls,
            files_json: str,
            quick_actions_json: str,
            workspaces_json: str,
            current_workspace: str,
            chat_id: str,
            message_id: str = "",
            model_id: str = "echo_orchestrator") -> str:
        """Génère le script JS complet du HUD Monaco Codex.
        Injection via __event_call__({type: 'execute', data: {code: ...}})."""
        return f"""
    (function() {{
      const CODEX_ID = 'echo-codex-hud';
      {cls.get_mobile_guard_js("echo-codex-hud")}
      const CID = '{chat_id}';
      const MID = '{message_id}';
      const MODEL_ID = '{model_id}';
      const STATE_KEY = 'echo_codex_' + CID;
      const MONACO_CDN = 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min';

      if (window._echoCodexPingInterval) {{
        clearInterval(window._echoCodexPingInterval);
        window._echoCodexPingInterval = null;
      }}
      if (window._echoCodexVisHandler) {{
        document.removeEventListener('visibilitychange', window._echoCodexVisHandler);
        window._echoCodexVisHandler = null;
      }}

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
      const EXT_TO_LANG = {{
        '.py':'python', '.js':'javascript', '.jsx':'javascript', '.ts':'typescript', '.tsx':'typescript',
        '.c':'c', '.h':'c', '.cpp':'cpp', '.hpp':'cpp', '.java':'java', '.go':'go', '.rs':'rust',
        '.rb':'ruby', '.php':'php', '.swift':'swift', '.kt':'kotlin', '.cs':'csharp', '.vb':'vb',
        '.sh':'shell', '.bash':'shell', '.ps1':'powershell', '.bat':'bat', '.cmd':'bat',
        '.html':'html', '.htm':'html', '.css':'css', '.json':'json', '.xml':'xml', '.svg':'xml',
        '.yaml':'yaml', '.yml':'yaml', '.toml':'toml', '.ini':'ini', '.md':'markdown', '.txt':'plaintext',
        '.sql':'sql', '.r':'r', '.lua':'lua', '.pl':'perl', '.dockerfile':'dockerfile', '.pdf':'pdf'
      }};
      function detectFileLang(filename) {{
        if (!filename) return 'plaintext';
        const found = files.find(f => f.filename === filename);
        if (found && found.lang && found.lang !== 'folder') return found.lang;
        const lower = filename.toLowerCase();
        if (lower.endsWith('dockerfile')) return 'dockerfile';
        const dotIdx = lower.lastIndexOf('.');
        if (dotIdx !== -1) {{
          const ext = lower.substring(dotIdx);
          if (EXT_TO_LANG[ext]) return EXT_TO_LANG[ext];
        }}
        return 'plaintext';
      }}
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
      const firstFileEntry = files.find(f => f.type !== 'directory');
      let currentFile = targetFileOverride || (firstFileEntry ? firstFileEntry.filename : null);
      let editor = null;
      let pendingContent = null;
      let diffEditor = null;
      let isDiffMode = false;
      let isHistoryMode = false;
      let historyContent = null;
      let lastInstruction = '';
      let lastModel = 'MODEL_APOLLO';
      let modified = false;
      let previewOpen = false;
      let editorRatio = 33;
      let previewRatio = 67;
      const PREVIEW_LANGS = ['markdown', 'html', 'css', 'xml', 'pdf'];
      let markedLoaded = false;
      let previewDebounceTimer = null;

      // --- COMMUNICATION PROXY & AUTO-RECONNECT RPC (Standby Resilience) ---
      let actionQueue = [];
      let inFlightPayload = null;
      let inFlightWatchdog = null;
      let waitResolveTimer = null;
      let isReconnecting = false;

      async function reconnectCodexBackend() {{
        if (isReconnecting || !document.getElementById(CODEX_ID)) return;
        isReconnecting = true;
        if (typeof updateStatus === 'function') updateStatus('🔄 Reconnexion au Codex...');
        try {{
          const token = localStorage.token ? localStorage.token.replace(/^"|"$/g, '') : '';
          let msgId = MID;
          if (!msgId) {{
            const msgs = document.querySelectorAll('[id^="message-"]');
            for (let i = msgs.length - 1; i >= 0; i--) {{
              const rawId = msgs[i].id.replace(/^message-/, '');
              if (rawId && !rawId.includes('input') && !rawId.includes('edit') && !rawId.includes('feedback')) {{
                msgId = rawId;
                break;
              }}
            }}
          }}
          await fetch('/api/chat/actions/echo_codex_action', {{
            method: 'POST',
            headers: {{
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${{token}}`
            }},
            body: JSON.stringify({{
              chat_id: CID,
              model: MODEL_ID || 'echo_orchestrator',
              id: msgId || 'codex-reconnect',
              session_id: '',
              messages: [],
              reconnect_only: true,
              workspace: currentWorkspace
            }})
          }});
        }} catch (e) {{
          console.error('[ECHO Codex] Reconnect error:', e);
        }} finally {{
          setTimeout(() => {{ isReconnecting = false; }}, 2500);
        }}
      }}

      function flushCodexQueue() {{
        if (actionQueue.length === 0) return;
        if (typeof window.echoCodexResolve === 'function') {{
          if (waitResolveTimer) {{ clearTimeout(waitResolveTimer); waitResolveTimer = null; }}
          const resolveFn = window.echoCodexResolve;
          window.echoCodexResolve = null;
          const payload = actionQueue.shift();
          inFlightPayload = payload;
          resolveFn(payload);

          if (inFlightWatchdog) clearTimeout(inFlightWatchdog);
          if (payload && payload.action !== 'close' && payload.action !== 'ai_edit') {{
            inFlightWatchdog = setTimeout(() => {{
              if (typeof window.echoCodexResolve !== 'function') {{
                if (inFlightPayload && inFlightPayload.action !== 'ping') {{
                  actionQueue.unshift(inFlightPayload);
                }} else if (currentFile) {{
                  actionQueue.push({{ action: 'load_file', filename: currentFile }});
                }}
                inFlightPayload = null;
                reconnectCodexBackend();
              }}
            }}, 2000);
          }}
        }} else {{
          if (!waitResolveTimer) {{
            waitResolveTimer = setTimeout(() => {{
              waitResolveTimer = null;
              if (typeof window.echoCodexResolve !== 'function' && actionQueue.length > 0) {{
                reconnectCodexBackend();
              }}
            }}, 1000);
          }}
        }}
      }}

      window._echoCodexOnReady = function() {{
        if (inFlightWatchdog) {{ clearTimeout(inFlightWatchdog); inFlightWatchdog = null; }}
        if (waitResolveTimer) {{ clearTimeout(waitResolveTimer); waitResolveTimer = null; }}
        inFlightPayload = null;
        isReconnecting = false;
        flushCodexQueue();
      }};

      window.sendCodexAction = function(payload) {{
        if (!payload) return;
        if (payload.action === 'ping') {{
          if (actionQueue.length > 0 || inFlightPayload) return;
        }} else {{
          actionQueue = actionQueue.filter(p => p.action !== 'ping');
        }}
        actionQueue.push(payload);
        flushCodexQueue();
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
      {cls.get_custom_modals_js()}

      // --- HUD Container ---
      const hud = document.createElement('div');
      hud.id = CODEX_ID;
      hud.setAttribute('data-chat-id', CID);
      hud.style.cssText = `position:fixed; z-index:10001; display:flex; flex-direction:column;
        background:${{bgColor}}; border:1px solid ${{borderColor}}; border-radius:12px;
        box-shadow:0 20px 60px rgba(0,0,0,0.4); font-family:'Segoe UI',system-ui,sans-serif;
        color:${{textColor}}; overflow:hidden; resize:both; min-width:600px; min-height:400px;
        width:${{savedState.w || '900px'}}; height:${{savedState.h || '600px'}};
        top:${{savedState.y || '60px'}}; left:${{savedState.x || '50%'}};
        ${{savedState.x ? '' : 'transform:translateX(-50%);'}}`;

      // --- HEADER (draggable) ---
      const header = document.createElement('div');
      header.id = CODEX_ID + '-header';
      header.style.cssText = `display:flex; align-items:center; padding:8px 12px; gap:8px;
        background:${{headerBg}}; border-bottom:1px solid ${{borderColor}}; cursor:move;
        user-select:none; flex-shrink:0;`;
      header.innerHTML = `
        <span style="font-weight:600; font-size:14px;">📝 ECHO Codex</span>
        <span style="flex:1;"></span>
        <select id="${{CODEX_ID}}-lang" style="background:transparent; border:1px solid ${{borderColor}};
          color:${{textColor}}; padding:2px 6px; border-radius:4px; font-size:12px;"></select>
        <button id="${{CODEX_ID}}-import" title="Importer (PC → Codex)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='17 8 12 3 7 8'/><line x1='12' y1='3' x2='12' y2='15'/></svg></button>
        <button id="${{CODEX_ID}}-export" title="Exporter (Codex → PC)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='7 10 12 15 17 10'/><line x1='12' y1='15' x2='12' y2='3'/></svg></button>
        <button id="${{CODEX_ID}}-copy" title="Copier" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='9' y='9' width='13' height='13' rx='2' ry='2'/><path d='M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'/></svg></button>
        <button id="${{CODEX_ID}}-refresh" title="Actualiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='23 4 23 10 17 10'/><polyline points='1 20 1 14 7 14'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/></svg></button>
        <button id="${{CODEX_ID}}-save" title="Sauvegarder (Ctrl+S)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1; opacity:0.3; transition:opacity 0.2s;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z'/><polyline points='17 21 17 13 7 13 7 21'/><polyline points='7 3 7 8 15 8'/></svg></button>
        <button id="${{CODEX_ID}}-preview-toggle" title="Prévisualisation" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px; opacity:0.4;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z'/><circle cx='12' cy='12' r='3'/></svg></button>
        <button id="${{CODEX_ID}}-fullscreen" title="Plein écran" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3'/></svg></button>
        <button id="${{CODEX_ID}}-minimize" title="Minimiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:16px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='5' y1='12' x2='19' y2='12'/></svg></button>
        <button id="${{CODEX_ID}}-close" title="Fermer" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:18px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><line x1='18' y1='6' x2='6' y2='18'/><line x1='6' y1='6' x2='18' y2='18'/></svg></button>`;
      hud.appendChild(header);

      // --- BODY (sidebar + editor) ---
      const body = document.createElement('div');
      body.id = CODEX_ID + '-body';
      body.style.cssText = 'display:flex; flex:1; overflow:hidden;';

      // Sidebar (file tree)
      const sidebar = document.createElement('div');
      sidebar.id = CODEX_ID + '-sidebar';
      sidebar.style.cssText = `width:${{sidebarWidth}}px; background:${{sidebarBg}}; border-right:1px solid ${{borderColor}};
        overflow-y:auto; flex-shrink:0; display:flex; flex-direction:column; padding:6px 0;`;

      // Sidebar Splitter
      const sidebarSplitter = document.createElement('div');
      sidebarSplitter.id = CODEX_ID + '-sidebar-splitter';
      sidebarSplitter.style.cssText = `width:5px; cursor:col-resize; background:${{borderColor}}; flex-shrink:0; transition:background 0.15s;`;
      sidebarSplitter.onmouseenter = () => sidebarSplitter.style.background = accentColor;
      sidebarSplitter.onmouseleave = () => sidebarSplitter.style.background = borderColor;

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
          <button id="${{CODEX_ID}}-preview-copy" title="Copier le rendu" style="background:none; border:none; color:${{textColor}}; cursor:pointer; padding:0; display:none; align-items:center; opacity:0.8; transition:opacity 0.2s;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.8">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          </button>
          <button id="${{CODEX_ID}}-preview-print" title="Print / PDF" style="background:none; border:none; color:${{textColor}}; cursor:pointer; padding:0; display:none; align-items:center; opacity:0.8; transition:opacity 0.2s;" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.8">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="12" y1="12" x2="12" y2="18"/><polyline points="9 15 12 18 15 15"/></svg>
          </button>
          <span id="${{CODEX_ID}}-preview-label" style="flex:1;">Preview</span>
        </div>
        <div id="${{CODEX_ID}}-preview-content" style="flex:1; padding:12px; overflow:auto; font-size:14px; line-height:1.6; min-width:0;"></div>
      `;

      body.appendChild(sidebar);
      body.appendChild(sidebarSplitter);
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
            <option value="MODEL_APOLLO" selected>Apollo</option>
            <option value="MODEL_PROMETHEUS">Prometheus</option>
            <option value="MODEL_ZEPHYR">Zephyr</option>
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
        <button id="${{CODEX_ID}}-hist-prev" title="Version pr\u00e9c\u00e9dente" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='15 18 9 12 15 6'/></svg></button>
        <span id="${{CODEX_ID}}-status-text" style="flex:1; color:${{isDark ? '#a6adc8' : '#666'}};">Pr\u00eat</span>
        <button id="${{CODEX_ID}}-hist-next" title="Version suivante" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='9 18 15 12 9 6'/></svg></button>
        <div id="${{CODEX_ID}}-hist-actions" style="display:none; gap:6px;">
          <button id="${{CODEX_ID}}-hist-pin" style="background:none; border:1px solid ${{borderColor}}; color:${{textColor}}; cursor:pointer; padding:1px 8px; border-radius:4px; font-size:11px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M6 8l4 4-4 4M18 8l-4 4 4 4M12 4v16'/></svg> Revenir au pr\u00e9sent</button>
          <button id="${{CODEX_ID}}-hist-restore" style="background:none; border:1px solid ${{borderColor}}; color:${{textColor}}; cursor:pointer; padding:1px 8px; border-radius:4px; font-size:11px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='9 14 4 9 9 4'/><path d='M20 20v-7a4 4 0 0 0-4-4H4'/></svg> Restaurer</button>
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
          input.placeholder = 'ex: src/main.py ou dossier/';
          input.style.cssText = `width:100%; background:rgba(0,0,0,0.2); border:1px solid ${{borderColor}}; color:${{textColor}}; padding:4px 6px; border-radius:4px; font-size:12px; outline:none; font-family:monospace;`;

          let isSubmitted = false;
          const submitFile = () => {{
            if (isSubmitted) return;
            isSubmitted = true;
            const name = input.value.trim();
            if (name) {{
              updateStatus('Création de ' + name + '...');
              window.sendCodexAction({{action:'new_file', filename:name}});
            }} else {{
              renderFileTree();
            }}
          }};

          input.onkeydown = (e) => {{
            e.stopPropagation();
            if (e.key === 'Enter') {{ e.preventDefault(); submitFile(); }}
            if (e.key === 'Escape') {{ e.preventDefault(); isSubmitted = true; renderFileTree(); }}
          }};
          input.onblur = () => {{
            if (!isSubmitted) submitFile();
          }};

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
          language: detectFileLang(currentFile),
          model: modelSelect ? modelSelect.value : 'MODEL_APOLLO',
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
        window.sendCodexAction({{action:'close'}});
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

      // Fullscreen Toggle
      let isFullscreen = false;
      let preFsState = {{}};
      const fsBtn = document.getElementById(CODEX_ID + '-fullscreen');
      if (fsBtn) {{
        fsBtn.onclick = () => {{
          if (!isFullscreen) {{
            preFsState = {{ w: hud.style.width, h: hud.style.height, t: hud.style.top, l: hud.style.left, tx: hud.style.transform }};
            hud.style.width = '95vw'; hud.style.height = '95vh'; hud.style.top = '2.5vh'; hud.style.left = '2.5vw'; hud.style.transform = 'none'; hud.style.resize = 'none';
            isFullscreen = true;
          }} else {{
            hud.style.width = '50vw'; hud.style.height = '50vh'; hud.style.top = '25vh'; hud.style.left = '25vw'; hud.style.transform = 'none'; hud.style.resize = 'both';
            isFullscreen = false;
          }}
        }};
      }};

      // Import (PC → Codex)
      const importInput = document.createElement('input');
      importInput.type = 'file';
      importInput.multiple = true;
      importInput.style.display = 'none';
      hud.appendChild(importInput);
      importInput.onchange = async () => {{
        if (!importInput.files || importInput.files.length === 0) return;
        const filesData = [];
        updateStatus('📂 Lecture de ' + importInput.files.length + ' fichier(s)...');
        for (const file of importInput.files) {{
          try {{
            let contentStr = '';
            if (file.name.toLowerCase().endsWith('.pdf')) {{
              const buf = await file.arrayBuffer();
              const bytes = new Uint8Array(buf);
              const chunks = [];
              for (let i = 0; i < bytes.length; i += 8192) {{
                chunks.push(String.fromCharCode.apply(null, bytes.subarray(i, i + 8192)));
              }}
              contentStr = chunks.join('');
            }} else {{
              contentStr = await file.text();
            }}
            filesData.push({{filename: file.name, content: contentStr}});
          }} catch (err) {{
            console.error('[ECHO Codex] File read error:', err);
          }}
        }}
        if (filesData.length > 0) {{
          updateStatus('📂 Importation de ' + filesData.length + ' fichier(s)...');
          window.sendCodexAction({{action:'upload', files: filesData}});
        }}
      }};
      document.getElementById(CODEX_ID + '-import').onclick = () => {{
        importInput.value = '';
        importInput.click();
      }};

      // Export (Codex → PC)
      document.getElementById(CODEX_ID + '-export').onclick = () => {{
        if (currentFile) window.sendCodexAction({{action:'download', filename:currentFile}});
      }};

      // ===== CLIPBOARD UTILS =====
      function copyPlainText(text) {{
        if (navigator.clipboard && window.isSecureContext) {{
          navigator.clipboard.writeText(text).then(() => updateStatus('📋 Code copi\u00e9 !')).catch(() => fallbackCopyText(text));
        }} else {{
          fallbackCopyText(text);
        }}
      }}
      function fallbackCopyText(text) {{
        const ta = document.createElement('textarea');
        ta.value = text;
        ta.style.cssText = 'position:fixed;left:-9999px;opacity:0;';
        document.body.appendChild(ta);
        ta.select();
        try {{
          document.execCommand('copy');
          updateStatus('📋 Code copi\u00e9 (Fallback HTTP) !');
        }} catch (e) {{ updateStatus('❌ Erreur de copie'); }}
        document.body.removeChild(ta);
      }}
      function copyRichText(html, text) {{
        if (navigator.clipboard && window.ClipboardItem && window.isSecureContext) {{
          try {{
            const blobHtml = new Blob([html], {{ type: 'text/html' }});
            const blobText = new Blob([text], {{ type: 'text/plain' }});
            const data = [new ClipboardItem({{ 'text/html': blobHtml, 'text/plain': blobText }})];
            navigator.clipboard.write(data).then(() => {{
              updateStatus('📋 Rendu copi\u00e9 (Rich Text) !');
            }}).catch(() => fallbackCopyRichText(html));
          }} catch(e) {{ fallbackCopyRichText(html); }}
        }} else {{
          fallbackCopyRichText(html);
        }}
      }}
      function fallbackCopyRichText(html) {{
        const div = document.createElement('div');
        div.innerHTML = html;
        div.style.cssText = 'position:fixed;left:-9999px;opacity:0;pointer-events:none;';
        document.body.appendChild(div);
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(div);
        selection.removeAllRanges();
        selection.addRange(range);
        try {{
          document.execCommand('copy');
          updateStatus('📋 Rendu copi\u00e9 (Fallback HTTP) !');
        }} catch (e) {{ updateStatus('❌ Erreur de copie'); }}
        selection.removeAllRanges();
        document.body.removeChild(div);
      }}

      // ===== COPY BINDINGS =====
      document.getElementById(CODEX_ID + '-copy').onclick = () => {{
        if (!editor) return;
        copyPlainText(editor.getValue());
      }};

      const previewCopyBtn = document.getElementById(CODEX_ID + '-preview-copy');
      if (previewCopyBtn) {{
        previewCopyBtn.onclick = () => {{
          if (!currentFile) return;
          const lang = detectFileLang(currentFile);
          const content = editor ? editor.getValue() : '';

          if (lang === 'markdown') {{
            const container = document.getElementById(CODEX_ID + '-preview-content');
            copyRichText(container.innerHTML, container.innerText);
          }} else {{
            copyPlainText(content);
          }}
        }};
      }}

      // History ◀ ▶
      document.getElementById(CODEX_ID + '-hist-prev').onclick = () => {{
        if (currentFile) window.sendCodexAction({{action:'history_prev', filename:currentFile}});
      }};
      document.getElementById(CODEX_ID + '-hist-next').onclick = () => {{
        if (currentFile) window.sendCodexAction({{action:'history_next', filename:currentFile}});
      }};

      // Refresh 🔄
      document.getElementById(CODEX_ID + '-refresh').onclick = () => {{
        window.sendCodexAction({{action:'refresh', filename:currentFile || ''}});
      }};
      document.getElementById(CODEX_ID + '-hist-pin').onclick = () => {{
        if (currentFile) window.sendCodexAction({{action:'history_exit', filename:currentFile}});
      }};
      document.getElementById(CODEX_ID + '-hist-restore').onclick = () => {{
        if (currentFile && historyContent !== null) {{
          window.sendCodexAction({{action:'history_restore', filename:currentFile, content:historyContent, source_hash:document.getElementById(CODEX_ID+'-status-text').dataset.hash||''}});
        }}
      }};

      // ===== DIFF ACCEPT/REJECT =====
      document.getElementById(CODEX_ID + '-diff-accept').onclick = () => {{
        if (diffEditor) {{
          const content = diffEditor.getModifiedEditor().getValue();
          window.sendCodexAction({{action:'accept_diff', filename:currentFile, content:content, instruction:lastInstruction}});
        }}
      }};
      document.getElementById(CODEX_ID + '-diff-reject').onclick = () => {{
        window.sendCodexAction({{action:'reject_diff'}});
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
            sidebarWidth: sidebarWidth,
          }}));
        }} catch(e) {{}}
      }}

      // Ctrl+S
      function doSave() {{
        if (currentFile && editor) {{
          window.sendCodexAction({{
            action: 'save',
            filename: currentFile,
            content: editor.getValue(),
            language: detectFileLang(currentFile)
          }});
          
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

      // ===== GLOBAL API (callable from Python) =====

      window.echoCodexNotify = (type, msg) => {{
        hideButtonSpinner();
        if (type === 'saved') {{ updateStatus('💾 Sauvegardé • ' + msg); modified = false; renderFileTree(); }}
        else if (type === 'committed') {{
          updateStatus('✅ Commité • ' + msg); exitDiffMode();
          modified = false; renderFileTree();
          // Reload géré côté Python (push echoCodexSetContent)
        }}
        else if (type === 'restored') {{ updateStatus('⤴️ Restauré • ' + msg); exitHistoryMode(); }}
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
          accessibilitySupport: 'off'
        }});
        const originalModel = monaco.editor.createModel(editor ? editor.getValue() : '', detectFileLang(currentFile));
        const modifiedModel = monaco.editor.createModel(modifiedContent, detectFileLang(currentFile));
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

      window.echoCodexRefreshTree = (newFiles, newWorkspace) => {{
        files = newFiles;
        if (newWorkspace) currentWorkspace = newWorkspace;
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

      window.echoCodexSetCurrentFile = (filename) => {{
        currentFile = filename;
      }};

      // Chargement de contenu depuis le backend Python
      window.echoCodexSetContent = (content, filename) => {{
        if (!editor) {{
          pendingContent = {{ content: content ?? '', filename: filename ?? '' }};
          return;
        }}
        const lang = detectFileLang(filename);
        const model = monaco.editor.createModel(content ?? '', lang);
        editor.setModel(model);
        editor.onDidChangeModelContent(() => {{
          modified = true; renderFileTree(); updateSaveButton();
          if (previewOpen) {{
            clearTimeout(previewDebounceTimer);
            previewDebounceTimer = setTimeout(updatePreview, 400);
          }}
        }});
        modified = false;
        updateSaveButton();
        // Mettre à jour le sélecteur de langage
        const langSelect = document.getElementById(CODEX_ID + '-lang');
        if (langSelect) langSelect.value = lang;
        updateStatus(filename ? (filename + ' \u2022 charg\u00e9') : 'Pr\u00eat');
        // Preview : mise à jour du bouton et du rendu
        updatePreviewButton();
        if (previewOpen && !PREVIEW_LANGS.includes(lang)) {{
          togglePreview(false);
        }} else if (previewOpen) {{
          updatePreview();
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
        const lang = detectFileLang(currentFile);
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
        const copyBtn = document.getElementById(CODEX_ID + '-preview-copy');
        if (!container) return;

        if (printBtn) printBtn.style.display = (lang === 'markdown' || lang === 'html') ? 'flex' : 'none';
        if (copyBtn) copyBtn.style.display = PREVIEW_LANGS.includes(lang) ? 'flex' : 'none';

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
        }} else if (lang === 'pdf') {{
          if (label) label.textContent = 'PDF Preview';
          renderPDF(content, container);
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
          iframe.sandbox = 'allow-scripts allow-modals';
          iframe.style.cssText = 'width:100%; height:100%; border:none; background:white;';
          container.appendChild(iframe);
        }}
        iframe.srcdoc = '<script>window.addEventListener("message", function(e) {{ if (e.data === "echo-print") window.print(); }});</script>' + content;
      }}

      function renderCSS(content, container) {{
        container.style.padding = '0';
        let iframe = container.querySelector('iframe');
        if (!iframe) {{
          container.innerHTML = '';
          iframe = document.createElement('iframe');
          iframe.sandbox = 'allow-scripts allow-modals';
          iframe.style.cssText = 'width:100%; height:100%; border:none; background:white;';
          container.appendChild(iframe);
        }}
        iframe.srcdoc = '<!DOCTYPE html><html><head><script>window.addEventListener("message", function(e) {{ if (e.data === "echo-print") window.print(); }});</script><style>' + content + '</style></head><body>' +
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
        const cleaned = content.replace(/<script[\\s\\S]*?<\\/script>/gi, '');
        container.innerHTML = '<div style="display:flex; align-items:center; justify-content:center; min-height:200px;">' + cleaned + '</div>';
        const svg = container.querySelector('svg');
        if (svg) {{
          svg.style.maxWidth = '100%';
          svg.style.height = 'auto';
        }}
      }}

      function renderPDF(content, container) {{
        container.style.padding = '0';
        try {{
          const bytes = new Uint8Array(content.length);
          for (let i = 0; i < content.length; i++) {{
              bytes[i] = content.charCodeAt(i) & 0xff;
          }}
          const blob = new Blob([bytes], {{ type: 'application/pdf' }});
          const url = URL.createObjectURL(blob);
          
          const oldIframe = container.querySelector('iframe');
          if (oldIframe) {{
              if (oldIframe.src.startsWith('blob:')) {{
                  URL.revokeObjectURL(oldIframe.src);
              }}
              oldIframe.remove();
          }}
          
          container.innerHTML = '';
          const iframe = document.createElement('iframe');
          iframe.style.cssText = 'width:100%; height:100%; border:none; background:white;';
          iframe.src = url;
          container.appendChild(iframe);
        }} catch (e) {{
          container.innerHTML = '<div style="color:#f38ba8; padding:12px;">\u274c Erreur lors du rendu du PDF (encodage invalide)</div>';
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
          {cls.get_print_isolation_js('#echo-codex-hud-preview-content')}
        }};
        isolationFn();
      }};

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
        const lang = detectFileLang(currentFile);
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

        if (pendingContent) {{
          const pc = pendingContent;
          pendingContent = null;
          window.echoCodexSetContent(pc.content, pc.filename);
        }} else if (currentFile) {{
          updateStatus(currentFile + ' \u2022 chargement...');
          window.sendCodexAction({{action:'load_file', filename: currentFile}});
        }}
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

      // ===== PING HEARTBEAT, VISIBILITY WAKEUP & SPA ROUTE GUARD =====
      window._echoCodexVisHandler = () => {{
        if (!document.getElementById(CODEX_ID)) {{
          document.removeEventListener('visibilitychange', window._echoCodexVisHandler);
          return;
        }}
        if (!document.hidden) {{
          window.sendCodexAction({{action: 'ping', current_file: currentFile}});
        }}
      }};
      document.addEventListener('visibilitychange', window._echoCodexVisHandler);

      const pingInterval = setInterval(() => {{
        if (!document.getElementById(CODEX_ID)) {{
            clearInterval(pingInterval);
            return;
        }}
        if (CID && window.location.pathname.startsWith('/c/') && !window.location.pathname.includes(CID)) {{
            clearInterval(pingInterval);
            hud.remove();
            window.sendCodexAction({{action: 'close'}});
            return;
        }}
        if (!document.hidden) {{
            window.sendCodexAction({{action: 'ping', current_file: currentFile}});
        }}
      }}, 5000);
      window._echoCodexPingInterval = pingInterval;

      loadMonaco();
    }})();
    """
