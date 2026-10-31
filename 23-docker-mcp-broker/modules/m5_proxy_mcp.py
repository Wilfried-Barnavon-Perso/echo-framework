# VERSION : 5.231.37.1
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.37.1: Propagation de chat_id aux hooks before_tool_call et after_tool_call de MCPServiceProvider.
# 5.231.20.1: Isolation des sessions MCP actives par compte optionnel (cache_key incluant account_id).
# 5.228.0.1: Import partagé de AuthRequiredError depuis m5_providers et déduplication du message d'erreur.
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.18: Hook générique after_list_tools pour l'adaptation des schémas de fournisseurs résidents

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

from core.security import user_id_var, chat_id_var
from modules.m5_providers import AuthRequiredError, get_service_provider

logger = logging.getLogger(__name__)


class TolerantClientSession(ClientSession):
    """ClientSession mcp 2.x tolérante aux schémas de sortie imprécis des serveurs tiers.

    `call_tool` appelle `self.validate_tool_result` (nom public en mcp 2.x). La surcharge
    journalise l'écart (RuntimeError uniquement) et laisse le résultat atteindre le LLM.
    """

    async def validate_tool_result(self, name, result):
        try:
            await super().validate_tool_result(name, result)
        except RuntimeError as val_err:
            logger.warning(f"Validation tolérée du schéma de sortie pour l'outil '{name}' : {val_err}")


# Délais (s) : démarrage d'un serveur stdio (npx/uvx peuvent télécharger) et fermeture propre
MCP_SESSION_START_TIMEOUT = 60
MCP_SESSION_CLOSE_TIMEOUT = 15


def _unwrap(exc: BaseException) -> BaseException:
    """Déroule les ExceptionGroup à exception unique (TaskGroup AnyIO) jusqu'à l'erreur réelle."""
    while isinstance(exc, BaseExceptionGroup) and len(exc.exceptions) == 1:
        exc = exc.exceptions[0]
    return exc


class _SessionHolder:
    """Possède le cycle de vie d'une session MCP dans UNE tâche asyncio dédiée.

    AnyIO exige que les cancel scopes (stdio_client, ClientSession) soient quittés par la
    tâche qui les a ouverts. Les requêtes HTTP du broker sont des tâches distinctes : la pile
    ne peut donc pas être fermée depuis une requête. La tâche propriétaire ouvre, attend le
    signal d'arrêt, puis ferme dans le même contexte. Les appels d'outils, eux, peuvent être
    émis depuis n'importe quelle tâche.
    """

    def __init__(self, opener, cred_hash: str):
        self._opener = opener
        self.cred_hash = cred_hash
        self.session = None
        self._ready = asyncio.Event()
        self._stop = asyncio.Event()
        self._error = None
        self._task = None

    async def _run(self):
        try:
            async with AsyncExitStack() as stack:
                read_stream, write_stream = await self._opener(stack)
                session = await stack.enter_async_context(TolerantClientSession(read_stream, write_stream))
                await session.initialize()
                self.session = session
                self._ready.set()
                await self._stop.wait()
        except asyncio.CancelledError:
            raise
        except BaseException as e:  # noqa: BLE001 - transmis tel quel à la requête appelante
            self._error = _unwrap(e)
        finally:
            self.session = None
            self._ready.set()  # débloque start() quel que soit le résultat

    async def start(self):
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=MCP_SESSION_START_TIMEOUT)
        except asyncio.TimeoutError:
            # Session jamais prête : pas d'arrêt « propre » à attendre, annulation directe
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            raise TimeoutError(f"Démarrage du serveur MCP > {MCP_SESSION_START_TIMEOUT}s")
        if self._error is not None:
            raise self._error  # type d'origine conservé (AuthRequiredError, ValueError...)

    async def close(self):
        self._stop.set()
        if self._task is None:
            return
        try:
            await asyncio.wait_for(self._task, timeout=MCP_SESSION_CLOSE_TIMEOUT)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Fermeture de session MCP : {e!r}")
            self._task.cancel()


def _dump(result) -> dict:
    """Sérialise un résultat MCP au format du protocole (camelCase : isError, structuredContent...)."""
    return result.model_dump(by_alias=True, mode="json", exclude_none=True)


