# VERSION : 5.231.37.1
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.37.1: Routage atomique des téléchargements M365 et Google Workspace vers le Download Broker (/mcp/).
# 5.229.1.3: En-tête Prefer text, assainisseur anti-HTML, bodyPreview et traçabilité new_id post-déplacement.
# 5.229.1.2: Lecteur batch (batch_get_emails) et recherche multi-axes (search_emails KQL/OData/dossiers).
# 5.229.1.1: Auto-résolution dossiers M365, pagination/tri list_emails, trash_email et batch_trash_emails ($batch).
# 5.228.0.1: Fail-Closed OAuth2 GoogleWorkspaceProvider (prepare_env + bouclier anti-localhost:8001).
"""
ECHO MCP Broker - Adaptateurs de cycle de vie spécifiques aux fournisseurs MCP résidents.
Isole la logique d'authentification et de préparation d'environnement (Microsoft 365 MSAL,
Google Workspace OAuth2) hors de l'orchestrateur de transport générique m5_proxy_mcp.py.
"""
import ast
import base64
import hashlib
import html
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.parse
import uuid
from contextlib import AsyncExitStack
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple, Union

import httpx
from core.security import chat_id_var

logger = logging.getLogger(__name__)

MCP_CACHE_ROOT = os.environ.get("ECHO_MCP_CACHE_DIR", "/app/mcp_cache")
MCP_DOWNLOADS_ROOT = os.environ.get("ECHO_DOWNLOADS_DIR", "/app/downloads")

