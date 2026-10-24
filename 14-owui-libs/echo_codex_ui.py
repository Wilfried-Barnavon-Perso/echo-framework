"""
title: ECHO Codex UI Rendering Engine
author: Wilfried BARNAVON
version: 5.230.0.1
description: Composant système interne : Moteur de rendu HUD Monaco et Éditeur Hexadécimal pour ECHO Codex.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.230.0.1: Prise en charge de target_chat_id dans window._echoCodexTarget pour aiguillage multi-chat à l'ouverture à froid.
# 5.229.7.3: Support de window.echoCodexUpdateChats pour actualisation à chaud des sessions multi-chat dans le select.
# 5.229.7.2: Réarmement systématique des spinners et selects d'en-tête (echoCodexNotify) et émission dual-key (chat_id, target_chat_id).
# 5.229.7.1: Intégration du sélecteur hiérarchique multi-chat, badge lecture seule et verrouillage d'édition contextuel.
# 5.228.12.1: Héritage factorisé de EchoUI.get_hud_geometry_js (echoBindHudGeometry) et EchoUI.get_hud_rpc_bridge_js (echoCreateHudRpcBridge).


import sys
import orjson as std_json

# Importations ECHO Standard
if "/app/backend/echo_libs" not in sys.path:
    sys.path.append("/app/backend/echo_libs")

from echo_constants import CODEX_LANG_MAP, CODEX_LANG_TO_EXT
from echo_ui import EchoUI


class EchoCodexUI(EchoUI):
    """Moteur de pilotage HUD Monaco Editor & Hex Editor pour ECHO Codex, héritant des primitives EchoUI."""

    @classmethod
    def _generate_codex_js(
            cls,
            files_json: str,
            quick_actions_json: str,
            workspaces_json: str,
            current_workspace: str,
            chat_id: str,
            message_id: str = "",
            model_id: str = "echo_orchestrator",
            chats_json: str = "{}",
            active_chat_id: str = "",
            active_chat_title: str = "") -> str:
        """Génère le script JS complet du HUD Monaco Codex.
        Injection via __event_call__({type: 'execute', data: {code: ...}})."""
        lang_to_ext_json = std_json.dumps(CODEX_LANG_TO_EXT).decode("utf-8")
        ext_to_lang_json = std_json.dumps(CODEX_LANG_MAP).decode("utf-8")
        return f"""
    (function() {{
      const CODEX_ID = 'echo-codex-hud';
      {cls.get_mobile_guard_js("echo-codex-hud")}
      const CID = '{chat_id}';
      let activeChatId = '{active_chat_id}' || CID;
      let activeChatTitle = '{active_chat_title}';
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
      const rawChats = {chats_json};
      let chatsData = (rawChats && rawChats.current) ? rawChats : {{ current: {{ id: CID, title: activeChatTitle || 'Conversation Actuelle' }}, others: [] }};
      let currentWorkspace = '{current_workspace}';
      let isReadOnly = (activeChatId !== CID);
      // Mapping langage Monaco ↔ extension (injecté depuis echo_constants SSOT)
      const LANG_TO_EXT = {lang_to_ext_json};
      const EXT_TO_LANG = {ext_to_lang_json};
      const BINARY_EXTS = new Set(['.pdf', '.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.heic', '.heif', '.tiff', '.zip', '.tar', '.gz', '.7z', '.wasm', '.bin', '.exe', '.dll', '.so', '.dylib', '.sqlite', '.db', '.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a', '.opus', '.mp4', '.webm', '.mov', '.avi', '.woff', '.woff2', '.ttf', '.otf', '.docx', '.xlsx', '.pptx']);
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
      function detectIsBinary(content, filename, explicitFlag) {{
        if (explicitFlag === true || explicitFlag === false) return explicitFlag;
        if (filename) {{
          const found = files.find(f => f.filename === filename);
          if (found && typeof found.is_binary === 'boolean') return found.is_binary;
          const lower = filename.toLowerCase();
          const dotIdx = lower.lastIndexOf('.');
          if (dotIdx !== -1 && BINARY_EXTS.has(lower.substring(dotIdx))) return true;
        }}
        if (typeof content === 'string' && content.length > 0) {{
          const checkLen = Math.min(content.length, 4096);
          for (let i = 0; i < checkLen; i++) {{
            if (content.charCodeAt(i) === 0) return true;
          }}
        }}
        return false;
      }}
      let targetFileOverride = null;
      if (window._echoCodexTarget) {{
          currentWorkspace = window._echoCodexTarget.workspace || currentWorkspace;
          targetFileOverride = window._echoCodexTarget.file;
          const initialTargetChat = window._echoCodexTarget.target_chat_id;
          if (initialTargetChat && initialTargetChat !== CID) {{
              activeChatId = initialTargetChat;
              isReadOnly = true;
          }}
          setTimeout(() => {{
              if (window.sendCodexAction) {{
                  if (initialTargetChat && initialTargetChat !== CID) {{
                      window.sendCodexAction({{
                          action: "switch_chat",
                          target_chat_id: initialTargetChat,
                          target_file: targetFileOverride
                      }});
                  }} else {{
                      window.sendCodexAction({{
                          action: "switch_workspace",
                          workspace: currentWorkspace,
                          current_file: targetFileOverride
                      }});
                  }}
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
      let isCurrentBinary = false;
      let hexBytes = null;
      let selectedHexOffset = 0;
      let hexEditArea = 'hex';
      let hexHighNibble = true;
      let previewOpen = false;
      let editorRatio = 33;
      let previewRatio = 67;
      let markedLoaded = false;
      let previewDebounceTimer = null;

      // --- COMMUNICATION PROXY & AUTO-RECONNECT RPC (Hérité de EchoUI) ---
      {cls.get_hud_geometry_js()}
      {cls.get_hud_rpc_bridge_js()}
      const rpcBridge = window.echoCreateHudRpcBridge({{
        hudId: CODEX_ID,
        actionId: 'echo_codex_action',
        resolveVar: 'echoCodexResolve',
        sendFn: 'sendCodexAction',
        readyFn: '_echoCodexOnReady',
        cid: CID,
        mid: MID,
        modelId: MODEL_ID || 'echo_orchestrator',
        visiblePingMs: 5000,
        hiddenPingMs: 25000,
        prePingActions: ['ai_edit'],
        noWatchdogActions: ['close', 'ai_edit'],
        onReconnectStart: () => {{ if (typeof updateStatus === 'function') updateStatus('🔄 Reconnexion au Codex...'); }},
        getExtraReconnect: () => ({{ workspace: currentWorkspace }}),
        getPingPayload: () => ({{ action: 'ping', current_file: currentFile }}),
        onWatchdogFallback: () => (currentFile ? {{ action: 'load_file', filename: currentFile }} : null)
      }});

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
        <span id="${{CODEX_ID}}-readonly-badge" class="codex-readonly-badge" style="font-size:11px; padding:2px 8px; border-radius:4px; background:rgba(239,68,68,0.15); color:#ef4444; font-weight:600; border:1px solid rgba(239,68,68,0.3); display:inline-flex; align-items:center; gap:4px; margin-right:4px;">🔒 Lecture Seule</span>
        <select id="${{CODEX_ID}}-lang" style="background:transparent; border:1px solid ${{borderColor}};
          color:${{textColor}}; padding:2px 6px; border-radius:4px; font-size:12px;"></select>
        <button id="${{CODEX_ID}}-import" class="codex-editable" title="Importer (PC → Codex)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='7 10 12 15 17 10'/><line x1='12' y1='15' x2='12' y2='3'/></svg></button>
        <button id="${{CODEX_ID}}-export" title="Exporter (Codex → PC)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4'/><polyline points='17 8 12 3 7 8'/><line x1='12' y1='3' x2='12' y2='15'/></svg></button>
        <button id="${{CODEX_ID}}-copy" title="Copier" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><rect x='9' y='9' width='13' height='13' rx='2' ry='2'/><path d='M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1'/></svg></button>
        <button id="${{CODEX_ID}}-refresh" title="Actualiser" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='23 4 23 10 17 10'/><polyline points='1 20 1 14 7 14'/><path d='M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15'/></svg></button>
        <button id="${{CODEX_ID}}-save" class="codex-editable" title="Sauvegarder (Ctrl+S)" style="background:none; border:none; color:${{textColor}}; cursor:pointer; font-size:14px; line-height:1; opacity:0.3; transition:opacity 0.2s;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><path d='M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z'/><polyline points='17 21 17 13 7 13 7 21'/><polyline points='7 3 7 8 15 8'/></svg></button>
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
      editorWrap.style.cssText = `flex:${{previewOpen ? editorRatio : 100}} 1 0%; overflow:hidden; position:relative; min-width:0; display:flex; flex-direction:column;`;

      const monacoWrap = document.createElement('div');
      monacoWrap.id = CODEX_ID + '-monaco';
      monacoWrap.style.cssText = 'flex:1; width:100%; height:100%; overflow:hidden; position:relative;';

      const hexWrap = document.createElement('div');
      hexWrap.id = CODEX_ID + '-hex';
      hexWrap.tabIndex = 0;
      hexWrap.style.cssText = `display:none; flex:1; width:100%; height:100%; flex-direction:column; overflow:hidden; background:${{bgColor}}; color:${{textColor}}; font-family:'Cascadia Code','Fira Code',Consolas,monospace; font-size:12px; outline:none; user-select:none;`;
      hexWrap.innerHTML = `
        <div style="display:flex; align-items:center; padding:6px 12px; background:${{sidebarBg}}; border-bottom:1px solid ${{borderColor}}; font-size:11px; color:${{isDark ? '#a6adc8' : '#666'}}; font-weight:600; flex-shrink:0; gap:12px; white-space:nowrap; overflow-x:auto;">
          <span style="width:76px; flex-shrink:0; white-space:nowrap;">OFFSET</span>
          <span style="width:336px; flex-shrink:0; letter-spacing:0.5px; white-space:nowrap;">00 01 02 03 04 05 06 07  08 09 0A 0B 0C 0D 0E 0F</span>
          <span style="flex:1; min-width:48px; flex-shrink:0; white-space:nowrap;">ASCII</span>
          <button id="${{CODEX_ID}}-hex-insert" class="codex-editable" title="Insérer un octet (0x00) après l'offset" style="background:transparent; border:1px solid ${{borderColor}}; color:${{textColor}}; border-radius:4px; padding:1px 6px; font-size:11px; cursor:pointer; flex-shrink:0; white-space:nowrap;">+ Octet</button>
          <button id="${{CODEX_ID}}-hex-delete" class="codex-editable" title="Supprimer l'octet sélectionné" style="background:transparent; border:1px solid ${{borderColor}}; color:#f38ba8; border-radius:4px; padding:1px 6px; font-size:11px; cursor:pointer; flex-shrink:0; white-space:nowrap;">- Octet</button>
        </div>
        <div id="${{CODEX_ID}}-hex-scroll" style="flex:1; overflow-y:auto; overflow-x:auto; position:relative; padding:0;">
          <div id="${{CODEX_ID}}-hex-spacer" style="position:relative; width:100%;">
            <div id="${{CODEX_ID}}-hex-rows" style="position:absolute; left:0; right:0; top:0;"></div>
          </div>
        </div>
        <div id="${{CODEX_ID}}-hex-inspector" style="display:flex; align-items:center; gap:14px; padding:5px 12px; background:${{sidebarBg}}; border-top:1px solid ${{borderColor}}; font-size:11px; color:${{isDark ? '#cba6f7' : '#555'}}; flex-shrink:0; flex-wrap:wrap;">
          <span id="${{CODEX_ID}}-hex-inspector-info">Éditeur Hexadécimal</span>
        </div>
      `;

      editorWrap.appendChild(monacoWrap);
      editorWrap.appendChild(hexWrap);

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
      aiPanel.classList.add('codex-editable');
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

      // --- STYLES & MICRO-SPINNER ---
      let spinnerTarget = null;
      let spinnerOriginal = '';
      const spinStyle = document.createElement('style');
      spinStyle.textContent = `
        @keyframes echoCodexSpin {{ from {{ transform:rotate(0deg); }} to {{ transform:rotate(360deg); }} }}
        #${{CODEX_ID}}.is-readonly .codex-editable {{ display: none !important; }}
        #${{CODEX_ID}}:not(.is-readonly) .codex-readonly-badge {{ display: none !important; }}
      `;
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

      function setReadOnlyMode(readOnly) {{
        isReadOnly = readOnly;
        if (hud) hud.classList.toggle('is-readonly', isReadOnly);
        if (editor) editor.updateOptions({{ readOnly: isReadOnly }});
        const chatSel = document.getElementById(CODEX_ID + '-chat-select');
        if (chatSel) {{
          chatSel.style.color = (activeChatId === CID) ? '#2563eb' : '#ef4444';
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
          <button id="${{CODEX_ID}}-hist-restore" class="codex-editable" style="background:none; border:1px solid ${{borderColor}}; color:${{textColor}}; cursor:pointer; padding:1px 8px; border-radius:4px; font-size:11px;"><svg width='16' height='16' viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'><polyline points='9 14 4 9 9 4'/><path d='M20 20v-7a4 4 0 0 0-4-4H4'/></svg> Restaurer</button>
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
      setReadOnlyMode(isReadOnly);

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

        // --- 0. Chat Selector ---
        const chatContainer = document.createElement('div');
        chatContainer.style.cssText = `display:flex; align-items:center; background:${{headerBg}}; border-bottom:1px solid ${{borderColor}}; flex-shrink:0; padding:2px 4px;`;

        const chatSelect = document.createElement('select');
        chatSelect.id = CODEX_ID + '-chat-select';
        chatSelect.style.cssText = `flex:1; padding:6px 4px; background:transparent; border:none; color:${{activeChatId === CID ? '#2563eb' : '#ef4444'}}; font-size:12px; font-weight:bold; outline:none; cursor:pointer; text-overflow:ellipsis; overflow:hidden; white-space:nowrap; max-width:calc(100% - 24px);`;

        // Option 1 : Conversation Actuelle (bleu foncé)
        const curOpt = document.createElement('option');
        curOpt.value = chatsData.current.id || CID;
        curOpt.textContent = '💬 Conversation Actuelle' + (chatsData.current.title ? ' : ' + chatsData.current.title : '');
        curOpt.style.cssText = 'color:#2563eb; font-weight:bold; background:#0f172a;';
        if (activeChatId === (chatsData.current.id || CID)) curOpt.selected = true;
        chatSelect.appendChild(curOpt);

        // Options suivantes : Conversations tierces (rouge, strictement différentes)
        if (chatsData.others && Array.isArray(chatsData.others)) {{
          chatsData.others.forEach(c => {{
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = '💬 ' + (c.title || ('Chat ' + c.id.substring(0, 8)));
            opt.style.cssText = 'color:#ef4444; font-weight:bold; background:#0f172a;';
            if (activeChatId === c.id) opt.selected = true;
            chatSelect.appendChild(opt);
          }});
        }}

        const chatSpinner = document.createElement('div');
        chatSpinner.id = CODEX_ID + '-chat-spinner';
        chatSpinner.style.cssText = `display:none; width:14px; height:14px; margin-right:8px; border:2px solid ${{textColor}}; border-top-color:transparent; border-radius:50%; animation:codex-spin 1s linear infinite;`;

        chatSelect.onchange = () => {{
          chatSelect.disabled = true;
          chatSpinner.style.display = 'block';
          window.sendCodexAction({{action:'switch_chat', chat_id:chatSelect.value, target_chat_id:chatSelect.value, workspace:currentWorkspace}});
        }};

        chatContainer.appendChild(chatSelect);
        chatContainer.appendChild(chatSpinner);
        sb.appendChild(chatContainer);

        // --- 1. Workspace Switcher ---
        const wsContainer = document.createElement('div');
        wsContainer.style.cssText = `display:flex; align-items:center; background:${{headerBg}}; border-bottom:1px solid ${{borderColor}}; flex-shrink:0; padding:2px 4px;`;

        const wsSelect = document.createElement('select');
        wsSelect.id = CODEX_ID + '-workspace';
        wsSelect.style.cssText = `flex:1; padding:6px 4px; background:transparent; border:none; color:${{textColor}}; font-size:12px; font-weight:bold; outline:none; cursor:pointer;`;
        
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
          window.sendCodexAction({{action:'switch_workspace', workspace:wsSelect.value, target_chat_id:activeChatId}});
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
                actionGroup.classList.add('codex-editable');
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
                actionGroup.classList.add('codex-editable');
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
        newBtn.classList.add('codex-editable');
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
        resetBtn.classList.add('codex-editable');
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

      // ===== DRAG, MINIMIZE & FULLSCREEN (Hérité de EchoUI) =====
      let isMinimized = false;
      let isFullscreen = false;
      const geoCtrl = window.echoBindHudGeometry(hud, header, {{
        defaultW: '900px',
        defaultH: '600px',
        defaultX: '50%',
        defaultY: '60px',
        defaultTx: 'translateX(-50%)',
        minW: '600px',
        minH: '400px',
        minimizedH: 'auto',
        minimizedMinH: '0',
        fsW: '95vw',
        fsH: '95vh',
        fsX: '2.5vw',
        fsY: '2.5vh',
        initialBounds: savedState,
        onSave: () => saveState(),
        onMinimize: (isMin) => {{
          isMinimized = isMin;
          body.style.display = isMin ? 'none' : 'flex';
          aiPanel.style.display = (!isMin && !isCurrentBinary && !isDiffMode) ? 'flex' : 'none';
          statusBar.style.display = isMin ? 'none' : 'flex';
          diffBar.style.display = (!isMin && isDiffMode) ? 'flex' : 'none';
          if (!isMin && editor) setTimeout(() => editor.layout(), 50);
        }},
        onFullscreen: (isFs) => {{
          isFullscreen = isFs;
          if (editor) setTimeout(() => editor.layout(), 50);
        }}
      }});

      // ===== HEADER BUTTONS =====
      document.getElementById(CODEX_ID + '-close').onclick = () => {{
        saveState();
        if (rpcBridge) rpcBridge.cleanup();
        hud.remove();
        window.sendCodexAction({{action:'close'}});
      }};
      document.getElementById(CODEX_ID + '-minimize').onclick = (e) => {{
        if (e && e.stopPropagation) e.stopPropagation();
        geoCtrl.toggleMinimize();
      }};

      // Fullscreen Toggle
      const fsBtn = document.getElementById(CODEX_ID + '-fullscreen');
      if (fsBtn) {{
        fsBtn.onclick = (e) => {{
          if (e && e.stopPropagation) e.stopPropagation();
          geoCtrl.toggleFullscreen();
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
            const buf = await file.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let isBin = detectIsBinary(null, file.name);
            if (!isBin) {{
              const checkLen = Math.min(bytes.length, 8192);
              for (let i = 0; i < checkLen; i++) {{
                if (bytes[i] === 0) {{ isBin = true; break; }}
              }}
            }}
            let contentStr = '';
            if (isBin) {{
              contentStr = bytesToLatin1(bytes);
            }} else {{
              contentStr = new TextDecoder('utf-8').decode(bytes);
            }}
            filesData.push({{filename: file.name, content: contentStr, is_binary: isBin}});
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

      // ===== HEXADECIMAL EDITOR ENGINE =====
      const HEX_BYTES_PER_ROW = 16;
      const HEX_ROW_HEIGHT = 20;

      function bytesToLatin1(u8) {{
        const chunks = [];
        for (let i = 0; i < u8.length; i += 8192) {{
          chunks.push(String.fromCharCode.apply(null, u8.subarray(i, i + 8192)));
        }}
        return chunks.join('');
      }}

      function latin1ToBytes(str) {{
        const s = str || '';
        const out = new Uint8Array(s.length);
        for (let i = 0; i < s.length; i++) {{
          out[i] = s.charCodeAt(i) & 0xff;
        }}
        return out;
      }}

      function formatHexDumpText(u8) {{
        if (!u8 || u8.length === 0) return '';
        const lines = [];
        for (let rowOff = 0; rowOff < u8.length; rowOff += HEX_BYTES_PER_ROW) {{
          const offHex = rowOff.toString(16).toUpperCase().padStart(8, '0');
          const hexParts = [];
          let asciiPart = '';
          for (let col = 0; col < HEX_BYTES_PER_ROW; col++) {{
            const idx = rowOff + col;
            if (idx < u8.length) {{
              const b = u8[idx];
              hexParts.push(b.toString(16).toUpperCase().padStart(2, '0'));
              asciiPart += (b >= 32 && b <= 126) ? String.fromCharCode(b) : '.';
            }} else {{
              hexParts.push('  ');
              asciiPart += ' ';
            }}
          }}
          lines.push(offHex + '  ' + hexParts.slice(0, 8).join(' ') + '  ' + hexParts.slice(8).join(' ') + '  |' + asciiPart + '|');
        }}
        return lines.join('\\n');
      }}

      function getActiveContent() {{
        if (isCurrentBinary && hexBytes) {{
          return bytesToLatin1(hexBytes);
        }}
        return editor ? editor.getValue() : '';
      }}

      function updateHexInspector() {{
        const infoEl = document.getElementById(CODEX_ID + '-hex-inspector-info');
        if (!infoEl) return;
        if (!hexBytes || hexBytes.length === 0) {{
          infoEl.textContent = 'Fichier binaire vide (0 octet)';
          return;
        }}
        const off = Math.max(0, Math.min(selectedHexOffset, hexBytes.length - 1));
        const u8 = hexBytes[off];
        const i8 = u8 > 127 ? u8 - 256 : u8;
        const binStr = u8.toString(2).padStart(8, '0');
        let u16 = 'N/A';
        if (off + 1 < hexBytes.length) {{
          u16 = (hexBytes[off] | (hexBytes[off + 1] << 8)).toString();
        }}
        infoEl.textContent = `Offset: 0x${{off.toString(16).toUpperCase().padStart(8, '0')}} (${{off}} / ${{hexBytes.length}} octets) | UInt8: ${{u8}} | Int8: ${{i8}} | UInt16LE: ${{u16}} | Bin: ${{binStr}}`;
      }}

      function renderHexRows() {{
        const scrollEl = document.getElementById(CODEX_ID + '-hex-scroll');
        const spacerEl = document.getElementById(CODEX_ID + '-hex-spacer');
        const rowsEl = document.getElementById(CODEX_ID + '-hex-rows');
        if (!scrollEl || !spacerEl || !rowsEl || !hexBytes) return;

        const totalRows = Math.max(1, Math.ceil(hexBytes.length / HEX_BYTES_PER_ROW));
        spacerEl.style.height = (totalRows * HEX_ROW_HEIGHT + 16) + 'px';

        const scrollTop = scrollEl.scrollTop;
        const viewHeight = scrollEl.clientHeight || 500;
        const firstRow = Math.max(0, Math.floor(scrollTop / HEX_ROW_HEIGHT) - 5);
        const visibleCount = Math.ceil(viewHeight / HEX_ROW_HEIGHT) + 15;
        const lastRow = Math.min(totalRows - 1, firstRow + visibleCount);

        rowsEl.style.top = (firstRow * HEX_ROW_HEIGHT) + 'px';
        let html = '';

        for (let r = firstRow; r <= lastRow; r++) {{
          const rowOffset = r * HEX_BYTES_PER_ROW;
          const offStr = rowOffset.toString(16).toUpperCase().padStart(8, '0');
          let hexCells = '';
          let asciiCells = '';

          for (let c = 0; c < HEX_BYTES_PER_ROW; c++) {{
            const idx = rowOffset + c;
            const sepSpace = (c === 7) ? 'margin-right:8px;' : '';
            if (idx < hexBytes.length) {{
              const b = hexBytes[idx];
              const hexVal = b.toString(16).toUpperCase().padStart(2, '0');
              const isSel = (idx === selectedHexOffset);
              const hexBg = isSel ? (hexEditArea === 'hex' ? accentColor : 'rgba(137,180,250,0.28)') : 'transparent';
              const hexFg = isSel && hexEditArea === 'hex' ? (isDark ? '#11111b' : '#ffffff') : (b === 0 ? (isDark ? '#585b70' : '#9ca0b0') : textColor);
              const ascBg = isSel ? (hexEditArea === 'ascii' ? accentColor : 'rgba(137,180,250,0.28)') : 'transparent';
              const ascFg = isSel && hexEditArea === 'ascii' ? (isDark ? '#11111b' : '#ffffff') : ((b >= 32 && b <= 126) ? textColor : (isDark ? '#585b70' : '#9ca0b0'));
              const ch = (b >= 32 && b <= 126)
                ? String.fromCharCode(b).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                : '.';

              hexCells += `<span data-offset="${{idx}}" data-area="hex" style="display:inline-block; width:20px; text-align:center; border-radius:3px; cursor:pointer; ${{sepSpace}} background:${{hexBg}}; color:${{hexFg}}; font-weight:${{isSel ? '700' : '400'}};">${{hexVal}}</span>`;
              asciiCells += `<span data-offset="${{idx}}" data-area="ascii" style="display:inline-block; width:9.6px; text-align:center; border-radius:2px; cursor:pointer; background:${{ascBg}}; color:${{ascFg}}; font-weight:${{isSel ? '700' : '400'}};">${{ch}}</span>`;
            }} else {{
              hexCells += `<span style="display:inline-block; width:20px; ${{sepSpace}}">&nbsp;&nbsp;</span>`;
              asciiCells += `<span style="display:inline-block; width:9.6px;">&nbsp;</span>`;
            }}
          }}

          html += `<div style="display:flex; align-items:center; height:${{HEX_ROW_HEIGHT}}px; padding:0 12px; gap:16px; white-space:pre;">` +
            `<span style="width:76px; flex-shrink:0; color:${{isDark ? '#7f849c' : '#6c6f85'}};">${{offStr}}</span>` +
            `<div style="display:flex; width:336px; flex-shrink:0;">${{hexCells}}</div>` +
            `<div style="display:flex; padding-left:10px; border-left:1px solid ${{borderColor}};">${{asciiCells}}</div>` +
            `</div>`;
        }}

        rowsEl.innerHTML = html;
        updateHexInspector();
      }}

      function ensureHexOffsetVisible() {{
        const scrollEl = document.getElementById(CODEX_ID + '-hex-scroll');
        if (!scrollEl || !hexBytes || hexBytes.length === 0) return;
        const row = Math.floor(selectedHexOffset / HEX_BYTES_PER_ROW);
        const rowTop = row * HEX_ROW_HEIGHT;
        const rowBottom = rowTop + HEX_ROW_HEIGHT;
        if (rowTop < scrollEl.scrollTop) {{
          scrollEl.scrollTop = rowTop;
        }} else if (rowBottom > scrollEl.scrollTop + scrollEl.clientHeight) {{
          scrollEl.scrollTop = rowBottom - scrollEl.clientHeight;
        }}
      }}

      function initHexEditorEvents() {{
        const scrollEl = document.getElementById(CODEX_ID + '-hex-scroll');
        const rowsEl = document.getElementById(CODEX_ID + '-hex-rows');
        const insBtn = document.getElementById(CODEX_ID + '-hex-insert');
        const delBtn = document.getElementById(CODEX_ID + '-hex-delete');
        if (!scrollEl || !rowsEl) return;

        scrollEl.addEventListener('scroll', () => renderHexRows());

        rowsEl.addEventListener('mousedown', (e) => {{
          const cell = e.target.closest('[data-offset]');
          if (!cell) return;
          selectedHexOffset = parseInt(cell.dataset.offset, 10) || 0;
          hexEditArea = cell.dataset.area || 'hex';
          hexHighNibble = true;
          hexWrap.focus();
          renderHexRows();
        }});

        if (insBtn) {{
          insBtn.onclick = () => {{
            if (!isCurrentBinary || isHistoryMode) return;
            const cur = hexBytes || new Uint8Array(0);
            const next = new Uint8Array(cur.length + 1);
            const pos = cur.length === 0 ? 0 : Math.min(selectedHexOffset + 1, cur.length);
            next.set(cur.subarray(0, pos), 0);
            next[pos] = 0;
            next.set(cur.subarray(pos), pos + 1);
            hexBytes = next;
            selectedHexOffset = pos;
            hexHighNibble = true;
            modified = true;
            renderFileTree();
            updateSaveButton();
            renderHexRows();
            if (previewOpen) {{
              clearTimeout(previewDebounceTimer);
              previewDebounceTimer = setTimeout(updatePreview, 400);
            }}
          }};
        }}

        if (delBtn) {{
          delBtn.onclick = () => {{
            if (!isCurrentBinary || isHistoryMode || !hexBytes || hexBytes.length === 0) return;
            const cur = hexBytes;
            const pos = Math.max(0, Math.min(selectedHexOffset, cur.length - 1));
            const next = new Uint8Array(cur.length - 1);
            next.set(cur.subarray(0, pos), 0);
            next.set(cur.subarray(pos + 1), pos);
            hexBytes = next;
            selectedHexOffset = Math.max(0, Math.min(pos, next.length - 1));
            hexHighNibble = true;
            modified = true;
            renderFileTree();
            updateSaveButton();
            renderHexRows();
            if (previewOpen) {{
              clearTimeout(previewDebounceTimer);
              previewDebounceTimer = setTimeout(updatePreview, 400);
            }}
          }};
        }}

        hexWrap.addEventListener('keydown', (e) => {{
          if (!isCurrentBinary || !hexBytes) return;
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 's') {{
            e.preventDefault();
            doSave();
            return;
          }}
          if (e.key === 'Tab') {{
            e.preventDefault();
            hexEditArea = (hexEditArea === 'hex') ? 'ascii' : 'hex';
            hexHighNibble = true;
            renderHexRows();
            return;
          }}
          if (hexBytes.length === 0) return;

          if (e.key === 'ArrowRight') {{
            e.preventDefault();
            selectedHexOffset = Math.min(hexBytes.length - 1, selectedHexOffset + 1);
            hexHighNibble = true;
            ensureHexOffsetVisible();
            renderHexRows();
            return;
          }}
          if (e.key === 'ArrowLeft') {{
            e.preventDefault();
            selectedHexOffset = Math.max(0, selectedHexOffset - 1);
            hexHighNibble = true;
            ensureHexOffsetVisible();
            renderHexRows();
            return;
          }}
          if (e.key === 'ArrowDown') {{
            e.preventDefault();
            selectedHexOffset = Math.min(hexBytes.length - 1, selectedHexOffset + HEX_BYTES_PER_ROW);
            hexHighNibble = true;
            ensureHexOffsetVisible();
            renderHexRows();
            return;
          }}
          if (e.key === 'ArrowUp') {{
            e.preventDefault();
            selectedHexOffset = Math.max(0, selectedHexOffset - HEX_BYTES_PER_ROW);
            hexHighNibble = true;
            ensureHexOffsetVisible();
            renderHexRows();
            return;
          }}

          if (isHistoryMode || e.ctrlKey || e.metaKey || e.altKey) return;

          if (hexEditArea === 'hex') {{
            if (/^[0-9a-fA-F]$/.test(e.key)) {{
              e.preventDefault();
              const nibble = parseInt(e.key, 16);
              if (hexHighNibble) {{
                hexBytes[selectedHexOffset] = (nibble << 4) | (hexBytes[selectedHexOffset] & 0x0f);
                hexHighNibble = false;
              }} else {{
                hexBytes[selectedHexOffset] = (hexBytes[selectedHexOffset] & 0xf0) | nibble;
                hexHighNibble = true;
                if (selectedHexOffset < hexBytes.length - 1) selectedHexOffset++;
                ensureHexOffsetVisible();
              }}
              modified = true;
              renderFileTree();
              updateSaveButton();
              renderHexRows();
              if (previewOpen) {{
                clearTimeout(previewDebounceTimer);
                previewDebounceTimer = setTimeout(updatePreview, 400);
              }}
            }}
          }} else if (hexEditArea === 'ascii') {{
            if (e.key.length === 1) {{
              e.preventDefault();
              hexBytes[selectedHexOffset] = e.key.charCodeAt(0) & 0xff;
              if (selectedHexOffset < hexBytes.length - 1) selectedHexOffset++;
              ensureHexOffsetVisible();
              modified = true;
              renderFileTree();
              updateSaveButton();
              renderHexRows();
              if (previewOpen) {{
                clearTimeout(previewDebounceTimer);
                previewDebounceTimer = setTimeout(updatePreview, 400);
              }}
            }}
          }}
        }});
      }}
      initHexEditorEvents();

      function setBinaryEditorMode(isBin, rawContent) {{
        isCurrentBinary = !!isBin;
        const langSelect = document.getElementById(CODEX_ID + '-lang');
        if (isCurrentBinary) {{
          monacoWrap.style.display = 'none';
          hexWrap.style.display = 'flex';
          aiPanel.style.display = 'none';
          if (langSelect) langSelect.disabled = true;
          hexBytes = latin1ToBytes(rawContent ?? '');
          selectedHexOffset = 0;
          hexHighNibble = true;
          setTimeout(() => renderHexRows(), 0);
        }} else {{
          hexWrap.style.display = 'none';
          monacoWrap.style.display = 'block';
          if (!isDiffMode) aiPanel.style.display = 'flex';
          if (langSelect) langSelect.disabled = false;
          hexBytes = null;
        }}
      }}

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
        if (isCurrentBinary && hexBytes) {{
          copyPlainText(formatHexDumpText(hexBytes));
          return;
        }}
        if (!editor) return;
        copyPlainText(editor.getValue());
      }};

      const previewCopyBtn = document.getElementById(CODEX_ID + '-preview-copy');
      if (previewCopyBtn) {{
        previewCopyBtn.onclick = () => {{
          if (!currentFile) return;
          const lang = detectFileLang(currentFile);
          const content = getActiveContent();

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
          if (geoCtrl) geoCtrl.captureNormalBounds();
          const nb = geoCtrl ? geoCtrl.normalBounds : {{ x: hud.style.left, y: hud.style.top, w: hud.style.width, h: hud.style.height }};
          localStorage.setItem(STATE_KEY, JSON.stringify({{
            x: nb.x, y: nb.y,
            w: nb.w, h: nb.h,
            previewOpen: previewOpen,
            editorRatio: editorRatio,
            previewRatio: previewRatio,
            sidebarWidth: sidebarWidth,
          }}));
        }} catch(e) {{}}
      }}

      // Ctrl+S
      function doSave() {{
        if (isReadOnly) {{
          if (window.echoCodexNotify) window.echoCodexNotify('error', '🔒 Cette conversation est en lecture seule.');
          return;
        }}
        if (currentFile && (editor || isCurrentBinary)) {{
          window.sendCodexAction({{
            action: 'save',
            filename: currentFile,
            content: getActiveContent(),
            language: detectFileLang(currentFile),
            is_binary: isCurrentBinary
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
        const chatSel = document.getElementById(CODEX_ID + '-chat-select');
        if (chatSel) {{
          chatSel.disabled = false;
          if (type === 'error') chatSel.value = activeChatId;
        }}
        const chatSpin = document.getElementById(CODEX_ID + '-chat-spinner');
        if (chatSpin) chatSpin.style.display = 'none';
        const wsSel = document.getElementById(CODEX_ID + '-workspace');
        if (wsSel) {{
          wsSel.disabled = false;
          if (type === 'error') wsSel.value = currentWorkspace;
        }}
        const wsSpin = document.getElementById(CODEX_ID + '-ws-spinner');
        if (wsSpin) wsSpin.style.display = 'none';
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
        hexWrap.style.display = 'none';
        monacoWrap.style.display = 'block';
        monacoWrap.innerHTML = '';
        diffEditor = monaco.editor.createDiffEditor(monacoWrap, {{
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
        aiPanel.style.display = isCurrentBinary ? 'none' : 'flex';
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

      window.echoCodexRefreshTree = (newFiles, newWorkspace, newActiveChatId, newIsReadOnly) => {{
        files = newFiles;
        if (newWorkspace) currentWorkspace = newWorkspace;
        if (newActiveChatId) activeChatId = newActiveChatId;
        if (typeof newIsReadOnly === 'boolean') {{
          setReadOnlyMode(newIsReadOnly);
        }} else {{
          setReadOnlyMode(activeChatId !== CID);
        }}
        renderFileTree();
      }};

      window.echoCodexUpdateChats = (newChatsData) => {{
        if (!newChatsData || !newChatsData.current) return;
        chatsData = newChatsData;
        const chatSelect = document.getElementById(CODEX_ID + '-chat-select');
        if (!chatSelect) return;
        chatSelect.innerHTML = '';

        // Option 1 : Conversation Actuelle (bleu foncé)
        const curOpt = document.createElement('option');
        curOpt.value = chatsData.current.id || CID;
        curOpt.textContent = '💬 Conversation Actuelle' + (chatsData.current.title ? ' : ' + chatsData.current.title : '');
        curOpt.style.cssText = 'color:#2563eb; font-weight:bold; background:#0f172a;';
        if (activeChatId === (chatsData.current.id || CID)) curOpt.selected = true;
        chatSelect.appendChild(curOpt);

        // Options suivantes : Conversations tierces (rouge, strictement différentes)
        if (chatsData.others && Array.isArray(chatsData.others)) {{
          chatsData.others.forEach(c => {{
            const opt = document.createElement('option');
            opt.value = c.id;
            opt.textContent = '💬 ' + (c.title || ('Chat ' + c.id.substring(0, 8)));
            opt.style.cssText = 'color:#ef4444; font-weight:bold; background:#0f172a;';
            if (activeChatId === c.id) opt.selected = true;
            chatSelect.appendChild(opt);
          }});
        }}
      }};

      window.echoCodexDownload = (name, content, isBinary) => {{
        let blob;
        if (detectIsBinary(content, name, isBinary)) {{
          blob = new Blob([latin1ToBytes(content)], {{type: 'application/octet-stream'}});
        }} else {{
          blob = new Blob([content], {{type: 'text/plain;charset=utf-8'}});
        }}
        const a = document.createElement('a');
        a.href = URL.createObjectURL(blob);
        a.download = name;
        a.click();
        URL.revokeObjectURL(a.href);
      }};

      window.echoCodexReset = () => {{ hud.remove(); }};

      window.echoCodexSetCurrentFile = (filename) => {{
        currentFile = filename || null;
        renderFileTree();
      }};

      // Chargement de contenu depuis le backend Python
      window.echoCodexSetContent = (content, filename, isBinary) => {{
        if (!editor) {{
          pendingContent = {{ content: content ?? '', filename: filename ?? '', isBinary: isBinary }};
          return;
        }}
        if (filename !== undefined) {{
          currentFile = filename || null;
        }}
        const lang = detectFileLang(filename);
        const isBin = detectIsBinary(content, filename, isBinary);
        setBinaryEditorMode(isBin, content ?? '');

        if (!isBin) {{
          const model = monaco.editor.createModel(content ?? '', lang);
          editor.setModel(model);
          editor.onDidChangeModelContent(() => {{
            modified = true; renderFileTree(); updateSaveButton();
            if (previewOpen) {{
              clearTimeout(previewDebounceTimer);
              previewDebounceTimer = setTimeout(updatePreview, 400);
            }}
          }});
        }}
        modified = false;
        renderFileTree();
        updateSaveButton();
        // Mettre à jour le sélecteur de langage
        const langSelect = document.getElementById(CODEX_ID + '-lang');
        if (langSelect) {{
          let opt = Array.from(langSelect.options).find(o => o.value === lang);
          if (!opt && lang) {{
            opt = document.createElement('option');
            opt.value = lang;
            opt.textContent = lang;
            langSelect.appendChild(opt);
          }}
          langSelect.value = lang;
        }}
        updateStatus(filename ? (filename + (isBin ? ' • HEX' : '') + ' \u2022 charg\u00e9') : 'Pr\u00eat');
        // Preview : ouverture automatique si le fichier est éligible au registre de preview (Option 2.A),
        // tout en restant désactivable/activable manuellement via le bouton 👁️
        updatePreviewButton();
        if (getPreviewSpec(lang)) {{
          if (!previewOpen) {{
            togglePreview(true);
          }} else {{
            updatePreview();
          }}
        }} else if (previewOpen) {{
          togglePreview(false);
        }}
      }};

      window.echoCodexLoadVersion = (content, info, idx, total) => {{
        isHistoryMode = true;
        historyContent = content;
        if (isCurrentBinary) {{
          hexBytes = latin1ToBytes(content ?? '');
          selectedHexOffset = 0;
          renderHexRows();
        }} else if (editor) {{
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

      // ===== PREVIEW PANEL & DECLARATIVE PREVIEW REGISTRY =====

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

      const PREVIEW_REGISTRY = {{
        markdown: {{ label: 'Markdown Preview', printable: true, copyable: true, render: (c, el) => renderMarkdown(c, el) }},
        html:     {{ label: 'HTML Preview',     printable: true, copyable: true, render: (c, el) => renderHTML(c, el) }},
        css:      {{ label: 'CSS Preview',      printable: false, copyable: true, render: (c, el) => renderCSS(c, el) }},
        xml:      {{ label: 'SVG Preview',      printable: false, copyable: true, render: (c, el) => renderSVG(c, el) }},
        pdf:      {{ label: 'PDF Preview',      printable: false, copyable: false, render: (c, el) => renderPDF(c, el) }},
        image:    {{ label: 'Image Preview',    printable: false, copyable: false, render: (c, el) => renderImage(c, el) }}
      }};

      function getPreviewSpec(langOverride) {{
        const lang = langOverride || (currentFile ? detectFileLang(currentFile) : null);
        return (lang && PREVIEW_REGISTRY[lang]) ? PREVIEW_REGISTRY[lang] : null;
      }}

      function getPreviewLang() {{
        if (!currentFile) return null;
        const lang = detectFileLang(currentFile);
        return PREVIEW_REGISTRY[lang] ? lang : null;
      }}

      function updatePreviewButton() {{
        const btn = document.getElementById(CODEX_ID + '-preview-toggle');
        if (!btn) return;
        const lang = getPreviewLang();
        const spec = getPreviewSpec(lang);
        if (spec) {{
          btn.disabled = false;
          btn.style.opacity = previewOpen ? '1' : '0.6';
          btn.style.color = previewOpen ? accentColor : textColor;
          btn.title = spec.label + (previewOpen ? ' (Actif - cliquer pour masquer)' : ' (Inactif - cliquer pour afficher)');
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

        if (previewOpen && !getPreviewSpec()) {{
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
        const spec = getPreviewSpec(lang);
        if (!spec || !previewOpen) return;

        const content = getActiveContent();
        const container = document.getElementById(CODEX_ID + '-preview-content');
        const label = document.getElementById(CODEX_ID + '-preview-label');
        const printBtn = document.getElementById(CODEX_ID + '-preview-print');
        const copyBtn = document.getElementById(CODEX_ID + '-preview-copy');
        if (!container) return;

        if (label) label.textContent = spec.label;
        if (printBtn) printBtn.style.display = spec.printable ? 'flex' : 'none';
        if (copyBtn) copyBtn.style.display = spec.copyable ? 'flex' : 'none';

        spec.render(content, container);
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
          const bytes = latin1ToBytes(content);
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

      function renderImage(content, container) {{
        container.style.padding = '12px';
        try {{
          const bytes = latin1ToBytes(content);
          const ext = currentFile ? currentFile.substring(currentFile.lastIndexOf('.')).toLowerCase() : '.png';
          const mimeMap = {{ '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp', '.ico': 'image/x-icon' }};
          const blob = new Blob([bytes], {{ type: mimeMap[ext] || 'image/png' }});
          const url = URL.createObjectURL(blob);
          const oldImg = container.querySelector('img[data-blob]');
          if (oldImg && oldImg.src.startsWith('blob:')) URL.revokeObjectURL(oldImg.src);
          container.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;"><img data-blob="1" src="${{url}}" style="max-width:100%;max-height:100%;object-fit:contain;border-radius:4px;" /></div>`;
        }} catch (e) {{
          container.innerHTML = '<div style="color:#f38ba8; padding:12px;">\u274c Erreur lors du rendu de l\u2019image</div>';
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
        monacoWrap.innerHTML = '';
        const lang = detectFileLang(currentFile);
        editor = monaco.editor.create(monacoWrap, {{
          value: '', language: lang, theme: theme,
          readOnly: isReadOnly,
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
          window.echoCodexSetContent(pc.content, pc.filename, pc.isBinary);
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

      loadMonaco();
    }})();
    """
