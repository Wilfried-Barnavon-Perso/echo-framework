"""
title: ECHO DB Schema
author: ECHO Framework
version: 5.237.20.2
description: Schéma déclaratif SQLite v2 et moteur de migration transactionnel (identity.db & session.db).
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.237.20.2: Conformité stricte Gemini 3/4 (priorité functionCall sur text dans _migrate et regroupement des appels parallèles dans backfill_session_shadows_from_webui).
# 5.237.20.1: Schéma déclaratif SQLite v2 (6 tables session.db, model_id sur message_shadows, purge suture_index/cognitive_signatures/tool_journal et rétro-conversion webui.db).
# 5.235.5.3: Schéma de production identity_vault (clé composite), 20 colonnes echo_resources et COALESCE défensif dans _migrate.
# 5.235.5.2: Schéma déclaratif SQLite v1 (identity.db 4 tables vs session.db 9 tables), PRAGMAs SSOT, migrations atomiques et table rag_index_state.

import argparse
from dataclasses import dataclass
import json
import os
import re
import sqlite3
import sys
import time
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from echo_constants import ECHO_USERS_ROOT

ECHO_DB_SCHEMA_VERSION = 2
DB_KIND_SESSION = "session"
DB_KIND_IDENTITY = "identity"


@dataclass(frozen=True)
class ColumnSpec:
    """Spécification déclarative d'une colonne de table SQLite."""
    name: str
    sql_type: str
    notnull: bool = False
    default_sql: Optional[str] = None


@dataclass(frozen=True)
class TableSpec:
    """Spécification déclarative d'une table SQLite avec colonnes, clé primaire et index."""
    name: str
    columns: Tuple[ColumnSpec, ...]
    primary_key: Tuple[str, ...]
    indexes: Tuple[Tuple[str, Tuple[str, ...]], ...] = ()

    def create_sql(self, table_name: Optional[str] = None) -> str:
        """Génère le DDL CREATE TABLE IF NOT EXISTS conforme aux spécifications."""
        tname = table_name or self.name
        col_defs = []
        is_single_pk = len(self.primary_key) == 1
        for col in self.columns:
            parts = [f'"{col.name}"', col.sql_type]
            if is_single_pk and col.name == self.primary_key[0]:
                parts.append("PRIMARY KEY")
            if col.notnull:
                parts.append("NOT NULL")
            if col.default_sql is not None:
                parts.append(f"DEFAULT {col.default_sql}")
            col_defs.append(" ".join(parts))

        if not is_single_pk and self.primary_key:
            pk_cols = ", ".join(f'"{c}"' for c in self.primary_key)
            col_defs.append(f"PRIMARY KEY ({pk_cols})")

        inner = ",\n    ".join(col_defs)
        return f'CREATE TABLE IF NOT EXISTS "{tname}" (\n    {inner}\n)'

    def index_sql(self) -> List[str]:
        """Génère la liste des DDL CREATE INDEX IF NOT EXISTS."""
        sqls = []
        for idx_name, idx_cols in self.indexes:
            cols = ", ".join(f'"{c}"' for c in idx_cols)
            sqls.append(f'CREATE INDEX IF NOT EXISTS {idx_name} ON "{self.name}"({cols})')
        return sqls


# ==============================================================================
# SPÉCIFICATIONS DES TABLES PARTAGÉES (identity.db & session.db)
# ==============================================================================

TABLE_ECHO_SETTINGS = TableSpec(
    name="echo_settings",
    columns=(
        ColumnSpec("key", "TEXT", notnull=True),
        ColumnSpec("value", "TEXT"),
        ColumnSpec("updated_at", "INTEGER", notnull=True, default_sql="0"),
    ),
    primary_key=("key",),
)

TABLE_ECHO_RESOURCES = TableSpec(
    name="echo_resources",
    columns=(
        ColumnSpec("id", "TEXT", notnull=True),
        ColumnSpec("name", "TEXT", notnull=True),
        ColumnSpec("resource_type", "TEXT", notnull=True),
        ColumnSpec("item_type", "TEXT", default_sql="'file'"),
        ColumnSpec("status", "TEXT", notnull=True),
        ColumnSpec("mime", "TEXT"),
        ColumnSpec("summary", "TEXT"),
        ColumnSpec("content", "TEXT"),
        ColumnSpec("metadata", "TEXT"),
        ColumnSpec("storage_path", "TEXT"),
        ColumnSpec("git_tracked", "INTEGER", default_sql="0"),
        ColumnSpec("message_id", "TEXT"),
        ColumnSpec("plan_goal", "TEXT"),
        ColumnSpec("author_model", "TEXT"),
        ColumnSpec("language", "TEXT"),
        ColumnSpec("lines", "INTEGER"),
        ColumnSpec("last_commit", "TEXT"),
        ColumnSpec("commit_msg", "TEXT"),
        ColumnSpec("created_at", "REAL", notnull=True, default_sql="0"),
        ColumnSpec("updated_at", "REAL", notnull=True, default_sql="0"),
    ),
    primary_key=("id",),
    indexes=(
        ("idx_res_type", ("resource_type",)),
        ("idx_res_updated", ("updated_at",)),
        ("idx_res_created", ("created_at",)),
        ("idx_res_msg", ("message_id",)),
        ("idx_res_storage_path", ("storage_path",)),
    ),
)


