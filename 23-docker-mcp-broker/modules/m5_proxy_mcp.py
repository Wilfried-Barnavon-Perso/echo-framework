import asyncio
import json
import httpx
import os
from contextlib import AsyncExitStack
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession

from core.database import get_identity
from core.security import user_id_var

# Cache des sessions actives : cache_key -> (stack, session)
_SESSIONS = {}
_SESSIONS_LOCK = asyncio.Lock()

async def _get_or_create_session(user_id: str, service: str) -> ClientSession:
    """Récupère une session MCP existante ou en initialise une nouvelle via l'alias."""
    cache_key = f"{user_id}_{service}"

    async with _SESSIONS_LOCK:
        if cache_key in _SESSIONS:
            return _SESSIONS[cache_key][1]

        stack = AsyncExitStack()
        try:
            from server import SERVICE_SCHEMAS
            schema_def = SERVICE_SCHEMAS.get(service, {})
            category = schema_def.get("category")

            if not category:
                raise ValueError(f"Type MCP non supporté ou alias inconnu : {service}")

            # Récupérer les identifiants
            credentials_str = await get_identity(user_id, service)
            credentials = {}
            if credentials_str:
                try:
                    credentials = json.loads(credentials_str)
                except Exception:
                    credentials = {"OAUTH2_TOKEN": credentials_str}

            if category in ("mcp_resident", "mcp_ephemeral"):
                if category == "mcp_resident":
                    command = schema_def.get("command")
                    if not command:
                        raise ValueError(f"Commande manquante dans le schéma pour {service}")
                    args = schema_def.get("args", [])
                    env = os.environ.copy()
                    env.update({str(k): str(v) for k, v in credentials.items()})
                else: # mcp_ephemeral
                    command = credentials.get("command")
                    if not command:
                        raise ValueError("Commande manquante pour exécution Stdio (ephemeral).")
                    
                    args_raw = credentials.get("args", "[]")
                    if isinstance(args_raw, str):
                        try:
                            args = json.loads(args_raw)
                        except Exception:
                            args = []
                    else:
                        args = args_raw

                    env = os.environ.copy()
                
                server_params = StdioServerParameters(command=command, args=args, env=env)
                stdio_transport = await stack.enter_async_context(stdio_client(server_params))
                read_stream, write_stream = stdio_transport

            elif category == "mcp_remote":
                url = credentials.get("url")
                if not url:
                    raise ValueError("URL manquante pour le serveur distant.")

                transport_type = credentials.get("transport", "sse").lower().strip()
                headers_raw = credentials.get("headers", "{}")
                if isinstance(headers_raw, str):
                    try:
                        headers = json.loads(headers_raw)
                    except Exception:
                        headers = {}
                else:
                    headers = headers_raw

                headers_lower = {k.lower(): v for k, v in headers.items()}
                if "user-agent" not in headers_lower:
                    headers["User-Agent"] = "antigravity/2.5.5 linux/amd64"

                if transport_type == "streamable_http":
                    http_client = httpx.AsyncClient(
                        headers=headers, 
                        timeout=httpx.Timeout(15.0, read=300.0)
                    )
                    await stack.enter_async_context(http_client)
                    mcp_transport = await stack.enter_async_context(streamable_http_client(url, http_client=http_client))
                    read_stream = mcp_transport[0]
                    write_stream = mcp_transport[1]
                else:
                    mcp_transport = await stack.enter_async_context(sse_client(url, headers=headers, timeout=15.0))
                    read_stream = mcp_transport[0]
                    write_stream = mcp_transport[1]
            else:
                raise ValueError(f"Type MCP non supporté : {category}")

            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()

            _SESSIONS[cache_key] = (stack, session)
            return session

        except Exception as e:
            await stack.aclose()
            raise e

async def proxy_mcp_request(payload: dict) -> dict:
    """
    Exécute une requête JSON-RPC sur le bon client MCP (Stdio ou SSE).
    Attend un payload de la forme Data-Driven (Universal MCP Tool) :
    {
        "user_id": "...",
        "service": "google_workspace",
        "rpc_request": {"jsonrpc": "2.0", "method": "tools/list", "id": 1, "params": {}}
    }
    """
    user_id = payload.get("user_id")
    service = payload.get("service")
    rpc_request = payload.get("rpc_request", {})
    
    if not user_id:
        user_id = user_id_var.get()
        
    method = rpc_request.get("method")
    params = rpc_request.get("params", {})

    if not service or not method:
        raise ValueError("Paramètres 'service' ou 'rpc_request.method' manquants.")

    session = await _get_or_create_session(user_id, service)

    try:
        if method == "tools/list":
            result = await session.list_tools()
            return result.model_dump()
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            result = await session.call_tool(name, args)
            return result.model_dump()
        else:
            raise ValueError(f"Méthode non supportée par le proxy : {method}")
    except Exception as e:
        cache_key = f"{user_id}_{service}"
        async with _SESSIONS_LOCK:
            if cache_key in _SESSIONS:
                stack, _ = _SESSIONS.pop(cache_key)
                await stack.aclose()
        raise e
