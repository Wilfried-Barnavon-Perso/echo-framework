"""
title: ECHO V6 Window Manager Router
author: Wilfried BARNAVON
version: 6.16.0.3
description: Routeur souverain FastAPI pour le gestionnaire de fenêtres et événements temps réel (/echo/v1/wm).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.16.0.3: Proxy HTTP/WS ticketé HMAC /app/{ticket}, shim HTML (<base>, fetch, WebSocket) et support expose_app dans wm_canvas_ws_endpoint.
# 6.16.0.2: Ajout du relais WebSocket full-duplex TEXT/BINARY /ws/canvas/{chat_id}/{run_id} vers CanvasRunHub.
# 6.16.0.1: Migration SSE /stream vers WebSocket bidirectionnel /ws (authenticate_echo_websocket, rpc_ack et keepalive 20s).
# 6.15.2.1: Réconciliation des frames Canvas (reconcile_canvas_frames) sur GET /chat/{chat_id} et action RPC canvas_stop_run sur POST /rpc.
# 6.7.20.1: Exposition de l'endpoint authentifié GET /echo/v1/wm/commands servant le catalogue déclaratif system-commands.xml.

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlencode

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


@router.websocket("/ws/canvas/{chat_id}/{run_id}")
async def wm_canvas_ws_endpoint(websocket: WebSocket, chat_id: str, run_id: str):
    """Relais WebSocket full-duplex TEXT/BINARY entre l'Iframe Canvas et CanvasRunHub."""
    auth_res = await authenticate_echo_websocket(websocket)
    if auth_res is None:
        return

    user, _ = auth_res
    wm = get_window_manager()
    if not await wm.verify_canvas_run_access(user.id, chat_id, run_id):
        await websocket.close(code=1008, reason="Canvas run access denied")
        return

    owner_uid = sanitize_echo_id(wm._resolve_owner_uid(user.id, chat_id))
    safe_cid = sanitize_echo_id(chat_id)
    safe_rid = sanitize_echo_id(run_id)
    ws_url = f"{ECHO_CODING_WORKER_WS_BASE_URL}/ws/canvas/{safe_rid}?user_id={owner_uid}&chat_id={safe_cid}"

    def _sync_frame_state(frame_obj: dict, default_status: str = "running") -> None:
        wid = str(frame_obj.get("id") or frame_obj.get("window_id") or "default")
        cstate = wm._get_canvas_state(user.id, chat_id)
        prev = cstate["frames"].get(wid) if isinstance(cstate.get("frames"), dict) else None
        mode = str(frame_obj.get("mode") or (prev or {}).get("mode") or "")
        server_status = str(frame_obj.get("server_status") or default_status)
        html_val = str(frame_obj.get("html") or "")
        src_val = str(frame_obj.get("src") or (prev or {}).get("src") or "")
        if mode == "app_proxy" and not src_val and server_status not in ("completed", "killed", "error", "timeout"):
            ticket = wm.issue_app_capability_ticket(user.id, chat_id, safe_rid, wid)
            src_val = f"/echo/v1/wm/app/{ticket}/"
            html_val = ""
        cstate["frames"][wid] = {
            "id": wid,
            "title": str(frame_obj.get("title") or (prev or {}).get("title") or "Monitor"),
            "html": html_val,
            "src": src_val,
            "mode": mode,
            "width": str(frame_obj.get("width") or (prev or {}).get("width") or "100%"),
            "height": str(frame_obj.get("height") or (prev or {}).get("height") or "400px"),
            "run_id": str(frame_obj.get("run_id") or safe_rid),
            "server_status": server_status,
            "exit_code": frame_obj.get("exit_code"),
            "ready": bool(frame_obj.get("ready", (prev or {}).get("ready", False))),
            "updated_at": float(frame_obj.get("updated_at") or time.time()),
        }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url, heartbeat=20.0) as upstream:
                async def _upstream_to_client() -> None:
                    async for msg in upstream:
                        if msg.type == aiohttp.WSMsgType.BINARY:
                            await websocket.send_bytes(msg.data)
                        elif msg.type == aiohttp.WSMsgType.TEXT:
                            await websocket.send_text(msg.data)
                            try:
                                frame = json.loads(msg.data)
                            except (json.JSONDecodeError, TypeError):
                                continue
                            if not isinstance(frame, dict):
                                continue
                            ftype = frame.get("type")
                            if ftype == "display":
                                _sync_frame_state(frame)
                            elif ftype == "expose_app":
                                wid = str(frame.get("window_id") or frame.get("id") or "default")
                                await wm.broadcast_canvas_frame(
                                    user.id,
                                    chat_id,
                                    {
                                        "id": wid,
                                        "title": frame.get("title") or "Application Sandbox",
                                        "width": frame.get("width") or "100%",
                                        "height": frame.get("height") or "600px",
                                        "mode": "app_proxy",
                                    },
                                    auto_open=False,
                                    run_id=safe_rid,
                                )
                            elif ftype == "snapshot":
                                jstate = str(frame.get("job_state") or "running")
                                for fitem in frame.get("frames") or []:
                                    if isinstance(fitem, dict):
                                        _sync_frame_state(fitem, default_status=jstate)
                            elif ftype == "status":
                                await wm.broadcast_canvas_status(
                                    user.id,
                                    chat_id,
                                    frame.get("window_ids") or [],
                                    server_status=str(frame.get("job_state") or "completed"),
                                    run_id=str(frame.get("run_id") or safe_rid),
                                    exit_code=frame.get("exit_code"),
                                )
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
                        if "bytes" in msg and msg["bytes"] is not None:
                            await upstream.send_bytes(msg["bytes"])
                        elif "text" in msg and msg["text"] is not None:
                            await upstream.send_str(msg["text"])

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
        log.debug("Erreur relais WS Canvas (%s/%s): %s", safe_cid, safe_rid, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011)


