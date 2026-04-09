"""
title: ECHO Shared Utils
author: ECHO Framework
version: 2.81
description: 2.81: WebPlayer v6.1 - Optimisation du moteur JS (Visualisation Auto Unifiée) et simplification de la gestion d'état.
"""

import os
import sqlite3
import orjson as json
import pybase64 as base64
import requests
import time
import asyncio
import glob
import hashlib
import re
import httpx
import random
import shutil
from typing import Optional, Tuple, List, Set, Any, Union, Dict, AsyncGenerator
from fastapi.responses import HTMLResponse

# Alias pour json standard si besoin
import orjson as std_json

# Importation directe (Strict)
from echo_constants import (
    ECHO_UPLOADS_DIR, ECHO_USER_DBS_DIR, ECHO_VERSION_PATH,
    GOOGLE_API_BASE_URL, ECHO_USER_AGENT, ECHO_USERS_ROOT
)

# ==============================================================================
# SECTION 0 : CLIENT HTTP GLOBAL (HTTP/2)
# ==============================================================================

_SHARED_ASYNC_CLIENT: Optional[httpx.AsyncClient] = None
_LAST_CLIENT_ACCESS: float = 0.0

async def _get_global_client(
    timeout: int = 600, 
    max_connections: int = 100,
    max_keepalive: int = 20,
    keepalive_expiry: int = 300
) -> httpx.AsyncClient:
    """Gestionnaire de client HTTP/2 STRICT (Mutualisé)."""
    global _SHARED_ASYNC_CLIENT, _LAST_CLIENT_ACCESS
    now = time.time()
    
    if _SHARED_ASYNC_CLIENT and (now - _LAST_CLIENT_ACCESS > timeout):
        old_client = _SHARED_ASYNC_CLIENT; _SHARED_ASYNC_CLIENT = None 
        try: await old_client.aclose()
        except: pass

    if _SHARED_ASYNC_CLIENT is None or _SHARED_ASYNC_CLIENT.is_closed:
        limits = httpx.Limits(
            max_keepalive_connections=max_keepalive, 
            max_connections=max_connections, 
            keepalive_expiry=keepalive_expiry
        )
        # HTTP/2 STRICT : Pas de fallback possible si h2 est installé
        _SHARED_ASYNC_CLIENT = httpx.AsyncClient(timeout=300, limits=limits, http2=True)
    
    _LAST_CLIENT_ACCESS = now
    return _SHARED_ASYNC_CLIENT

