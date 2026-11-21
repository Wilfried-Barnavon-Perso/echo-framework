"""
title: ECHO V6 Window Manager Router
author: Wilfried BARNAVON
version: 6.17.4.1
description: Routeur souverain FastAPI pour le gestionnaire de fenêtres et événements temps réel (/echo/v1/wm).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.17.4.1: Suppression /ws/canvas, ajout POST /internal/sandbox_event HMAC, CORS preflight/réponses et shim HTML autonome sans location.host.
# 6.16.0.3: Migration /ws bidirectionnel, relais WS /ws/canvas/{chat_id}/{run_id}, proxy HTTP/WS HMAC /app/{ticket} et shim HTML.
# 6.15.2.1: Réconciliation des frames Canvas (reconcile_canvas_frames) sur GET /chat/{chat_id} et action RPC canvas_stop_run sur POST /rpc.
# 6.7.20.1: Exposition de l'endpoint authentifié GET /echo/v1/wm/commands servant le catalogue déclaratif system-commands.xml.

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import aiohttp
import httpx
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse

from open_webui.echo.http_utils import (
    authenticate_echo_websocket,
    get_echo_verified_user,
)
from open_webui.echo.runtime import ensure_echo_sys_path

ensure_echo_sys_path()
try:
    from echo_auth import AuthService  # type: ignore
    from echo_constants import ECHO_CODING_WORKER_BASE_URL, ECHO_CODING_WORKER_WS_BASE_URL  # type: ignore
    from echo_paths import sanitize_echo_id  # type: ignore
    from echo_window_manager import get_window_manager  # type: ignore
except ImportError:
    local_libs = Path(__file__).resolve().parents[5] / "14-owui-libs"
    if local_libs.is_dir() and str(local_libs) not in sys.path:
        sys.path.insert(0, str(local_libs))
    from echo_auth import AuthService  # type: ignore
    from echo_constants import ECHO_CODING_WORKER_BASE_URL, ECHO_CODING_WORKER_WS_BASE_URL  # type: ignore
    from echo_paths import sanitize_echo_id  # type: ignore
    from echo_window_manager import get_window_manager  # type: ignore

log = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def wm_websocket_endpoint(websocket: WebSocket):
    """Abonne le client au flux WebSocket bidirectionnel du Window Manager."""
    auth_res = await authenticate_echo_websocket(websocket)
    if auth_res is None:
        return

    user, raw_cid = auth_res
    user_id = user.id
    cid = raw_cid or uuid.uuid4().hex
    wm = get_window_manager()
    queue = wm.subscribe(user_id, cid)
    send_lock = asyncio.Lock()

    async def _safe_send_json(data: dict | str) -> None:
        payload_str = data if isinstance(data, str) else json.dumps(data)
        async with send_lock:
            await websocket.send_text(payload_str)

    async def _sender() -> None:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=20.0)
                await _safe_send_json(msg)
            except asyncio.TimeoutError:
                await _safe_send_json('{"type":"ping"}')
            except (WebSocketDisconnect, RuntimeError):
                break

    async def _receiver() -> None:
        while True:
            try:
                raw = await websocket.receive_text()
            except (WebSocketDisconnect, RuntimeError):
                break

            try:
                frame = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            if not isinstance(frame, dict):
                continue

            ftype = frame.get("type")
            if ftype == "ping":
                await _safe_send_json('{"type":"pong"}')
            elif ftype == "rpc":
                req_id = frame.get("req_id")
                raw_payload = frame.get("payload")
                rpc_payload = dict(raw_payload) if isinstance(raw_payload, dict) else {}
                rpc_payload.setdefault("client_id", cid)
                try:
                    res = await wm.handle_rpc(user_id, rpc_payload)
                    await _safe_send_json({"type": "rpc_ack", "req_id": req_id, "result": res})
                except Exception as exc:
                    await _safe_send_json({"type": "rpc_ack", "req_id": req_id, "error": str(exc)})

    sender_task: asyncio.Task | None = None
    receiver_task: asyncio.Task | None = None
    try:
        await _safe_send_json({"type": "wm_connected", "client_id": cid, "user_id": user_id})
        sender_task = asyncio.create_task(_sender())
        receiver_task = asyncio.create_task(_receiver())
        _, pending = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in (sender_task, receiver_task):
            if task is not None:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(BaseException):
                    await task
        wm.unsubscribe(user_id, cid)


@router.get("/chat/{chat_id}")
async def wm_get_chat_snapshot(
    chat_id: str,
    refresh: bool = False,
    user=Depends(get_echo_verified_user),
):
    """Retourne l'état complet du chat enrichi des métriques de contexte et quotas."""
    user_id = user.id
    wm = get_window_manager()
    if refresh:
        try:
            uid = wm._resolve_owner_uid(user_id, chat_id)
            await AuthService(user_id=uid).refresh_quota_if_needed(force=True)
        except Exception as e:
            log.debug("Erreur refresh_quota_if_needed pour snapshot WM: %s", e)

    try:
        await wm.reconcile_canvas_frames(user_id, chat_id)
    except Exception as e:
        log.debug("Erreur reconcile_canvas_frames pour snapshot WM: %s", e)

    snapshot = await asyncio.to_thread(wm.get_chat_snapshot, user_id, chat_id)
    return JSONResponse(content=snapshot)