_WORKSPACE_UUID_SUFFIX_RE = re.compile(
    r"_[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=\.[^.]+$|$)"
)
_SAVED_TO_LINE_RE = re.compile(r"(Saved to:\s*)([^\r\n]+)")


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

    @staticmethod
    def _sanitize_segment(val: Optional[str], fallback: str = "anonymous") -> str:
        cleaned = "".join(
            c for c in str(val or "") if c.isalnum() or c in ("-", "_", ".")
        ).strip(".")
        return cleaned or fallback

    @staticmethod
    def _sanitize_filename(filename: Optional[str], fallback: str = "downloaded_file") -> str:
        if not filename:
            return fallback
        base = os.path.basename(str(filename).replace("\\", "/")).strip()
        sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base).rstrip(" .")
        if not sanitized or sanitized in (".", ".."):
            return fallback
        return sanitized

    @staticmethod
    def _extract_hex_fid(filename: str) -> Optional[str]:
        if "_" not in filename:
            return None
        prefix = filename.split("_", 1)[0].lower()
        if len(prefix) == 8 and all(c in "0123456789abcdef" for c in prefix):
            return prefix
        return None

    @classmethod
    def _get_mcp_drop_dir(cls, user_id: Optional[str], chat_id: Optional[str] = None) -> str:
        resolved_cid = chat_id or chat_id_var.get() or "default_chat"
        safe_uid = cls._sanitize_segment(user_id, "anonymous")
        safe_cid = cls._sanitize_segment(resolved_cid, "default_chat")
        drop_dir = os.path.join(MCP_DOWNLOADS_ROOT, safe_uid, safe_cid, "mcp")
        os.makedirs(drop_dir, exist_ok=True)
        for path_to_chmod in (
            MCP_DOWNLOADS_ROOT,
            os.path.join(MCP_DOWNLOADS_ROOT, safe_uid),
            os.path.join(MCP_DOWNLOADS_ROOT, safe_uid, safe_cid),
            drop_dir,
        ):
            try:
                os.chmod(path_to_chmod, 0o777)
            except Exception:
                pass
        return drop_dir

    @classmethod
    def _publish_file_to_broker(
        cls,
        src_or_bytes: Union[bytes, bytearray, str],
        user_id: Optional[str],
        chat_id: Optional[str],
        filename: Optional[str],
        fid: Optional[str] = None,
    ) -> Tuple[str, str, str]:
        """
        Écrit ou déplace atomiquement un fichier vers la Drop-Zone MCP du Download Broker
        via un fichier temporaire `.part`, ajuste les permissions (0o666) et antidate `st_mtime`
        de 5s pour permettre une collecte sans latence par `download_broker.py`.
        Retourne `(fid, safe_name, final_path)`.
        """
        fid = (fid or uuid.uuid4().hex[:8]).lower()
        clean_name = cls._sanitize_filename(filename)
        if clean_name.lower().startswith(f"{fid}_"):
            safe_name = f"{fid}_{clean_name.split('_', 1)[1]}"
        else:
            safe_name = f"{fid}_{clean_name}"

        drop_dir = cls._get_mcp_drop_dir(user_id, chat_id)
        final_path = os.path.join(drop_dir, safe_name)
        part_path = f"{final_path}.part"

        if isinstance(src_or_bytes, (bytes, bytearray)):
            with open(part_path, "wb") as f_out:
                f_out.write(src_or_bytes)
        elif isinstance(src_or_bytes, str):
            if os.path.abspath(src_or_bytes) != os.path.abspath(part_path):
                shutil.move(src_or_bytes, part_path)
        else:
            raise TypeError("src_or_bytes doit être bytes ou un chemin de fichier (str).")

        try:
            os.chmod(part_path, 0o666)
        except Exception:
            pass
        try:
            ready_mtime = time.time() - 5.0
            os.utime(part_path, (ready_mtime, ready_mtime))
        except Exception:
            pass

        os.replace(part_path, final_path)
        return fid, safe_name, final_path

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
        chat_id: Optional[str] = None,
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
        chat_id: Optional[str] = None,
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
    _USER_STAGING_DIRS: dict[str, set[str]] = {}

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
        attachments_staging = os.path.join(temp_dir_path, "attachments")
        os.makedirs(attachments_staging, exist_ok=True)
        env["WORKSPACE_ATTACHMENT_DIR"] = attachments_staging
        safe_uid = self._sanitize_segment(user_id)
        self._USER_STAGING_DIRS.setdefault(safe_uid, set()).add(attachments_staging)

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
        chat_id: Optional[str] = None,
    ) -> dict:
        """
        Bouclier anti-pollution : intercepte toute tentative de `workspace-mcp` d'exposer
        une URL locale (localhost:8001/oauth2callback) ou une erreur d'expiration OAuth2
        en cours de session, et lève `AuthRequiredError` pour évincer la session corrompue.
        Redirige également vers le Download Broker tout fichier écrit par `workspace-mcp`.
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

        moved_map: dict[str, Tuple[str, str, str]] = {}
        content_list = result.get("content")
        if isinstance(content_list, list):
            for item in content_list:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                text_val = item.get("text", "")
                for match in _SAVED_TO_LINE_RE.finditer(text_val):
                    raw_path = match.group(2).strip()
                    if raw_path and raw_path not in moved_map and os.path.isfile(raw_path):
                        base_name = os.path.basename(raw_path)
                        clean_orig = _WORKSPACE_UUID_SUFFIX_RE.sub("", base_name)
                        try:
                            moved_map[raw_path] = self._publish_file_to_broker(
                                raw_path, user_id, chat_id, clean_orig
                            )
                        except Exception as e:
                            logger.warning(
                                "Échec transfert fichier Google Workspace vers Download Broker: %s", e
                            )

        safe_uid = self._sanitize_segment(user_id)
        staging_dirs = self._USER_STAGING_DIRS.get(safe_uid, set())
        for staging_dir in list(staging_dirs):
            if not os.path.isdir(staging_dir):
                staging_dirs.discard(staging_dir)
                continue
            for fname in os.listdir(staging_dir):
                fpath = os.path.join(staging_dir, fname)
                if os.path.isfile(fpath) and fpath not in moved_map:
                    clean_orig = _WORKSPACE_UUID_SUFFIX_RE.sub("", fname)
                    try:
                        moved_map[fpath] = self._publish_file_to_broker(
                            fpath, user_id, chat_id, clean_orig
                        )
                    except Exception as e:
                        logger.warning(
                            "Échec transfert reliquat staging Google Workspace: %s", e
                        )

        if moved_map and isinstance(content_list, list):
            result = dict(result)
            new_content = []
            for item in content_list:
                if isinstance(item, dict) and item.get("type") == "text":
                    new_text = item.get("text", "")
                    for old_path, (_fid, safe_name, new_path) in moved_map.items():
                        new_text = new_text.replace(old_path, new_path)
                        old_fname = os.path.basename(old_path)
                        if old_fname != safe_name:
                            new_text = new_text.replace(old_fname, safe_name)
                    summary_lines = [
                        f"\n📦 ECHO Download Broker: file_id='{fid}', name='{safe_name}', path='{new_path}'"
                        for fid, safe_name, new_path in moved_map.values()
                    ]
                    new_item = dict(item)
                    new_item["text"] = new_text + "".join(summary_lines)
                    new_content.append(new_item)
                else:
                    new_content.append(item)
            result["content"] = new_content

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
    def _normalize_iso_date(date_str: str, is_end_of_day: bool = False) -> Tuple[str, str]:
        """
        Normalise une date (YYYY-MM-DD ou ISO 8601) vers :
        - une chaîne ISO 8601 UTC ('YYYY-MM-DDTHH:MM:SSZ') pour OData $filter
        - une chaîne courte ('YYYY-MM-DD') pour Graph KQL $search
        """
        raw = str(date_str or "").strip()
        if not raw:
            return "", ""
        try:
            if len(raw) == 10 and raw.count("-") == 2:
                short_date = raw
                iso_time = "23:59:59Z" if is_end_of_day else "00:00:00Z"
                return f"{raw}T{iso_time}", short_date
            cleaned = raw.replace("Z", "+00:00")
            dt_obj = datetime.fromisoformat(cleaned)
            if dt_obj.tzinfo is None:
                dt_obj = dt_obj.replace(tzinfo=timezone.utc)
            else:
                dt_obj = dt_obj.astimezone(timezone.utc)
            short_date = dt_obj.strftime("%Y-%m-%d")
            iso_str = dt_obj.strftime("%Y-%m-%dT%H:%M:%SZ")
            return iso_str, short_date
        except Exception:
            return raw, raw

    @staticmethod
    def _clean_html_to_text(html_text: str) -> str:
        """
        Nettoie et convertit un contenu HTML en texte brut dense à haut SNR :
        1. Supprime les balises <style> et <script> et leur contenu.
        2. Remplace les sauts de ligne HTML (<br>, </p>, </div>, </tr>, </li>) par des retours chariot.
        3. Supprime toutes les autres balises HTML.
        4. Décode les entités HTML standard (&nbsp;, &amp;, &lt;, &gt;, &quot;, &#39;).
        5. Normalise les espaces blancs consécutifs.
        """
        if not html_text or not isinstance(html_text, str):
            return ""
        if "<" not in html_text or ">" not in html_text:
            return html_text.strip()

        # Supprimer styles et scripts
        text = re.sub(r"(?is)<style.*?>.*?</style>", "", html_text)
        text = re.sub(r"(?is)<script.*?>.*?</script>", "", text)

        # Remplacer les sauts de lignes structurels
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(?:p|div|tr|li|h[1-6])>", "\n", text)

        # Supprimer les balises HTML restantes
        text = re.sub(r"<[^>]+>", "", text)

        # Décoder les entités HTML
        text = html.unescape(text)

        # Normaliser les espaces et sauts de ligne
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        return text

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
        has_batch_get = False

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
            elif t_name == "search_emails":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Search emails across the mailbox or in a specific folder by keywords, date range, "
                    "sender, and status. Supports KQL full-text search or pure OData date/status filtering."
                )
                props = {
                    "query": {
                        "type": "string",
                        "description": (
                            "Optional text keywords to search for. Can be omitted when filtering by dates, "
                            "sender, or folder alone."
                        ),
                    },
                    "folder": {
                        "type": "string",
                        "description": (
                            "Optional folder name, path (e.g. 'Boîte de réception/LinkedIn', 'inbox'), "
                            "or Graph ID. If omitted, searches across the entire mailbox."
                        ),
                    },
                    "received_after": {
                        "type": "string",
                        "description": (
                            "Optional start date/time (format 'YYYY-MM-DD' or ISO 'YYYY-MM-DDTHH:MM:SSZ'). "
                            "Matches emails received on or after this timestamp."
                        ),
                    },
                    "received_before": {
                        "type": "string",
                        "description": (
                            "Optional end date/time (format 'YYYY-MM-DD' or ISO 'YYYY-MM-DDTHH:MM:SSZ'). "
                            "Matches emails received on or before this timestamp."
                        ),
                    },
                    "sender": {
                        "type": "string",
                        "description": "Optional sender name or email address filter (e.g. 'linkedin').",
                    },
                    "unread_only": {
                        "type": "boolean",
                        "description": "Optional boolean: if true, returns only unread messages.",
                    },
                    "has_attachments": {
                        "type": "boolean",
                        "description": "Optional boolean: filter by presence of attachments.",
                    },
                    "order_by": {
                        "type": "string",
                        "enum": ["receivedDateTime desc", "receivedDateTime asc"],
                        "default": "receivedDateTime desc",
                        "description": (
                            "Sort order: 'receivedDateTime desc' (newest first) or "
                            "'receivedDateTime asc' (oldest first). "
                            "Note: applies when query keywords are omitted (OData mode)."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "default": 50,
                        "description": "Maximum number of messages to return (default 50, max 100).",
                    },
                    "include_body": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Whether to include email body in search results (default false for fast scanning; "
                            "use batch_get_emails for deep reading)."
                        ),
                    },
                    "account_id": {
                        "type": "string",
                        "description": "Optional account ID or email address. Auto-resolved if omitted.",
                    },
                }
                t_copy["inputSchema"] = {
                    "type": "object",
                    "properties": props,
                    "required": [],
                }
                patched_tools.append(t_copy)
            elif t_name == "delete_email":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "PERMANENTLY PURGE an email (Hard Delete). CAUTION: This deletes the message permanently "
                    "without moving it to the trash folder. For safe reversible deletion, use 'trash_email' instead."
                )
                patched_tools.append(t_copy)
            elif t_name == "get_file":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Download a file from OneDrive directly into the ECHO Download Broker for automatic "
                    "ingestion into the Unified Registry. 'download_path' is optional (only a custom "
                    "filename may be suggested; directory routing to the Download Broker is enforced)."
                )
                schema = dict(t_copy.get("inputSchema", {}))
                props = dict(schema.get("properties", {}))
                if "download_path" in props:
                    dp_prop = dict(props["download_path"])
                    dp_prop["description"] = (
                        "Optional desired filename (e.g. 'report.pdf'). Directory path is automatically "
                        "overridden to the ECHO Download Broker drop-zone."
                    )
                    props["download_path"] = dp_prop
                schema["properties"] = props
                schema["required"] = ["file_id"]
                t_copy["inputSchema"] = schema
                patched_tools.append(t_copy)
            elif t_name == "get_attachment":
                t_copy = dict(tool)
                t_copy["description"] = (
                    "Download an email attachment directly into the ECHO Download Broker for automatic "
                    "ingestion into the Unified Registry. 'save_path' is optional (only a custom "
                    "filename may be suggested; directory routing to the Download Broker is enforced)."
                )
                schema = dict(t_copy.get("inputSchema", {}))
                props = dict(schema.get("properties", {}))
                if "save_path" in props:
                    sp_prop = dict(props["save_path"])
                    sp_prop["description"] = (
                        "Optional desired filename (e.g. 'invoice.pdf'). Directory path is automatically "
                        "overridden to the ECHO Download Broker drop-zone."
                    )
                    props["save_path"] = sp_prop
                schema["properties"] = props
                schema["required"] = ["email_id", "attachment_id"]
                t_copy["inputSchema"] = schema
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
            elif t_name == "batch_get_emails":
                has_batch_get = True
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

        if not has_batch_get:
            patched_tools.append(
                {
                    "name": "batch_get_emails",
                    "description": (
                        "Retrieve full details and content for multiple emails in batch using Graph $batch API "
                        "(up to 100 emails at a time). Significantly faster and more token-efficient than calling "
                        "get_email repeatedly."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "email_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "List of email IDs to retrieve.",
                            },
                            "include_body": {
                                "type": "boolean",
                                "default": True,
                                "description": "Whether to include body content in the response.",
                            },
                            "body_max_length": {
                                "type": "integer",
                                "default": 5000,
                                "description": (
                                    "Maximum characters for body content per email to preserve LLM context."
                                ),
                            },
                            "include_attachments": {
                                "type": "boolean",
                                "default": False,
                                "description": "Whether to include attachment metadata (name, size, type).",
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
        chat_id: Optional[str] = None,
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
                        res_json = res.json() if res.content else {}
                        new_id = res_json.get("id", email_id)
                        return args, self._make_tool_response(
                            {
                                "status": "trashed",
                                "message_id": email_id,
                                "old_id": email_id,
                                "new_id": new_id,
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
                id_mapping = {}

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
                                    if 0 <= idx_req < len(chunk):
                                        orig_id = chunk[idx_req]
                                        res_b = r.get("body", {})
                                        new_id = res_b.get("id", orig_id) if isinstance(res_b, dict) else orig_id
                                        id_mapping[orig_id] = new_id
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
                        "id_mapping": id_mapping,
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

        # Cas 7 : get_email -> interception Graph directe avec Prefer text et nettoyage HTML
        if tool_name == "get_email":
            email_id = args.get("email_id")
            if not email_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id obligatoire."}, is_error=True
                )
            include_body = bool(args.get("include_body", True))
            body_max_length = max(int(args.get("body_max_length", 50000) or 50000), 100)
            include_attachments = bool(args.get("include_attachments", True))

            if include_body:
                select_fields = (
                    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                    "hasAttachments,body,bodyPreview,conversationId,isRead"
                )
            else:
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "bodyPreview,conversationId,isRead"
                )

            params = {"$select": select_fields}
            if include_attachments:
                params["$expand"] = "attachments($select=id,name,size,contentType)"

            try:
                token = self._get_access_token(user_id, resolved_account)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Prefer": 'outlook.body-content-type="text"',
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.get(
                        f"https://graph.microsoft.com/v1.0/me/messages/{email_id}",
                        headers=headers,
                        params=params,
                    )
                    if res.status_code == 200:
                        msg = res.json()
                        if include_body and "body" in msg and isinstance(msg["body"], dict):
                            raw_content = msg["body"].get("content", "")
                            clean_content = self._clean_html_to_text(raw_content)
                            if len(clean_content) > body_max_length:
                                trunc_msg = f"\n\n[Content truncated - {len(clean_content)} total characters]"
                                msg["body"]["content"] = clean_content[:body_max_length] + trunc_msg
                                msg["body"]["truncated"] = True
                                msg["body"]["total_length"] = len(clean_content)
                            else:
                                msg["body"]["content"] = clean_content
                        elif not include_body and "body" in msg:
                            del msg["body"]

                        if "attachments" in msg and isinstance(msg["attachments"], list):
                            for att in msg["attachments"]:
                                if isinstance(att, dict) and "contentBytes" in att:
                                    del att["contentBytes"]

                        return args, self._make_tool_response(msg)
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph get_email ({res.status_code}): {res.text}",
                        },
                        is_error=True,
                    )
            except Exception as e:
                return args, self._make_tool_response(
                    {"status": "error", "message": f"Exception lors de get_email: {e}"},
                    is_error=True,
                )

        # Cas 8 : Outil virtuel batch_get_emails -> lecture par lot via Graph $batch
        if tool_name == "batch_get_emails":
            email_ids = args.get("email_ids", [])
            if not email_ids or not isinstance(email_ids, list):
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": "email_ids DOIT être une liste non vide d'identifiants.",
                    },
                    is_error=True,
                )

            email_ids = [str(e).strip() for e in email_ids if str(e).strip()][:100]
            include_body = bool(args.get("include_body", True))
            body_max_length = max(int(args.get("body_max_length", 5000) or 5000), 100)
            include_attachments = bool(args.get("include_attachments", False))

            if include_body:
                select_fields = (
                    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                    "hasAttachments,body,bodyPreview,conversationId,isRead"
                )
            else:
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "bodyPreview,conversationId,isRead"
                )

            expand_clause = (
                "&$expand=attachments($select=id,name,size,contentType)"
                if include_attachments
                else ""
            )

            try:
                token = self._get_access_token(user_id, resolved_account)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                }
                retrieved_emails = []
                failed_ids = []

                chunks = [
                    email_ids[i:i + 20] for i in range(0, len(email_ids), 20)
                ]
                async with httpx.AsyncClient(timeout=30.0) as client:
                    for chunk in chunks:
                        sub_requests = [
                            {
                                "id": str(idx),
                                "method": "GET",
                                "url": f"/me/messages/{eid}?$select={select_fields}{expand_clause}",
                                "headers": {"Prefer": 'outlook.body-content-type="text"'},
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
                                if r.get("status") == 200:
                                    msg = r.get("body", {})
                                    if include_body and "body" in msg and isinstance(msg["body"], dict):
                                        raw_content = msg["body"].get("content", "")
                                        clean_content = self._clean_html_to_text(raw_content)
                                        if len(clean_content) > body_max_length:
                                            trunc_msg = (
                                                f"\n\n[Content truncated - {len(clean_content)} total characters]"
                                            )
                                            msg["body"]["content"] = clean_content[:body_max_length] + trunc_msg
                                            msg["body"]["truncated"] = True
                                            msg["body"]["total_length"] = len(clean_content)
                                        else:
                                            msg["body"]["content"] = clean_content
                                    elif not include_body and "body" in msg:
                                        del msg["body"]

                                    if "attachments" in msg and isinstance(msg["attachments"], list):
                                        for att in msg["attachments"]:
                                            if isinstance(att, dict) and "contentBytes" in att:
                                                del att["contentBytes"]

                                    retrieved_emails.append(msg)
                                else:
                                    if 0 <= idx_req < len(chunk):
                                        failed_ids.append(chunk[idx_req])
                        else:
                            failed_ids.extend(chunk)

                return args, self._make_tool_response(
                    {
                        "status": "success" if not failed_ids else "partial_success",
                        "total_requested": len(email_ids),
                        "retrieved_count": len(retrieved_emails),
                        "failed_count": len(failed_ids),
                        "emails": retrieved_emails,
                        "failed_ids": failed_ids,
                    }
                )
            except Exception as e:
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Exception lors de batch_get_emails: {e}",
                    },
                    is_error=True,
                )

        # Cas 9 : list_emails -> exécution Graph directe (supporte skip, order_by, auto-résolution)
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
                    "hasAttachments,body,bodyPreview,conversationId,isRead"
                )
            else:
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "bodyPreview,conversationId,isRead"
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
                            "Prefer": 'outlook.body-content-type="text"',
                        },
                        params=params,
                    )
                    if res.status_code == 200:
                        messages = res.json().get("value", [])
                        if include_body:
                            for m in messages:
                                if "body" in m and isinstance(m["body"], dict):
                                    m["body"]["content"] = self._clean_html_to_text(
                                        m["body"].get("content", "")
                                    )
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

        # Cas 10 : search_emails -> recherche multi-axes (mots-clés, temporalité, expéditeur, dossier, statut)
        if tool_name == "search_emails":
            query = str(args.get("query") or "").strip()
            folder_arg = args.get("folder")
            received_after = str(args.get("received_after") or "").strip()
            received_before = str(args.get("received_before") or "").strip()
            sender = str(args.get("sender") or "").strip()
            unread_only = args.get("unread_only")
            has_attachments = args.get("has_attachments")
            order_by = str(args.get("order_by") or "receivedDateTime desc").strip()
            limit = min(max(int(args.get("limit", 50) or 50), 1), 100)
            include_body = bool(args.get("include_body", False))

            if folder_arg:
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
                                f"Le dossier '{folder_arg}' est introuvable dans Microsoft 365. "
                                f"Dossiers disponibles : {', '.join(str(x) for x in available[:30])}."
                            ),
                        },
                        is_error=True,
                    )
                folder_endpoint_id = self.WELL_KNOWN_PATHS.get(
                    resolved_fid.casefold(), resolved_fid
                )
                base_url = f"https://graph.microsoft.com/v1.0/me/mailFolders/{folder_endpoint_id}/messages"
            else:
                base_url = "https://graph.microsoft.com/v1.0/me/messages"

            if include_body:
                select_fields = (
                    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,"
                    "hasAttachments,body,bodyPreview,conversationId,isRead"
                )
            else:
                select_fields = (
                    "id,subject,from,toRecipients,receivedDateTime,hasAttachments,"
                    "bodyPreview,conversationId,isRead"
                )

            iso_after, short_after = self._normalize_iso_date(received_after, is_end_of_day=False)
            iso_before, short_before = self._normalize_iso_date(received_before, is_end_of_day=True)

            params = {
                "$top": str(limit),
                "$select": select_fields,
            }

            use_kql = bool(query) or (bool(sender) and "@" not in sender)
            if use_kql:
                kql_terms = []
                if query:
                    kql_terms.append(f'"{query}"')
                if sender:
                    kql_terms.append(f"from:{sender}")
                if short_after:
                    kql_terms.append(f"received>={short_after}")
                if short_before:
                    kql_terms.append(f"received<={short_before}")
                if has_attachments is True:
                    kql_terms.append("hasAttachments:true")
                if unread_only is True:
                    kql_terms.append("isRead:false")

                params["$search"] = " AND ".join(kql_terms)
            else:
                filters = []
                if iso_after:
                    filters.append(f"receivedDateTime ge {iso_after}")
                if iso_before:
                    filters.append(f"receivedDateTime le {iso_before}")
                if sender:
                    clean_sender = sender.replace("'", "''")
                    filters.append(f"from/emailAddress/address eq '{clean_sender}'")
                if unread_only is True:
                    filters.append("isRead eq false")
                if has_attachments is not None:
                    filters.append(f"hasAttachments eq {'true' if has_attachments else 'false'}")

                if filters:
                    params["$filter"] = " and ".join(filters)
                params["$orderby"] = order_by

            try:
                token = self._get_access_token(user_id, resolved_account)
                headers = {
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/json",
                    "Prefer": 'outlook.body-content-type="text"',
                    "ConsistencyLevel": "eventual",
                }
                async with httpx.AsyncClient(timeout=20.0) as client:
                    res = await client.get(
                        base_url,
                        headers=headers,
                        params=params,
                    )
                    if res.status_code == 200:
                        messages = res.json().get("value", [])
                        if include_body:
                            for m in messages:
                                if "body" in m and isinstance(m["body"], dict):
                                    m["body"]["content"] = self._clean_html_to_text(
                                        m["body"].get("content", "")
                                    )
                        return args, self._make_tool_response(messages)
                    return args, self._make_tool_response(
                        {
                            "status": "error",
                            "message": f"Échec Graph search_emails ({res.status_code}): {res.text}",
                        },
                        is_error=True,
                    )
            except Exception as e:
                return args, self._make_tool_response(
                    {
                        "status": "error",
                        "message": f"Exception lors de search_emails: {e}",
                    },
                    is_error=True,
                )

        # Cas 11 : move_email -> résolution et exécution directe Graph (support de tous sous-dossiers)
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
                            res_json = move_res.json() if move_res.content else {}
                            return args, self._make_tool_response(
                                {
                                    "status": "moved",
                                    "old_id": email_id,
                                    "new_id": res_json.get("id"),
                                    "destination_folder": dest_arg,
                                }
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

        # Cas 12 : get_file (OneDrive) -> téléchargement et routage forcé vers le Download Broker
        if tool_name == "get_file":
            file_id = str(args.get("file_id") or "").strip()
            if not file_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "file_id obligatoire."}, is_error=True
                )
            fid = uuid.uuid4().hex[:8]
            req_path = str(args.get("download_path") or "").strip()
            req_name = self._sanitize_filename(req_path, "") if req_path else ""
            safe_fid_seg = self._sanitize_segment(file_id, "file")

            try:
                token = self._get_access_token(user_id, resolved_account)
                async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                    meta_res = await client.get(
                        f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    )
                    if meta_res.status_code != 200:
                        return args, self._make_tool_response(
                            {
                                "status": "error",
                                "message": f"Échec Graph get_file metadata ({meta_res.status_code}): {meta_res.text}",
                            },
                            is_error=True,
                        )
                    metadata = meta_res.json()
                    download_url = metadata.get("@microsoft.graph.downloadUrl")
                    if not download_url:
                        return args, self._make_tool_response(
                            {"status": "error", "message": "Aucune downloadUrl disponible pour ce fichier OneDrive."},
                            is_error=True,
                        )
                    remote_name = self._sanitize_filename(metadata.get("name"), "")
                    chosen_name = remote_name or req_name or f"onedrive_{safe_fid_seg}"
                    drop_dir = self._get_mcp_drop_dir(user_id, chat_id)
                    safe_name = f"{fid}_{chosen_name}"
                    final_path = os.path.join(drop_dir, safe_name)
                    part_path = f"{final_path}.part"

                    async with client.stream("GET", download_url) as dl_stream:
                        dl_stream.raise_for_status()
                        with open(part_path, "wb") as f_out:
                            async for chunk in dl_stream.aiter_bytes(chunk_size=65536):
                                f_out.write(chunk)

                    _, safe_name, final_path = self._publish_file_to_broker(
                        part_path, user_id, chat_id, chosen_name, fid=fid
                    )
                    size_bytes = metadata.get("size") or os.path.getsize(final_path)
                    return args, self._make_tool_response(
                        {
                            "status": "downloaded_to_broker",
                            "file_id": fid,
                            "name": safe_name,
                            "original_name": metadata.get("name", chosen_name),
                            "path": final_path,
                            "size_mb": round(size_bytes / (1024 * 1024), 2),
                            "mime_type": (metadata.get("file") or {}).get("mimeType"),
                        }
                    )
            except Exception as e:
                logger.warning("Fallback get_file via serveur MCP suite à exception Graph directe: %s", e)
                fallback_name = req_name or f"onedrive_{safe_fid_seg}"
                drop_dir = self._get_mcp_drop_dir(user_id, chat_id)
                args["download_path"] = os.path.join(drop_dir, f"{fid}_{fallback_name}")

        # Cas 13 : get_attachment (Outlook) -> téléchargement et routage forcé vers le Download Broker
        if tool_name == "get_attachment":
            email_id = str(args.get("email_id") or "").strip()
            attachment_id = str(args.get("attachment_id") or "").strip()
            if not email_id or not attachment_id:
                return args, self._make_tool_response(
                    {"status": "error", "message": "email_id et attachment_id obligatoires."},
                    is_error=True,
                )
            fid = uuid.uuid4().hex[:8]
            req_path = str(args.get("save_path") or "").strip()
            req_name = self._sanitize_filename(req_path, "") if req_path else ""
            safe_att_seg = self._sanitize_segment(attachment_id[:8], "att")

            try:
                token = self._get_access_token(user_id, resolved_account)
                async with httpx.AsyncClient(timeout=60.0) as client:
                    att_res = await client.get(
                        f"https://graph.microsoft.com/v1.0/me/messages/{email_id}/attachments/{attachment_id}",
                        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                    )
                    if att_res.status_code != 200:
                        return args, self._make_tool_response(
                            {
                                "status": "error",
                                "message": f"Échec Graph get_attachment ({att_res.status_code}): {att_res.text}",
                            },
                            is_error=True,
                        )
                    att_data = att_res.json()
                    content_b64 = att_data.get("contentBytes")
                    if not content_b64:
                        return args, self._make_tool_response(
                            {"status": "error", "message": "Contenu binaire (contentBytes) indisponible."},
                            is_error=True,
                        )
                    remote_name = self._sanitize_filename(att_data.get("name"), "")
                    chosen_name = remote_name or req_name or f"attachment_{safe_att_seg}"
                    content_bytes = base64.b64decode(content_b64)
                    _, safe_name, final_path = self._publish_file_to_broker(
                        content_bytes, user_id, chat_id, chosen_name, fid=fid
                    )
                    return args, self._make_tool_response(
                        {
                            "status": "downloaded_to_broker",
                            "file_id": fid,
                            "name": safe_name,
                            "original_name": att_data.get("name", chosen_name),
                            "saved_to": final_path,
                            "content_type": att_data.get("contentType", "application/octet-stream"),
                            "size": att_data.get("size", len(content_bytes)),
                        }
                    )
            except Exception as e:
                logger.warning("Fallback get_attachment via serveur MCP suite à exception Graph directe: %s", e)
                fallback_name = req_name or f"attachment_{safe_att_seg}"
                drop_dir = self._get_mcp_drop_dir(user_id, chat_id)
                args["save_path"] = os.path.join(drop_dir, f"{fid}_{fallback_name}")

        return args, None

    async def after_tool_call(
        self,
        user_id: str,
        tool_name: str,
        result: dict,
        chat_id: Optional[str] = None,
    ) -> dict:
        if tool_name not in (
            "authenticate_account",
            "complete_authentication",
            "get_file",
            "get_attachment",
        ):
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

        elif tool_name in ("get_file", "get_attachment"):
            raw_path = payload.get("path") or payload.get("saved_to")
            if raw_path and os.path.isfile(raw_path):
                base_fname = os.path.basename(raw_path)
                fid = self._extract_hex_fid(base_fname)
                orig_name = payload.get("name")
                target_name = orig_name if orig_name and orig_name != "unknown" else base_fname
                try:
                    fid, safe_name, final_path = self._publish_file_to_broker(
                        raw_path, user_id, chat_id, target_name, fid=fid
                    )
                    updated = dict(payload)
                    updated["status"] = "downloaded_to_broker"
                    updated["file_id"] = fid
                    updated["name"] = safe_name
                    if orig_name:
                        updated["original_name"] = orig_name
                    if "path" in updated:
                        updated["path"] = final_path
                    if "saved_to" in updated:
                        updated["saved_to"] = final_path
                    return self._make_tool_response(updated, is_error=bool(result.get("isError")))
                except Exception as e:
                    logger.warning("MS365: échec finalisation fichier Download Broker: %s", e)

        return result


_DEFAULT_PROVIDER = MCPServiceProvider()
_PROVIDERS: dict[str, MCPServiceProvider] = {
    "microsoft_365": Microsoft365Provider(),
    "google_workspace": GoogleWorkspaceProvider(),
}


def get_service_provider(service: str) -> MCPServiceProvider:
    """Retourne l'adaptateur spécifique au service ou l'adaptateur No-Op par défaut."""
    return _PROVIDERS.get(service, _DEFAULT_PROVIDER)
