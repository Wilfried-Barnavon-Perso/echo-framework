# VERSION : 1.0 (Adaptateurs de cycle de vie des fournisseurs MCP résidents : Microsoft 365 MSAL & Google Workspace OAuth2)
"""
ECHO MCP Broker - Adaptateurs de cycle de vie spécifiques aux fournisseurs MCP résidents.
Isole la logique d'authentification et de préparation d'environnement (Microsoft 365 MSAL,
Google Workspace OAuth2) hors de l'orchestrateur de transport générique m5_proxy_mcp.py.
"""
import ast
import hashlib
import json
import logging
import os
import tempfile
import urllib.parse
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

MCP_CACHE_ROOT = os.environ.get("ECHO_MCP_CACHE_DIR", "/app/mcp_cache")


class MCPServiceProvider:
    """Interface de base (No-Op) pour les adaptateurs de services MCP."""

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
    ) -> None:
        """Prépare le système de fichiers et les variables d'environnement avant lancement Stdio."""
        return None

    async def before_tool_call(
        self,
        user_id: str,
        tool_name: str,
        args: dict,
    ) -> Tuple[dict, Optional[dict]]:
        """
        Hook exécuté avant `session.call_tool(tool_name, args)`.
        Retourne `(normalized_args, short_circuit_response)`.
        Si `short_circuit_response` n'est pas None, l'appel MCP est court-circuité.
        """
        return args, None

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
    ) -> dict:
        """Hook exécuté après `session.call_tool(tool_name, args)` sur le résultat sérialisé."""
        return result


class GoogleWorkspaceProvider(MCPServiceProvider):
    """Adaptateur OAuth2 pour le serveur résident Google Workspace (`workspace-mcp`)."""

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
    ) -> None:
        refresh_token = credentials.get("GOOGLE_OAUTH_REFRESH_TOKEN")
        user_email = credentials.get("USER_GOOGLE_EMAIL")

        if not (refresh_token and user_email):
            return

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
            "client_secret": client_secret,
        }

        try:
            async with httpx.AsyncClient() as client:
                token_resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "refresh_token": refresh_token,
                        "grant_type": "refresh_token",
                    },
                    timeout=10.0,
                )
                if token_resp.status_code == 200:
                    t_data = token_resp.json()
                    if "access_token" in t_data:
                        token_data["token"] = t_data["access_token"]
                    if "scope" in t_data:
                        token_data["scopes"] = t_data["scope"].split(" ")
                    if "expires_in" in t_data:
                        expiry_dt = datetime.now(timezone.utc) + timedelta(
                            seconds=int(t_data["expires_in"])
                        )
                        token_data["expiry"] = expiry_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                else:
                    try:
                        err_data = token_resp.json()
                        logger.warning(
                            "Google OAuth refresh failed with status %s: %s",
                            token_resp.status_code,
                            err_data.get("error", "Unknown"),
                        )
                    except Exception:
                        logger.warning(
                            "Google OAuth refresh failed with status %s",
                            token_resp.status_code,
                        )
        except Exception as ex:
            logger.warning("Google OAuth refresh exception: %s", ex)

        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        env["WORKSPACE_MCP_CREDENTIALS_DIR"] = temp_dir_path


