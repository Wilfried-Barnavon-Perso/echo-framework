# -*- coding: utf-8 -*-
"""
title: ECHO Echo State Manager
author: Wilfried BARNAVON
version: 5.239.11.1
description: Gestionnaire d'état SQLite et RAG.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.11.1: Fallback compte par défaut dans get_vault_credential si account_id est un alias externe.
# 5.237.20.1: Persistance model_id dans message_shadows, lecture get_signature_by_id depuis les Shadows et suppression des méthodes de hachage cumulatif.
# 5.236.0.1: Auto-matérialisation déclarative idempotente via ensure_db en écriture non-readonly.
# 5.235.5.3: Alignement sur les 20 colonnes déclaratives de echo_resources (CRUD, allowed fields et _row_to_resource).
# 5.235.5.2: Décorateur @_session_scope, suppression des DDL et tables mortes, intégration ECHO_TURN_CONTEXT et alignement 14 colonnes echo_resources.
import contextlib
import functools
import os
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

import orjson as json
import orjson as std_json

from echo_constants import ECHO_TURN_CONTEXT, ECHO_UPLOADS_TRANSIT_DIR, ECHO_USERS_ROOT
from echo_db_schema import (
    DB_KIND_IDENTITY,
    DB_KIND_SESSION,
    EchoDbMissingError,
    ensure_db,
    open_db,
)
from echo_paths import get_echo_identity_db_path, get_echo_session_path, resolve_upload_file_path, sanitize_echo_id


def _session_scope(default_return: Any = None):
    """Garde-fou d'étanchéité interdisant l'accès aux tables de session sans chat_id."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(self, *args, **kwargs):
            if not self.chat_id:
                print(f"[EchoStateManager] {func.__name__} ignoré : portée session requise (chat_id absent).")
                return default_return() if callable(default_return) else default_return
            return func(self, *args, **kwargs)
        return wrapper
    return decorator


class BranchShadowsList(list):
    """Liste ordonnée d'ombres conversationnelles supportant l'accès rétrocompatible par clés (dict)."""
    def keys(self):
        return [r.get("message_id") for r in self if isinstance(r, dict) and r.get("message_id")]

    def __eq__(self, other):
        if isinstance(other, dict) and len(other) == 0 and len(self) == 0:
            return True
        return super().__eq__(other)

    def __getitem__(self, item):
        res = super().__getitem__(item)
        if isinstance(item, slice):
            return BranchShadowsList(res)
        return res


