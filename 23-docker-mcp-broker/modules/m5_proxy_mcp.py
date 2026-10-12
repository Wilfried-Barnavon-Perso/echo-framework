# VERSION : 1.12 (Logging des erreurs OAuth2 dynamique et correction PEP8)

import asyncio
import json
import httpx
import os
import hashlib
import logging
from contextlib import AsyncExitStack
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.session import ClientSession

from core.security import user_id_var

logger = logging.getLogger(__name__)

# Cache des sessions actives : cache_key -> (stack, session, cred_hash)
_SESSIONS = {}
_SESSIONS_LOCK = asyncio.Lock()


async def _get_or_create_session(user_id: str, service: str, credentials_str: str) -> ClientSession:
    """Récupère une session MCP existante ou en initialise une nouvelle via l'alias."""
    cache_key = f"{user_id}_{service}"

    # Récupérer les identifiants et calculer l'empreinte
    cred_hash = hashlib.md5((credentials_str or "").encode()).hexdigest()

    async with _SESSIONS_LOCK:
        if cache_key in _SESSIONS:
            stack, session, cached_hash = _SESSIONS[cache_key]
            if cached_hash == cred_hash:
                return session

            # Les identifiants ont changé : fermeture de l'ancienne session
            try:
                await stack.aclose()
            except Exception:
                pass
            del _SESSIONS[cache_key]

        stack = AsyncExitStack()
        try:
            from server import SERVICE_SCHEMAS
            schema_def = SERVICE_SCHEMAS.get(service, {})
            category = schema_def.get("category")

            if not category:
                raise ValueError(f"Type MCP non supporté ou alias inconnu : {service}")

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

                    # ---- INJECTION DYNAMIQUE ISOLÉE POUR MICROSOFT 365 (MSAL CACHE) ----
                    if service == "microsoft_365":
                        cache_dir = f"/app/mcp_cache/microsoft_365/{user_id}"
                        os.makedirs(cache_dir, exist_ok=True)
                        env["HOME"] = cache_dir

                    # ---- INJECTION DYNAMIQUE ISOLÉE POUR GOOGLE WORKSPACE ----
                    if service == "google_workspace":
                        import tempfile
                        import urllib.parse
                        refresh_token = credentials.get("GOOGLE_OAUTH_REFRESH_TOKEN")
                        user_email = credentials.get("USER_GOOGLE_EMAIL")
                        
                        if refresh_token and user_email:
                            temp_dir_path = stack.enter_context(tempfile.TemporaryDirectory())
                            safe_email = urllib.parse.quote(user_email, safe="@._-")
                            token_file = os.path.join(temp_dir_path, f"{safe_email}.json")
                            
                            client_id = credentials.get("GOOGLE_OAUTH_CLIENT_ID", "")
                            client_secret = credentials.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
                            
                            token_data = {
                                "token": "dummy_if_missing",
                                "refresh_token": refresh_token,
                                "token_uri": "https://oauth2.googleapis.com/token",
                                "client_id": client_id,
                                "client_secret": client_secret
                            }
                            
                            try:
                                async with httpx.AsyncClient() as client:
                                    token_resp = await client.post("https://oauth2.googleapis.com/token", data={
                                        "client_id": client_id,
                                        "client_secret": client_secret,
                                        "refresh_token": refresh_token,
                                        "grant_type": "refresh_token"
                                    }, timeout=10.0)
                                    if token_resp.status_code == 200:
                                        t_data = token_resp.json()
                                        if "access_token" in t_data:
                                            token_data["token"] = t_data["access_token"]
                                        if "scope" in t_data:
                                            token_data["scopes"] = t_data["scope"].split(" ")
                                        if "expires_in" in t_data:
                                            from datetime import datetime, timedelta, timezone
                                            expiry_dt = datetime.now(timezone.utc) + timedelta(seconds=int(t_data["expires_in"]))
                                            token_data["expiry"] = expiry_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                                    else:
                                        try:
                                            err_data = token_resp.json()
                                            logger.warning(f"Google OAuth refresh failed with status {token_resp.status_code}: {err_data.get('error', 'Unknown')}")
                                        except Exception:
                                            logger.warning(f"Google OAuth refresh failed with status {token_resp.status_code}")
                            except Exception as ex:
                                logger.warning(f"Google OAuth refresh exception: {ex}")
                                # Fallback to dummy

                            with open(token_file, "w") as f:
                                json.dump(token_data, f)
                            
                            env["WORKSPACE_MCP_CREDENTIALS_DIR"] = temp_dir_path
                else:  # mcp_ephemeral
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

            _SESSIONS[cache_key] = (stack, session, cred_hash)
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
    credentials = payload.get("credentials", "")
    rpc_request = payload.get("rpc_request", {})

    if not user_id:
        user_id = user_id_var.get()

    method = rpc_request.get("method")
    params = rpc_request.get("params", {})

    if not service or not method:
        raise ValueError("Paramètres 'service' ou 'rpc_request.method' manquants.")

    session = await _get_or_create_session(user_id, service, credentials)

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