# Cache des sessions actives : cache_key -> _SessionHolder
_SESSIONS = {}
_SESSIONS_LOCK = asyncio.Lock()


async def _open_transport(stack: AsyncExitStack, user_id: str, service: str, credentials_str: str):
    """Ouvre le transport MCP (stdio résident/éphémère, SSE, streamable HTTP) dans `stack`.

    Retourne (read_stream, write_stream). Corps identique à la v1.14 (déplacé, non modifié).
    """
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

            # Validation préventive des variables obligatoires déclarées dans le schéma
            required_fields = [
                f["id"] for f in schema_def.get("fields", [])
                if f.get("required") is True
            ]
            missing_fields = [
                fid for fid in required_fields
                if not credentials.get(fid) and not os.environ.get(fid)
            ]
            if missing_fields:
                raise AuthRequiredError(
                    f"Variables obligatoires manquantes dans l'Identity Vault pour '{service}' : "
                    f"{', '.join(missing_fields)}."
                )

            args = schema_def.get("args", [])
            env = os.environ.copy()
            env.update({str(k): str(v) for k, v in credentials.items()})

            provider = get_service_provider(service)
            await provider.prepare_env(stack, user_id, credentials, env)
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

    return read_stream, write_stream


async def _get_or_create_session(
    user_id: str, service: str, credentials_str: str, account_id: str = None
) -> ClientSession:
    """Récupère une session MCP existante ou en initialise une nouvelle via l'alias."""
    cache_key = f"{user_id}_{service}_{account_id}" if account_id else f"{user_id}_{service}"

    # Récupérer les identifiants et calculer l'empreinte
    cred_hash = hashlib.md5((credentials_str or "").encode()).hexdigest()

    async with _SESSIONS_LOCK:
        holder = _SESSIONS.get(cache_key)
        if holder is not None:
            if holder.cred_hash == cred_hash and holder.session is not None:
                return holder.session

            # Identifiants modifiés ou session morte : fermeture par sa tâche propriétaire
            await holder.close()
            _SESSIONS.pop(cache_key, None)

        holder = _SessionHolder(
            lambda stack: _open_transport(stack, user_id, service, credentials_str), cred_hash
        )
        await holder.start()
        _SESSIONS[cache_key] = holder
        return holder.session


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
    chat_id = payload.get("chat_id")
    service = payload.get("service")
    account_id = payload.get("account_id")
    credentials = payload.get("credentials", "")
    rpc_request = payload.get("rpc_request", {})

    if not user_id:
        user_id = user_id_var.get()
    if not chat_id:
        chat_id = chat_id_var.get()
    if chat_id:
        chat_id_var.set(chat_id)

    method = rpc_request.get("method")
    params = rpc_request.get("params", {})

    if not service or not method:
        raise ValueError("Paramètres 'service' ou 'rpc_request.method' manquants.")

    session = await _get_or_create_session(user_id, service, credentials, account_id=account_id)
    provider = get_service_provider(service)

    try:
        if method == "tools/list":
            dumped_tools = _dump(await session.list_tools())
            return await provider.after_list_tools(user_id, dumped_tools)
        elif method == "tools/call":
            name = params.get("name")
            args = params.get("arguments", {})
            args, short_circuit = await provider.before_tool_call(
                user_id, name, args, chat_id=chat_id
            )
            if short_circuit is not None:
                return short_circuit
            dumped = _dump(await session.call_tool(name, args))
            return await provider.after_tool_call(
                user_id, name, dumped, chat_id=chat_id
            )
        else:
            raise ValueError(f"Méthode non supportée par le proxy : {method}")
    except BaseException:
        cache_key = f"{user_id}_{service}_{account_id}" if account_id else f"{user_id}_{service}"
        async with _SESSIONS_LOCK:
            holder = _SESSIONS.get(cache_key)
            if holder is not None and holder.session is session:
                _SESSIONS.pop(cache_key, None)
            else:
                holder = None
        if holder is not None:
            try:
                await asyncio.shield(holder.close())
            except BaseException:
                pass
        raise