def get_stealth_headers(url: Optional[str] = None) -> Dict[str, str]:
    """Génère des en-têtes HTTP de haute fidélité pour simuler un navigateur réel (Stealth)."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "max-age=0",
        "sec-ch-ua": '"Chromium";v="123", "Not:A-Brand";v="8", "Google Chrome";v="123"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "image",
        "sec-fetch-mode": "no-cors",
        "sec-fetch-site": "cross-site",
        "sec-fetch-user": "?1",
        "Upgrade-Insecure-Requests": "1",
        "DNT": "1"
    }
    if url:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"
        headers["Host"] = parsed.netloc
        # Wikimedia et sites sensibles exigent un comportement documentaire pour les URLs directes
        if any(x in parsed.netloc for x in ["wikimedia", "wikipedia"]):
             headers["sec-fetch-dest"] = "document"
             headers["sec-fetch-mode"] = "navigate"
             headers["sec-fetch-site"] = "none"
             headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
    return headers

# ==============================================================================
# SECTION 1 : STANDARDS DE COMMUNICATION (MULTI-PARTS)
# ==============================================================================

def split_thought_process(text: str) -> Tuple[str, Optional[str]]:
    if not isinstance(text, str): return text, None
    for tag in ["think", "thought"]:
        pattern = rf"<{tag}>(.*?)</{tag}>"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            thoughts = match.group(1).strip()
            clean_text = re.sub(pattern, "", text, flags=re.DOTALL).strip()
            return clean_text, thoughts
    return text, None

def wrap_tool_output(text: str, status: dict = None, echo_tool_multiparts: List[dict] = None, nouveaux_fichiers: List[dict] = None) -> dict:
    if nouveaux_fichiers:
        json_str = json.dumps(nouveaux_fichiers, option=json.OPT_INDENT_2).decode('utf-8')
        text += f"\n\n```json:nouveaux_artefacts\n{json_str}\n```"
    return {"text": text, "status": status or {"status": "success"}, "echo_tool_multiparts": echo_tool_multiparts or []}

# ==============================================================================
# SECTION 2 : RÉSOLUTION DE FICHIERS & VERSIONS
# ==============================================================================

def generate_echo_file_id(user_id: str, chat_id: str) -> str:
    ts = int(time.time() * 1000)
    return f"U_{user_id}_C_{chat_id}_T_{ts}"

def resolve_upload_file_path(user_id: str, file_id: str, uploads_dir: str = ECHO_UPLOADS_DIR) -> Optional[str]:
    if not file_id: return None
    
    # 1. Recherche PRIORITAIRE dans le Coffre-Fort (Vault) de l'utilisateur
    if user_id and user_id != "anonymous" and "/" not in str(user_id):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        user_vault = os.path.join(ECHO_USERS_ROOT, safe_uid, "files")
        pattern = os.path.join(user_vault, f"{file_id}_*")
        matches = glob.glob(pattern)
        if matches: return matches[0]
        
    # 2. Recherche de SECOURS dans le dossier de transit (Uploads OWUI)
    pattern = os.path.join(uploads_dir, f"{file_id}_*")
    matches = glob.glob(pattern)
    return matches[0] if matches else None

def get_echo_version() -> str:
    try:
        if os.path.exists(ECHO_VERSION_PATH):
            with open(ECHO_VERSION_PATH, "r") as f: return f.read().strip()
    except: pass
    return ""

# ==============================================================================
# SECTION 3 : GESTION DES ÉVÉNEMENTS (OWUI COMPAT)
# ==============================================================================

class EchoEvents:
    def __init__(self, emitter: Any = None, caller: Any = None):
        self.emitter = emitter; self.caller = caller
    async def emit(self, event_type: str, data: dict):
        if self.emitter:
            try: await self.emitter({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Emit Error: {e}")
    async def status(self, description: str, done: bool = False, hidden: bool = False):
        await self.emit("status", {"description": description, "done": done, "hidden": hidden})
    async def toast(self, content: str, level: str = "info"):
        await self.emit("notification", {"type": level, "content": content})
    async def call(self, event_type: str, data: dict) -> Any:
        if self.caller:
            try: return await self.caller({"type": event_type, "data": data})
            except Exception as e: print(f"[EchoEvents] Call Error: {e}")
        return None
    async def input(self, title: str, message: str, placeholder: str = "", type: str = "text") -> Optional[str]:
        return await self.call("input", {"title": title, "message": message, "placeholder": placeholder, "type": type})
    async def confirm(self, title: str, message: str) -> bool:
        res = await self.call("confirmation", {"title": title, "message": message})
        return bool(res)

# ==============================================================================
# SECTION 3b : ECHO RICH UI FRAMEWORK (OWUI EMBEDDING)
# ==============================================================================

class EchoRichUI:
    @staticmethod
    def _get_boilerplate(content: str, title: str = "ECHO Visualizer") -> str:
        """Encapsule le contenu selon le standard strict Rich UI d'Open WebUI."""
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <title>{title}</title>
            <style>
                body {{ 
                    margin: 0; padding: 12px; 
                    font-family: -apple-system, sans-serif; 
                    background: transparent; 
                    color: inherit;
                    overflow: hidden;
                }}
                .rich-card {{
                    border-radius: 12px;
                    overflow: hidden;
                    background: rgba(255, 255, 255, 0.05);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
                }}
            </style>
        </head>
        <body>
            <div class="rich-card">
                {content}
            </div>
            <script>
                // Script de rapport de hauteur STRICT de la documentation Open WebUI
                function reportHeight() {{
                    const h = document.documentElement.scrollHeight;
                    parent.postMessage({{ type: 'iframe:height', height: h }}, '*');
                }}
                window.addEventListener('load', reportHeight);
                // Observation dynamique des changements de taille
                new ResizeObserver(reportHeight).observe(document.body);
            </script>
        </body>
        </html>
        """

    @classmethod
    def image_viewer(cls, target_data: str, is_url: bool = False, mime: str = "image/png", title: str = "Premium Viewer") -> HTMLResponse:
        """
        Génère un viewer d'image Premium avec Zoom interactif (Ctrl+Molette), Pan et Sélection.
        """
        src = target_data if is_url else f"data:{mime};base64,{target_data}"
        
        content = f"""
        <style>
            .img-container {{ 
                position: relative; width: 100%; height: 550px; 
                background: #0a0a0a; overflow: auto; 
                display: flex; justify-content: center; align-items: center;
                cursor: grab; user-select: none;
            }}
            .img-container:active {{ cursor: grabbing; }}
            #main-img {{ 
                max-width: none; max-height: none; 
                transition: transform 0.1s ease-out; 
                transform-origin: center center;
                will-change: transform;
            }}
            
            .hud-bar {{
                position: absolute; top: 12px; right: 12px; display: flex; gap: 10px; z-index: 1000;
                background: rgba(15, 15, 20, 0.85); padding: 6px 12px; border-radius: 25px; 
                backdrop-filter: blur(8px); border: 1px solid rgba(255,255,255,0.15);
            }}
            .hud-btn {{
                background: none; border: none; color: #ccc; font-size: 16px; cursor: pointer; 
                transition: all 0.2s; padding: 2px 6px;
            }}
            .hud-btn:hover {{ color: #fff; transform: scale(1.1); }}
            .hud-btn.active {{ color: #4ade80; text-shadow: 0 0 8px rgba(74, 222, 128, 0.5); }}
            
            .help-tooltip {{
                position: absolute; top: 55px; right: 12px; width: 220px;
                background: rgba(0, 0, 0, 0.95); color: #eee; padding: 12px; border-radius: 8px;
                border: 1px solid #4ade80; font-size: 11px; line-height: 1.8; z-index: 2000;
                display: none; box-shadow: 0 10px 30px rgba(0,0,0,0.6); pointer-events: none;
            }}
            .hud-btn:hover + .help-tooltip {{ display: block; }}
            
            #crop-box {{
                position: absolute; border: 2px dashed #4ade80; background: rgba(74, 222, 128, 0.1); 
                display: none; box-sizing: border-box; z-index: 400; pointer-events: none;
            }}
            .coords-panel {{
                position: absolute; bottom: 12px; left: 12px; background: rgba(15, 15, 20, 0.85); 
                color: #4ade80; padding: 4px 10px; border-radius: 6px; font-size: 11px; 
                display: none; z-index: 1000; font-family: monospace;
            }}
            .img-container::-webkit-scrollbar {{ width: 8px; height: 8px; }}
            .img-container::-webkit-scrollbar-thumb {{ background: rgba(255,255,255,0.2); border-radius: 4px; }}
        </style>

        <div class="img-container" id="v-container">
            <div class="hud-bar">
                <button class="hud-btn" title="Aide">❓</button>
                <div class="help-tooltip">
                    <b>COMMANDES ECHO :</b><br>
                    🖱️ <b>Ctrl + Molette</b> : Zoomer<br>
                    ✋ <b>Clic + Glisse</b> : Déplacer l'image<br>
                    ⛶ <b>Touche S</b> : Mode Sélection<br>
                    ❐ <b>Touche C</b> : Copier (Crop/Full)<br>
                    🔄 <b>Touche R</b> : Réinitialiser la vue
                </div>
                <button id="btn-sel" class="hud-btn" onclick="toggleSel()" title="Sélection (S)">⛶</button>
                <button id="btn-copy" class="hud-btn" onclick="exportMedia('copy')" title="Copier l'image (C)">❐</button>
                <button class="hud-btn" onclick="exportMedia('save')" title="Télécharger">📥</button>
                <button class="hud-btn" onclick="resetAll()" title="Réinitialiser (R)">↺</button>
            </div>
            
            <img src="{src}" id="main-img" alt="{title}" draggable="false">
            <div id="crop-box"></div>
            <div id="coords" class="coords-panel"></div>
        </div>

        <script>
            const img = document.getElementById('main-img');
            const container = document.getElementById('v-container');
            const crop = document.getElementById('crop-box');
            const coords = document.getElementById('coords');
            
            let selOn = false, isDragging = false, isPanning = false;
            let startX, startY, scrollLeft, scrollTop;
            let scale = 1;

            function toggleSel() {{
                selOn = !selOn;
                document.getElementById('btn-sel').classList.toggle('active', selOn);
                crop.style.display = 'none';
                coords.style.display = 'none';
                container.style.cursor = selOn ? 'crosshair' : 'grab';
            }}

            function resetAll() {{
                selOn = false; scale = 1;
                img.style.transform = `scale(${{scale}})`;
                document.getElementById('btn-sel').classList.remove('active');
                crop.style.display = 'none';
                coords.style.display = 'none';
                container.style.cursor = 'grab';
            }}

            function exportMedia(mode) {{
                const canvas = document.createElement('canvas');
                const ctx = canvas.getContext('2d');
                const natW = img.naturalWidth, natH = img.naturalHeight;

                let isCropped = false;
                if (selOn && crop.style.display !== 'none' && crop.offsetWidth > 5) {{
                    const rect = crop.getBoundingClientRect();
                    const iRect = img.getBoundingClientRect();
                    const scaleX = natW / iRect.width;
                    const scaleY = natH / iRect.height;
                    
                    const sx = (rect.left - iRect.left) * scaleX;
                    const sy = (rect.top - iRect.top) * scaleY;
                    const sw = rect.width * scaleX;
                    const sh = rect.height * scaleY;
                    
                    canvas.width = sw; canvas.height = sh;
                    ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
                    isCropped = true;
                }} else {{
                    canvas.width = natW; canvas.height = natH;
                    ctx.drawImage(img, 0, 0);
                }}

                let dataUrl;
                try {{
                    dataUrl = canvas.toDataURL('image/png');
                }} catch (e) {{
                    // Fallback natif si l'image distante lève une erreur de sécurité (CORS Tainted)
                    if (isCropped) {{
                        parent.postMessage({{ type: 'notification', data: {{ content: 'CORS: Impossible de rogner. L\\'image d\\'origine va s\\'ouvrir.', type: 'warning' }} }}, '*');
                    }}
                    window.open(img.src, '_blank');
                    return;
                }}

                if (mode === 'copy') {{
                    try {{
                        if (!navigator.clipboard || !window.ClipboardItem) throw new Error("API Clipboard absente.");
                        canvas.toBlob(async (blob) => {{
                            try {{
                                await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                                parent.postMessage({{ type: 'notification', data: {{ content: 'Image copiée !', type: 'success' }} }}, '*');
                                const btn = document.getElementById('btn-copy');
                                if(btn) {{ const old = btn.innerText; btn.innerText='✓'; setTimeout(()=>btn.innerText=old, 1000); }}
                            }} catch (err) {{
                                showFallbackOverlay(dataUrl);
                            }}
                        }}, 'image/png');
                    }} catch(err) {{
                        showFallbackOverlay(dataUrl);
                    }}
                }} else {{
                    const link = document.createElement('a');
                    link.download = "ECHO_" + (isCropped ? "Crop_" : "Full_") + Date.now() + ".png";
                    link.href = dataUrl;
                    link.click();
                }}
            }}

            function showFallbackOverlay(dataUrl) {{
                const over = document.createElement('div');
                over.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.85);z-index:9999;display:flex;flex-direction:column;align-items:center;justify-content:center;color:#fff;backdrop-filter:blur(4px);';
                over.innerHTML = '<p style="margin-bottom:15px;font-size:14px;background:#4ade80;color:#000;padding:4px 12px;border-radius:4px;font-weight:bold;">Mode HTTP/Iframe restreint. Faites Clic droit -> Copier l\\'image</p><img src="' + dataUrl + '" style="max-width:80%;max-height:75%;border:2px solid #555;border-radius:4px;box-shadow:0 0 20px rgba(0,0,0,0.5);" /><p style="margin-top:15px;font-size:12px;color:#aaa;cursor:pointer;">[ Cliquez n\\'importe où pour fermer ]</p>';
                over.onclick = () => over.remove();
                document.body.appendChild(over);
            }}

            // Zoom interactif
            container.addEventListener('wheel', (e) => {{
                if (e.ctrlKey) {{
                    e.preventDefault();
                    const delta = e.deltaY > 0 ? 0.9 : 1.1;
                    scale = Math.min(Math.max(0.1, scale * delta), 10);
                    img.style.transform = `scale(${{scale}})`;
                }}
            }}, {{ passive: false }});

            container.onmousedown = (e) => {{
                if (e.target.closest('.hud-bar')) return;
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
                    coords.textContent = `${{Math.round(width*rx)}}x${{Math.round(height*ry)}}px`;
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

            // Shortcuts
            window.addEventListener('keydown', (e) => {{
                if (e.key.toLowerCase() === 's') toggleSel();
                if (e.key.toLowerCase() === 'r') resetAll();
            }});
        </script>
        """
        html = cls._get_boilerplate(content, title)
        return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

    @classmethod
    def map_viewer(cls, query: str, title: str = "Localisation") -> HTMLResponse:
        """
        Génère un HTMLResponse pour l'embed Google Maps natif (sans clé API).
        Supporte les recherches de lieux et les itinéraires (ex: "A vers B").
        """
        from urllib.parse import quote
        # Nettoyage et encodage de la requête pour l'URL Google Maps
        safe_query = quote(query.strip())
        
        content = f"""
        <div style="width: 100%; height: 500px; background: #1a1a1b;">
            <iframe 
                width="100%" 
                height="100%" 
                frameborder="0" 
                style="border:0; display: block;" 
                src="https://www.google.com/maps?q={safe_query}&output=embed" 
                allowfullscreen
                loading="lazy">
            </iframe>
        </div>
        """
        html = cls._get_boilerplate(content, title)
        return HTMLResponse(content=html, headers={"Content-Disposition": "inline"})

# ==============================================================================
# SECTION 4 : SERVICE D'AUTHENTIFICATION (DAL) & CLIENT GEMINI
# ==============================================================================

class EchoAuth:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system"):
        # Flexibilité pour les appels sans user_id
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.user_db_dir = db_dir

    def _get_db_path(self, user_id: str = None) -> str:
        uid = user_id or self.user_id
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        path = os.path.join(ECHO_USERS_ROOT, safe_uid, "identity.db")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def get_api_keys(self, user_id: str = None) -> List[str]:
        """Récupère la liste des clés API Google (primaire et optionnellement secondaire)."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return []
        keys = []
        try:
            conn = sqlite3.connect(f"file://{db_path}?mode=ro", uri=True, timeout=5.0)
            cursor = conn.cursor()
            # On récupère les deux clés potentielles
            for key_name in ['google_api_key', 'google_api_key_secondary']:
                cursor.execute("SELECT value FROM auth_data WHERE key = ?", (key_name,))
                row = cursor.fetchone()
                if row and row[0]: keys.append(row[0])
            conn.close()
        except: pass
        return keys

    def save_api_key(self, key_name: str, value: str, user_id: str = None):
        """Enregistre ou met à jour une clé API."""
        db_path = self._get_db_path(user_id)
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key_name, value, int(time.time())))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur sauvegarde clé {key_name}: {e}")

    def delete_api_key(self, key_name: str, user_id: str = None):
        """Supprime une clé API."""
        db_path = self._get_db_path(user_id)
        if not os.path.exists(db_path): return
        try:
            with sqlite3.connect(db_path, timeout=10.0) as conn:
                conn.execute("DELETE FROM auth_data WHERE key = ?", (key_name,))
                conn.commit()
        except Exception as e:
            print(f"[EchoAuth] Erreur suppression clé {key_name}: {e}")

class EchoGeminiClient:
    """Moteur factorisé pour les appels API Gemini avec Fallback et Résilience."""

    @staticmethod
    async def call(
        keys: List[str],
        target_model: str,
        payload: dict,
        threshold: int = 2,
        max_retries: int = 3,
        events: Optional[EchoEvents] = None,
        timeout: int = 120
    ) -> dict:
        """Appel JSON classique (pour Filtres et Outils)."""
        if not keys: raise ValueError("Aucune clé API fournie.")
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2 # RETRY_TIMEBASE par défaut

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:generateContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}

            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                
                if resp.status_code == 200:
                    return resp.json()
                
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    # Condition de bascule sur la clé de secours
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue

                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(0.7, 1.3)
                        if events: await events.status(f"⚠️ Surcharge API Google ({resp.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                        await asyncio.sleep(wait_time)
                        current_delay *= 2
                        continue
                
                resp.raise_for_status()

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                raise e
        
        raise Exception(f"Échec après {max_retries} tentatives.")

    @staticmethod
    async def stream(
        keys: List[str],
        target_model: str,
        payload: dict,
        threshold: int = 2,
        max_retries: int = 5,
        events: Optional[EchoEvents] = None,
        process_callback: Optional[Any] = None,
        timeout: int = 300
    ) -> AsyncGenerator[Union[str, Dict], None]:
        """Appel SSE avec streaming (pour le Pipe)."""
        if not keys: yield "🚫 Aucune clé API configurée."; return
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2 # RETRY_TIMEBASE par défaut

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            # alt=sse est crucial pour le streaming Gemini
            api_url = f"{GOOGLE_API_BASE_URL}/models/{target_model}:streamGenerateContent?key={api_key}&alt=sse"
            headers = {
                "x-goog-api-key": api_key,
                "Content-Type": "application/json",
                "User-Agent": ECHO_USER_AGENT
            }

            try:
                async with client.stream("POST", api_url, content=json.dumps(payload), headers=headers, timeout=timeout) as r:
                    if r.status_code in [429, 500, 503]:
                        consecutive_errors += 1
                        if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                            active_key_idx += 1
                            consecutive_errors = 0
                            if events: await events.status(f"🔄 Surcharge API ({r.status_code}). Bascule sur la clé de secours...", done=False)
                            continue

                        if attempt < max_retries:
                            wait_time = current_delay * random.uniform(0.7, 1.3)
                            if events: await events.status(f"⚠️ Surcharge API Google ({r.status_code}). Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                            await asyncio.sleep(wait_time)
                            current_delay *= 3
                            continue
                        else: yield f"🚫 Erreur API Google ({r.status_code})."; return
                    
                    r.raise_for_status()
                    
                    if r.http_version != "HTTP/2":
                        yield "🚫 Erreur de protocole : HTTP/2 obligatoire pour Gemini AI Studio."; return
                    
                    if process_callback:
                        async for chunk in process_callback(r): yield chunk
                break

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    if events: await events.status(f"⚠️ Instabilité réseau. Essai {attempt + 1}/{max_retries} dans {wait_time:.1f}s...", done=False)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                else: yield f"🚫 Erreur système : {str(e)}"; return

    @staticmethod
    async def embed(
        keys: List[str],
        model: str,
        content: dict,
        threshold: int = 2,
        max_retries: int = 3,
        events: Optional[EchoEvents] = None,
        timeout: int = 30
    ) -> dict:
        """Appel Embedding (pour les Outils de mémoire)."""
        if not keys: raise ValueError("Aucune clé API fournie.")
        
        client = await _get_global_client()
        active_key_idx = 0
        consecutive_errors = 0
        current_delay = 2

        for attempt in range(max_retries + 1):
            api_key = keys[active_key_idx]
            api_url = f"{GOOGLE_API_BASE_URL}/models/{model}:embedContent?key={api_key}"
            headers = {"Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            payload = {"model": f"models/{model}", "content": content}

            try:
                resp = await client.post(api_url, json=payload, headers=headers, timeout=timeout)
                
                if resp.status_code == 200:
                    return resp.json()
                
                if resp.status_code in [429, 500, 503]:
                    consecutive_errors += 1
                    if consecutive_errors >= threshold and active_key_idx < len(keys) - 1:
                        active_key_idx += 1
                        consecutive_errors = 0
                        if events: await events.status(f"🔄 Surcharge API Embedding ({resp.status_code}). Bascule sur la clé de secours...", done=False)
                        continue

                    if attempt < max_retries:
                        wait_time = current_delay * random.uniform(0.7, 1.3)
                        await asyncio.sleep(wait_time)
                        current_delay *= 2
                        continue
                
                resp.raise_for_status()

            except Exception as e:
                if attempt < max_retries:
                    wait_time = current_delay * random.uniform(0.7, 1.3)
                    await asyncio.sleep(wait_time)
                    current_delay *= 2
                    continue
                raise e
        
        raise Exception(f"Échec Embedding après {max_retries} tentatives.")

# ==============================================================================
# SECTION 5 : GESTIONNAIRE D'ÉTAT (SQLite)
# ==============================================================================

class EchoStateManager:
    def __init__(self, db_dir: str = ECHO_USER_DBS_DIR, user_id: str = "system", chat_id: Optional[str] = None):
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.db_dir = db_dir; self.chat_id = chat_id
        
        safe_uid = "".join(x for x in str(self.user_id) if x.isalnum() or x in "-_")
        self.user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        os.makedirs(os.path.join(self.user_dir, "files"), exist_ok=True)
        os.makedirs(os.path.join(self.user_dir, "chats"), exist_ok=True)

        if chat_id:
            safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
            self.db_path = os.path.join(self.user_dir, "chats", f"{safe_cid}.db")
        else: self.db_path = os.path.join(self.user_dir, "identity.db")
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;"); return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                # Tables existantes (Suture & Payloads)
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, created_at INTEGER)")
                
                # --- NOUVELLE TABLE DES OMBRES (Suture par ID) ---
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")

                # Migration du schéma (Ajout de message_id aux anciennes tables)
                try: conn.execute("ALTER TABLE suture_index ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE rich_payloads ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE tool_journal ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE processed_files ADD COLUMN file_content TEXT")
                except: pass
                try: conn.execute("ALTER TABLE processed_files ADD COLUMN message_id TEXT")
                except: pass

                # Autres tables de l'infrastructure
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_inv_hash ON suture_index (invariant_hash)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS thought_archive (cumulative_hash TEXT PRIMARY KEY, raw_thought TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] Init DB Error: {e}")

    # --- MÉTHODES DE SHADOWING (SUTURE PAR ID) ---
    
    def save_message_shadow(self, message_id: str, chat_id: str, role: str, parts: List[dict]):
        """Scelle l'état complet (parts) d'un message pour une restauration Bit-Perfect."""
        if not message_id: return
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) VALUES (?, ?, ?, ?, ?)",
                    (message_id, chat_id, role, std_json.dumps(parts).decode('utf-8'), int(time.time()))
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] Save Shadow Error: {e}")

    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        """Récupère le moulage original d'un message SEULEMENT s'il correspond au timestamp physique."""
        if not message_id: return None
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?", 
                    (message_id, int(updated_at))
                ).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    # --- MÉTHODES DE HACHAGE (LEGACY & SUTURE) ---

    def calculate_invariant_hash(self, role: str, content: Any, tool_io: dict = None) -> str:
        norm_c = content.strip() if isinstance(content, str) else json.dumps(content, option=json.OPT_SORT_KEYS).decode('utf-8')
        norm_t = json.dumps(tool_io, option=json.OPT_SORT_KEYS).decode('utf-8') if tool_io else ""
        return hashlib.sha256(f"{role.lower()}|{norm_c}|{norm_t}".encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, inv: str, parent: str = None) -> str:
        return hashlib.sha256(f"{inv}|{parent or ''}".encode("utf-8")).hexdigest()

    def get_session_registry(self, chat_id: str, active_message_ids: Optional[List[str]] = None) -> dict:
        reg = {}
        try:
            with self._get_connection() as conn:
                if active_message_ids:
                    placeholders = ','.join('?' for _ in active_message_ids)
                    query = f"SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ? AND message_id IN ({placeholders})"
                    params = [chat_id] + active_message_ids
                    rows = conn.execute(query, params).fetchall()
                else:
                    rows = conn.execute("SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ?", (chat_id,)).fetchall()
                
                for row in rows:
                    reg[row[0]] = {
                        "id": row[1],
                        "mime": row[2] or "application/octet-stream",
                        "statut": row[3] or "unknown"
                    }
        except: pass
        return reg
    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str, content: Optional[str] = None, message_id: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp, file_content, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time()), content, message_id))
                conn.commit()
        except:
            try:
                with self._get_connection() as conn:
                    conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time())))
                    conn.commit()
            except: pass

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) VALUES (?, ?, ?, ?, ?)", (call_id, signature, function_name, json.dumps(args).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?", (call_id,)).fetchone()
                if row: return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except: pass
        return None

    def get_rich_payload(self, inv: str) -> Optional[List[dict]]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT rich_parts_json FROM rich_payloads WHERE invariant_hash = ?", (inv,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_rich_payload(self, inv: str, rich: List[dict], message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)",
                    (inv, json.dumps(rich).decode('utf-8'), message_id, int(time.time()))
                )
                conn.commit()
        except: pass

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (cumul, chat_id, inv, parent, message_id, int(time.time()))
                )
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):
        try:
            with self._get_connection() as conn:
                if sig: 
                    try:
                        conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, model_id, updated_at) VALUES (?, ?, ?, ?, ?)", (cumul, sig, message_id, model_id, int(time.time())))
                    except:
                        # Fallback pour l'ancien schéma de BDD sans model_id
                        conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, updated_at) VALUES (?, ?, ?, ?)", (cumul, sig, message_id, int(time.time())))
                
                if thought: conn.execute("INSERT OR REPLACE INTO thought_archive (cumulative_hash, raw_thought, updated_at) VALUES (?, ?, ?)", (cumul, thought, int(time.time())))
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, json.dumps(tool_io).decode('utf-8'), int(time.time())))
                
                # Table dédiée pour la persistance du modèle actuel
                if model_id:
                    conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER)")
                    conn.execute("INSERT OR REPLACE INTO session_state (id, last_model_id, updated_at) VALUES (1, ?, ?)", (model_id, int(time.time())))
                conn.commit()
        except: pass

    def get_last_active_model(self) -> Optional[str]:
        """Récupère l'ID du dernier modèle ayant répondu dans cette session."""
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT last_model_id FROM session_state WHERE id = 1").fetchone()
                if row: return row[0]
        except: pass
        return None

    def get_thought_signature(self, cumul: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE cumulative_hash = ?", (cumul,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_tool_io(self, cumul: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT io_json FROM tool_journal WHERE cumulative_hash = ?", (cumul,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def save_context_stats(self, stats: dict):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)", (std_json.dumps(stats).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_last_context_stats(self) -> dict:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT data FROM context_stats WHERE id = 1").fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return {}

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        old_path = resolve_upload_file_path(self.user_id, file_id)
        if not old_path: return False
        new_path = os.path.join(self.user_dir, "files", os.path.basename(old_path))
        try:
            if not os.path.exists(new_path): shutil.move(old_path, new_path)
            return True
        except: return False

