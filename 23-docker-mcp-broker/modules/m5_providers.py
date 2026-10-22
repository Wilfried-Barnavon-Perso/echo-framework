# VERSION : 5.229.1.1
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.229.1.1: Auto-résolution dossiers M365, pagination/tri list_emails, trash_email et batch_trash_emails ($batch).
# 5.228.0.1: Fail-Closed OAuth2 GoogleWorkspaceProvider (prepare_env + bouclier anti-localhost:8001).
# 5.226.18.0: Alignement sur le versioning global ECHO.
# 1.1: Occultation intégrale de _flow_cache dans tools/list et authenticate_account pour zéro fuite en contexte LLM
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


class AuthRequiredError(Exception):
    """Levée lorsque des variables d'authentification obligatoires sont manquantes ou expirées."""
    pass


class MCPServiceProvider:
    """Interface de base (No-Op) pour les adaptateurs de services MCP."""

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
        """Prépare le système de fichiers et les variables d'environnement avant lancement Stdio."""
        return None

    async def after_list_tools(
        self,
        user_id: str,
        result: dict,
    ) -> dict:
        """Hook exécuté après `session.list_tools()` sur le dictionnaire sérialisé."""
        return result

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

    OAUTH_ERROR_MARKERS = (
        "localhost:8001",
        "oauth2callback",
        "accounts.google.com/o/oauth2",
        "invalid_grant",
        "RefreshError",
        "Token has been expired or revoked",
    )

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
            raise AuthRequiredError(
                "Identifiants Google Workspace incomplets (USER_GOOGLE_EMAIL ou GOOGLE_OAUTH_REFRESH_TOKEN manquant)."
            )

        client_id = credentials.get("GOOGLE_OAUTH_CLIENT_ID", "")
        client_secret = credentials.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

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
        except Exception as ex:
            logger.warning("Google OAuth refresh network exception: %s", ex)
            raise AuthRequiredError(
                f"Échec réseau lors du rafraîchissement du jeton OAuth2 Google Workspace : {ex}"
            ) from ex

        if token_resp.status_code != 200:
            err_detail = f"HTTP {token_resp.status_code}"
            try:
                err_data = token_resp.json()
                err_code = err_data.get("error", "unknown_error")
                err_desc = err_data.get("error_description", "")
                err_detail = f"{err_code} ({err_desc})" if err_desc else str(err_code)
            except Exception:
                pass
            logger.warning("Google OAuth refresh failed: %s", err_detail)
            raise AuthRequiredError(
                f"Authentification Google Workspace invalide ou expirée ({err_detail}). "
                "Le rafraîchissement automatique OAuth2 a échoué. Les flux d'authentification "
                "interactive locale (localhost:8001) sont strictement proscrits en environnement conteneurisé."
            )

        t_data = token_resp.json()
        access_token = t_data.get("access_token")
        if not access_token:
            raise AuthRequiredError(
                "Réponse OAuth2 Google invalide (aucun access_token retourné). "
                "Veuillez régénérer GOOGLE_OAUTH_REFRESH_TOKEN."
            )

        token_data = {
            "token": access_token,
            "refresh_token": refresh_token,
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        if "scope" in t_data:
            token_data["scopes"] = t_data["scope"].split(" ")
        if "expires_in" in t_data:
            expiry_dt = datetime.now(timezone.utc) + timedelta(
                seconds=int(t_data["expires_in"])
            )
            token_data["expiry"] = expiry_dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

        temp_dir_path = stack.enter_context(tempfile.TemporaryDirectory())
        safe_email = urllib.parse.quote(user_email, safe="@._-")
        token_file = os.path.join(temp_dir_path, f"{safe_email}.json")

        with open(token_file, "w", encoding="utf-8") as f:
            json.dump(token_data, f)

        env["WORKSPACE_MCP_CREDENTIALS_DIR"] = temp_dir_path

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
    ) -> dict:
        """
        Bouclier anti-pollution : intercepte toute tentative de `workspace-mcp` d'exposer
        une URL locale (localhost:8001/oauth2callback) ou une erreur d'expiration OAuth2
        en cours de session, et lève `AuthRequiredError` pour évincer la session corrompue.
        """
        if not isinstance(result, dict):
            return result

        serialized = json.dumps(result, ensure_ascii=False)
        if any(marker in serialized for marker in self.OAUTH_ERROR_MARKERS):
            logger.warning(
                "Interception d'une fuite d'authentification locale/expirée Google Workspace sur l'outil '%s'.",
                tool_name,
            )
            raise AuthRequiredError(
                "Authentification Google Workspace invalide ou expirée en cours de session. "
                "Le flux interactif local (localhost:8001/oauth2callback) émis par workspace-mcp "
                "a été intercepté et bloqué car il est inaccessible depuis l'hôte."
            )

        return result


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
    WELL_KNOWN_PATHS = {
        "inbox": "inbox",
        "sent": "sentitems",
        "sentitems": "sentitems",
        "drafts": "drafts",
        "deleted": "deleteditems",
        "deleteditems": "deleteditems",
        "junk": "junkemail",
        "junkemail": "junkemail",
        "archive": "archive",
    }
    WELL_KNOWN_FOLDERS = frozenset(WELL_KNOWN_PATHS.keys())
    _FOLDERS_CACHE: dict[str, dict] = {}

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

    async def prepare_env(
        self,
        stack: AsyncExitStack,
        user_id: str,
        credentials: dict,
        env: dict,
    ) -> None:
        cache_dir = self._user_cache_dir(user_id)
        os.makedirs(cache_dir, exist_ok=True)
        cid = credentials.get("MICROSOFT_MCP_CLIENT_ID", "")
        tid = credentials.get("MICROSOFT_MCP_TENANT_ID", "")
        ms_sig = hashlib.md5(f"{cid}:{tid}".encode()).hexdigest()
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

        # Sauvegarde persistante des identifiants d'app pour les requêtes Graph directes
        cfg_file = os.path.join(cache_dir, ".cred_config.json")
        try:
            with open(cfg_file, "w", encoding="utf-8") as cf_out:
                json.dump(
                    {
                        "MICROSOFT_MCP_CLIENT_ID": credentials.get(
                            "MICROSOFT_MCP_CLIENT_ID", ""
                        ),
                        "MICROSOFT_MCP_TENANT_ID": credentials.get(
                            "MICROSOFT_MCP_TENANT_ID", "common"
                        ),
                    },
                    cf_out,
                )
        except Exception:
            pass

        env["HOME"] = cache_dir

    @classmethod
    def _get_access_token(cls, user_id: str, account_id: Optional[str] = None) -> str:
        """
        Extrait un jeton d'accès Graph valide depuis le cache MSAL persistant.
        Tente d'abord une lecture directe du jeton actif, sinon rafraîchit
        silencieusement via MSAL SerializableTokenCache ou renvoie une exception.
        """
        cache_path = cls._token_cache_path(user_id)
        if not os.path.exists(cache_path):
            raise ValueError(f"Cache MSAL introuvable dans {cache_path}")

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            raise ValueError(f"Lecture du cache MSAL impossible: {e}")

        now = int(datetime.now(timezone.utc).timestamp())

        # 1. Recherche d'un AccessToken non expiré dans le cache sérialisé MSAL
        access_tokens = data.get("AccessToken", {})
        if isinstance(access_tokens, dict):
            for item in access_tokens.values():
                if not isinstance(item, dict):
                    continue
                if account_id and item.get("home_account_id") != account_id:
                    continue
                secret = item.get("secret")
                expires_on = int(item.get("expires_on", 0))
                if secret and expires_on > (now + 60):
                    return secret

        # 2. Rafraîchissement silencieux via MSAL
        try:
            import msal
        except ImportError:
            raise ValueError("Module 'msal' indisponible pour rafraîchir le jeton.")

        cache_dir = cls._user_cache_dir(user_id)
        client_id = os.environ.get("MICROSOFT_MCP_CLIENT_ID")
        tenant_id = os.environ.get("MICROSOFT_MCP_TENANT_ID", "common")

        cfg_path = os.path.join(cache_dir, ".cred_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as cf:
                    saved_cfg = json.load(cf)
                client_id = client_id or saved_cfg.get("MICROSOFT_MCP_CLIENT_ID")
                tenant_id = tenant_id or saved_cfg.get("MICROSOFT_MCP_TENANT_ID")
            except Exception:
                pass

        if not client_id:
            raise ValueError("MICROSOFT_MCP_CLIENT_ID manquant pour rafraîchir le jeton.")

        authority = f"https://login.microsoftonline.com/{tenant_id}"
        cache = msal.SerializableTokenCache()
        with open(cache_path, "r", encoding="utf-8") as f:
            cache.deserialize(f.read())

        app = msal.PublicClientApplication(
            client_id, authority=authority, token_cache=cache
        )
        accounts = app.get_accounts()
        account = None
        if account_id:
            account = next(
                (a for a in accounts if a.get("home_account_id") == account_id),
                None,
            )
        elif accounts:
            account = accounts[0]

        res = app.acquire_token_silent(
            ["https://graph.microsoft.com/.default"], account=account
        )
        if res and "access_token" in res:
            if cache.has_state_changed:
                try:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        f.write(cache.serialize())
                except Exception as e:
                    logger.warning("Échec sauvegarde cache MSAL après rafraîchissement: %s", e)
            return res["access_token"]

        raise ValueError("Impossible d'obtenir un jeton Graph valide silencieusement.")

    async def _fetch_all_mail_folders(
        self, user_id: str, account_id: str
    ) -> list[dict]:
        """
        Explore récursivement tous les dossiers et sous-dossiers de messagerie
        via l'API Microsoft Graph v1.0.
        """
        token = self._get_access_token(user_id, account_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }
        all_folders: list[dict] = []
        fields = (
            "id,displayName,parentFolderId,childFolderCount,"
            "unreadItemCount,totalItemCount"
        )

        async with httpx.AsyncClient(timeout=15.0) as client:
            url = (
                f"https://graph.microsoft.com/v1.0/me/mailFolders"
                f"?$top=100&$select={fields}"
            )
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                logger.warning(
                    "Graph API mailFolders error %s: %s", resp.status_code, resp.text
                )
                return []

            root_folders = resp.json().get("value", [])

            async def _fetch_children(
                parent_id: str, parent_path: str, depth: int = 1
            ) -> None:
                if depth > 5:
                    return
                child_url = (
                    f"https://graph.microsoft.com/v1.0/me/mailFolders/{parent_id}/childFolders"
                    f"?$top=100&$select={fields}"
                )
                c_resp = await client.get(child_url, headers=headers)
                if c_resp.status_code == 200:
                    for child in c_resp.json().get("value", []):
                        c_name = child.get("displayName", "")
                        c_path = f"{parent_path}/{c_name}" if parent_path else c_name
                        child_entry = dict(child)
                        child_entry["path"] = c_path
                        all_folders.append(child_entry)
                        if child_entry.get("childFolderCount", 0) > 0:
                            await _fetch_children(child_entry["id"], c_path, depth + 1)

            for folder in root_folders:
                f_name = folder.get("displayName", "")
                folder_entry = dict(folder)
                folder_entry["path"] = f_name
                all_folders.append(folder_entry)
                if folder_entry.get("childFolderCount", 0) > 0:
                    await _fetch_children(folder_entry["id"], f_name, 1)

        return all_folders

    async def _get_cached_folders(
        self, user_id: str, account_id: str, force_refresh: bool = False
    ) -> list[dict]:
        """Retourne la liste des dossiers depuis le cache mémoire (TTL 300s) ou rafraîchit."""
        now = datetime.now(timezone.utc).timestamp()
        cached = self._FOLDERS_CACHE.get(user_id)
        if not force_refresh and cached and (now - cached.get("timestamp", 0) < 300):
            return cached.get("folders", [])

        try:
            folders = await self._fetch_all_mail_folders(user_id, account_id)
            if folders:
                self._FOLDERS_CACHE[user_id] = {"timestamp": now, "folders": folders}
            return folders
        except Exception as e:
            logger.warning("Échec de récupération des dossiers M365: %s", e)
            if cached:
                return cached.get("folders", [])
            return []

    async def _resolve_folder_id(
        self, user_id: str, account_id: str, folder_name: str
    ) -> Optional[str]:
        """
        Résout un libellé ou chemin de dossier en Graph ID immuable.
        Laisse passer tels quels les alias réservés (inbox, etc.) et les FolderId déjà formatés.
        """
        if not folder_name:
            return None
        cleaned = folder_name.strip()
        if cleaned.casefold() in self.WELL_KNOWN_PATHS:
            return self.WELL_KNOWN_PATHS[cleaned.casefold()]

        # Détection d'un ID Graph immuable (Base64-like préfixé ou long)
        if cleaned.startswith(("AQMk", "AAMk")) or (len(cleaned) >= 50 and "=" in cleaned):
            return folder_name

        folders = await self._get_cached_folders(user_id, account_id)
        target = cleaned.lower()

        # 1. Correspondance exacte sur displayName ou chemin complet
        for f in folders:
            d_name = (f.get("displayName") or "").lower()
            f_path = (f.get("path") or "").lower()
            if target in (d_name, f_path):
                return f.get("id")

        # 2. Correspondance sur le segment final (ex: "Entretiens" pour ".../Entretiens")
        for f in folders:
            f_path = (f.get("path") or "").lower()
            if f_path.endswith(f"/{target}"):
                return f.get("id")

        # 3. Forcer un rafraîchissement au cas où le dossier vient d'être créé
        refreshed = await self._get_cached_folders(
            user_id, account_id, force_refresh=True
        )
        for f in refreshed:
            d_name = (f.get("displayName") or "").lower()
            f_path = (f.get("path") or "").lower()
            if target in (d_name, f_path) or f_path.endswith(f"/{target}"):
                return f.get("id")

        return None

    async def after_list_tools(
        self,
        user_id: str,
        result: dict,
    ) -> dict:
        """Retire `flow_cache` du schéma exposé au LLM pour `complete_authentication`."""
        tools = result.get("tools")
        if not isinstance(tools, list):
            return result

        result = dict(result)
        patched_tools = []
        has_list_mail_folders = False
        has_trash_email = False
        has_batch_trash = False

        for tool in tools:
            if not isinstance(tool, dict):
                patched_tools.append(tool)
                continue

            t_name = tool.get("name")
            if t_name == "complete_authentication":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Complete the authentication process after the user has entered the device code. "
                    "No arguments required (the Device Flow state is persisted automatically by the MCP Broker)."
                )
                t_copy["inputSchema"] = {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                }
                patched_tools.append(t_copy)
            elif t_name == "list_emails":
                t_copy = dict(tool)
                props = dict(t_copy.get("inputSchema", {}).get("properties", {}))
                props["skip"] = {
                    "type": "integer",
                    "description": "Number of emails to skip for pagination (default: 0).",
                    "default": 0,
                }
                props["order_by"] = {
                    "type": "string",
                    "description": (
                        "Sort order: 'receivedDateTime desc' (newest first) or "
                        "'receivedDateTime asc' (oldest first)."
                    ),
                    "default": "receivedDateTime desc",
                }
                t_copy["inputSchema"] = dict(t_copy.get("inputSchema", {}))
                t_copy["inputSchema"]["properties"] = props
                patched_tools.append(t_copy)
            elif t_name == "delete_email":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "PERMANENTLY PURGE an email (Hard Delete). CAUTION: This deletes the message permanently "
                    "without moving it to the trash folder. For safe reversible deletion, use 'trash_email' instead."
                )
                patched_tools.append(t_copy)
            elif t_name == "list_mail_folders":
                has_list_mail_folders = True
                patched_tools.append(tool)
            elif t_name == "trash_email":
                has_trash_email = True
                patched_tools.append(tool)
            elif t_name == "batch_trash_emails":
                has_batch_trash = True
                patched_tools.append(tool)
            else:
                patched_tools.append(tool)

        if not has_list_mail_folders:
            patched_tools.append(
                {
                    "name": "list_mail_folders",
                    "description": (
                        "List all mail folders and subfolders in the user's Microsoft 365 mailbox with their "
                        "display names, full hierarchical paths, Graph IDs, and unread/total message counts."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            }
                        },
                        "required": [],
                    },
                }
            )

        if not has_trash_email:
            patched_tools.append(
                {
                    "name": "trash_email",
                    "description": (
                        "Safely move an email to Deleted Items (Trash / Corbeille). Reversible soft delete."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_id": {
                                "type": "string",
                                "description": "The ID of the email to move to trash.",
                            },
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            },
                        },
                        "required": ["email_id"],
                    },
                }
            )

        if not has_batch_trash:
            patched_tools.append(
                {
                    "name": "batch_trash_emails",
                    "description": (
                        "Move multiple emails to Trash in batch using Graph $batch API (up to 100 emails at a time). "
                        "Highly recommended for bulk cleanup operations."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of email IDs to move to trash.",
                            },
                            "account_id": {
                                "type": "string",
                                "description": "Optional account ID or email address. Auto-resolved if omitted.",
                            },
                        },
                        "required": ["email_ids"],
                    },
                }
            )

        result["tools"] = patched_tools
        return result

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
        by_username = {
            a["username"].lower(): a["account_id"]
            for a in accounts
            if a.get("username")
        }

        resolved_account = None
        if provided_id in by_id:
            resolved_account = provided_id
        elif provided_id and provided_id.lower() in by_username:
            resolved_account = by_username[provided_id.lower()]
            logger.info("MS365: account_id '%s' résolu vers '%s'", provided_id, resolved_account)
            args["account_id"] = resolved_account
        elif len(accounts) == 1:
            resolved_account = accounts[0]["account_id"]
            logger.info(
                "MS365: account_id '%s' auto-résolu vers le compte unique '%s'",
                provided_id,
                resolved_account,
            )
            args["account_id"] = resolved_account
        else:
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

        # Cas 4 : Outil virtuel list_mail_folders -> court-circuit direct
        if tool_name == "list_mail_folders":
            folders = await self._get_cached_folders(user_id, resolved_account)
            formatted = [
                {
                    "displayName": f.get("displayName"),
                    "path": f.get("path"),
                    "id": f.get("id"),
                    "parentFolderId": f.get("parentFolderId"),
                    "unreadItemCount": f.get("unreadItemCount", 0),
                    "totalItemCount": f.get("totalItemCount", 0),
                    "childFolderCount": f.get("childFolderCount", 0),
                }
                for f in folders
            ]
            return args, self._make_tool_response(formatted)

        # Cas 5 : Outil virtuel trash_email -> mise à la corbeille réversible (Soft Delete)
        if tool_name == "trash_email":
            email_id = args.get("email_id")
            if not email_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id obligatoire."}, is_error=True
                )
            try:
                token = self._get_access_token(user_id, resolved_account)
                async with httpx.AsyncClient(timeout=15.0) as client:
                    res = await client.post(
                        f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/move",
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Content-Type": "application/json",
                        },
                        json={"destinationId": "deleteditems"},
                    )
                    if res.status_code in (200, 201):
                        return args, self._make_tool_response(
                            {
                                "status": "trashed",
                                "message_id": email_id,
                                "folder": "deleteditems",
                            }
                        )
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec de mise à la corbeille Graph: {res.text}",
                        },
                        is_error=True,
                    )
            except Exception as e:
                return args, self._make_tool_response(
                    {"status": "error", "message": f"Exception lors de trash_email: {e}"},
                    is_error=True,
                )

        # Cas 6 : Outil virtuel batch_trash_emails -> suppression par lot via Graph $batch
        if tool_name == "batch_trash_emails":
            email_ids = args.get("email_ids", [])
            if not email_ids or not isinstance(email_ids, list):
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": "email_ids DOIT être une liste non vide d'identifiants.",
                    },
                    is_error=True,
                )
            try:
                token = self._get_access_token(user_id, resolved_account)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                trashed_count = 0
                failed_ids = []

                chunks = [
                    email_ids[i:i + 20] for i in range(0, len(email_ids), 20)
                ]
                async with httpx.AsyncClient(timeout=30.0) as client:
                    for chunk in chunks:
                        sub_requests = [
                            {
                                "id": str(idx),
                                "method": "POST",
                                "url": f"/me/messages/{eid}/move",
                                "headers": {"Content-Type": "application/json"},
                                "body": {"destinationId": "deleteditems"},
                            }
                            for idx, eid in enumerate(chunk)
                        ]
                        batch_res = await client.post(
                            "https://graph.microsoft.com/v1.0/$batch",
                            headers=headers,
                            json={"requests": sub_requests},
                        )
                        if batch_res.status_code == 200:
                            b_data = batch_res.json()
                            for r in b_data.get("responses", []):
                                idx_req = int(r.get("id", -1))
                                if r.get("status") in (200, 201):
                                    trashed_count += 1
                                else:
                                    if 0 <= idx_req < len(chunk):
                                        failed_ids.append(chunk[idx_req])
                        else:
                            failed_ids.extend(chunk)

                return args, self._make_tool_response(
                    {
                        "status": "success" if not failed_ids else "partial_success",
                        "total_requested": len(email_ids),
                        "trashed_count": trashed_count,
                        "failed_count": len(failed_ids),
                        "failed_ids": failed_ids,
                    }
                )
            except Exception as e:
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Exception lors de batch_trash_emails: {e}",
                    },
                    is_error=True,
                )

        # Cas 7 : list_emails -> exécution Graph directe (supporte skip, order_by, auto-résolution)
        if tool_name == "list_emails":
            folder_arg = args.get("folder", "inbox")
            limit = min(int(args.get("limit", 10) or 10), 100)
            skip = max(int(args.get("skip", 0) or 0), 0)
            order_by = str(args.get("order_by") or "receivedDateTime desc").strip()
            include_body = bool(args.get("include_body", True))

            resolved_fid = await self._resolve_folder_id(
                user_id, resolved_account, folder_arg
            )
            if not resolved_fid:
                folders = await self._get_cached_folders(user_id, resolved_account)
                available = [
                    f.get("path") or f.get("displayName") for f in folders
                ]
                return args, self._make_tool_response(
                    {
                        "status": "folder_not_found",
                        "message": (
                            f"Le dossier '{folder_arg}' est introuvable dans la messagerie Microsoft 365. "
                            f"Dossiers disponibles : {', '.join(str(x) for x in available[:30])}. "
                            "Appelez `list_mail_folders` pour examiner l'arborescence complète."
                        ),
                    },
                    is_error=True,
                )

            folder_endpoint_id = self.WELL_KNOWN_PATHS.get(
                resolved_fid.casefold(), resolved_fid
            )
            if include_body:
                select_fields = (
                    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                    "hasAttachments,body,conversationId,isRead"
                )
            else:
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "conversationId,isRead"
                )

            try:
                token = self._get_access_token(user_id, resolved_account)
                url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_endpoint_id}/messages"
                params = {
                    "$top": str(limit),
                    "$skip": str(skip),
                    "$orderby": order_by,
                    "$select": select_fields,
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.get(
                        url,
                        headers={
                            "Authorization": f"Bearer {token}",
                            "Accept": "application/json",
                        },
                        params=params,
                    )
                    if res.status_code == 200:
                        messages = res.json().get("value", [])
                        return args, self._make_tool_response(messages)
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph list_emails ({res.status_code}): {res.text}",
                        },
                        is_error=True,
                    )
            except Exception as e:
                logger.warning("Échec Graph list_emails direct: %s", e)
                args["folder"] = folder_endpoint_id

        # Cas 8 : search_emails -> fiabilisation comptes personnels + résolution dossier
        if tool_name == "search_emails":
            folder_arg = args.get("folder")
            if folder_arg:
                resolved_fid = await self._resolve_folder_id(
                    user_id, resolved_account, folder_arg
                )
                if resolved_fid:
                    args["folder"] = resolved_fid
                else:
                    folders = await self._get_cached_folders(user_id, resolved_account)
                    available = [
                        f.get("path") or f.get("displayName") for f in folders
                    ]
                    return args, self._make_tool_response(
                        {
                            "status": "folder_not_found",
                            "message": (
                                f"Le dossier '{folder_arg}' est introuvable dans Microsoft 365. "
                                f"Dossiers disponibles : {', '.join(str(x) for x in available[:30])}."
                            ),
                        },
                        is_error=True,
                    )
            else:
                # Si folder est omis, requêter directement /me/messages?$search pour éviter l'erreur 400
                query = args.get("query", "")
                limit = min(int(args.get("limit", 50) or 50), 100)
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "body,conversationId,isRead"
                )
                try:
                    token = self._get_access_token(user_id, resolved_account)
                    url = "https://graph.microsoft.com/v1.0/me/messages"
                    params = {
                        "$search": f'"{query}"',
                        "$top": str(limit),
                        "$select": select_fields,
                    }
                    async with httpx.AsyncClient(timeout=20.0) as client:
                        res = await client.get(
                            url,
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Accept": "application/json",
                            },
                            params=params,
                        )
                        if res.status_code == 200:
                            messages = res.json().get("value", [])
                            return args, self._make_tool_response(messages)
                except Exception as e:
                    logger.warning("Échec search_emails direct Graph: %s", e)

        # Cas 9 : move_email -> résolution et exécution directe Graph (support de tous sous-dossiers)
        if tool_name == "move_email":
            dest_arg = args.get("destination_folder")
            email_id = args.get("email_id")
            if dest_arg and email_id:
                resolved_fid = await self._resolve_folder_id(
                    user_id, resolved_account, dest_arg
                )
                if not resolved_fid:
                    folders = await self._get_cached_folders(user_id, resolved_account)
                    available = [
                        f.get("path") or f.get("displayName") for f in folders
                    ]
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": (
                                f"Dossier de destination '{dest_arg}' introuvable. "
                                f"Dossiers disponibles : {', '.join(str(x) for x in available[:25])}."
                            ),
                        },
                        is_error=True,
                    )
                try:
                    token = self._get_access_token(user_id, resolved_account)
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        move_res = await client.post(
                            f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/move",
                            headers={
                                "Authorization": f"Bearer {token}",
                                "Content-Type": "application/json",
                            },
                            json={"destinationId": resolved_fid},
                        )
                        if move_res.status_code in (200, 201):
                            res_json = move_res.json()
                            return args, self._make_tool_response(
                                {"status": "moved", "new_id": res_json.get("id")}
                            )
                        return args, self._make_tool_response(
                            {
                                "status": "error",
                                "message": f"Échec Graph move_email: {move_res.text}",
                            },
                            is_error=True,
                        )
                except Exception as e:
                    logger.warning("Échec de move_email direct Graph: %s", e)
                    args["destination_folder"] = resolved_fid

        return args, None

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

                # Suppression complète de _flow_cache de la réponse exposée au LLM
                sanitized = dict(payload)
                sanitized.pop("_flow_cache", None)
                sanitized["step4"] = (
                    "After the user confirms authentication, call 'complete_authentication' "
                    "with empty arguments {}."
                )
                result = dict(result)
                if isinstance(result.get("structuredContent"), dict):
                    result["structuredContent"] = sanitized
                if isinstance(result.get("content"), list):
                    new_content = []
                    for item in result["content"]:
                        if isinstance(item, dict) and item.get("type") == "text":
                            new_item = dict(item)
                            new_item["text"] = json.dumps(sanitized, ensure_ascii=False)
                            new_content.append(new_item)
                        else:
                            new_content.append(item)
                    result["content"] = new_content
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