# ==============================================================================
# TABLES DE SESSION (session.db) — 6 tables autorisées
# ==============================================================================

SESSION_TABLES: Dict[str, TableSpec] = {
    "message_shadows": TableSpec(
        name="message_shadows",
        columns=(
            ColumnSpec("message_id", "TEXT", notnull=True),
            ColumnSpec("role", "TEXT"),
            ColumnSpec("full_parts_json", "TEXT"),
            ColumnSpec("model_id", "TEXT"),
            ColumnSpec("updated_at", "INTEGER", notnull=True, default_sql="0"),
        ),
        primary_key=("message_id",),
        indexes=(
            ("idx_shadows_updated_at", ("updated_at",)),
        ),
    ),
    "rag_index_state": TableSpec(
        name="rag_index_state",
        columns=(
            ColumnSpec("message_id", "TEXT", notnull=True),
            ColumnSpec("embedded_at", "INTEGER", notnull=True, default_sql="0"),
        ),
        primary_key=("message_id",),
    ),
    "call_bridge": TableSpec(
        name="call_bridge",
        columns=(
            ColumnSpec("call_id", "TEXT", notnull=True),
            ColumnSpec("signature", "TEXT"),
            ColumnSpec("function_name", "TEXT"),
            ColumnSpec("args_json", "TEXT"),
            ColumnSpec("message_id", "TEXT"),
            ColumnSpec("timestamp", "INTEGER", notnull=True, default_sql="0"),
        ),
        primary_key=("call_id",),
        indexes=(
            ("idx_call_bridge_message_id", ("message_id",)),
        ),
    ),
    "cognitive_threads": TableSpec(
        name="cognitive_threads",
        columns=(
            ColumnSpec("sub_sid", "TEXT", notnull=True),
            ColumnSpec("step_index", "INTEGER", notnull=True),
            ColumnSpec("role_id", "TEXT"),
            ColumnSpec("role", "TEXT"),
            ColumnSpec("content_json", "TEXT"),
            ColumnSpec("thought_signature", "TEXT"),
            ColumnSpec("updated_at", "INTEGER", notnull=True, default_sql="0"),
        ),
        primary_key=("sub_sid", "step_index"),
        indexes=(
            ("idx_cog_threads_updated_at", ("updated_at",)),
        ),
    ),
    "echo_resources": TABLE_ECHO_RESOURCES,
    "echo_settings": TABLE_ECHO_SETTINGS,
}


# ==============================================================================
# TABLES UTILISATEUR (identity.db) — 4 tables autorisées
# ==============================================================================

IDENTITY_TABLES: Dict[str, TableSpec] = {
    "auth_data": TableSpec(
        name="auth_data",
        columns=(
            ColumnSpec("key", "TEXT", notnull=True),
            ColumnSpec("value", "TEXT"),
            ColumnSpec("updated_at", "INTEGER", notnull=True, default_sql="0"),
        ),
        primary_key=("key",),
    ),
    "identity_vault": TableSpec(
        name="identity_vault",
        columns=(
            ColumnSpec("user_id", "TEXT", notnull=True),
            ColumnSpec("service", "TEXT", notnull=True),
            ColumnSpec("account_id", "TEXT", notnull=True),
            ColumnSpec("credentials", "TEXT"),
        ),
        primary_key=("user_id", "service", "account_id"),
    ),
    "echo_settings": TABLE_ECHO_SETTINGS,
    "echo_resources": TABLE_ECHO_RESOURCES,
}

OBSOLETE_TABLES: Dict[str, Tuple[str, ...]] = {
    DB_KIND_SESSION: (
        "rich_payloads",
        "processed_files",
        "auth_data",
        "identity_vault",
        "thought_archive",
        "suture_index",
        "cognitive_signatures",
        "tool_journal",
    ),
    DB_KIND_IDENTITY: (
        "message_shadows",
        "suture_index",
        "cognitive_signatures",
        "tool_journal",
        "rich_payloads",
        "processed_files",
        "call_bridge",
        "cognitive_threads",
        "thought_archive",
        "rag_index_state",
    ),
}


# ==============================================================================
# FONCTIONS UTILITAIRES & EXCEPTIONS
# ==============================================================================

def extract_call_ids_from_json(raw_json: Optional[str]) -> Set[str]:
    """Extrait récursivement tous les identifiants d'appels d'outils (call_id) depuis un JSON."""
    call_ids: Set[str] = set()
    if not raw_json:
        return call_ids
    try:
        data = json.loads(raw_json)
    except Exception:
        return call_ids

    def _walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                _walk(item)
        elif isinstance(node, dict):
            calls = node.get("calls")
            if isinstance(calls, list):
                for c in calls:
                    if isinstance(c, dict) and c.get("id"):
                        call_ids.add(str(c["id"]))
            fc = node.get("functionCall")
            if isinstance(fc, dict) and fc.get("id"):
                call_ids.add(str(fc["id"]))
            fr = node.get("functionResponse")
            if isinstance(fr, dict) and fr.get("id"):
                call_ids.add(str(fr["id"]))
            parts = node.get("parts")
            if isinstance(parts, list):
                _walk(parts)

    _walk(data)
    return call_ids