# ==============================================================================
# SECTION 6 : HUD & UI COMPONENTS (ECHO UI)
# ==============================================================================

class EchoUI:
    @staticmethod
    def _generate_webplayer_js(b64: str, mime: str, metadata: list, current_url: str, hud_id: str, state_key: str) -> str:
        """Génère le moteur de pilotage ECHO WEBPLAYER (Mode Visualisation Uniquement)."""
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

                    // --- VUE (ZOOM / PAN) ---
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

                    // --- GESTION HEADER ---
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
                        const a = document.getElementById(HUD_ID + "-area");
                        if(a) a.style.display = 'block';
                        const size = this.getBestSize(this.ratio, 0.5);
                        this.hud.style.width = size.w + "px";
                        this.hud.style.height = (size.h + 40) + "px";
                        this.posX = (window.innerWidth - size.w) / 2;
                        this.posY = (window.innerHeight - (size.h + 40)) / 2;
                        this.clampHud();
                        
                        const img = document.getElementById(HUD_ID + "-img");
                        if (img && img.naturalWidth && a) {{
                            const r = a.getBoundingClientRect();
                            const sc = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
                            this.imgScale = sc; this.imgX = 0; this.imgY = 0;
                            matrix.style.transform = "scale(" + sc + ") translate3d(0px, 0px, 0px)";
                        }}
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
                    
                    document.getElementById(HUD_ID + "-btn-max").onclick = (e) => {{
                        e.stopPropagation();
                        const a = document.getElementById(HUD_ID + "-area");
                        if(a) a.style.display = 'block';
                        
                        const maxW = window.innerWidth - 30;
                        const maxH = window.innerHeight - 30 - 40;
                        let nw = maxW;
                        let nh = nw * this.ratio;
                        if (nh > maxH) {{
                            nh = maxH;
                            nw = nh / this.ratio;
                        }}
                        
                        this.hud.style.width = nw + "px";
                        this.hud.style.height = (nh + 40) + "px";
                        this.posX = (window.innerWidth - nw) / 2;
                        this.posY = (window.innerHeight - (nh + 40)) / 2;
                        this.clampHud();
                        
                        const img = document.getElementById(HUD_ID + "-img");
                        if (img && img.naturalWidth && a) {{
                            const r = a.getBoundingClientRect();
                            const sc = Math.min(r.width / img.naturalWidth, r.height / img.naturalHeight);
                            this.imgScale = sc; this.imgX = 0; this.imgY = 0;
                            matrix.style.transform = "scale(" + sc + ") translate3d(0px, 0px, 0px)";
                        }}
                        this.saveState();
                    }};

                    document.getElementById(HUD_ID + "-btn-close").onclick = () => {{
                        this.hud.remove();
                    }};
                    
                    // Resizers (hdl)
                    this.hud.querySelectorAll('.hdl').forEach(hdl => {{
                        hdl.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation();
                            const isR = hdl.classList.contains('tr') || hdl.classList.contains('br');
                            const isT = hdl.classList.contains('tl') || hdl.classList.contains('tr');
                            const isL = hdl.classList.contains('tl') || hdl.classList.contains('bl');
                            const isB = hdl.classList.contains('bl') || hdl.classList.contains('br');
                            const startW = this.hud.offsetWidth, startH = this.hud.offsetHeight, startX = this.posX, startY = this.posY;
                            const ox = e.clientX, oy = e.clientY;
                            
                            document.onmousemove = (me) => {{
                                let nw = isR ? (startW + (me.clientX - ox)) : (startW - (me.clientX - ox));
                                if (nw < 300) nw = 300;
                                let nh = (nw * this.ratio) + 40;
                                
                                const maxW = window.innerWidth - 30;
                                const maxH = window.innerHeight - 30;
                                if (nw > maxW || nh > maxH) {{
                                    if ((maxW * this.ratio + 40) <= maxH) {{
                                        nw = maxW;
                                        nh = (nw * this.ratio) + 40;
                                    }} else {{
                                        nh = maxH;
                                        nw = (nh - 40) / this.ratio;
                                    }}
                                }}
                                
                                if (isL && !isR) this.posX = startX + (startW - nw);
                                if (isT && !isB) this.posY = startY + (startH - nh);
                                this.hud.style.width = nw + 'px'; this.hud.style.height = nh + 'px';
                                this.clampHud();
                                
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
                            document.onmouseup = () => {{ document.onmousemove = null; this.saveState(); }};
                        }};
                    }});
                }},

                create: function(data) {{
                    const old = document.getElementById(HUD_ID); if(old) old.remove();
                    this.hud = document.createElement('div');
                    this.hud.id = HUD_ID;
                    this.hud.style.cssText = 'position:fixed; top:0px; left:0px; z-index:10000; background:rgba(20,20,20,0.98); backdrop-filter:blur(15px); border:1px solid #444; border-radius:10px; box-shadow:0 15px 60px rgba(0,0,0,0.8); color:white; font-family:sans-serif; display:flex; flex-direction:column; min-width:300px; max-width:95vw; overflow:hidden; transition: opacity 0.3s; box-sizing: border-box;';
                    
                    this.hud.innerHTML = `
                        <div id="${{HUD_ID}}-header" style="padding:8px 12px; background:rgba(0,0,0,0.5); display:flex; align-items:center; gap:8px; border-bottom:1px solid #333; cursor:move; user-select:none; flex-wrap:wrap; box-sizing:border-box;">
                            <span style="font-size:10px; font-weight:bold; padding:2px 6px; border-radius:4px; text-transform:uppercase; background:#10b981; color:black;">🤖 AUTO</span>
                            <input id="${{HUD_ID}}-url" type="text" style="flex:1; background:rgba(255,255,255,0.05); border:1px solid #444; border-radius:4px; color:#aaa; font-size:11px; padding:4px 8px; outline:none; min-width:150px;" readonly />
                            <div style="display:flex; align-items:center; gap:10px;">
                                <button id="${{HUD_ID}}-btn-help" title="Aide" style="background:none; border:none; color:#888; cursor:pointer; font-size:14px;">?</button>
                                <button id="${{HUD_ID}}-btn-zoom" title="Fit to window" style="background:none; border:none; color:#888; cursor:pointer; font-size:14px;">🔍</button>
                                <button id="${{HUD_ID}}-btn-reset" title="Default size (50%)" style="background:none; border:none; color:#888; cursor:pointer; font-size:14px;">🔄</button>
                                <button id="${{HUD_ID}}-btn-min" title="Réduire" style="background:none; border:none; color:#888; cursor:pointer; font-size:14px; font-weight:bold;">_</button>
                                <button id="${{HUD_ID}}-btn-max" title="Plein Écran" style="background:none; border:none; color:#888; cursor:pointer; font-size:14px; font-weight:bold;">□</button>
                                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:18px; font-weight:bold;">×</button>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-area" style="flex:1; position:relative; background:black; overflow:hidden; cursor:crosshair;">
                            <div id="${{HUD_ID}}-matrix" style="width:100%; height:100%; transform-origin: 0 0; will-change: transform;">
                                <img id="${{HUD_ID}}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" draggable="false" />
                                <div id="${{HUD_ID}}-hitboxes" style="position:absolute; inset:0; pointer-events:none;"></div>
                            </div>
                            
                            <div id="${{HUD_ID}}-help-overlay" style="position:absolute; inset:0; background:rgba(0,0,0,0.85); display:none; flex-direction:column; align-items:center; justify-content:center; z-index:100; padding:20px; text-align:center;">
                                <h3 style="color:#4ade80; margin-bottom:15px;">ECHO WEBPLAYER</h3>
                                <div style="font-size:12px; line-height:1.8; color:#eee;">
                                    🖱️ <b>Ctrl + Molette</b> : Zoomer<br>
                                    ✋ <b>Clic milieu (ou Alt+Clic)</b> : Déplacer la vue<br>
                                </div>
                                <button onclick="document.getElementById('${{HUD_ID}}-help-overlay').style.display='none'" style="margin-top:20px; background:#4ade80; border:none; color:black; padding:5px 15px; border-radius:4px; cursor:pointer; font-weight:bold;">Compris</button>
                            </div>
                        </div>
                        
                        <div class="hdl tl" style="position:absolute; width:15px; height:15px; left:0; top:0; cursor:nwse-resize; z-index:101;"></div>
                        <div class="hdl tr" style="position:absolute; width:15px; height:15px; right:0; top:0; cursor:nesw-resize; z-index:101;"></div>
                        <div class="hdl bl" style="position:absolute; width:15px; height:15px; left:0; bottom:0; cursor:nesw-resize; z-index:101;"></div>
                        <div class="hdl br" style="position:absolute; width:15px; height:15px; right:0; bottom:0; cursor:nwse-resize; z-index:101;"></div>
                    `;
                    document.body.appendChild(this.hud);
                    this.attachEvents();
                    
                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                    if (saved && saved.w) {{
                        this.posX = saved.x; this.posY = saved.y;
                        this.hud.style.width = saved.w + 'px';
                        if (saved.m) {{
                            const a = document.getElementById(HUD_ID + "-area"); if(a) a.style.display = 'none';
                            this.hud.style.height = 'auto';
                        }}
                        this.clampHud();
                    }}
                }},

                update: function(data) {{
                    if (!this.hud || !document.getElementById(HUD_ID)) {{ this.create(data); }}
                    
                    const img = document.getElementById(HUD_ID + "-img");
                    const urlInput = document.getElementById(HUD_ID + "-url");
                    const hitboxes = document.getElementById(HUD_ID + "-hitboxes");
                    const matrix = document.getElementById(HUD_ID + "-matrix");
                    const area = document.getElementById(HUD_ID + "-area");

                    urlInput.value = data.url;

                    img.onload = () => {{
                        const natW = img.naturalWidth;
                        const natH = img.naturalHeight;
                        this.ratio = natH / natW;
                        
                        const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                        let targetW, targetH;

                        if (saved && saved.w) {{
                            targetW = Math.min(saved.w, window.innerWidth * 0.95);
                            if (saved.m) {{
                                targetH = 'auto';
                            }} else {{
                                targetH = Math.min(targetW * this.ratio + 40, window.innerHeight * 0.95) + 'px';
                            }}
                        }} else {{
                            const size = this.getBestSize(this.ratio, 0.5);
                            targetW = size.w; 
                            targetH = (size.h + 40) + 'px';
                            this.posX = (window.innerWidth - targetW) / 2;
                            this.posY = (window.innerHeight - parseInt(targetH)) / 2;
                        }}

                        this.hud.style.width = targetW + 'px';
                        this.hud.style.height = targetH;
                        this.clampHud();

                        matrix.style.width = natW + "px";
                        matrix.style.height = natH + "px";
                        
                        const areaRect = area.getBoundingClientRect();
                        const scaleW = areaRect.width / natW;
                        const scaleH = areaRect.height / natH;
                        this.imgScale = Math.min(scaleW, scaleH);
                        this.imgX = 0; this.imgY = 0;
                        matrix.style.transform = "scale(" + this.imgScale + ") translate3d(0px, 0px, 0px)";
                        
                        hitboxes.innerHTML = "";
                        data.metadata.forEach(el => {{
                            const box = document.createElement('div');
                            box.style.cssText = `position:absolute; left:${{el.x}}px; top:${{el.y}}px; width:${{el.w}}px; height:${{el.h}}px; pointer-events:auto; cursor:pointer; z-index:5;`;
                            box.onmouseover = () => box.style.background = "rgba(74, 222, 128, 0.25)";
                            box.onmouseout = () => box.style.background = "transparent";
                            box.onclick = (e) => {{
                                e.stopPropagation();
                                parent.postMessage({{ type: "notification", data: {{ content: "⚠️ Le navigateur est en mode Automatique exclusif.", type: "info" }} }}, "*");
                            }};
                            hitboxes.appendChild(box);
                        }});
                    }};
                    img.src = "data:" + data.mime + ";base64," + data.b64;
                }}
            }};
        }}
        
        window[ENGINE_KEY].update(payload);
    }})();