class EchoStateManager:
    def __init__(self, user_id: str = "system", chat_id: Optional[str] = None, db_root_dir: Optional[str] = None):
        if user_id and "/" in str(user_id):
            self.user_id = "system"
        else:
            self.user_id = user_id
        self.chat_id = chat_id
        self.safe_uid = sanitize_echo_id(self.user_id)

        if db_root_dir is None or db_root_dir == ECHO_USERS_ROOT:
            self.identity_db_path = get_echo_identity_db_path(self.user_id)
            self.session_dir = os.path.dirname(get_echo_session_path(self.user_id, self.chat_id, "db")) if chat_id else os.path.dirname(self.identity_db_path)
            self.db_path = get_echo_session_path(self.user_id, self.chat_id, "db") if chat_id else self.identity_db_path
            self.user_dir = os.path.dirname(self.identity_db_path)
        else:
            self.user_dir = os.path.join(db_root_dir, self.safe_uid)
            self.identity_db_path = os.path.join(self.user_dir, "identity.db")
            self.session_dir = os.path.join(self.user_dir, "chats", sanitize_echo_id(chat_id)) if chat_id else self.user_dir
            self.db_path = os.path.join(self.session_dir, "session.db") if chat_id else self.identity_db_path

    @contextlib.contextmanager
    def _get_connection(self, readonly: bool = False):
        """Ouvre une connexion sur la base courante (session.db si chat_id, sinon identity.db)."""
        if not readonly and not os.path.exists(self.db_path):
            kind = DB_KIND_SESSION if self.chat_id else DB_KIND_IDENTITY
            ensure_db(self.db_path, kind)
        conn = open_db(self.db_path, readonly=readonly)
        try:
            yield conn
        finally:
            conn.close()

    @contextlib.contextmanager
    def _get_identity_connection(self, readonly: bool = False):
        """Ouvre une connexion pointant exclusivement vers identity.db."""
        if not readonly and not os.path.exists(self.identity_db_path):
            ensure_db(self.identity_db_path, DB_KIND_IDENTITY)
        conn = open_db(self.identity_db_path, readonly=readonly)
        try:
            yield conn
        finally:
            conn.close()

    # =========================================================================
    # SUTURE & SHADOWS (SESSION.DB)
    # =========================================================================

    @_session_scope()
    def save_message_shadow(
        self,
        message_id: str,
        chat_id: str,
        role: str,
        parts: List[dict],
        updated_at: Optional[int] = None,
        model_id: Optional[str] = None,
    ):
        """Persiste une ombre de message dans message_shadows (chat_id conservé pour compatibilité, ignoré en SQL)."""
        if not message_id:
            return
        ts = updated_at if updated_at is not None else int(time.time())
        try:
            with self._get_connection() as conn, conn:
                conn.execute("""
                    INSERT INTO message_shadows (message_id, role, full_parts_json, model_id, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        role = excluded.role,
                        full_parts_json = excluded.full_parts_json,
                        model_id = COALESCE(excluded.model_id, message_shadows.model_id),
                        updated_at = excluded.updated_at
                """, (message_id, role, json.dumps(parts).decode('utf-8'), model_id, ts))
                if model_id:
                    self.save_session_setting('last_model_id', model_id, conn=conn)
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[ECHO_DEBUG_FATAL] Erreur écriture save_message_shadow: {e}")

    @_session_scope()
    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        if not message_id:
            return None
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?",
                    (message_id, int(updated_at)),
                ).fetchone()
                if row:
                    return std_json.loads(row[0])
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[ECHO_DEBUG_FATAL] Erreur lecture get_message_shadow: {e}")
        return None

    @_session_scope()
    def get_shadow(self, message_id: str, updated_at: Optional[int] = None) -> Optional[List[dict]]:
        """Alias pour la lecture d'une ombre de message avec updated_at facultatif."""
        if not message_id:
            return None
        try:
            with self._get_connection(readonly=True) as conn:
                if updated_at is not None:
                    row = conn.execute(
                        "SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?",
                        (message_id, int(updated_at)),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT full_parts_json FROM message_shadows WHERE message_id = ? ORDER BY updated_at DESC LIMIT 1",
                        (message_id,),
                    ).fetchone()
                if row and row[0]:
                    return std_json.loads(row[0])
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_shadow error: {e}")
        return None

    @_session_scope()
    def save_shadow(
        self,
        message_id: str,
        parts: List[dict],
        role: str = "user",
        updated_at: Optional[int] = None,
        model_id: Optional[str] = None,
    ):
        """Alias pour persister une ombre de message."""
        return self.save_message_shadow(
            message_id=message_id,
            chat_id=self.chat_id or "",
            role=role,
            parts=parts,
            updated_at=updated_at,
            model_id=model_id,
        )

    @_session_scope()
    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None, message_id: Optional[str] = None):
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)",
                    (call_id, signature, function_name, json.dumps(args).decode('utf-8') if args is not None else None, message_id, int(time.time())),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_call_bridge error: {e}")

    @_session_scope()
    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?",
                    (call_id,),
                ).fetchone()
                if row:
                    return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_call_bridge error: {e}")
        return None

    @_session_scope()
    def get_signature_by_id(self, message_id: str) -> Optional[str]:
        if not message_id:
            return None
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT full_parts_json FROM message_shadows WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if not row or not row[0]:
                    return None
                data = std_json.loads(row[0])
                last_sig: Optional[str] = None
                if isinstance(data, list):
                    if data and isinstance(data[0], dict) and "role" in data[0]:
                        for msg in data:
                            if isinstance(msg, dict):
                                for p in msg.get("parts", []):
                                    if isinstance(p, dict) and p.get("thoughtSignature"):
                                        last_sig = p["thoughtSignature"]
                    else:
                        for p in data:
                            if isinstance(p, dict) and p.get("thoughtSignature"):
                                last_sig = p["thoughtSignature"]
                return last_sig
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_signature_by_id error: {e}")
        return None

    @_session_scope()
    def get_last_active_model(self) -> Optional[str]:
        return self.get_session_setting('last_model_id')

    # =========================================================================
    # IDENTITY VAULT (CRUD CHIFFRÉ AES-256-GCM ISOLÉ SUR IDENTITY.DB)
    # =========================================================================

    def _decrypt_vault_row(self, service: str, account_id: str, raw_creds: str) -> str:
        """Déchiffre en lecture seule pure une entrée du Vault (retourne '' si l'entrée est corrompue)."""
        from echo_crypto import decrypt_vault_payload
        try:
            return decrypt_vault_payload(raw_creds, self.user_id, service, account_id)
        except Exception:
            return ""

    def save_vault_credential(self, service: str, account_id: str, credentials: str) -> None:
        """Chiffre en AES-256-GCM (AAD=user_id:service:account_id) et persiste une identité dans identity_vault."""
        from echo_crypto import encrypt_vault_payload
        enc_creds = encrypt_vault_payload(credentials, self.user_id, service, account_id)
        try:
            with self._get_identity_connection() as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO identity_vault (user_id, service, account_id, credentials) VALUES (?, ?, ?, ?)",
                    (self.user_id, service, account_id, enc_creds),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_vault_credential error: {e}")

    def get_vault_credential(self, service: str, account_id: Optional[str] = None) -> str:
        """Récupère et déchiffre une identité depuis identity_vault (repli sur le compte du service)."""
        try:
            with self._get_identity_connection(readonly=True) as conn:
                row = None
                if account_id:
                    row = conn.execute(
                        "SELECT account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ? AND account_id = ? LIMIT 1",
                        (self.user_id, service, account_id),
                    ).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ? LIMIT 1",
                        (self.user_id, service),
                    ).fetchone()
                if not row or not row[1]:
                    return ""
                return self._decrypt_vault_row(service, row[0], row[1])
        except EchoDbMissingError:
            return ""
        except Exception as e:
            print(f"[EchoStateManager] get_vault_credential error: {e}")
            return ""

    def list_vault_credentials(self, service: str) -> List[tuple]:
        """Retourne [(account_id, decrypted_credentials), ...] pour un service donné."""
        results = []
        try:
            with self._get_identity_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT account_id, credentials FROM identity_vault WHERE user_id = ? AND service = ?",
                    (self.user_id, service),
                ).fetchall()
                for acc_id, raw_creds in rows:
                    results.append((acc_id, self._decrypt_vault_row(service, acc_id, raw_creds or "")))
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] list_vault_credentials error: {e}")
        return results

    def list_vault_services(self) -> List[str]:
        """Liste les services disposant d'au moins une identité configurée."""
        try:
            with self._get_identity_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT DISTINCT service FROM identity_vault WHERE user_id = ?",
                    (self.user_id,),
                ).fetchall()
                return [r[0] for r in rows] if rows else []
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] list_vault_services error: {e}")
            return []

    def list_vault_accounts(self) -> List[dict]:
        """Liste les couples {service, account_id} sans lire ni déchiffrer la colonne credentials."""
        try:
            with self._get_identity_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT service, account_id FROM identity_vault WHERE user_id = ?",
                    (self.user_id,),
                ).fetchall()
                return [{"service": r[0], "account_id": r[1]} for r in rows]
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] list_vault_accounts error: {e}")
            return []

    def has_vault_credential(self, service: str, account_id: str) -> bool:
        """Vérifie en O(1) l'existence d'un secret non vide dans identity_vault."""
        try:
            with self._get_identity_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT 1 FROM identity_vault WHERE user_id = ? AND service = ? AND account_id = ? AND credentials IS NOT NULL AND credentials != ''",
                    (self.user_id, service, account_id),
                ).fetchone()
                return row is not None
        except EchoDbMissingError:
            return False
        except Exception as e:
            print(f"[EchoStateManager] has_vault_credential error: {e}")
            return False

    def delete_vault_credential(self, service: str, account_id: str) -> None:
        """Supprime une identité de identity_vault."""
        try:
            with self._get_identity_connection() as conn, conn:
                conn.execute(
                    "DELETE FROM identity_vault WHERE user_id = ? AND service = ? AND account_id = ?",
                    (self.user_id, service, account_id),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] delete_vault_credential error: {e}")

    # =========================================================================
    # RÉGLAGES UTILISATEUR & SESSION (ECHO_SETTINGS)
    # =========================================================================

    def save_setting(self, key: str, value: str):
        """Persiste une préférence utilisateur dans echo_settings (identity.db)."""
        try:
            with self._get_identity_connection() as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, int(time.time())),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_setting error: {e}")

    def set_setting(self, key: str, value: str):
        """Définit un réglage dans la portée courante (session.db si chat_id, sinon identity.db)."""
        if self.chat_id:
            return self.save_session_setting(key, value)
        return self.save_setting(key, value)

    def get_setting(self, key: str) -> Optional[str]:
        """Récupère un réglage depuis la portée courante (session.db si chat_id, sinon identity.db)."""
        if self.chat_id:
            val = self.get_session_setting(key)
            if val is not None:
                return val
        try:
            with self._get_identity_connection(readonly=True) as conn:
                row = conn.execute("SELECT value FROM echo_settings WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_setting error: {e}")
            return None

    @_session_scope()
    def save_session_setting(self, key: str, value: str, conn: Optional[sqlite3.Connection] = None):
        """Persiste une variable KV dans echo_settings de la session active (session.db)."""
        try:
            if conn:
                conn.execute(
                    "INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (key, value, int(time.time())),
                )
            else:
                with self._get_connection() as c, c:
                    c.execute(
                        "INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)",
                        (key, value, int(time.time())),
                    )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_session_setting error: {e}")

    @_session_scope()
    def get_session_setting(self, key: str) -> Optional[str]:
        """Récupère une variable KV depuis echo_settings de la session active (session.db)."""
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute("SELECT value FROM echo_settings WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_session_setting error: {e}")
            return None

    def get_agy_endpoint(self) -> tuple[int, str]:
        """Analyse les verrous temporels de chaque URL. Retourne (index, url_saine)."""
        from datetime import datetime, timezone
        from echo_constants import AGY_BASE_URLS

        now = datetime.now(timezone.utc).isoformat()
        for idx, url in enumerate(AGY_BASE_URLS):
            reset_time = self.get_setting(f"agy_locked_url_{idx}")
            if not reset_time or now > reset_time:
                return idx, url

        fallback_idx = len(AGY_BASE_URLS) - 1
        return fallback_idx, AGY_BASE_URLS[fallback_idx]

    def lock_agy_endpoint(self, idx: int, reset_time_utc: str):
        """Verrouille une URL jusqu'à son reset_time."""
        self.save_setting(f"agy_locked_url_{idx}", reset_time_utc)

    def unlock_agy_endpoint(self, idx: int):
        """Déverrouille proactivement une URL sur un succès HTTP (Auto-Heal)."""
        self.save_setting(f"agy_locked_url_{idx}", "")

    @_session_scope()
    def init_session_context_baseline(self, quota_5h_fraction: Optional[float] = None, quota_weekly_fraction: Optional[float] = None):
        """Initialise la référence initiale de la session au tour 1 si aucun historique n'existe."""
        if not self.get_previous_context_stats() and not self.get_last_context_stats():
            baseline = {
                "totalTokenCount": 0,
                "promptTokenCount": 0,
                "candidatesTokenCount": 0,
                "cachedContentTokenCount": 0,
                "quota_5h_fraction": quota_5h_fraction,
                "quota_weekly_fraction": quota_weekly_fraction,
                "is_first_turn": True,
            }
            self.save_session_setting('previous_context_stats', std_json.dumps(baseline).decode('utf-8'))

    @_session_scope()
    def archive_previous_context_stats(self):
        """Archive l'état final du tour précédent au tout début d'un nouveau tour utilisateur."""
        old_dict = self.get_last_context_stats()
        if old_dict and old_dict.get("totalTokenCount", 0) > 0:
            self.save_session_setting('previous_context_stats', std_json.dumps(old_dict).decode('utf-8'))

    @_session_scope()
    def save_context_stats(self, stats: dict):
        """Met à jour context_stats par fusion sans écraser previous_context_stats en cours de tour."""
        merged = self.get_last_context_stats()
        merged.update(stats)
        self.save_session_setting('context_stats', std_json.dumps(merged).decode('utf-8'))

    @_session_scope(dict)
    def get_last_context_stats(self) -> dict:
        data = self.get_session_setting('context_stats')
        if data:
            try:
                return std_json.loads(data)
            except Exception:
                pass
        return {}

    @_session_scope(dict)
    def get_previous_context_stats(self) -> dict:
        data = self.get_session_setting('previous_context_stats')
        if data:
            try:
                return std_json.loads(data)
            except Exception:
                pass
        return {}

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        if not self.chat_id:
            return False
        old_path = resolve_upload_file_path(self.user_id, file_id, chat_id=self.chat_id)
        if not old_path:
            return False
        new_path = os.path.join(get_echo_session_path(self.user_id, self.chat_id, "files"), os.path.basename(old_path))
        try:
            if not os.path.exists(new_path):
                if ECHO_UPLOADS_TRANSIT_DIR in old_path:
                    shutil.move(old_path, new_path)
                else:
                    shutil.copy2(old_path, new_path)
            return True
        except Exception as e:
            print(f"[EchoStateManager] move_to_vault error: {e}")
            return False

    @_session_scope()
    def save_thread_step(self, sub_sid: str, chat_id: Optional[str], role_id: str, step_index: int, role: str, content: List[dict], signature: Optional[str] = None):
        """Persiste une étape de thread cognitif (chat_id conservé pour compatibilité, ignoré en SQL)."""
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cognitive_threads (sub_sid, step_index, role_id, role, content_json, thought_signature, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sub_sid, step_index, role_id, role, json.dumps(content).decode('utf-8'), signature, int(time.time())),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_thread_step error: {e}")

    @_session_scope(list)
    def get_thread_history(self, sub_sid: str) -> List[dict]:
        history = []
        try:
            with self._get_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT role, content_json, thought_signature FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,),
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[1])
                    item = {"role": row[0], "parts": parts}
                    if row[2]:
                        if parts and isinstance(parts, list):
                            if "functionCall" in parts[0] or "text" in parts[0]:
                                parts[0]["thoughtSignature"] = row[2]
                    history.append(item)
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] get_thread_history error: {e}")
        return history

    @_session_scope(list)
    def get_thread_steps_enriched(self, sub_sid: str) -> List[dict]:
        """Retourne les steps enrichis d'un thread (role, role_id, parts, timestamp)."""
        steps = []
        try:
            with self._get_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT step_index, role, role_id, content_json, updated_at "
                    "FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,),
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[3])
                    steps.append({
                        "index": row[0],
                        "role": row[1],
                        "role_id": row[2],
                        "parts": parts,
                        "timestamp": row[4],
                    })
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] get_thread_steps_enriched error: {e}")
        return steps

    @_session_scope(list)
    def list_threads(self, chat_id: Optional[str] = None) -> List[dict]:
        threads = []
        try:
            with self._get_connection(readonly=True) as conn:
                rows = conn.execute(
                    "SELECT sub_sid, role_id, MAX(step_index) as steps, MAX(updated_at) as last_active FROM cognitive_threads GROUP BY sub_sid ORDER BY last_active DESC"
                ).fetchall()
                for row in rows:
                    threads.append({
                        "sub_sid": row[0],
                        "role_id": row[1],
                        "last_step": row[2],
                        "updated_at": row[3],
                    })
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] list_threads error: {e}")
        return threads

    @_session_scope()
    def delete_thread(self, sub_sid: str):
        try:
            with self._get_connection() as conn, conn:
                conn.execute("DELETE FROM cognitive_threads WHERE sub_sid = ?", (sub_sid,))
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] delete_thread error: {e}")

    # ==========================================================================
    # REGISTRE UNIFIÉ — ECHO_RESOURCES
    # ==========================================================================

    def save_resource(
        self,
        id: str,
        name: str = None,
        resource_type: str = None,
        status: str = None,
        mime: str = None,
        summary: str = None,
        storage_path: str = None,
        git_tracked: bool = False,
        message_id: str = None,
        plan_goal: str = None,
        author_model: str = None,
        language: str = None,
        lines: int = None,
        last_commit: str = None,
        commit_msg: str = None,
        item_type: str = "file",
        content: str = None,
        metadata: Any = None,
        **kwargs,
    ):
        """Crée ou met à jour une ressource dans le registre unifié echo_resources (schéma v1).

        Lot 2 : injecte automatiquement la provenance message_id depuis ECHO_TURN_CONTEXT si absent.
        """
        # Détection adaptative d'un appel positionnel (id, type_or_name, name_or_summary, metadata, content)
        if isinstance(status, dict):
            metadata = status
            content = mime
            mime = None
            if name == "codex_urn_directive":
                name = "codex_urn_directive"
                resource_type = "aec_directive"
                status = "active"
            elif name in ("aec_event", "aec_directive", "codex_artifact", "n8n_template", "codex", "plan", "media", "binary"):
                actual_type = name
                name = resource_type or name
                resource_type = actual_type
                status = "active" if resource_type in ("aec_event", "aec_directive") else "ready"
            else:
                status = "active" if resource_type in ("aec_event", "aec_directive") else "ready"

        if not status:
            status = "active" if resource_type in ("aec_event", "aec_directive") else "ready"
        if message_id is None and self.chat_id and resource_type not in ("aec_event", "aec_directive"):
            turn_ctx = ECHO_TURN_CONTEXT.get() or {}
            if turn_ctx.get("chat_id") == self.chat_id:
                message_id = turn_ctx.get("assistant_message_id") or turn_ctx.get("user_message_id") or turn_ctx.get("message_id")

        # Préservation rétrocompatible des champs additionnels dans metadata JSON
        meta_dict = {}
        if metadata:
            try:
                meta_dict = json.loads(metadata) if isinstance(metadata, str) else dict(metadata)
            except Exception:
                meta_dict = {"raw_metadata": str(metadata)}
        if message_id and "message_id" not in meta_dict:
            meta_dict["message_id"] = message_id
        for extra_key, extra_val in (
            ("plan_goal", plan_goal),
            ("author_model", author_model),
            ("language", language),
            ("lines", lines),
            ("last_commit", last_commit),
            ("commit_msg", commit_msg),
        ):
            if extra_val is not None and extra_key not in meta_dict:
                meta_dict[extra_key] = extra_val
        meta_str = json.dumps(meta_dict).decode("utf-8")

        ts = time.time()
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "INSERT INTO echo_resources "
                    "(id, name, resource_type, item_type, status, mime, summary, content, metadata, "
                    "storage_path, git_tracked, message_id, plan_goal, author_model, language, lines, last_commit, commit_msg, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name, resource_type=excluded.resource_type, item_type=excluded.item_type, "
                    "status=excluded.status, mime=excluded.mime, summary=excluded.summary, "
                    "content=COALESCE(excluded.content, echo_resources.content), "
                    "metadata=COALESCE(excluded.metadata, echo_resources.metadata), "
                    "storage_path=excluded.storage_path, git_tracked=excluded.git_tracked, "
                    "message_id=COALESCE(excluded.message_id, echo_resources.message_id), "
                    "plan_goal=COALESCE(excluded.plan_goal, echo_resources.plan_goal), "
                    "author_model=COALESCE(excluded.author_model, echo_resources.author_model), "
                    "language=COALESCE(excluded.language, echo_resources.language), "
                    "lines=COALESCE(excluded.lines, echo_resources.lines), "
                    "last_commit=COALESCE(excluded.last_commit, echo_resources.last_commit), "
                    "commit_msg=COALESCE(excluded.commit_msg, echo_resources.commit_msg), "
                    "updated_at=excluded.updated_at",
                    (
                        id, name, resource_type, item_type, status, mime, summary, content, meta_str, storage_path,
                        1 if git_tracked else 0, message_id, plan_goal, author_model, language, lines, last_commit, commit_msg, ts, ts,
                    ),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] save_resource error: {e}")

    def update_resource_status(self, id: str, status: str):
        """Met à jour le statut d'une ressource (préserve les autres champs)."""
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "UPDATE echo_resources SET status = ?, updated_at = ? WHERE id = ?",
                    (status, time.time(), id),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] update_resource_status error: {e}")

    def update_resource_fields(self, id: str, **fields):
        """Met à jour un ou plusieurs champs d'une ressource."""
        allowed = {
            "name", "status", "mime", "summary", "content", "metadata",
            "storage_path", "git_tracked", "message_id", "item_type",
            "plan_goal", "author_model", "language", "lines", "last_commit", "commit_msg",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [id]
        try:
            with self._get_connection() as conn, conn:
                conn.execute(f"UPDATE echo_resources SET {set_clause} WHERE id = ?", values)
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] update_resource_fields error: {e}")

    def delete_resource(self, id: str):
        """Supprime une ressource par ID."""
        try:
            with self._get_connection() as conn, conn:
                conn.execute("DELETE FROM echo_resources WHERE id = ?", (id,))
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] delete_resource error: {e}")

    def get_resource(self, id: str) -> Optional[dict]:
        """Retourne une ressource par ID, ou None si inexistante."""
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT id, name, resource_type, item_type, status, mime, summary, content, metadata, "
                    "storage_path, git_tracked, message_id, plan_goal, author_model, language, lines, last_commit, commit_msg, created_at, updated_at "
                    "FROM echo_resources WHERE id = ?", (id,),
                ).fetchone()
                if row:
                    return self._row_to_resource(row)
        except EchoDbMissingError:
            return None
        except Exception as e:
            print(f"[EchoStateManager] get_resource error: {e}")
        return None

    def get_resources(
        self,
        resource_type: str = None,
        status: str = None,
        search: str = None,
        created_after: float = None,
        message_ids: List[str] = None,
        workspace: str = None,
    ) -> List[dict]:
        """Liste les ressources avec filtres combinés (AND)."""
        conditions = []
        params = []
        if resource_type:
            conditions.append("resource_type = ?")
            params.append(resource_type)
        if status:
            conditions.append("status = ?")
            params.append(status)
        if search:
            conditions.append("name LIKE ?")
            params.append(f"%{search}%")
        if created_after is not None:
            conditions.append("created_at > ?")
            params.append(created_after)
        if message_ids:
            placeholders = ','.join('?' for _ in message_ids)
            conditions.append(f"message_id IN ({placeholders})")
            params.extend(message_ids)
        if workspace:
            clean_ws = str(workspace).strip().strip("/\\")
            if clean_ws:
                conditions.append("storage_path LIKE ?")
                params.append(f"codex/{clean_ws}/%")

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        resources = []
        try:
            with self._get_connection(readonly=True) as conn:
                rows = conn.execute(
                    f"SELECT id, name, resource_type, item_type, status, mime, summary, content, metadata, "
                    f"storage_path, git_tracked, message_id, plan_goal, author_model, language, lines, last_commit, commit_msg, created_at, updated_at "
                    f"FROM echo_resources{where} ORDER BY updated_at DESC", params,
                ).fetchall()
                for row in rows:
                    resources.append(self._row_to_resource(row))
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] get_resources error: {e}")
        return resources

    def clear_resources_by_type(self, resource_type: str):
        """Supprime toutes les ressources d'un type donné."""
        try:
            with self._get_connection() as conn, conn:
                conn.execute("DELETE FROM echo_resources WHERE resource_type = ?", (resource_type,))
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] clear_resources_by_type error: {e}")

    def delete_codex_resource(self, workspace: str, rel_path: str):
        """Supprime une ressource Codex ciblée par son workspace et son chemin relatif."""
        clean_ws = str(workspace or "").strip().strip("/\\")
        clean_rel = str(rel_path or "").strip().lstrip("/\\").replace("\\", "/")
        storage_path = f"codex/{clean_ws}/{clean_rel}"
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "DELETE FROM echo_resources WHERE storage_path = ? OR (id = ? AND storage_path LIKE ?)",
                    (storage_path, clean_rel, f"codex/{clean_ws}/%"),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] delete_codex_resource error: {e}")

    def clear_codex_workspace_resources(self, workspace: str):
        """Purge toutes les ressources associées à un workspace Codex spécifique."""
        clean_ws = str(workspace or "").strip().strip("/\\")
        if not clean_ws:
            return
        try:
            with self._get_connection() as conn, conn:
                conn.execute("DELETE FROM echo_resources WHERE storage_path LIKE ?", (f"codex/{clean_ws}/%",))
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] clear_codex_workspace_resources error: {e}")

    def get_next_codex_name(self, name: str) -> str:
        """Retourne un nom unique pour le Codex avec suffixe incrémental en cas de collision."""
        try:
            with self._get_connection(readonly=True) as conn:
                existing = conn.execute(
                    "SELECT id FROM echo_resources WHERE id = ? AND resource_type = 'codex'", (name,),
                ).fetchone()
                if not existing:
                    return name

                base, ext = os.path.splitext(name)
                idx = 1
                while True:
                    candidate = f"{base}_{idx}{ext}"
                    exists = conn.execute(
                        "SELECT id FROM echo_resources WHERE id = ? AND resource_type = 'codex'", (candidate,),
                    ).fetchone()
                    if not exists:
                        return candidate
                    idx += 1
        except EchoDbMissingError:
            return name
        except Exception as e:
            print(f"[EchoStateManager] get_next_codex_name error: {e}")
        return name

    @staticmethod
    def _row_to_resource(row) -> dict:
        """Convertit un tuple SQLite en dict ressource conforme au schéma v1."""
        meta = row[8]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta) if meta else {}
            except Exception:
                meta = {}
        elif not isinstance(meta, dict):
            meta = {}
        msg_id = row[11]
        if msg_id and "message_id" not in meta:
            meta["message_id"] = msg_id

        plan_goal = row[12] if row[12] is not None else meta.get("plan_goal")
        author_model = row[13] if row[13] is not None else meta.get("author_model")
        language = row[14] if row[14] is not None else meta.get("language")
        lines = row[15] if row[15] is not None else meta.get("lines")
        last_commit = row[16] if row[16] is not None else meta.get("last_commit")
        commit_msg = row[17] if row[17] is not None else meta.get("commit_msg")

        for k, v in (
            ("plan_goal", plan_goal),
            ("author_model", author_model),
            ("language", language),
            ("lines", lines),
            ("last_commit", last_commit),
            ("commit_msg", commit_msg),
        ):
            if v is not None and k not in meta:
                meta[k] = v

        return {
            "id": row[0],
            "name": row[1],
            "resource_type": row[2],
            "type": row[2],
            "item_type": row[3],
            "status": row[4],
            "mime": row[5],
            "summary": row[6],
            "content": row[7],
            "metadata": meta,
            "storage_path": row[9],
            "git_tracked": bool(row[10]),
            "message_id": msg_id,
            "plan_goal": plan_goal,
            "author_model": author_model,
            "language": language,
            "lines": lines,
            "last_commit": last_commit,
            "commit_msg": commit_msg,
            "created_at": row[18],
            "updated_at": row[19],
            "timestamp": row[19],
        }

    # =========================================================================
    # LOT 3 : BRANCHE ACTIVE & LOT 1 : RAG_INDEX_STATE & AEC / CODEX UTILS
    # =========================================================================

    @_session_scope(BranchShadowsList)
    def get_active_branch_shadows(
        self,
        chat_id_or_ids: Any = None,
        limit: int = 20,
        active_message_ids: Optional[List[str]] = None,
    ) -> Any:
        """Récupère les ombres des messages appartenant à la branche active ordonnée."""
        if isinstance(chat_id_or_ids, (list, tuple, set)):
            effective_ids = list(chat_id_or_ids)
        elif active_message_ids is not None:
            effective_ids = list(active_message_ids)
        else:
            effective_ids = []

        if not effective_ids:
            return BranchShadowsList()
        try:
            with self._get_connection(readonly=True) as conn:
                placeholders = ", ".join("?" for _ in effective_ids)
                rows = conn.execute(
                    f"SELECT message_id, role, full_parts_json FROM message_shadows WHERE message_id IN ({placeholders})",
                    effective_ids,
                ).fetchall()
                shadow_by_id = {r[0]: (r[1], r[2]) for r in rows}

                ordered = BranchShadowsList()
                for mid in effective_ids:
                    if mid in shadow_by_id:
                        r_role, r_json = shadow_by_id[mid]
                        try:
                            parts = json.loads(r_json)
                        except Exception:
                            parts = []
                        ordered.append({"message_id": mid, "role": r_role, "parts": parts})

                return BranchShadowsList(ordered[-limit:]) if limit > 0 else ordered
        except EchoDbMissingError:
            return BranchShadowsList()
        except Exception as e:
            print(f"[EchoStateManager] get_active_branch_shadows error: {e}")
            return BranchShadowsList()

    @_session_scope(False)
    def is_message_embedded(self, message_id: str) -> bool:
        """Vérifie dans rag_index_state si le message a déjà été vectorisé dans Qdrant."""
        if not message_id:
            return False
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute("SELECT 1 FROM rag_index_state WHERE message_id = ?", (message_id,)).fetchone()
                return row is not None
        except EchoDbMissingError:
            return False
        except Exception as e:
            print(f"[EchoStateManager] is_message_embedded error: {e}")
            return False

    @_session_scope()
    def mark_message_embedded(self, message_id: str):
        """Enregistre le message dans rag_index_state sans créer de fausses lignes Shadows."""
        if not message_id:
            return
        try:
            with self._get_connection() as conn, conn:
                conn.execute(
                    "INSERT OR IGNORE INTO rag_index_state (message_id, embedded_at) VALUES (?, ?)",
                    (message_id, int(time.time())),
                )
        except EchoDbMissingError:
            pass
        except Exception as e:
            print(f"[EchoStateManager] mark_message_embedded error: {e}")

    @_session_scope(False)
    def has_pending_directive(self, name: str) -> bool:
        """Vérifie l'existence d'une directive AEC active non encore consommée."""
        try:
            with self._get_connection(readonly=True) as conn:
                row = conn.execute(
                    "SELECT 1 FROM echo_resources WHERE (resource_type = 'aec_directive' OR resource_type = ?) AND (name = ? OR resource_type = ?) AND status = 'active' LIMIT 1",
                    (name, name, name),
                ).fetchone()
                return row is not None
        except EchoDbMissingError:
            return False
        except Exception as e:
            print(f"[EchoStateManager] has_pending_directive error: {e}")
            return False

    @_session_scope(list)
    def drain_resources_by_types(self, resource_types: Tuple[str, ...] = ("aec_directive", "aec_event")) -> List[Dict[str, Any]]:
        """Extrait et purge atomiquement les ressources des types spécifiés."""
        if not resource_types:
            return []
        try:
            with self._get_connection() as conn, conn:
                placeholders = ", ".join("?" for _ in resource_types)
                rows = conn.execute(
                    f"SELECT id, name, resource_type, status, summary, content, created_at FROM echo_resources WHERE resource_type IN ({placeholders}) ORDER BY created_at ASC",
                    resource_types,
                ).fetchall()
                if not rows:
                    return []
                ids = [r[0] for r in rows]
                id_placeholders = ", ".join("?" for _ in ids)
                conn.execute(f"DELETE FROM echo_resources WHERE id IN ({id_placeholders})", ids)
                return [
                    {
                        "id": r[0],
                        "name": r[1],
                        "resource_type": r[2],
                        "status": r[3],
                        "summary": r[4],
                        "content": r[5],
                        "created_at": r[6],
                    }
                    for r in rows
                ]
        except EchoDbMissingError:
            return []
        except Exception as e:
            print(f"[EchoStateManager] drain_resources_by_types error: {e}")
            return []

    @_session_scope(0)
    def delete_resources_by_types_after(self, resource_types: Tuple[str, ...], t_pivot: float) -> int:
        """Supprime les ressources des types spécifiés créées strictement après t_pivot."""
        if not resource_types:
            return 0
        try:
            with self._get_connection() as conn, conn:
                placeholders = ", ".join("?" for _ in resource_types)
                params = list(resource_types) + [t_pivot]
                cur = conn.execute(f"DELETE FROM echo_resources WHERE resource_type IN ({placeholders}) AND created_at > ?", params)
                return cur.rowcount
        except EchoDbMissingError:
            return 0
        except Exception as e:
            print(f"[EchoStateManager] delete_resources_by_types_after error: {e}")
            return 0