@router.post("/rpc")
async def wm_post_rpc(
    request: Request,
    user=Depends(get_echo_verified_user),
):
    """Traite les retours RPC et synchronisations d'état du Window Manager."""
    try:
        payload = await request.json()
    except Exception:
        try:
            raw = await request.body()
            payload = json.loads(raw) if raw else {}
        except Exception:
            payload = {}

    user_id = user.id
    wm = get_window_manager()
    res = await wm.handle_rpc(user_id, payload)
    return JSONResponse(content=res)


@router.get("/commands")
async def get_echo_authorized_commands(user=Depends(get_echo_verified_user)):
    """Expose le registre des commandes '/' ECHO depuis system-commands.xml."""
    from echo_core import load_echo_commands_registry
    ui_commands, _, _ = await asyncio.to_thread(load_echo_commands_registry)
    return JSONResponse(content={"commands": ui_commands})


@router.post("/internal/sandbox_event")
async def wm_internal_sandbox_event(request: Request):
    """Webhook interne Worker -> WebUI authentifié par HMAC-SHA256 (Contrat C2)."""
    token = request.headers.get("x-echo-callback-token") or ""
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        return JSONResponse({"detail": "Invalid payload"}, status_code=400)

    user_id = str(payload.get("user_id") or "")
    chat_id = str(payload.get("chat_id") or "")
    wm = get_window_manager()
    if not wm.verify_sandbox_callback_token(token, user_id, chat_id):
        return JSONResponse({"detail": "Invalid callback token"}, status_code=403)

    event = str(payload.get("event") or "")
    run_id = str(payload.get("run_id") or "")
    if event == "frame_upsert":
        frame = payload.get("frame") if isinstance(payload.get("frame"), dict) else {}
        frame_id = str(frame.get("id") or frame.get("window_id") or payload.get("window_id") or "default")
        await wm.broadcast_canvas_frame(
            user_id,
            chat_id,
            frame_id,
            html=str(frame.get("html") or ""),
            title=str(frame.get("title") or "Monitor"),
            width=str(frame.get("width") or "100%"),
            height=str(frame.get("height") or "400px"),
            auto_open=True,
            run_id=run_id,
            server_status="running",
            mode=str(frame.get("mode") or ""),
        )
    elif event == "run_finished":
        wm.revoke_app_run_tickets(run_id)
        window_ids = payload.get("window_ids") or []
        state = str(payload.get("state") or "completed")
        exit_code = payload.get("exit_code")
        await wm.broadcast_canvas_status(
            user_id,
            chat_id,
            window_ids,
            server_status=state,
            exit_code=exit_code,
            run_id=run_id,
        )
    return JSONResponse({"status": "ok"})


def _apply_app_cors_headers(headers: dict | object, request: Request) -> None:
    """Ajoute les en-têtes CORS permissifs requis pour le proxy applicatif /app/{ticket}."""
    req_headers = request.headers.get("access-control-request-headers") or "*"
    headers["Access-Control-Allow-Origin"] = "*"
    headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"
    headers["Access-Control-Allow-Headers"] = req_headers
    headers["Access-Control-Expose-Headers"] = "*"
    headers["Access-Control-Max-Age"] = "3600"