"""

    @staticmethod
    async def monitor_ECHO(events: EchoEvents, b64: str, metadata: list, current_url: str, hud_id: str = "echo-webplayer", title: str = "ECHO WEBPLAYER", state_key: str = "echo_webplayer_state"):
        """Interface unifiée WEBPLAYER (Mode Visualisation)."""
        js_code = EchoUI._generate_webplayer_js(b64, "image/png", metadata, current_url, hud_id, state_key)
        try:
            import asyncio
            await events.call("execute", {"code": js_code})
        except Exception as e:
            pass

    @staticmethod
    async def deploy_context_gauge(events: EchoEvents, plan_name: str, credits_val: str, quota_str: str, c_t: int, active_p_t: int, g_t: int, max_t: int, cache_pct: float, prompt_pct: float, gen_pct: float):
        js_code = f"""
        (function() {{
            var navContainer = document.querySelector('nav div.flex.items-center.w-full.max-w-full');
            if (!navContainer) return;
            var rightControls = navContainer.querySelector('div.self-start.flex.flex-none.items-center');
            var oldHud = document.getElementById('echo-nav-context-hud');
            if (oldHud) oldHud.remove();
            var hud = document.createElement('div');
            hud.id = 'echo-nav-context-hud';
            hud.style.cssText = 'display:flex;align-items:center;margin:0 12px;flex-grow:8;width:66%;min-width:350px;opacity:0.9;transition:opacity 0.2s;';
            hud.onmouseover = function() {{ this.style.opacity = '1'; }};
            hud.onmouseout = function() {{ this.style.opacity = '0.9'; }};
            var billingInfo = "";
            if ("{plan_name}") billingInfo += `💳 {plan_name} | {quota_str}`;
            if ("{credits_val}" !== "0") billingInfo += `🔋 {credits_val} crédits IA | `;
            hud.title = billingInfo + `🟪 Cache: {c_t} | 🟩 User/Prompt: {active_p_t} | 🟧 Generated: {g_t} | ⬜ Max: {max_t}`;
            var label = document.createElement('span');
            label.innerText = 'CTX'; label.style.cssText = 'font-size:10px;font-weight:bold;color:var(--color-gray-500, #6b7280);margin-right:6px;white-space:nowrap;';
            if (window.innerWidth < 640) label.style.display = 'none';
            hud.appendChild(label);
            var barContainer = document.createElement('div');
            barContainer.style.cssText = 'display:flex;width:100%;height:8px;background-color:rgba(128,128,128,0.2);border-radius:4px;overflow:hidden;';
            var bars = [['#8b5cf6', {cache_pct}], ['#10b981', {prompt_pct}], ['#f59e0b', {gen_pct}]];
            bars.forEach(b => {{
                var div = document.createElement('div');
                div.style.width = b[1] + '%'; div.style.backgroundColor = b[0];
                barContainer.appendChild(div);
            }});
            hud.appendChild(barContainer);
            if (rightControls) navContainer.insertBefore(hud, rightControls); else navContainer.appendChild(hud);
        }})();
        """
        await events.emit("execute", {"code": js_code})