def backfill_session_shadows_from_webui(
    conn: sqlite3.Connection,
    chat_id: str,
    webui_conn: sqlite3.Connection,
) -> int:
    """Rétro-remplit les message_shadows d'une session depuis webui.db et call_bridge."""
    row = webui_conn.execute("SELECT chat FROM chat WHERE id = ?", (chat_id,)).fetchone()
    if not row or not row[0]:
        return 0

    if isinstance(row[0], dict):
        chat_obj = row[0]
    elif isinstance(row[0], str):
        try:
            chat_obj = json.loads(row[0])
        except Exception:
            return 0
    else:
        return 0

    if not isinstance(chat_obj, dict):
        return 0

    messages_map = ((chat_obj.get("history") or {}).get("messages") or {})
    if not isinstance(messages_map, dict) or not messages_map:
        return 0

    cb_map: Dict[str, Dict[str, Any]] = {}
    try:
        cur_cb = conn.execute("SELECT call_id, signature, function_name, args_json FROM call_bridge")
        for cb_row in cur_cb.fetchall():
            if cb_row[0]:
                cb_map[str(cb_row[0])] = {
                    "signature": cb_row[1],
                    "name": cb_row[2],
                    "args_json": cb_row[3],
                }
    except Exception:
        pass

    converted = 0
    for msg_id, msg in messages_map.items():
        if not (isinstance(msg, dict) and msg.get("role") == "assistant"):
            continue

        s_row = conn.execute(
            "SELECT full_parts_json, model_id, updated_at FROM message_shadows WHERE message_id = ?",
            (msg_id,),
        ).fetchone()

        raw_json = s_row[0] if s_row else None
        existing_model_id = s_row[1] if s_row else None
        existing_updated_at = s_row[2] if s_row else None

        existing_data = None
        if raw_json:
            try:
                existing_data = json.loads(raw_json)
            except Exception:
                existing_data = None

        is_multi = (
            isinstance(existing_data, list) and
            len(existing_data) > 0 and
            isinstance(existing_data[0], dict) and
            "role" in existing_data[0]
        )

        msg_output = msg.get("output")
        has_fn_output = isinstance(msg_output, list) and any(
            isinstance(it, dict) and it.get("type") in ("function_call", "function_call_output")
            for it in msg_output
        )

        existing_has_tools = False
        if is_multi:
            for m in existing_data:
                if isinstance(m, dict):
                    for p in m.get("parts", []):
                        if isinstance(p, dict) and ("functionCall" in p or "functionResponse" in p):
                            existing_has_tools = True
                            break
                if existing_has_tools:
                    break
        elif isinstance(existing_data, list):
            for p in existing_data:
                if isinstance(p, dict) and ("functionCall" in p or "functionResponse" in p):
                    existing_has_tools = True
                    break

        if has_fn_output and (not is_multi or not existing_has_tools):
            seq: List[Dict[str, Any]] = []
            if is_multi and existing_data:
                for m in existing_data:
                    if isinstance(m, dict):
                        parts = m.get("parts", [])
                        is_cog = False
                        for p in parts:
                            if isinstance(p, dict):
                                fc = p.get("functionCall") or {}
                                fr = p.get("functionResponse") or {}
                                if (
                                    fc.get("name") == "new_cognitive_level" or
                                    fr.get("name") == "new_cognitive_level"
                                ):
                                    is_cog = True
                                    break
                        if is_cog:
                            seq.append(m)

            last_sig: Optional[str] = None
            pending_fc_parts: List[Dict[str, Any]] = []
            pending_fr_parts: List[Dict[str, Any]] = []

            def _flush_fc():
                nonlocal pending_fc_parts
                if pending_fc_parts:
                    for idx_p, fc_p in enumerate(pending_fc_parts):
                        if idx_p > 0:
                            fc_p.pop("thoughtSignature", None)
                    seq.append({"role": "model", "parts": pending_fc_parts})
                    pending_fc_parts = []

            def _flush_fr():
                nonlocal pending_fr_parts
                if pending_fr_parts:
                    seq.append({"role": "user", "parts": pending_fr_parts})
                    pending_fr_parts = []

            for item in msg_output:
                if not isinstance(item, dict):
                    continue
                itype = item.get("type")
                if itype == "reasoning":
                    continue

                if itype == "function_call":
                    _flush_fr()
                    cid = item.get("call_id") or item.get("id")
                    name = item.get("name", "tool")
                    raw_args = item.get("arguments", "{}")
                    if isinstance(raw_args, dict):
                        args = raw_args
                    elif isinstance(raw_args, str):
                        try:
                            args = json.loads(raw_args)
                        except Exception:
                            args = {}
                    else:
                        args = {}

                    sig = cb_map.get(str(cid), {}).get("signature") if cid else None
                    if not sig:
                        sig = "context_engineering_is_the_way_to_go"
                    last_sig = sig

                    fc_payload: Dict[str, Any] = {"name": name, "args": args}
                    if cid:
                        fc_payload["id"] = cid

                    part: Dict[str, Any] = {"functionCall": fc_payload, "thoughtSignature": sig}
                    pending_fc_parts.append(part)

                elif itype == "function_call_output":
                    _flush_fc()
                    cid = item.get("call_id") or item.get("id")
                    raw_out = item.get("output", "")
                    fn_name = cb_map.get(str(cid), {}).get("name") if cid else None
                    if not fn_name:
                        fn_name = item.get("name") or "tool"

                    if isinstance(raw_out, dict):
                        resp_obj = raw_out
                    elif isinstance(raw_out, str):
                        try:
                            parsed = json.loads(raw_out)
                            resp_obj = parsed if isinstance(parsed, dict) else {"result": parsed}
                        except Exception:
                            resp_obj = {"result": str(raw_out)}
                    else:
                        resp_obj = {"result": str(raw_out)}

                    fr_payload: Dict[str, Any] = {"name": fn_name, "response": resp_obj}
                    if cid:
                        fr_payload["id"] = cid

                    pending_fr_parts.append({"functionResponse": fr_payload})

            _flush_fc()
            _flush_fr()

            content_str = msg.get("content", "")
            clean_text = re.sub(r"<think>.*?</think>", "", content_str, flags=re.DOTALL).strip()
            if clean_text:
                text_part: Dict[str, Any] = {"text": clean_text}
                if last_sig:
                    text_part["thoughtSignature"] = last_sig
                seq.append({"role": "model", "parts": [text_part]})

            target_model_id = existing_model_id or msg.get("model")
            target_updated_at = (
                existing_updated_at
                if (existing_updated_at is not None and existing_updated_at > 0)
                else int(msg.get("timestamp") or time.time())
            )
            conn.execute(
                "INSERT OR REPLACE INTO message_shadows "
                "(message_id, role, full_parts_json, model_id, updated_at) "
                "VALUES (?, 'assistant', ?, ?, ?)",
                (msg_id, json.dumps(seq, ensure_ascii=False), target_model_id, target_updated_at),
            )
            converted += 1

        elif (
            isinstance(existing_data, list) and
            existing_data and
            isinstance(existing_data[0], dict) and
            "role" not in existing_data[0]
        ):
            wrapped = json.dumps([{"role": "model", "parts": existing_data}], ensure_ascii=False)
            conn.execute(
                "UPDATE message_shadows SET role = 'assistant', full_parts_json = ? WHERE message_id = ?",
                (wrapped, msg_id),
            )
            converted += 1

    return converted