class Microsoft365Provider(MCPServiceProvider):
    """
    Adaptateur MSAL pour le serveur résident Microsoft 365 (`microsoft-mcp`).
    Résout :
    1. La persistance du cache MSAL dans `/app/mcp_cache/microsoft_365/<user_id>`.
    2. La corruption de `_flow_cache` par le LLM lors de `complete_authentication`.
    3. Le blocage synchrone de 900s de `msal.acquire_token_by_device_flow` (`expires_at = 0`).
    4. Le blocage de 900s + corruption stdout dans `get_token()` lorsque `account_id` est un email
       ou que le cache MSAL est vide.
    """

    AUTH_EXEMPT_TOOLS = frozenset(
        {"list_accounts", "authenticate_account", "complete_authentication"}
    )

    @staticmethod
    def _user_cache_dir(user_id: str) -> str:
        safe_uid = "".join(
            c for c in str(user_id or "anonymous") if c.isalnum() or c in ("-", "_", ".")
        )
        return os.path.join(MCP_CACHE_ROOT, "microsoft_365", safe_uid or "anonymous")

    @classmethod
    def _token_cache_path(cls, user_id: str) -> str:
        return os.path.join(cls._user_cache_dir(user_id), ".microsoft_mcp_token_cache.json")

    @classmethod
    def _flow_cache_path(cls, user_id: str) -> str:
        return os.path.join(cls._user_cache_dir(user_id), ".microsoft_mcp_device_flow.json")

    @classmethod
    def _load_cached_accounts(cls, user_id: str) -> list[dict]:
        """Lit les comptes MSAL persistés dans `.microsoft_mcp_token_cache.json`."""
        cache_path = cls._token_cache_path(user_id)
        if not os.path.exists(cache_path):
            return []
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            accounts_map = data.get("Account", {})
            if not isinstance(accounts_map, dict):
                return []
            result = []
            for acc in accounts_map.values():
                if isinstance(acc, dict) and acc.get("home_account_id"):
                    result.append(
                        {
                            "account_id": str(acc.get("home_account_id")),
                            "username": str(acc.get("username", "")),
                        }
                    )
            return result
        except Exception as e:
            logger.warning("Lecture du cache MSAL impossible (%s): %s", cache_path, e)
            return []

    @staticmethod
    def _make_tool_response(payload: dict, is_error: bool = False) -> dict:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": payload,
            "isError": is_error,
        }

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
    ) -> None:
        cache_dir = self._user_cache_dir(user_id)
        os.makedirs(cache_dir, exist_ok=True)
        ms_sig = hashlib.md5(
            f"{credentials.get('MICROSOFT_MCP_CLIENT_ID', '')}:{credentials.get('MICROSOFT_MCP_TENANT_ID', '')}".encode()
        ).hexdigest()
        sig_file = os.path.join(cache_dir, ".cred_hash")
        token_cache_file = self._token_cache_path(user_id)
        flow_cache_file = self._flow_cache_path(user_id)

        old_sig = None
        if os.path.exists(sig_file):
            try:
                with open(sig_file, "r", encoding="utf-8") as sf_in:
                    old_sig = sf_in.read().strip()
            except Exception:
                old_sig = None

        if old_sig and old_sig != ms_sig:
            for stale_file in (token_cache_file, flow_cache_file):
                if os.path.exists(stale_file):
                    try:
                        os.remove(stale_file)
                    except Exception:
                        pass

        if old_sig != ms_sig:
            try:
                with open(sig_file, "w", encoding="utf-8") as sf_out:
                    sf_out.write(ms_sig)
            except Exception:
                pass

        env["HOME"] = cache_dir

    async def before_tool_call(
        self,
        user_id: str,
        tool_name: str,
        args: dict,
    ) -> Tuple[dict, Optional[dict]]:
        args = dict(args or {})

        # Cas 1 : Finalisation Device Code Flow -> restauration du flow exact + single-poll
        if tool_name == "complete_authentication":
            flow_path = self._flow_cache_path(user_id)
            raw_flow = None
            if os.path.exists(flow_path):
                try:
                    with open(flow_path, "r", encoding="utf-8") as f:
                        saved = json.load(f)
                    raw_flow = saved.get("_flow_cache")
                except Exception as e:
                    logger.warning("Impossible de relire le device flow MS365: %s", e)

            flow_str = raw_flow or args.get("flow_cache", "")
            if not flow_str:
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": (
                            "Aucun flux d'authentification en cours trouvé dans le cache. "
                            "Veuillez d'abord appeler authenticate_account."
                        ),
                    },
                    is_error=True,
                )

            try:
                flow_dict = (
                    ast.literal_eval(flow_str) if isinstance(flow_str, str) else dict(flow_str)
                )
                if isinstance(flow_dict, dict):
                    # Force MSAL obtain_token_by_device_flow à exécuter 1 seule requête HTTP
                    # immédiate au lieu de bloquer 900s sur authorization_pending.
                    flow_dict["expires_at"] = 0
                    args["flow_cache"] = repr(flow_dict)
            except Exception as e:
                logger.warning("Échec de normalisation du flow_cache MS365: %s", e)
                if raw_flow:
                    args["flow_cache"] = raw_flow

            return args, None

        # Cas 2 : Outils d'authentification/listing de comptes -> passage direct
        if tool_name in self.AUTH_EXEMPT_TOOLS:
            return args, None

        # Cas 3 : Outils métiers -> vérification et résolution déterministe de account_id
        accounts = self._load_cached_accounts(user_id)
        if not accounts:
            return args, self._make_tool_response(
                {
                    "status": "auth_required",
                    "message": (
                        "Aucun compte Microsoft 365 authentifié dans le cache MSAL persistant. "
                        "Appelez d'abord `authenticate_account`, présentez le code et l'URL à "
                        "l'utilisateur, puis appelez `complete_authentication` après validation."
                    ),
                },
                is_error=True,
            )

        provided_id = str(args.get("account_id") or "").strip()
        by_id = {a["account_id"]: a["account_id"] for a in accounts}
        by_username = {a["username"].lower(): a["account_id"] for a in accounts if a.get("username")}

        if provided_id in by_id:
            return args, None

        if provided_id and provided_id.lower() in by_username:
            resolved = by_username[provided_id.lower()]
            logger.info("MS365: account_id '%s' résolu vers '%s'", provided_id, resolved)
            args["account_id"] = resolved
            return args, None

        if len(accounts) == 1:
            resolved = accounts[0]["account_id"]
            logger.info(
                "MS365: account_id '%s' auto-résolu vers le compte unique '%s'",
                provided_id,
                resolved,
            )
            args["account_id"] = resolved
            return args, None

        return args, self._make_tool_response(
            {
                "status": "invalid_account_id",
                "message": (
                    f"Le paramètre account_id='{provided_id}' ne correspond à aucun compte MSAL "
                    "en cache. Utilisez l'un des identifiants `account_id` ci-dessous."
                ),
                "available_accounts": accounts,
            },
            is_error=True,
        )

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
    ) -> dict:
        if tool_name not in ("authenticate_account", "complete_authentication"):
            return result

        payload = None
        if isinstance(result.get("structuredContent"), dict):
            payload = result["structuredContent"]
        else:
            for item in result.get("content", []):
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        parsed = json.loads(item.get("text", ""))
                        if isinstance(parsed, dict):
                            payload = parsed
                            break
                    except Exception:
                        pass

        if not isinstance(payload, dict):
            return result

        flow_path = self._flow_cache_path(user_id)
        if tool_name == "authenticate_account" and payload.get("_flow_cache"):
            try:
                os.makedirs(os.path.dirname(flow_path), exist_ok=True)
                with open(flow_path, "w", encoding="utf-8") as f:
                    json.dump({"_flow_cache": str(payload["_flow_cache"])}, f)
                logger.info("MS365: _flow_cache sauvegardé dans %s", flow_path)
            except Exception as e:
                logger.warning("MS365: échec de sauvegarde de _flow_cache: %s", e)

        elif tool_name == "complete_authentication" and payload.get("status") == "success":
            if os.path.exists(flow_path):
                try:
                    os.remove(flow_path)
                except Exception:
                    pass

        return result


_DEFAULT_PROVIDER = MCPServiceProvider()
_PROVIDERS: dict[str, MCPServiceProvider] = {
    "microsoft_365": Microsoft365Provider(),
    "google_workspace": GoogleWorkspaceProvider(),
}


def get_service_provider(service: str) -> MCPServiceProvider:
    """Retourne l'adaptateur spécifique au service ou l'adaptateur No-Op par défaut."""
    return _PROVIDERS.get(service, _DEFAULT_PROVIDER)
