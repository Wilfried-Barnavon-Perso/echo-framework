"""
title: ECHO File Content Explorer
author: ECHO Framework
version: 1.23
description: 1.23: Forced user_id in resolve_upload_file_path.
"""

import os
import sys
import glob
import json
import base64
import random
import uuid
import asyncio
import httpx
import hashlib
import zlib
import re
from urllib.parse import urlparse, quote
from typing import Optional, List, Dict, Any, Union, Tuple, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoAuth, EchoEvents, resolve_upload_file_path, wrap_tool_output, split_thought_process, generate_echo_file_id
from echo_constants import ECHO_USER_AGENT, ECHO_UPLOADS_DIR, get_gemini_mime

def _generate_image_viewer_js(b64: str, mime: str, file_id: str) -> str:
    """Générateur du HUD pour la visualisation d'images statiques."""
    return f"""
    (function() {{
        const HUD_ID = 'echo-viewer-{file_id}';
        const STATE_KEY = 'echo_viewer_state_{file_id}';
        const payload = {{ b64: "{b64}", mime: "{mime}", fid: "{file_id}" }};

        if (!window.echoImageViewerEngine) {{
            window.echoImageViewerEngine = {{
                hud: null, isCropping: false, zoomActive: false, ratio: 1.0, posX: 0, posY: 0,

                getBestSize: function(ratio, percent = 0.25) {{
                    const vw = window.innerWidth, vh = window.innerHeight;
                    let w = Math.sqrt(percent * vw * vh / ratio);
                    let h = w * ratio;
                    if (w > vw * 0.97) {{ w = vw * 0.97; h = w * ratio; }}
                    if (h > vh * 0.97) {{ h = vh * 0.97; w = h / ratio; }}
                    return {{ w, h }};
                }},

                clampHud: function() {{
                    if (!this.hud) return;
                    const vw = window.innerWidth, vh = window.innerHeight;
                    const rect = this.hud.getBoundingClientRect();
                    const marginW = 0.015 * vw, marginH = 0.015 * vh;
                    if (this.posX < marginW) this.posX = marginW;
                    if (this.posY < marginH) this.posY = marginH;
                    if (this.posX + rect.width > vw - marginW) this.posX = vw - marginW - rect.width;
                    if (this.posY + rect.height > vh - marginH) this.posY = vh - marginH - rect.height;
                    this.hud.style.transform = "translate3d(" + this.posX + "px, " + this.posY + "px, 0px)";
                }},

                saveState: function(isFS = null) {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const isM = area && area.style.display === 'none';
                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || '{{}}');
                    localStorage.setItem(STATE_KEY, JSON.stringify({{
                        w: this.hud.offsetWidth, x: this.posX, y: this.posY, m: isM, f: isFS !== null ? isFS : (saved.f || false)
                    }}));
                }},

                applyTransition: function(enabled) {{
                    if (!this.hud) return;
                    this.hud.style.transition = enabled ? 'opacity 0.3s, transform 0.3s ease-out, width 0.3s ease-out, height 0.3s ease-out' : 'opacity 0.3s';
                }},

                exportMedia: async function(mode) {{
                    const img = document.getElementById(HUD_ID + "-img");
                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    const area = document.getElementById(HUD_ID + "-area");
                    const canvas = document.createElement('canvas');
                    const ctx = canvas.getContext('2d');
                    const natW = img.naturalWidth, natH = img.naturalHeight;

                    if (this.isCropping && cropBox.style.display !== 'none') {{
                        const rect = cropBox.getBoundingClientRect(), aRect = area.getBoundingClientRect();
                        const scaleX = natW / aRect.width, scaleY = natH / aRect.height;
                        const sx = (rect.left - aRect.left) * scaleX, sy = (rect.top - aRect.top) * scaleY;
                        const sw = rect.width * scaleX, sh = rect.height * scaleY;
                        canvas.width = sw; canvas.height = sh;
                        ctx.drawImage(img, sx, sy, sw, sh, 0, 0, sw, sh);
                    }} else {{
                        canvas.width = natW; canvas.height = natH;
                        ctx.drawImage(img, 0, 0);
                    }}

                    if (mode === 'copy') {{
                        if (!navigator.clipboard || !navigator.clipboard.write) {{
                            const win = window.open();
                            win.document.write('<p>Mode non-sécurisé (HTTP). <br>Faites <b>Clic droit -> Copier</b> :</p><img src="' + canvas.toDataURL('image/png') + '" style="max-width:100%; border:1px solid #ccc;" />');
                            return;
                        }}
                        canvas.toBlob(async (blob) => {{
                            try {{
                                await navigator.clipboard.write([new ClipboardItem({{ 'image/png': blob }})]);
                                const btn = document.getElementById(HUD_ID + "-btn-copy");
                                const old = btn.innerText; btn.innerText = '✓'; btn.style.color = '#4ade80';
                                setTimeout(() => {{ btn.innerText = old; btn.style.color = '#aaa'; }}, 1000);
                            }} catch (err) {{ alert("Erreur copie : " + err); }}
                        }}, 'image/png');
                    }} else {{
                        const link = document.createElement('a');
                        const label = this.isCropping ? 'Crop' : 'Full';
                        link.download = "ECHO_IMG_" + label + "_" + Date.now() + ".png";
                        link.href = canvas.toDataURL('image/png');
                        link.click();
                    }}
                }},

                attachEvents: function() {{
                    if (!this.hud) return;
                    const area = document.getElementById(HUD_ID + "-area");
                    const lens = document.getElementById(HUD_ID + "-lens");
                    const img = document.getElementById(HUD_ID + "-img");
                    
                    this.hud.ondblclick = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        this.clampHud();
                        if(area) area.style.display = 'flex';
                        setTimeout(() => this.saveState(false), 350);
                    }};

                    const header = document.getElementById(HUD_ID + "-header");
                    header.onmousedown = (e) => {{
                        if (e.target.tagName === 'BUTTON') return;
                        e.preventDefault(); this.applyTransition(false);
                        let ox = e.clientX, oy = e.clientY;
                        document.onmousemove = (me) => {{
                            this.posX += (me.clientX - ox); this.posY += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            this.clampHud();
                        }};
                        document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                    }};

                    this.hud.querySelectorAll('.hdl').forEach(hdl => {{
                        hdl.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation(); this.applyTransition(false);
                            const isR = hdl.classList.contains('tr') || hdl.classList.contains('br'), isT = hdl.classList.contains('tl') || hdl.classList.contains('tr');
                            const startW = this.hud.offsetWidth, startH = this.hud.offsetHeight, startX = this.posX, startY = this.posY;
                            const ox = e.clientX, oy = e.clientY;
                            document.onmousemove = (me) => {{
                                let nw = isR ? (startW + (me.clientX - ox)) : (startW - (me.clientX - ox));
                                if (nw < 200) nw = 200; if (nw > window.innerWidth * 0.97) nw = window.innerWidth * 0.97;
                                let nh = nw * this.ratio;
                                if (nh > window.innerHeight * 0.97) {{ nh = window.innerHeight * 0.97; nw = nh / this.ratio; }}
                                if (!isR) this.posX = startX + (startW - nw);
                                if (isT) this.posY = startY + (startH - nh);
                                this.hud.style.width = nw + 'px'; this.hud.style.height = nh + 'px';
                                this.clampHud();
                            }};
                            document.onmouseup = () => {{ document.onmousemove = null; this.saveState(false); }};
                        }};
                    }});

                    area.onmousemove = (e) => {{
                        if (!this.zoomActive) return;
                        const aRect = area.getBoundingClientRect(), hRect = this.hud.getBoundingClientRect();
                        const lx = e.clientX - hRect.left - 75, ly = e.clientY - hRect.top - 75;
                        lens.style.transform = "translate3d(" + lx + "px, " + ly + "px, 0px)";
                        
                        const natW = img.naturalWidth, natH = img.naturalHeight;
                        if (!natW || !natH) return;
                        
                        let renderW = aRect.width, renderH = renderW * (natH / natW);
                        if (renderH > aRect.height) {{
                            renderH = aRect.height;
                            renderW = renderH * (natW / natH);
                        }}
                        
                        const offsetX = (aRect.width - renderW) / 2;
                        const offsetY = (aRect.height - renderH) / 2;
                        
                        const mouseX_on_image = e.clientX - aRect.left - offsetX;
                        const mouseY_on_image = e.clientY - aRect.top - offsetY;
                        
                        const zoomFactor = 2.5;
                        lens.style.backgroundSize = (renderW * zoomFactor) + "px " + (renderH * zoomFactor) + "px";
                        
                        const bgPosX = 75 - (mouseX_on_image * zoomFactor);
                        const bgPosY = 75 - (mouseY_on_image * zoomFactor);
                        lens.style.backgroundPosition = bgPosX + "px " + bgPosY + "px";
                    }};
                    area.onmouseenter = () => {{ if(this.zoomActive) lens.style.display = 'block'; }};
                    area.onmouseleave = () => {{ lens.style.display = 'none'; }};

                    document.getElementById(HUD_ID + "-btn-crop").onclick = (e) => {{
                        e.stopPropagation(); this.isCropping = !this.isCropping;
                        const cropBox = document.getElementById(HUD_ID + "-crop-box");
                        cropBox.style.display = this.isCropping ? 'block' : 'none';
                        e.target.style.color = this.isCropping ? '#fff' : '#aaa';
                        if (this.isCropping) {{
                            cropBox.style.width = area.offsetWidth + 'px'; cropBox.style.height = area.offsetHeight + 'px';
                            cropBox.style.transform = 'translate3d(0px, 0px, 0px)';
                        }}
                    }};

                    const cropBox = document.getElementById(HUD_ID + "-crop-box");
                    cropBox.onmousedown = (e) => {{
                        if (e.target.classList.contains('cp')) return;
                        e.preventDefault(); e.stopPropagation();
                        let ox = e.clientX, oy = e.clientY;
                        let tx = 0, ty = 0;
                        const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                        if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                        document.onmousemove = (me) => {{
                            tx += (me.clientX - ox); ty += (me.clientY - oy);
                            ox = me.clientX; oy = me.clientY;
                            cropBox.style.transform = "translate3d(" + tx + "px, " + ty + "px, 0px)";
                        }};
                        document.onmouseup = () => document.onmousemove = null;
                    }};

                    cropBox.querySelectorAll('.cp').forEach(cp => {{
                        cp.onmousedown = (e) => {{
                            e.preventDefault(); e.stopPropagation();
                            const isL = cp.classList.contains('tl') || cp.classList.contains('bl') || cp.classList.contains('lc');
                            const isR = cp.classList.contains('tr') || cp.classList.contains('br') || cp.classList.contains('rc');
                            const isT = cp.classList.contains('tl') || cp.classList.contains('tr') || cp.classList.contains('tc');
                            const isB = cp.classList.contains('bl') || cp.classList.contains('br') || cp.classList.contains('bc');
                            let startW = cropBox.offsetWidth, startH = cropBox.offsetHeight, ox = e.clientX, oy = e.clientY;
                            let tx = 0, ty = 0;
                            const match = cropBox.style.transform.match(/translate3d\\(([-0-9.]+)px,\\s*([-0-9.]+)px/);
                            if(match) {{ tx = parseFloat(match[1]); ty = parseFloat(match[2]); }}
                            const startX = tx, startY = ty;
                            document.onmousemove = (me) => {{
                                if (isR) cropBox.style.width = (startW + (me.clientX - ox)) + "px";
                                else if (isL) {{
                                    const nw = startW - (me.clientX - ox);
                                    cropBox.style.width = nw + "px"; 
                                    cropBox.style.transform = "translate3d(" + (startX + (startW - nw)) + "px, " + ty + "px, 0px)";
                                }}
                                if (isB) cropBox.style.height = (startH + (me.clientY - oy)) + "px";
                                else if (isT) {{
                                    const nh = startH - (me.clientY - oy);
                                    cropBox.style.height = nh + "px";
                                    const curX = isL ? (startX + (startW - cropBox.offsetWidth)) : tx;
                                    cropBox.style.transform = "translate3d(" + curX + "px, " + (startY + (startH - nh)) + "px, 0px)";
                                }}
                            }};
                            document.onmouseup = () => document.onmousemove = null;
                        }};
                    }});

                    document.getElementById(HUD_ID + "-btn-copy").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('copy'); }};
                    document.getElementById(HUD_ID + "-btn-save").onclick = (e) => {{ e.stopPropagation(); this.exportMedia('save'); }};
                    document.getElementById(HUD_ID + "-btn-zoom").onclick = (e) => {{
                        e.stopPropagation(); this.zoomActive = !this.zoomActive;
                        e.target.style.color = this.zoomActive ? '#4ade80' : '#aaa';
                        if(!this.zoomActive) lens.style.display = 'none';
                    }};
                    document.getElementById(HUD_ID + "-btn-min").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const a = document.getElementById(HUD_ID + "-area");
                        a.style.display = a.style.display === 'none' ? 'flex' : 'none';
                        this.hud.style.height = a.style.display === 'none' ? 'auto' : (this.hud.offsetWidth * this.ratio) + 'px';
                        this.saveState(false);
                    }};
                    document.getElementById(HUD_ID + "-btn-def").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.25);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(false), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-full").onclick = (e) => {{
                        e.stopPropagation(); this.applyTransition(true);
                        const size = this.getBestSize(this.ratio, 0.97);
                        this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                        this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        if(area) area.style.display = 'flex'; this.clampHud();
                        setTimeout(() => this.saveState(true), 350);
                    }};
                    document.getElementById(HUD_ID + "-btn-close").onclick = (e) => {{ e.stopPropagation(); this.hud.remove(); }};
                }},

                create: function(data) {{
                    const old = document.getElementById(HUD_ID); if(old) old.remove();
                    this.hud = document.createElement('div');
                    this.hud.id = HUD_ID;
                    this.hud.style.cssText = 'position:fixed; top:0; left:0; z-index:10000; background:rgba(30,30,30,0.95); backdrop-filter:blur(12px); border:1px solid #444; border-radius:8px; box-shadow:0 10px 50px rgba(0,0,0,0.7); color:white; font-family:sans-serif; display:flex; flex-direction:column; opacity:0; min-width:200px; transition:opacity 0.3s; will-change:transform, opacity; transform: translate3d(20px, 50px, 0px);';
                    
                    this.hud.innerHTML = `
                        <div id="${{HUD_ID}}-header" style="padding:6px 12px; background:rgba(0,0,0,0.4); display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid #444; cursor:move; user-select:none; border-radius: 8px 8px 0 0; min-height: 32px;">
                            <div style="display:flex; align-items:center; gap:8px;">
                                <span style="font-size:11px; font-weight:bold; color:#4ade80;">📸 ECHO VIEWER</span>
                            </div>
                            <div style="display:flex; gap:10px;">
                                <button id="${{HUD_ID}}-btn-crop" title="Sélection" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">⛶</button>
                                <button id="${{HUD_ID}}-btn-copy" title="Copier" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">❐</button>
                                <button id="${{HUD_ID}}-btn-save" title="Télécharger" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">📥</button>
                                <button id="${{HUD_ID}}-btn-zoom" title="Loupe" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">🔍</button>
                                <button id="${{HUD_ID}}-btn-min" title="Réduire" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">_</button>
                                <button id="${{HUD_ID}}-btn-def" title="Taille Défaut" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">↺</button>
                                <button id="${{HUD_ID}}-btn-full" title="Plein Écran" style="background:none; border:none; color:#aaa; cursor:pointer; font-size:14px; padding:2px;">□</button>
                                <button id="${{HUD_ID}}-btn-close" title="Fermer" style="background:none; border:none; color:#ff4444; cursor:pointer; font-size:18px; font-weight:bold; line-height:1; padding:2px;">×</button>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-area" style="flex:1; width:100%; height:100%; background:black; display:flex; justify-content:center; overflow:hidden; border-radius: 0 0 8px 8px; position:relative; cursor:crosshair;">
                            <img id="${{HUD_ID}}-img" style="width:100%; height:100%; object-fit:contain; pointer-events:none;" />
                            <div id="${{HUD_ID}}-crop-box" style="position:absolute; border:2px dashed #fff; display:none; box-sizing:border-box; z-index:10002; cursor:move; box-shadow: 0 0 0 1px #000; will-change: transform;">
                                <div class="cp tl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tr" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:-5px; cursor:nesw-resize;"></div>
                                <div class="cp bl" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; bottom:-5px; cursor:nesw-resize;"></div>
                                <div class="cp br" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; bottom:-5px; cursor:nwse-resize;"></div>
                                <div class="cp tc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; top:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp bc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:50%; bottom:-5px; margin-left:-5px; cursor:ns-resize;"></div>
                                <div class="cp lc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; left:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                                <div class="cp rc" style="position:absolute; width:10px; height:10px; background:#fff; border:1px solid #000; right:-5px; top:50%; margin-top:-5px; cursor:ew-resize;"></div>
                            </div>
                        </div>
                        <div id="${{HUD_ID}}-lens" style="position:absolute; width:150px; height:150px; border:2px solid #4ade80; border-radius:50%; pointer-events:none; display:none; box-shadow:0 0 30px rgba(0,0,0,0.8); z-index:10005; background-repeat:no-repeat; will-change: transform;"></div>
                        <div class="hdl tl" style="position:absolute; width:20px; height:20px; left:-10px; top:-10px; cursor:nwse-resize; z-index:100;"></div>
                        <div class="hdl tr" style="position:absolute; width:20px; height:20px; right:-10px; top:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl bl" style="position:absolute; width:20px; height:20px; left:-10px; bottom:-10px; cursor:nesw-resize; z-index:100;"></div>
                        <div class="hdl br" style="position:absolute; width:20px; height:20px; right:-10px; bottom:-10px; cursor:nwse-resize; z-index:100;"></div>
                    `;
                    document.body.appendChild(this.hud);
                    this.attachEvents();
                    setTimeout(() => {{ if(this.hud) this.hud.style.opacity = '1'; }}, 50);

                    const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                    if (saved && saved.w) {{
                        this.applyTransition(false);
                        this.posX = saved.x; this.posY = saved.y;
                        this.hud.style.width = saved.w + 'px';
                        if (!saved.f && saved.m) {{
                            const a = document.getElementById(HUD_ID + "-area"); if(a) a.style.display = 'none';
                            this.hud.style.height = 'auto';
                        }}
                        this.clampHud();
                    }}
                }},

                update: function(data) {{
                    if (!this.hud || !document.getElementById(HUD_ID)) {{ this.create(data); }}
                    const img = document.getElementById(HUD_ID + "-img");
                    img.onload = () => {{
                        this.ratio = img.naturalHeight / img.naturalWidth;
                        const lens = document.getElementById(HUD_ID + "-lens");
                        if (lens) lens.style.backgroundImage = 'url("' + img.src + '")';
                        const area = document.getElementById(HUD_ID + "-area");
                        const saved = JSON.parse(localStorage.getItem(STATE_KEY) || 'null');
                        if (saved && saved.w) {{
                            if (saved.f) {{
                                const size = this.getBestSize(this.ratio, 0.97);
                                this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                                this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                            }} else if (area && area.style.display !== 'none') {{
                                this.hud.style.height = (this.hud.offsetWidth * this.ratio) + 'px';
                            }}
                        }} else {{
                            const size = this.getBestSize(this.ratio, 0.25);
                            this.hud.style.width = size.w + "px"; this.hud.style.height = size.h + "px";
                            this.posX = (window.innerWidth - size.w) / 2; this.posY = (window.innerHeight - size.h) / 2;
                        }}
                        this.clampHud();
                    }};
                    img.src = "data:" + data.mime + ";base64," + data.b64;
                }}
            }};
        }}
        window.echoImageViewerEngine.update(payload);
    }})();
    """