class EchoDbMissingError(FileNotFoundError):
    """Exception levée lorsqu'une base SQLite attendue est absente du disque."""
    pass


class EchoDbOutdatedError(EchoDbMissingError):
    """Exception levée lorsqu'une base SQLite présente une version antérieure à ECHO_DB_SCHEMA_VERSION."""
    pass


def get_schema_version(conn: sqlite3.Connection) -> int:
    """Lit la version déclarative actuelle de la base via PRAGMA user_version."""
    row = conn.execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def open_db(
    path: str,
    *,
    readonly: bool = False,
    create: bool = False,
    require_version: bool = True,
    timeout: float = 10.0,
) -> sqlite3.Connection:
    """Ouvre une connexion SQLite standardisée avec vérification de version et PRAGMA sécurisés."""
    if not create and not os.path.exists(path):
        raise EchoDbMissingError(f"Base SQLite absente: {path}")

    if create:
        parent_dir = os.path.dirname(os.path.abspath(path))
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

    uri_path = os.path.abspath(path).replace("\\", "/")
    mode = "ro" if readonly else ("rwc" if create else "rw")

    try:
        conn = sqlite3.connect(f"file:{uri_path}?mode={mode}", uri=True, timeout=timeout)
    except sqlite3.OperationalError as e:
        if not create and not os.path.exists(path):
            raise EchoDbMissingError(f"Base SQLite absente: {path}") from e
        raise

    conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
    if not readonly:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = NORMAL")

    if require_version and not create:
        ver = get_schema_version(conn)
        if ver < ECHO_DB_SCHEMA_VERSION:
            conn.close()
            print(f"[ECHO-DB] schema_outdated: {path} (v{ver} < v{ECHO_DB_SCHEMA_VERSION})")
            raise EchoDbOutdatedError(f"Schéma SQLite obsolète pour {path}")

    return conn


def _table_needs_rebuild(conn: sqlite3.Connection, spec: TableSpec) -> bool:
    """Détermine si la table existante diverge de la spécification déclarative (colonnes, types, PK)."""
    cursor = conn.execute(f'PRAGMA table_info("{spec.name}")')
    current_cols = cursor.fetchall()
    # Format current_cols : (cid, name, type, notnull, dflt_value, pk)
    if len(current_cols) != len(spec.columns):
        return True

    for curr, target in zip(current_cols, spec.columns):
        if curr[1] != target.name:
            return True
        if curr[2].upper() != target.sql_type.upper():
            return True
        if bool(curr[3]) != target.notnull:
            return True

    pk_cols = [c[1] for c in sorted([c for c in current_cols if c[5] > 0], key=lambda x: x[5])]
    if tuple(pk_cols) != spec.primary_key:
        return True

    return False


# ==============================================================================
# MOTEUR DE MIGRATION TRANSACTIONNELLE & ENSURE_DB
# ==============================================================================