def _rewrite_app_location_header(loc: str, base_prefix: str, public_host: str) -> str:
    """Réécrit les redirections Location racine ou locales vers /echo/v1/wm/app/{ticket}/..."""
    if not loc:
        return loc
    parsed = urlsplit(loc)
    if not parsed.scheme and not parsed.netloc:
        if loc.startswith("/") and not loc.startswith("//") and not loc.startswith("/echo/v1/wm/app/"):
            return f"{base_prefix}{loc.lstrip('/')}"
        return loc
    host_l = (parsed.hostname or "").lower()
    netloc_l = (parsed.netloc or "").lower()
    is_local_host = host_l in ("localhost", "127.0.0.1") or (public_host and netloc_l == public_host.lower())
    if parsed.scheme in ("http", "https") and is_local_host and not parsed.path.startswith("/echo/v1/wm/app/"):
        rewritten = f"{base_prefix}{parsed.path.lstrip('/')}"
        if parsed.query:
            rewritten += f"?{parsed.query}"
        if parsed.fragment:
            rewritten += f"#{parsed.fragment}"
        return rewritten
    return loc


def _inject_app_proxy_html_shim(
    html_bytes: bytes,
    base_prefix: str,
    public_host: str,
    is_https: bool,
) -> bytes:
    """Réécrit les attributs HTML racine et injecte <base> + shim JS autonome (sans location.host/origin)."""
    html_text = html_bytes.decode("utf-8", errors="replace")
    html_text = re.sub(
        r"""(\b(?:src|href|action|poster)\s*=\s*)(["'])/(?!/|echo/v1/wm/app/)([^"']*)\2""",
        lambda m: f"{m.group(1)}{m.group(2)}{base_prefix}{m.group(3)}{m.group(2)}",
        html_text,
        flags=re.IGNORECASE,
    )

    safe_attr = base_prefix.replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
    base_js = json.dumps(base_prefix)
    host_js = json.dumps(public_host or "")
    https_js = "true" if is_https else "false"
    shim_html = (
        f'<base href="{safe_attr}">'
        "<script>(function(){"
        f"var B={base_js},H={host_js},S={https_js};"
        "function mkStore(){"
        "var d=Object.create(null);"
        "var api={"
        "getItem:function(k){k=String(k);return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null;},"
        "setItem:function(k,v){d[String(k)]=String(v);},"
        "removeItem:function(k){delete d[String(k)];},"
        "clear:function(){d=Object.create(null);},"
        "key:function(i){var ks=Object.keys(d);return(i>=0&&i<ks.length)?ks[i]:null;}"
        "};"
        "return new Proxy(api,{"
        "get:function(t,p){"
        "if(p==='length')return Object.keys(d).length;"
        "if(p in t)return t[p];"
        "if(typeof p==='string'&&Object.prototype.hasOwnProperty.call(d,p))return d[p];"
        "return undefined;},"
        "set:function(t,p,v){if(p==='length'||p in t)return false;d[String(p)]=String(v);return true;},"
        "deleteProperty:function(t,p){if(typeof p==='string'){delete d[p];return true;}return false;},"
        "has:function(t,p){return(p in t)||Object.prototype.hasOwnProperty.call(d,String(p));},"
        "ownKeys:function(){return Object.keys(d);},"
        "getOwnPropertyDescriptor:function(t,p){"
        "if(typeof p==='string'&&Object.prototype.hasOwnProperty.call(d,p)){"
        "return{enumerable:true,configurable:true,writable:true,value:d[p]};}"
        "return Object.getOwnPropertyDescriptor(t,p);}"
        "});}"
        "try{var _t='__echo_t__';window.localStorage.setItem(_t,'1');window.localStorage.removeItem(_t);}"
        "catch(_){try{Object.defineProperty(window,'localStorage',{configurable:true,"
        "get:function(){return window.__echoLS||(window.__echoLS=mkStore());}});}catch(__){}}"
        "try{var _t2='__echo_t__';window.sessionStorage.setItem(_t2,'1');window.sessionStorage.removeItem(_t2);}"
        "catch(_){try{Object.defineProperty(window,'sessionStorage',{configurable:true,"
        "get:function(){return window.__echoSS||(window.__echoSS=mkStore());}});}catch(__){}}"
        "window.ECHO_APP_BASE=B;"
        "window.echoAppUrl=function(p){var s=String(p==null?'':p);"
        "if(/^(?:[a-z][a-z0-9+.-]*:)?\\/\\//i.test(s)||s.indexOf(B)===0||s.indexOf('/echo/v1/wm/app/')===0)return s;"
        "return B+s.replace(/^\\/+/, '');};"
        "window.echoWsUrl=function(p){var s=String(p==null?'':p),pr=S?'wss://':'ws://';"
        "if(/^(?:wss?:)?\\/\\//i.test(s))return s;"
        "if(s.indexOf('/echo/v1/wm/app/')===0)return pr+H+s;"
        "return pr+H+B+s.replace(/^\\/+/, '');};"
        "function rw(s){"
        "s=String(s==null?'':s);"
        "if(s.charAt(0)==='/'&&s.indexOf('//')!==0&&s.indexOf('/echo/v1/wm/app/')!==0)return B+s.slice(1);"
        "if(H&&/^(?:https?:)?\\/\\//i.test(s)){try{"
        "var u=new URL(s,(S?'https://':'http://')+H);"
        "if(u.host===H&&u.pathname.charAt(0)==='/'&&u.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "return B+u.pathname.slice(1)+u.search+u.hash;}}catch(_){}}"
        "return s;}"
        "var oF=window.fetch;"
        "if(oF){window.fetch=function(input,init){"
        "if(typeof input==='string'){input=rw(input);}"
        "else if(typeof URL!=='undefined'&&input instanceof URL){"
        "if(H&&input.host===H&&input.pathname.charAt(0)==='/'&&input.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "input=new URL(B+input.pathname.slice(1)+input.search+input.hash,(S?'https://':'http://')+H);}}"
        "else if(typeof Request!=='undefined'&&input instanceof Request){"
        "try{var u=new URL(input.url);"
        "if(H&&u.host===H&&u.pathname.charAt(0)==='/'&&u.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "input=new Request(B+u.pathname.slice(1)+u.search,input);}}catch(_){}}"
        "return oF.call(this,input,init);};}"
        "if(typeof XMLHttpRequest!=='undefined'&&XMLHttpRequest.prototype&&XMLHttpRequest.prototype.open){"
        "var oX=XMLHttpRequest.prototype.open;"
        "XMLHttpRequest.prototype.open=function(method,url){"
        "var args=Array.prototype.slice.call(arguments);"
        "if(args.length>=2&&args[1]!=null){args[1]=rw(args[1] instanceof URL?args[1].href:args[1]);}"
        "return oX.apply(this,args);};}"
        "var oE=window.EventSource;"
        "if(oE){window.EventSource=function(url,init){"
        "var u=rw(url instanceof URL?url.href:url);"
        "return init!==undefined?new oE(u,init):new oE(u);};"
        "window.EventSource.prototype=oE.prototype;"
        "window.EventSource.CONNECTING=oE.CONNECTING;window.EventSource.OPEN=oE.OPEN;window.EventSource.CLOSED=oE.CLOSED;}"
        "var oW=window.WebSocket;"
        "if(oW){window.WebSocket=function(url,protocols){"
        "var s=String(url instanceof URL?url.href:(url==null?'':url)),pr=S?'wss://':'ws://';"
        "if(s.charAt(0)==='/'&&s.indexOf('//')!==0&&s.indexOf('/echo/v1/wm/app/')!==0){"
        "s=pr+H+B+s.slice(1);"
        "}else if(H){try{var u=new URL(s,(S?'https://':'http://')+H+B);"
        "if((u.protocol==='ws:'||u.protocol==='wss:')&&u.host===H&&u.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "s=pr+H+B+u.pathname.replace(/^\\/+/, '')+u.search;}}catch(_){}}"
        "return protocols!==undefined?new oW(s,protocols):new oW(s);};"
        "window.WebSocket.prototype=oW.prototype;"
        "window.WebSocket.CONNECTING=oW.CONNECTING;window.WebSocket.OPEN=oW.OPEN;"
        "window.WebSocket.CLOSING=oW.CLOSING;window.WebSocket.CLOSED=oW.CLOSED;}"
        "})();</script>"
    )

    patched, count = re.subn(
        r"(<head\b[^>]*>)",
        lambda m: m.group(1) + shim_html,
        html_text,
        count=1,
        flags=re.IGNORECASE,
    )
    if count > 0:
        return patched.encode("utf-8")
    return (shim_html + html_text).encode("utf-8")