class Tools:
    class Valves(BaseModel):
        GEMINI_FLASH_MODEL: str = Field(default="gemini-3-flash-preview", description="Modèle pour les sondages sémantiques.")
        MAX_READ_SIZE_KB: int = Field(default=16, description="Taille maximale (en Ko) pour la lecture brute (RAW). Brider à 16 pour conformité API.")
        MAX_DOWNLOAD_SIZE_MB: int = Field(default=20, description="Taille max autorisée pour un téléchargement distant (en Mo).")

    def __init__(self):
        self.valves = self.Valves()
        self.auth = EchoAuth()
        self.uploads_dir = ECHO_UPLOADS_DIR

    async def read_raw_file_content(
        self, 
        file_id: str, 
        output_mode: Literal["utf8", "base64", "hex"] = "utf8",
        start_line: int = 1, 
        end_line: int = 500,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Sonde universelle du stockage physique. Permet d'extraire le contenu brut d'un fichier.
        Note : La sortie est strictement limitée à un 'chunk' de 16 Ko (16384 caractères) pour garantir la conformité avec l'API Gemini.
        :param file_id: L'identifiant unique du fichier cible.
        :param output_mode: Format de sortie souhaité ('utf8' pour du texte, 'base64' pour des données binaires, 'hex' pour une vue hexadécimale). Par défaut 'utf8'.
        :param start_line: Ligne de début pour l'extraction (utile uniquement en mode utf8). Par défaut 1.
        :param end_line: Ligne de fin pour l'extraction (utile uniquement en mode utf8). Par défaut 500.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text=f"❌ Fichier {file_id} introuvable.", status={"status": "error"})

        # --- Limites Strictes 16Ko (16384 caractères) ---
        MAX_CHARS = 16384

        try:
            await events.status(f"📖 Lecture ({output_mode}) : {os.path.basename(fpath)}...")
            
            if output_mode == "utf8":
                with open(fpath, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
                    total = len(lines)
                    subset = lines[start_line-1:end_line]
                    content = "".join(subset)
                    # Tronquage strict à 16Ko de texte
                    if len(content) > MAX_CHARS:
                        content = content[:MAX_CHARS] + "\n[... SORTIE TRONQUÉE À 16Ko POUR CONFORMITÉ API ...]"
                res_text = f"--- CONTENU UTF-8 (Lignes {start_line}-{min(end_line, total)} sur {total}) ---\n\n{content}\n\n--- FIN DU BLOC ---"
            
            elif output_mode == "base64":
                # Pour Base64, 3 octets source -> 4 caractères. Limite = 12288 octets.
                max_bytes = 12288
                with open(fpath, 'rb') as f:
                    raw_data = f.read(max_bytes)
                    content = base64.b64encode(raw_data).decode('utf-8')
                
                size = os.path.getsize(fpath)
                suffix = " (TRONQUÉ À 12Ko SOURCE)" if size > max_bytes else ""
                res_text = f"--- CONTENU BASE64{suffix} ({len(raw_data)} octets lus) ---\n\n{content}\n\n--- FIN DU BLOC ---"
                
            elif output_mode == "hex":
                # Pour Hex, 1 octet source -> 2 caractères. Limite = 8192 octets.
                max_bytes = 8192
                with open(fpath, 'rb') as f:
                    raw_data = f.read(max_bytes)
                    content = raw_data.hex()
                
                size = os.path.getsize(fpath)
                suffix = " (TRONQUÉ À 8Ko SOURCE)" if size > max_bytes else ""
                res_text = f"--- CONTENU HEXADECIMAL{suffix} ({len(raw_data)} octets lus) ---\n\n{content}\n\n--- FIN DU BLOC ---"

            await events.status(f"Lecture terminée.", done=True)
            return wrap_tool_output(text=res_text, status={"status": "success", "file": os.path.basename(fpath), "mode": output_mode})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur : {str(e)}", status={"status": "error"})

    async def semantic_probe(
        self, 
        file_id: str, 
        query: str, 
        thinking_level: str = "MEDIUM",
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Sonde sémantiquement un fichier volumineux ou complexe via Gemini Flash.
        Permet d'extraire le sens, de résumer ou de chercher des informations spécifiques sans lire tout le fichier brute.
        :param file_id: L'identifiant du fichier à analyser.
        :param query: Votre question ou instruction précise pour l'analyse.
        :param thinking_level: Niveau de réflexion du modèle (MINIMAL, LOW, MEDIUM, HIGH). Par défaut MEDIUM.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        token, project_id = self.auth.get_credentials(__user__.get("id"))
        if not token or not project_id: return wrap_tool_output(text="❌ Erreur Auth.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not supported: return wrap_tool_output(text=f"❌ Type {mime} non supporté.", status={"status": "error"})

        await events.status(f"🤖 Sondage Sémantique ({thinking_level})...")

        try:
            with open(fpath, 'rb') as f: b64 = base64.b64encode(f.read()).decode('utf-8')
            url = "https://cloudcode-pa.googleapis.com/v1internal:streamGenerateContent?alt=sse"
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json", "User-Agent": ECHO_USER_AGENT}
            
            payload = {
                "model": self.valves.GEMINI_FLASH_MODEL,
                "project": project_id,
                "request": {
                    "contents": [{"role": "user", "parts": [{"text": query}, {"inline_data": {"mime_type": mime, "data": b64}}]}],
                    "generationConfig": {
                        "thinkingConfig": {"includeThoughts": True, "thinkingLevel": thinking_level.upper()},
                        "responseMimeType": "text/plain"
                    }
                }
            }

            full_text = ""
            async with httpx.AsyncClient(timeout=120) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line.startswith("data:"):
                            try:
                                data = json.loads(line[6:].strip())
                                cand = data.get("response", {}).get("candidates", [])[0]
                                if "content" in cand:
                                    parts = cand["content"].get("parts", [])
                                    for p in parts:
                                        if "text" in p: full_text += p["text"]
                            except: pass

            clean_text, thoughts = split_thought_process(full_text)
            await events.status(f"🤖 Analyse terminée.", done=True)
            multiparts = [{"type": "thought", "content": thoughts}] if thoughts else []
            return wrap_tool_output(text=clean_text, status={"status": "success"}, echo_tool_multiparts=multiparts)
        except Exception as e: return wrap_tool_output(text=f"❌ Exception: {str(e)}", status={"status": "error"})

    async def calculate_file_hashes(
        self, 
        file_id: str, 
        algorithms: List[str] = ["sha256"],
        __user__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Calcule les empreintes numériques (Hash) d'un fichier pour vérification d'intégrité.
        :param file_id: L'identifiant du fichier.
        :param algorithms: Liste des algorithmes souhaités (md5, sha1, sha256, sha512, crc32). Par défaut ['sha256'].
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text="❌ Fichier introuvable.", status={"status": "error"})

        supported = {"md5": hashlib.md5, "sha1": hashlib.sha1, "sha256": hashlib.sha256, "sha512": hashlib.sha512, "sha3_256": hashlib.sha3_256, "sha3_512": hashlib.sha3_512}
        results = {}; active_hashes = {}; do_crc32 = False; crc32_val = 0

        for algo in algorithms:
            a_lower = algo.lower()
            if a_lower in supported: active_hashes[a_lower] = supported[a_lower]()
            elif a_lower == "crc32": do_crc32 = True
            else: results[algo] = "Unsupported"

        try:
            filename = os.path.basename(fpath)
            await events.status(f"🧮 Calcul des hashs pour {filename}...")
            with open(fpath, "rb") as f:
                while chunk := f.read(65536):
                    for h_obj in active_hashes.values(): h_obj.update(chunk)
                    if do_crc32: crc32_val = zlib.crc32(chunk, crc32_val)
            
            for name, h_obj in active_hashes.items(): results[name] = h_obj.hexdigest()
            if do_crc32: results["crc32"] = format(crc32_val & 0xFFFFFFFF, '08x')

            await events.status(f"Calculs terminés.", done=True)
            return wrap_tool_output(text=json.dumps(results, indent=2), status={"status": "success", "filename": filename})
        except Exception as e: return wrap_tool_output(text=f"❌ Erreur: {str(e)}", status={"status": "error"})

    async def show_image(
        self, 
        file_id: str, 
        __user__: dict = {}, 
        __event_emitter__: Any = None, 
        __event_call__: Any = None
    ) -> str:
        """
        Affiche une image dans l'interface utilisateur à partir de son ID de fichier.
        :param file_id: L'identifiant du fichier provenant d'un upload, ou du navigateur).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid = __user__.get("id", "anonymous")
        fpath = resolve_upload_file_path(uid, file_id, self.uploads_dir)
        if not fpath: return wrap_tool_output(text=f"❌ Fichier {file_id} introuvable.", status={"status": "error"})

        mime, supported = get_gemini_mime(fpath)
        if not mime or not mime.startswith("image/"): 
            return wrap_tool_output(text=f"❌ Le fichier n'est pas une image supportée (MIME: {mime}).", status={"status": "error"})

        try:
            await events.status(f"🖼️ Affichage de l'image : {os.path.basename(fpath)}...")
            with open(fpath, 'rb') as f:
                b64 = base64.b64encode(f.read()).decode('utf-8')
            
            js_code = _generate_image_viewer_js(b64, mime, file_id)
            if __event_call__:
                await __event_call__({"type": "execute", "data": {"code": js_code}})
            
            await events.status("Image affichée.", done=True)
            return wrap_tool_output(text="✅ L'image a été correctement affichée dans le HUD de l'interface utilisateur.", status={"status": "success", "file": os.path.basename(fpath)})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur lors de l'affichage de l'image : {str(e)}", status={"status": "error"})

    async def download_from_url(
        self, 
        url: str, 
        __user__: dict = {}, 
        __metadata__: dict = {}, 
        __event_emitter__: Any = None, 
        __event_call__: Any = None
    ) -> str:
        """
        Télécharge un fichier distant depuis une URL et l'intègre au contexte ECHO du prochain tour.
        Accepte les images, documents, PDFs, etc. Supporte les Data URIs (Base64).
        :param url: L'URL directe du fichier à télécharger ou un Data URI.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        
        # 1. Identification
        uid = __user__.get("id", "anonymous")
        cid = __metadata__.get("chat_id", "unknown")
        file_id = generate_echo_file_id(uid, cid)
        
        max_bytes = self.valves.MAX_DOWNLOAD_SIZE_MB * 1024 * 1024
        downloaded = 0
        mime_type = "application/octet-stream"
        
        if url.startswith("data:"):
            # Traitement natif des Data URIs (Base64)
            await events.status("📦 Extraction du média embarqué (Base64)...")
            try:
                # Format: data:[<mediatype>][;base64],<data>
                header, b64_data = url.split(",", 1)
                mime_type = header.split(":")[1].split(";")[0] if ";" in header else "application/octet-stream"
                ext = mime_type.split("/")[1] if "/" in mime_type else "bin"
                
                # Gestion spécifique du SVG (souvent retourné sans extension propre)
                if ext.startswith("svg"): ext = "svg"
                if ext == "jpeg": ext = "jpg"
                
                original_name = f"extracted_media.{ext}"
                
                raw_bytes = base64.b64decode(b64_data)
                downloaded = len(raw_bytes)
                
                if downloaded > max_bytes:
                    return wrap_tool_output(
                        text=f"❌ Média trop volumineux. Limite : {self.valves.MAX_DOWNLOAD_SIZE_MB} Mo.", 
                        status={"status": "error"}
                    )
                
                safe_name = quote(original_name)
                filename = f"{file_id}_{safe_name}"
                fpath = os.path.join(self.uploads_dir, filename)
                
                with open(fpath, 'wb') as f:
                    f.write(raw_bytes)
                    
                await events.status("Média extrait avec succès.", done=True)
            except Exception as e:
                return wrap_tool_output(text=f"❌ Erreur de décodage Base64: {str(e)}", status={"status": "error"})
                
        else:
            # Traitement standard HTTP/HTTPS
            parsed_url = urlparse(url)
            original_name = os.path.basename(parsed_url.path)
            if not original_name: original_name = "downloaded_file.bin"
            safe_name = quote(original_name)
            
            filename = f"{file_id}_{safe_name}"
            fpath = os.path.join(self.uploads_dir, filename)
            
            await events.status(f"📥 Téléchargement en cours : {original_name}...")
            
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    async with client.stream("GET", url, headers={"User-Agent": ECHO_USER_AGENT}) as response:
                        response.raise_for_status()
                        mime_type = response.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
                        
                        with open(fpath, 'wb') as f:
                            async for chunk in response.aiter_bytes():
                                downloaded += len(chunk)
                                if downloaded > max_bytes:
                                    f.close()
                                    os.remove(fpath)
                                    return wrap_tool_output(
                                        text=f"❌ Fichier trop volumineux. Limite : {self.valves.MAX_DOWNLOAD_SIZE_MB} Mo.", 
                                        status={"status": "error"}
                                    )
                                f.write(chunk)
                                
            except Exception as e:
                if os.path.exists(fpath): os.remove(fpath)
                return wrap_tool_output(text=f"❌ Erreur de téléchargement: {str(e)}", status={"status": "error"})
                
            await events.status(f"Fichier téléchargé ({downloaded} octets).", done=True)
        
        # 4. Intégration Suture
        nouveau_fichier = {
            "id": file_id,
            "name": original_name,
            "meta": {"content_type": mime_type},
            "type": "file"
        }
        
        return wrap_tool_output(
            text=f"✅ Fichier '{original_name}' téléchargé avec succès (ID: {file_id}).", 
            status={"status": "success"},
            nouveaux_fichiers=[nouveau_fichier]
        )