def _migrate(
    conn: sqlite3.Connection,
    kind: str,
    *,
    dry_run: bool = False,
    is_new: bool = False,
    path: str = "",
) -> Dict[str, Any]:
    """Applique de manière transactionnelle les migrations et le scellement v1 sur une base SQLite.

    Garantit l'atomicité complète par BEGIN IMMEDIATE. En cas de dry_run ou d'erreur,
    un ROLLBACK intégral est appliqué sans modifier les données sur disque.
    """
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    actions: List[str] = []
    rows_dropped: Dict[str, int] = {}
    target_tables = SESSION_TABLES if kind == DB_KIND_SESSION else IDENTITY_TABLES

    try:
        # 1. Étape préliminaire session legacy : rag_index_state, nettoyage Shadows vides et rapatriement templates
        if kind == DB_KIND_SESSION:
            has_shadows = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_shadows'"
            ).fetchone() is not None

            if has_shadows:
                # Création immédiate de rag_index_state si absente
                rag_spec = SESSION_TABLES["rag_index_state"]
                conn.execute(rag_spec.create_sql())
                for idx_sql in rag_spec.index_sql():
                    conn.execute(idx_sql)

                # Migration des messages déjà vectorisés (is_embedded) vers rag_index_state
                cols_info = conn.execute("PRAGMA table_info(message_shadows)").fetchall()
                col_names = {c[1] for c in cols_info}
                ts_col = "updated_at" if "updated_at" in col_names else ("timestamp" if "timestamp" in col_names else "0")
                if "is_embedded" in col_names:
                    conn.execute(
                        "INSERT OR IGNORE INTO rag_index_state(message_id, embedded_at) "
                        f"SELECT message_id, COALESCE({ts_col}, 0) FROM message_shadows "
                        "WHERE is_embedded = 1 AND message_id IS NOT NULL"
                    )

                # Purge chirurgicale des fausses lignes Shadows créées historiquement
                parts_col = "full_parts_json" if "full_parts_json" in col_names else ("parts_json" if "parts_json" in col_names else None)
                if parts_col:
                    del_cur = conn.execute(f"DELETE FROM message_shadows WHERE {parts_col} = '[]'")
                    if del_cur.rowcount > 0:
                        actions.append(f"pruned_empty_shadows:{del_cur.rowcount}")

            # Rapatriement de n8n_template vers identity.db si présent dans session.db
            has_res = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='echo_resources'"
            ).fetchone() is not None
            if has_res:
                cols_res = {c[1] for c in conn.execute("PRAGMA table_info(echo_resources)").fetchall()}
                type_col = "resource_type" if "resource_type" in cols_res else ("type" if "type" in cols_res else None)
                if type_col:
                    tpl_rows = conn.execute(
                        f"SELECT * FROM echo_resources WHERE {type_col} = 'n8n_template'"
                    ).fetchall()
                    if tpl_rows:
                        norm_p = os.path.normpath(path)
                        p_parts = norm_p.split(os.sep)
                        if "chats" in p_parts:
                            c_idx = p_parts.index("chats")
                            u_dir = os.sep.join(p_parts[:c_idx])
                            id_db_path = os.path.join(u_dir, "identity.db")
                            try:
                                ensure_db(id_db_path, DB_KIND_IDENTITY)
                                res_cols_list = [c[1] for c in conn.execute("PRAGMA table_info(echo_resources)").fetchall()]
                                with sqlite3.connect(id_db_path, timeout=10.0) as id_conn:
                                    for r_tuple in tpl_rows:
                                        r_map = dict(zip(res_cols_list, r_tuple))
                                        r_id = r_map.get("id")
                                        r_name = r_map.get("name") or "Template"
                                        r_content = r_map.get("content") or "{}"
                                        r_meta = r_map.get("metadata") or "{}"
                                        r_ts = r_map.get("updated_at") or r_map.get("timestamp") or time.time()
                                        id_conn.execute(
                                            "INSERT OR REPLACE INTO echo_resources "
                                            "(id, name, resource_type, item_type, status, mime, summary, content, metadata, storage_path, git_tracked, message_id, created_at, updated_at) "
                                            "VALUES (?, ?, 'n8n_template', 'file', 'ready', 'application/json', '', ?, ?, '', 0, NULL, ?, ?)",
                                            (r_id, r_name, r_content, r_meta, r_ts, r_ts),
                                        )
                                    id_conn.commit()
                                conn.execute(f"DELETE FROM echo_resources WHERE {type_col} = 'n8n_template'")
                                actions.append(f"migrated_n8n_template:{len(tpl_rows)}")
                            except Exception as e:
                                print(f"[ECHO-DB-SCHEMA] Erreur rapatriement n8n_template: {e}")

        # 2. Synchronisation et reconstruction déclarative des tables cibles
        for spec in target_tables.values():
            has_tbl = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (spec.name,)
            ).fetchone() is not None

            if not has_tbl:
                conn.execute(spec.create_sql())
                actions.append(f"create:{spec.name}")
            else:
                if _table_needs_rebuild(conn, spec):
                    # Reconstruction sécurisée hors-place :
                    # (a) Création de la table temporaire _echo_mig_{nom} avec le schéma cible exact
                    temp_name = f"_echo_mig_{spec.name}"
                    conn.execute(f'DROP TABLE IF EXISTS "{temp_name}"')
                    conn.execute(spec.create_sql(temp_name))

                    # (b) Transfert des données existantes avec mappage d'alias et COALESCE
                    cursor = conn.execute(f'PRAGMA table_info("{spec.name}")')
                    curr_col_names = {c[1] for c in cursor.fetchall()}

                    LEGACY_ALIASES = {
                        "message_shadows": {
                            "full_parts_json": "parts_json",
                            "updated_at": "timestamp",
                        },
                        "echo_resources": {
                            "resource_type": "type",
                            "created_at": "timestamp",
                            "updated_at": "timestamp",
                        },
                        "call_bridge": {
                            "updated_at": "timestamp",
                        },
                    }
                    aliases = LEGACY_ALIASES.get(spec.name, {})

                    select_exprs = []
                    for col in spec.columns:
                        source_col = None
                        if col.name in curr_col_names:
                            source_col = col.name
                        elif col.name in aliases and aliases[col.name] in curr_col_names:
                            source_col = aliases[col.name]

                        if source_col:
                            if col.notnull:
                                if col.default_sql is not None:
                                    select_exprs.append(f'COALESCE("{source_col}", {col.default_sql})')
                                elif col.sql_type.upper() in ("INTEGER", "REAL"):
                                    select_exprs.append(f'COALESCE("{source_col}", 0)')
                                else:
                                    select_exprs.append(f'COALESCE("{source_col}", \'\')')
                            else:
                                select_exprs.append(f'"{source_col}"')
                        else:
                            if col.notnull and col.default_sql is None:
                                if col.sql_type.upper() in ("INTEGER", "REAL"):
                                    select_exprs.append("0")
                                elif col.name == "status":
                                    select_exprs.append("'ready'")
                                else:
                                    select_exprs.append("''")
                            else:
                                select_exprs.append(str(col.default_sql or "NULL"))

                    col_names_str = ", ".join(f'"{c.name}"' for c in spec.columns)
                    select_exprs_str = ", ".join(select_exprs)

                    conn.execute(
                        f'INSERT OR REPLACE INTO "{temp_name}" ({col_names_str}) '
                        f'SELECT {select_exprs_str} FROM "{spec.name}"'
                    )
                    # (c) Remplacement atomique de l'ancienne table par la nouvelle
                    conn.execute(f'DROP TABLE "{spec.name}"')
                    conn.execute(f'ALTER TABLE "{temp_name}" RENAME TO "{spec.name}"')
                    actions.append(f"rebuild:{spec.name}")

        # 3. Création systématique de tous les index déclaratifs
        for spec in target_tables.values():
            for idx_sql in spec.index_sql():
                conn.execute(idx_sql)

        # 4. Traitements relationnels spécifiques session.db
        if kind == DB_KIND_SESSION:
            has_cs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cognitive_signatures'"
            ).fetchone() is not None

            if has_cs:
                conn.execute("""
                    UPDATE message_shadows
                    SET model_id = (
                        SELECT cs.model_id FROM cognitive_signatures cs
                        WHERE cs.message_id = message_shadows.message_id
                          AND cs.model_id IS NOT NULL AND cs.model_id != ''
                        ORDER BY cs.updated_at DESC LIMIT 1
                    )
                    WHERE model_id IS NULL
                """)

                cur_cs_sig = conn.execute(
                    "SELECT message_id, thought_signature FROM cognitive_signatures "
                    "WHERE message_id IS NOT NULL AND thought_signature IS NOT NULL AND thought_signature != '' "
                    "ORDER BY updated_at ASC"
                )
                cs_sigs: Dict[str, str] = {}
                for mid, tsig in cur_cs_sig.fetchall():
                    cs_sigs[str(mid)] = str(tsig)

                if cs_sigs:
                    def _attach_sig_gemini3(parts_list: List[Any], sig: str) -> bool:
                        for p in parts_list:
                            if isinstance(p, dict) and "functionCall" in p:
                                p["thoughtSignature"] = sig
                                return True
                        for p in parts_list:
                            if isinstance(p, dict) and "text" in p:
                                p["thoughtSignature"] = sig
                                return True
                        return False

                    cur_sh = conn.execute(
                        "SELECT message_id, full_parts_json FROM message_shadows "
                        "WHERE role IN ('assistant', 'model') AND message_id IS NOT NULL AND full_parts_json IS NOT NULL"
                    )
                    for mid, raw_parts in cur_sh.fetchall():
                        mid_str = str(mid)
                        if mid_str in cs_sigs and raw_parts and "thoughtSignature" not in raw_parts:
                            try:
                                p_data = json.loads(raw_parts)
                                sig_val = cs_sigs[mid_str]
                                modified = False
                                if isinstance(p_data, list):
                                    if p_data and isinstance(p_data[0], dict) and "role" in p_data[0]:
                                        for m in p_data:
                                            if m.get("role") == "model":
                                                if _attach_sig_gemini3(m.get("parts", []), sig_val):
                                                    modified = True
                                                    break
                                    else:
                                        if _attach_sig_gemini3(p_data, sig_val):
                                            modified = True
                                if modified:
                                    conn.execute(
                                        "UPDATE message_shadows SET full_parts_json = ? WHERE message_id = ?",
                                        (json.dumps(p_data, ensure_ascii=False), mid),
                                    )
                            except Exception:
                                pass

            # Normalise toute ligne role IN ('assistant', 'model') de message_shadows
            cur_norm = conn.execute(
                "SELECT message_id, full_parts_json FROM message_shadows "
                "WHERE role IN ('assistant', 'model') AND full_parts_json IS NOT NULL"
            )
            for mid, raw_parts in cur_norm.fetchall():
                try:
                    p_data = json.loads(raw_parts)
                    if isinstance(p_data, list) and p_data and isinstance(p_data[0], dict) and "role" not in p_data[0]:
                        normalized = json.dumps([{"role": "model", "parts": p_data}], ensure_ascii=False)
                        conn.execute(
                            "UPDATE message_shadows SET role = 'assistant', full_parts_json = ? WHERE message_id = ?",
                            (normalized, mid),
                        )
                except Exception:
                    pass

            conn.execute("UPDATE message_shadows SET role = 'assistant' WHERE role = 'model'")

            # Rétro-remplissage de call_bridge.message_id depuis message_shadows (assistant/model)
            cur_shadows = conn.execute(
                "SELECT message_id, full_parts_json FROM message_shadows "
                "WHERE role IN ('assistant', 'model') AND message_id IS NOT NULL AND full_parts_json IS NOT NULL"
            )
            for msg_id, raw_json in cur_shadows.fetchall():
                call_ids = extract_call_ids_from_json(raw_json)
                for cid in call_ids:
                    conn.execute(
                        "UPDATE call_bridge SET message_id = ? WHERE call_id = ? AND message_id IS NULL",
                        (msg_id, cid),
                    )

            # Rétro-remplissage de call_bridge.message_id depuis tool_journal JOIN cognitive_signatures si existants
            has_tj = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tool_journal'"
            ).fetchone() is not None
            if has_cs and has_tj:
                cur_tj = conn.execute(
                    "SELECT cs.message_id, tj.io_json FROM tool_journal tj "
                    "JOIN cognitive_signatures cs ON cs.cumulative_hash = tj.cumulative_hash "
                    "WHERE cs.message_id IS NOT NULL AND tj.io_json IS NOT NULL"
                )
                for msg_id, raw_json in cur_tj.fetchall():
                    call_ids = extract_call_ids_from_json(raw_json)
                    for cid in call_ids:
                        conn.execute(
                            "UPDATE call_bridge SET message_id = ? WHERE call_id = ? AND message_id IS NULL",
                            (msg_id, cid),
                        )

            # Rétro-remplissage depuis webui.db si disponible
            norm_p = os.path.normpath(path)
            p_parts = norm_p.split(os.sep)
            if "chats" in p_parts:
                c_idx = p_parts.index("chats")
                u_dir = os.sep.join(p_parts[:c_idx])
                chat_id = p_parts[c_idx + 1] if len(p_parts) > c_idx + 1 else ""
                if chat_id:
                    cand1 = os.path.join(os.path.dirname(os.path.dirname(u_dir)), "webui.db")
                    cand2 = os.path.join(os.path.dirname(u_dir), "webui.db")
                    webui_db_path = cand1 if os.path.exists(cand1) else cand2
                    if os.path.exists(webui_db_path):
                        try:
                            wuri = os.path.abspath(webui_db_path).replace("\\", "/")
                            with sqlite3.connect(f"file:{wuri}?mode=ro", uri=True, timeout=5.0) as wconn:
                                bf_count = backfill_session_shadows_from_webui(conn, chat_id, wconn)
                                if bf_count > 0:
                                    actions.append(f"backfill_shadows:{bf_count}")
                        except Exception as e:
                            print(f"[ECHO-DB-SCHEMA] Erreur backfill shadows depuis webui.db: {e}")

        # 5. Purge des tables obsolètes
        for obs in OBSOLETE_TABLES.get(kind, ()):
            has_obs = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (obs,)
            ).fetchone() is not None
            if has_obs:
                count_row = conn.execute(f'SELECT COUNT(*) FROM "{obs}"').fetchone()
                cnt = int(count_row[0]) if count_row else 0
                rows_dropped[obs] = cnt
                conn.execute(f'DROP TABLE IF EXISTS "{obs}"')
                actions.append(f"drop:{obs}")

        # 6. Scellement de la version du schéma
        conn.execute(f"PRAGMA user_version = {ECHO_DB_SCHEMA_VERSION}")

        # 7. Finalisation transactionnelle
        if dry_run:
            conn.execute("ROLLBACK")
        else:
            conn.execute("COMMIT")

        return {"actions": actions, "rows_dropped": rows_dropped}

    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise


def ensure_db(path: str, kind: str, *, dry_run: bool = False) -> Dict[str, Any]:
    """Assure l'intégrité et la mise à niveau d'une base SQLite (session.db ou identity.db)."""
    if kind not in (DB_KIND_SESSION, DB_KIND_IDENTITY):
        raise ValueError(f"Type de base de données invalide : {kind}")

    if dry_run and not os.path.exists(path):
        return {
            "path": path,
            "kind": kind,
            "status": "created",
            "actions": ["create_all"],
            "rows_dropped": {},
        }

    existed_before = os.path.exists(path)
    target_specs = SESSION_TABLES if kind == DB_KIND_SESSION else IDENTITY_TABLES
    obsolete_names = set(OBSOLETE_TABLES.get(kind, ()))

    conn: Optional[sqlite3.Connection] = None
    try:
        if existed_before:
            # Chemin rapide : vérifier si la base est déjà conforme sans migration
            conn = open_db(path, readonly=False, create=False, require_version=False, timeout=10.0)
            ver = get_schema_version(conn)
            if ver >= ECHO_DB_SCHEMA_VERSION:
                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                existing_tables = {row[0] for row in cur.fetchall()}
                target_names = set(target_specs.keys())
                if target_names.issubset(existing_tables) and not (obsolete_names & existing_tables):
                    return {
                        "path": path,
                        "kind": kind,
                        "status": "up_to_date",
                        "actions": [],
                        "rows_dropped": {},
                    }
        else:
            conn = open_db(path, readonly=False, create=True, require_version=False, timeout=10.0)

        # Migration transactionnelle requise
        mig_res = _migrate(conn, kind, dry_run=dry_run, is_new=not existed_before, path=path)
        status = "created" if not existed_before else ("migrated" if (mig_res["actions"] or mig_res["rows_dropped"]) else "up_to_date")
        return {
            "path": path,
            "kind": kind,
            "status": status,
            "actions": mig_res["actions"],
            "rows_dropped": mig_res["rows_dropped"],
        }
    except sqlite3.OperationalError as e:
        err_msg = str(e).lower()
        if "locked" in err_msg or "busy" in err_msg:
            return {
                "path": path,
                "kind": kind,
                "status": "locked",
                "actions": [],
                "rows_dropped": {},
                "error": str(e),
            }
        return {
            "path": path,
            "kind": kind,
            "status": "error",
            "actions": [],
            "rows_dropped": {},
            "error": str(e),
        }
    except Exception as e:
        return {
            "path": path,
            "kind": kind,
            "status": "error",
            "actions": [],
            "rows_dropped": {},
            "error": str(e),
        }
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ==============================================================================
# EXPLORATION SYSTÈME & CLI
# ==============================================================================