_APP_PROXY_HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@router.api_route("/app/{ticket}", methods=_APP_PROXY_HTTP_METHODS)
@router.api_route("/app/{ticket}/{path:path}", methods=_APP_PROXY_HTTP_METHODS)
async def wm_app_http_proxy_endpoint(request: Request, ticket: str, path: str = ""):
    """Proxifie les requêtes HTTP ticketées HMAC vers le serveur applicatif sandboxé sans cookies SSO."""
    wm = get_window_manager()
    claims = wm.verify_app_capability_ticket(ticket)
    if claims is None:
        return JSONResponse({"detail": "Invalid or expired capability ticket"}, status_code=403)

    if request.method == "OPTIONS":
        preflight = Response(status_code=204)
        _apply_app_cors_headers(preflight.headers, request)
        return preflight

    raw_host = request.headers.get("x-forwarded-host") or request.headers.get("host") or (request.url.netloc or "")
    public_host = raw_host.split(",")[0].strip()
    raw_proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    is_https = raw_proto.split(",")[0].strip().lower() == "https"
    base_prefix = f"/echo/v1/wm/app/{ticket}/"

    safe_rid = sanitize_echo_id(claims["run_id"])
    wid = claims["window_id"]
    rel_path = (path or "").lstrip("/")
    query_items = [("__echo_wid", wid)] + [
        (k, v) for k, v in request.query_params.multi_items() if k != "__echo_wid"
    ]
    suffix = f"/{rel_path}" if rel_path else ""
    target_url = f"{ECHO_CODING_WORKER_BASE_URL}/app_proxy/{safe_rid}{suffix}?{urlencode(query_items)}"

    strip_req_headers = {
        "host", "cookie", "authorization", "connection", "keep-alive",
        "transfer-encoding", "te", "trailers", "upgrade", "content-length",
    }
    fwd_headers = [
        (k, v) for k, v in request.headers.items()
        if k.lower() not in strip_req_headers and not k.lower().startswith("x-webui-")
    ]
    body = await request.body()

    accept_hdr = str(request.headers.get("accept") or "").lower()
    is_sse_req = "text/event-stream" in accept_hdr
    client_timeout = httpx.Timeout(connect=10.0, write=30.0, read=None if is_sse_req else 300.0, pool=10.0)
    client = httpx.AsyncClient(follow_redirects=False, timeout=client_timeout)
    try:
        upstream_req = client.build_request(
            method=request.method,
            url=target_url,
            headers=fwd_headers,
            content=body if body else None,
        )
        upstream_resp = await client.send(upstream_req, stream=True)
    except Exception as exc:
        await client.aclose()
        return JSONResponse({"detail": f"Sandbox app unreachable: {exc}"}, status_code=502)

    hop_by_hop = {
        "transfer-encoding", "connection", "keep-alive",
        "proxy-authenticate", "proxy-authorization", "te", "trailers", "upgrade",
    }
    content_type = str(upstream_resp.headers.get("content-type") or "").lower()
    is_redirect = upstream_resp.status_code in (301, 302, 303, 307, 308)

    if "text/html" in content_type:
        try:
            raw_html = await upstream_resp.aread()
        finally:
            await upstream_resp.aclose()
            await client.aclose()
        patched_html = _inject_app_proxy_html_shim(raw_html, base_prefix, public_host, is_https)
        html_skip = hop_by_hop | {
            "content-length", "content-security-policy", "cache-control",
            "origin-agent-cluster", "access-control-allow-origin",
            "access-control-allow-methods", "access-control-allow-headers",
            "access-control-expose-headers", "access-control-max-age",
        }
        resp = Response(
            content=patched_html,
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get("content-type"),
        )
        for k, v in upstream_resp.headers.multi_items():
            kl = k.lower()
            if kl in html_skip:
                continue
            if kl == "location" and is_redirect:
                v = _rewrite_app_location_header(v, base_prefix, public_host)
            resp.headers.append(k, v)
        resp.headers["Origin-Agent-Cluster"] = "?1"
        resp.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts allow-downloads allow-forms allow-modals allow-popups;"
        )
        resp.headers["Cache-Control"] = "no-store"
        _apply_app_cors_headers(resp.headers, request)
        return resp

    stream_skip = hop_by_hop | {
        "cache-control", "access-control-allow-origin",
        "access-control-allow-methods", "access-control-allow-headers",
        "access-control-expose-headers", "access-control-max-age",
    }
    resp_headers = []
    for k, v in upstream_resp.headers.multi_items():
        kl = k.lower()
        if kl in stream_skip:
            continue
        if kl == "location" and is_redirect:
            v = _rewrite_app_location_header(v, base_prefix, public_host)
        resp_headers.append((k, v))

    async def _stream_body():
        try:
            async for chunk in upstream_resp.aiter_raw():
                if chunk:
                    yield chunk
        finally:
            await upstream_resp.aclose()
            await client.aclose()

    resp = StreamingResponse(_stream_body(), status_code=upstream_resp.status_code)
    for k, v in resp_headers:
        resp.headers.append(k, v)
    resp.headers["Cache-Control"] = "no-store"
    if "text/event-stream" in content_type:
        resp.headers["X-Accel-Buffering"] = "no"
    _apply_app_cors_headers(resp.headers, request)
    return resp