def _inject_app_proxy_html_shim(html_bytes: bytes, base_prefix: str) -> bytes:
    """Injecte <base> et le shim JS (ECHO_APP_BASE, echoAppUrl, echoWsUrl, fetch, WebSocket)."""
    safe_attr = base_prefix.replace('"', "%22").replace("<", "%3C").replace(">", "%3E")
    base_js = json.dumps(base_prefix)
    shim_html = (
        f'<base href="{safe_attr}">'
        "<script>(function(){"
        f"var B={base_js};"
        "window.ECHO_APP_BASE=B;"
        "window.echoAppUrl=function(p){var s=String(p==null?'':p);"
        "if(/^(?:[a-z]+:)?\\/\\//i.test(s)||s.indexOf(B)===0)return s;"
        "return B+s.replace(/^\\/+/, '');};"
        "window.echoWsUrl=function(p){var pr=location.protocol==='https:'?'wss://':'ws://';"
        "return pr+location.host+B+String(p==null?'':p).replace(/^\\/+/, '');};"
        "var oF=window.fetch;"
        "if(oF){window.fetch=function(input,init){"
        "if(typeof input==='string'&&input.charAt(0)==='/'&&input.indexOf('//')!==0&&input.indexOf('/echo/v1/wm/app/')!==0){"
        "input=B+input.slice(1);"
        "}else if(typeof Request!=='undefined'&&input instanceof Request){"
        "try{var u=new URL(input.url);"
        "if(u.origin===location.origin&&u.pathname.charAt(0)==='/'&&u.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "input=new Request(B+u.pathname.slice(1)+u.search,input);}}catch(_){}}"
        "return oF.call(this,input,init);};}"
        "var oW=window.WebSocket;"
        "if(oW){window.WebSocket=function(url,protocols){"
        "var s=String(url==null?'':url),pr=location.protocol==='https:'?'wss://':'ws://';"
        "if(s.charAt(0)==='/'&&s.indexOf('//')!==0&&s.indexOf('/echo/v1/wm/app/')!==0){"
        "s=pr+location.host+B+s.slice(1);"
        "}else{try{var u=new URL(s,location.href);"
        "if((u.protocol==='ws:'||u.protocol==='wss:')&&u.host===location.host&&u.pathname.indexOf('/echo/v1/wm/app/')!==0){"
        "s=pr+location.host+B+u.pathname.replace(/^\\/+/, '')+u.search;}}catch(_){}}"
        "return protocols!==undefined?new oW(s,protocols):new oW(s);};"
        "window.WebSocket.prototype=oW.prototype;"
        "window.WebSocket.CONNECTING=oW.CONNECTING;window.WebSocket.OPEN=oW.OPEN;"
        "window.WebSocket.CLOSING=oW.CLOSING;window.WebSocket.CLOSED=oW.CLOSED;}"
        "})();</script>"
    ).encode("utf-8")

    patched, count = re.subn(
        rb"(<head\b[^>]*>)",
        lambda m: m.group(1) + shim_html,
        html_bytes,
        count=1,
        flags=re.IGNORECASE,
    )
    if count > 0:
        return patched
    return shim_html + html_bytes


_APP_PROXY_HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]


@router.api_route("/app/{ticket}", methods=_APP_PROXY_HTTP_METHODS)
@router.api_route("/app/{ticket}/{path:path}", methods=_APP_PROXY_HTTP_METHODS)
async def wm_app_http_proxy_endpoint(request: Request, ticket: str, path: str = ""):
    """Proxifie les requêtes HTTP ticketées HMAC vers le serveur applicatif sandboxé sans cookies SSO."""
    wm = get_window_manager()
    claims = wm.verify_app_capability_ticket(ticket)
    if claims is None:
        return JSONResponse({"detail": "Invalid or expired capability ticket"}, status_code=403)

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

    client = httpx.AsyncClient(follow_redirects=False, timeout=60.0)
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

    if "text/html" in content_type:
        try:
            raw_html = await upstream_resp.aread()
        finally:
            await upstream_resp.aclose()
            await client.aclose()
        patched_html = _inject_app_proxy_html_shim(raw_html, f"/echo/v1/wm/app/{ticket}/")
        html_skip = hop_by_hop | {"content-length", "content-security-policy", "cache-control"}
        resp = Response(
            content=patched_html,
            status_code=upstream_resp.status_code,
            media_type=upstream_resp.headers.get("content-type"),
        )
        for k, v in upstream_resp.headers.multi_items():
            if k.lower() not in html_skip:
                resp.headers.append(k, v)
        resp.headers["Content-Security-Policy"] = (
            "sandbox allow-scripts allow-downloads allow-forms allow-modals allow-popups;"
        )
        resp.headers["Cache-Control"] = "no-store"
        return resp

    resp_headers = [
        (k, v) for k, v in upstream_resp.headers.multi_items()
        if k.lower() not in (hop_by_hop | {"cache-control"})
    ]

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