def iter_echo_databases(users_root: str = ECHO_USERS_ROOT) -> Iterator[Tuple[str, str]]:
    """Parcourt l'infrastructure users_root et yield (chemin, kind) pour chaque base identity.db et session.db."""
    if not os.path.isdir(users_root):
        return

    for user_entry in os.scandir(users_root):
        if not user_entry.is_dir() or user_entry.name.startswith("."):
            continue

        identity_path = os.path.join(user_entry.path, "identity.db")
        if os.path.exists(identity_path):
            yield (identity_path, DB_KIND_IDENTITY)

        chats_dir = os.path.join(user_entry.path, "chats")
        if os.path.isdir(chats_dir):
            for chat_entry in os.scandir(chats_dir):
                if not chat_entry.is_dir() or chat_entry.name.startswith("."):
                    continue
                session_path = os.path.join(chat_entry.path, "session.db")
                if os.path.exists(session_path):
                    yield (session_path, DB_KIND_SESSION)


def migrate_all(users_root: str = ECHO_USERS_ROOT, dry_run: bool = False) -> Dict[str, Any]:
    """Migre ou valide exhaustivement l'ensemble des bases de données de l'infrastructure ECHO."""
    details: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {
        "created": 0,
        "migrated": 0,
        "up_to_date": 0,
        "locked": 0,
        "error": 0,
    }
    rows_dropped_total = 0

    for path, kind in iter_echo_databases(users_root):
        res = ensure_db(path, kind, dry_run=dry_run)
        details.append(res)
        st = res.get("status", "error")
        if st in counts:
            counts[st] += 1
        else:
            counts["error"] += 1
        for count in res.get("rows_dropped", {}).values():
            rows_dropped_total += count

    return {
        "dry_run": dry_run,
        "users_root": users_root,
        "total": len(details),
        "counts": counts,
        "migrated": counts.get("migrated", 0),
        "created": counts.get("created", 0),
        "up_to_date": counts.get("up_to_date", 0),
        "error": counts.get("error", 0),
        "locked": counts.get("locked", 0),
        "rows_dropped_total": rows_dropped_total,
        "details": details,
    }


def cli(argv: Optional[List[str]] = None) -> int:
    """Point d'entrée CLI pour la commande de migration des bases SQLite ECHO."""
    parser = argparse.ArgumentParser(description="Migration et vérification du schéma SQLite déclaratif ECHO.")
    parser.add_argument("--dry-run", action="store_true", help="Simule les migrations sans les appliquer.")
    parser.add_argument("--users-root", default=ECHO_USERS_ROOT, help="Chemin vers le dossier users.")
    args = parser.parse_args(argv)

    rep = migrate_all(users_root=args.users_root, dry_run=args.dry_run)
    print(json.dumps(rep, indent=2, ensure_ascii=False))
    return 0 if (rep["counts"]["error"] == 0 and rep["counts"]["locked"] == 0) else 1


if __name__ == "__main__":
    sys.exit(cli())