@router.websocket("/app/{ticket}")
@router.websocket("/app/{ticket}/{path:path}")
async def wm_app_ws_proxy_endpoint(websocket: WebSocket, ticket: str, path: str = ""):
    """Relais WebSocket full-duplex TEXT/BINARY ticketé HMAC vers le serveur applicatif sandboxé."""
    await websocket.accept()
    wm = get_window_manager()
    claims = wm.verify_app_capability_ticket(ticket)
    if claims is None:
        await websocket.close(code=1008, reason="Invalid ticket")
        return

    safe_rid = sanitize_echo_id(claims["run_id"])
    wid = claims["window_id"]
    rel_path = (path or "").lstrip("/")
    suffix = f"/{rel_path}" if rel_path else ""
    query_items = [("__echo_wid", wid)] + [
        (k, v) for k, v in websocket.query_params.multi_items() if k != "__echo_wid"
    ]
    ws_url = f"{ECHO_CODING_WORKER_WS_BASE_URL}/ws/app/{safe_rid}{suffix}?{urlencode(query_items)}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, heartbeat=20.0) as upstream:
                async def _upstream_to_client() -> None:
                    async for msg in upstream:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(msg.data)
                        elif msg.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(msg.data)
                        elif msg.type in (
                            aiohttp.WSMsgType.CLOSE,
                            aiohttp.WSMsgType.CLOSED,
                            aiohttp.WSMsgType.ERROR,
                        ):
                            break

                async def _client_to_upstream() -> None:
                    while True:
                        msg = await websocket.receive()
                        if msg.get("type") == "websocket.disconnect":
                            break
                        if "text" in msg and msg["text"] is not None:
                            await upstream.send_str(msg["text"])
                        elif "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send_bytes(msg["bytes"])

                up_task = asyncio.create_task(_upstream_to_client())
                down_task = asyncio.create_task(_client_to_upstream())
                try:
                    _, pending = await asyncio.wait(
                        {up_task, down_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                finally:
                    for t in (up_task, down_task):
                        if not t.done():
                            t.cancel()
                        with contextlib.suppress(BaseException):
                            await t
    except (WebSocketDisconnect, RuntimeError):
        pass
    except Exception as exc:
        log.debug("Erreur relais WS App (%s): %s", safe_rid, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)
