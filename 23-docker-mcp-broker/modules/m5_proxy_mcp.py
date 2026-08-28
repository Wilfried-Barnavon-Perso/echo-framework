import asyncio
import json
import hashlib
from contextlib import AsyncExitStack
from mcp.client.stdio import stdio_client, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.session import ClientSession

# Cache des sessions actives : cache_key -> (stack, session)
_SESSIONS = {}
_SESSIONS_LOCK = asyncio.Lock()


async def _get_or_create_session(service_config: dict) -> ClientSession:
    """Récupère une session MCP existante ou en initialise une nouvelle."""
    config_str = json.dumps(service_config, sort_keys=True)
    cache_key = hashlib.md5(config_str.encode()).hexdigest()

    async with _SESSIONS_LOCK:
        if cache_key in _SESSIONS:
            return _SESSIONS[cache_key][1]

        stack = AsyncExitStack()
        try:
            mcp_type = service_config.get("type")

            if mcp_type == "stdio_mcp":
                command = service_config.get("command")
                if not command:
                    raise ValueError("Commande manquante pour stdio_mcp.")

                args_raw = service_config.get("args", "[]")
                if isinstance(args_raw, str):
                    try:
                        args = json.loads(args_raw)
                    except Exception:
                        args = []
                else:
                    args = args_raw

                env = service_config.get("env", None)

                server_params = StdioServerParameters(command=command, args=args, env=env)
                stdio_transport = await stack.enter_async_context(stdio_client(server_params))
                read_stream, write_stream = stdio_transport

            elif mcp_type == "remote_mcp":
                url = service_config.get("url")
                if not url:
                    raise ValueError("URL manquante pour remote_mcp.")

                headers_raw = service_config.get("headers", "{}")
                if isinstance(headers_raw, str):
                    try:
                        headers = json.loads(headers_raw)
                    except Exception:
                        headers = {}
                else:
                    headers = headers_raw

                sse_transport = await stack.enter_async_context(sse_client(url, headers=headers))
                read_stream, write_stream = sse_transport

            else:
                raise ValueError(f"Type MCP non supporté : {mcp_type}")

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
    Attend un payload de la forme :
    {
        "service_config": {"type": "stdio_mcp", "command": "npx", "args": ...},
        "method": "tools/list",
        "params": {}
    }
    """
    service_config = payload.get("service_config", {})
    method = payload.get("method")
    params = payload.get("params", {})

    session = await _get_or_create_session(service_config)

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
        # En cas d'erreur fatale (ex: pipe cassé), on purge le cache pour forcer la reconnexion
        config_str = json.dumps(service_config, sort_keys=True)
        cache_key = hashlib.md5(config_str.encode()).hexdigest()
        async with _SESSIONS_LOCK:
            if cache_key in _SESSIONS:
                stack, _ = _SESSIONS.pop(cache_key)
                await stack.aclose()
        raise e
