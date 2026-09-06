# -*- coding: utf-8 -*-
"""
title: ECHO Echo State Manager
author: Wilfried BARNAVON
version: 1.1
description: Gestionnaire d'état SQLite et RAG.
# Historique des versions :
# 1.1: Correction du get_agy_endpoint pour fallback sur l'URL de secours (1) au lieu de 0 en cas de verrouillage global.
"""
import os
import time
import sqlite3
import shutil
import hashlib
import orjson as json
import orjson as std_json
from datetime import datetime
from typing import Any, List, Optional
from echo_paths import get_echo_global_path, get_echo_session_path, resolve_upload_file_path
from echo_constants import ECHO_GLOBAL_DOMAINS, ECHO_SESSION_DOMAINS, ECHO_UPLOADS_TRANSIT_DIR, ECHO_USERS_ROOT

class EchoStateManager:
    def __init__(self, user_id: str = "system", chat_id: Optional[str] = None):
        if user_id and "/" in str(user_id): self.user_id = "system"
        else: self.user_id = user_id
        self.chat_id = chat_id
        safe_uid = "".join(x for x in str(self.user_id) if x.isalnum() or x in "-_")
        self.user_dir = os.path.join(ECHO_USERS_ROOT, safe_uid)
        
        if chat_id:
            for domain in ECHO_SESSION_DOMAINS:
                if domain != "db":
                    os.makedirs(get_echo_session_path(self.user_id, self.chat_id, domain), exist_ok=True)
            self.db_path = get_echo_session_path(self.user_id, self.chat_id, "db")
        else:
            for domain in ECHO_GLOBAL_DOMAINS:
                os.makedirs(get_echo_global_path(self.user_id, domain), exist_ok=True)
            self.db_path = os.path.join(self.user_dir, "identity.db")
            
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL;"); return conn

    def _init_db(self):
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS suture_index (cumulative_hash TEXT PRIMARY KEY, chat_id TEXT NOT NULL, invariant_hash TEXT NOT NULL, parent_hash TEXT, message_id TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS rich_payloads (invariant_hash TEXT PRIMARY KEY, rich_parts_json TEXT NOT NULL, message_id TEXT, created_at INTEGER)")   
                # Table 'message_shadows' : Métadonnées Spécifiques à Gemini
                # Nom conservé pour compatibilité avec les bases de données en production.
                # Stocke les métadonnées propres à l'API Gemini (thoughtSignature, usageMetadata,
                # candidateIndex) nécessaires à la Suture Bit-Perfect des Métadonnées Gemini.
                conn.execute("CREATE TABLE IF NOT EXISTS message_shadows (message_id TEXT PRIMARY KEY, chat_id TEXT NOT NULL, role TEXT NOT NULL, full_parts_json TEXT NOT NULL, updated_at INTEGER)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_shadow_chat_id ON message_shadows (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_chat_id ON suture_index (chat_id)")
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_signatures (cumulative_hash TEXT PRIMARY KEY, thought_signature TEXT NOT NULL, message_id TEXT, model_id TEXT, updated_at INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS tool_journal (cumulative_hash TEXT PRIMARY KEY, io_json TEXT NOT NULL, updated_at INTEGER)")

                try: conn.execute("ALTER TABLE message_shadows ADD COLUMN is_embedded INTEGER DEFAULT 0")
                except: pass

                conn.execute("CREATE TABLE IF NOT EXISTS processed_files (chat_id TEXT, file_id TEXT, filename TEXT, mime TEXT, mode TEXT, timestamp INTEGER, file_content TEXT, message_id TEXT, PRIMARY KEY (chat_id, file_id))")
                conn.execute("CREATE TABLE IF NOT EXISTS call_bridge (call_id TEXT PRIMARY KEY, signature TEXT NOT NULL, function_name TEXT NOT NULL, args_json TEXT, timestamp INTEGER)")
                conn.execute("CREATE TABLE IF NOT EXISTS context_stats (id INTEGER PRIMARY KEY CHECK (id = 1), data TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS auth_data (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("CREATE TABLE IF NOT EXISTS session_state (id INTEGER PRIMARY KEY, last_model_id TEXT, updated_at INTEGER NOT NULL)")
                # Préférences Pipe propagées aux outils (politique modèle, crédits)
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")

                # Echos Skills & Cognitive Council (V9)
                conn.execute("CREATE TABLE IF NOT EXISTS cognitive_threads (sub_sid TEXT, chat_id TEXT NOT NULL, role_id TEXT NOT NULL, step_index INTEGER, role TEXT NOT NULL, content_json TEXT NOT NULL, thought_signature TEXT, updated_at INTEGER, PRIMARY KEY (sub_sid, step_index))")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_thread_chat ON cognitive_threads (chat_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_thread_sid ON cognitive_threads (sub_sid)")

                # Strategic Planner (v5.159)
                conn.execute("""CREATE TABLE IF NOT EXISTS plans (
                    plan_id      TEXT PRIMARY KEY,
                    filename     TEXT NOT NULL,
                    goal         TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'draft',
                    author_model TEXT,
                    created_at   INTEGER NOT NULL,
                    updated_at   INTEGER NOT NULL
                )""")

                # ECHO Codex (v5.165)
                conn.execute("""CREATE TABLE IF NOT EXISTS codex_docs (
                    filename     TEXT PRIMARY KEY,
                    language     TEXT NOT NULL DEFAULT 'plaintext',
                    lines        INTEGER NOT NULL DEFAULT 0,
                    last_commit  TEXT,
                    commit_msg   TEXT,
                    created_at   INTEGER NOT NULL,
                    updated_at   INTEGER NOT NULL
                )""")

                # Registre Unifié (v8.0) — Remplace à terme processed_files, plans, codex_docs
                conn.execute("""CREATE TABLE IF NOT EXISTS echo_resources (
                    id            TEXT PRIMARY KEY,
                    name          TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    mime          TEXT,
                    status        TEXT NOT NULL,
                    summary       TEXT,
                    storage_path  TEXT,
                    git_tracked   INTEGER DEFAULT 0,
                    message_id    TEXT,
                    plan_goal     TEXT,
                    author_model  TEXT,
                    language      TEXT,
                    lines         INTEGER,
                    last_commit   TEXT,
                    commit_msg    TEXT,
                    created_at    INTEGER NOT NULL,
                    updated_at    INTEGER NOT NULL
                )""")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_res_type ON echo_resources (resource_type)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_res_updated ON echo_resources (updated_at)")

                # MIGRATION : Ajout des colonnes manquantes si nécessaire
                try: conn.execute("ALTER TABLE rich_payloads ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN message_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE cognitive_signatures ADD COLUMN model_id TEXT")
                except: pass
                try: conn.execute("ALTER TABLE suture_index ADD COLUMN message_id TEXT")
                except: pass

                conn.commit()
        except Exception as e: print(f"[EchoStateManager] Init DB Error: {e}")

    def save_message_shadow(self, message_id: str, chat_id: str, role: str, parts: List[dict], updated_at: Optional[int] = None):
        if not message_id: return
        ts = updated_at if updated_at is not None else int(time.time())
        try:
            with self._get_connection() as conn:
                conn.execute("""
                    INSERT INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at) 
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(message_id) DO UPDATE SET
                        chat_id = excluded.chat_id,
                        role = excluded.role,
                        full_parts_json = excluded.full_parts_json,
                        updated_at = excluded.updated_at
                """, (message_id, chat_id, role, json.dumps(parts).decode('utf-8'), ts))
                conn.commit()
        except Exception as e:
            print(f"[ECHO_DEBUG_FATAL] Erreur écriture save_message_shadow: {e}")

    def get_message_shadow(self, message_id: str, updated_at: int) -> Optional[List[dict]]:
        if not message_id: return None
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT full_parts_json FROM message_shadows WHERE message_id = ? AND updated_at = ?", (message_id, int(updated_at))).fetchone()
                if row: return std_json.loads(row[0])
        except Exception as e:
            print(f"[ECHO_DEBUG_FATAL] Erreur lecture get_message_shadow: {e}")
        return None

    def calculate_invariant_hash(self, role: str, content: Any, tool_io: dict = None) -> str:
        norm_c = content.strip() if isinstance(content, str) else json.dumps(content, option=json.OPT_SORT_KEYS).decode('utf-8')
        norm_t = json.dumps(tool_io, option=json.OPT_SORT_KEYS).decode('utf-8') if tool_io else ""
        return hashlib.sha256(f"{role.lower()}|{norm_c}|{norm_t}".encode("utf-8")).hexdigest()

    def calculate_cumulative_hash(self, inv: str, parent: str = None) -> str:
        return hashlib.sha256(f"{inv}|{parent or ''}".encode("utf-8")).hexdigest()

    def get_session_registry(self, chat_id: str, active_message_ids: Optional[List[str]] = None) -> dict:
        reg = {}
        try:
            with self._get_connection() as conn:
                if active_message_ids:
                    placeholders = ','.join('?' for _ in active_message_ids)
                    query = f"SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ? AND message_id IN ({placeholders})"
                    rows = conn.execute(query, [chat_id] + active_message_ids).fetchall()
                else:
                    rows = conn.execute("SELECT filename, file_id, mime, mode FROM processed_files WHERE chat_id = ?", (chat_id,)).fetchall()
                for row in rows: reg[row[0]] = {"id": row[1], "mime": row[2] or "application/octet-stream", "statut": row[3] or "unknown"}
        except: pass
        return reg

    def mark_processed(self, chat_id: str, file_id: str, filename: str, mime: str, mode: str, content: Optional[str] = None, message_id: Optional[str] = None): 
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO processed_files (chat_id, file_id, filename, mime, mode, timestamp, file_content, message_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (chat_id, file_id, filename, mime, mode, int(time.time()), content, message_id))
                conn.commit()
        except: pass

    def save_call_bridge(self, call_id: str, signature: str, function_name: str, args: dict = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO call_bridge (call_id, signature, function_name, args_json, timestamp) VALUES (?, ?, ?, ?, ?)", (call_id, signature, function_name, json.dumps(args).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_call_bridge(self, call_id: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT signature, function_name, args_json FROM call_bridge WHERE call_id = ?", (call_id,)).fetchone()
                if row: return {"signature": row[0], "name": row[1], "args": std_json.loads(row[2]) if row[2] else {}}
        except: pass
        return None

    def get_rich_payload(self, inv: str) -> Optional[List[dict]]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT rich_parts_json FROM rich_payloads WHERE invariant_hash = ?", (inv,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_rich_payload(self, inv: str, rich: List[dict], message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO rich_payloads (invariant_hash, rich_parts_json, message_id, created_at) VALUES (?, ?, ?, ?)", (inv, json.dumps(rich).decode('utf-8'), message_id, int(time.time())))
                conn.commit()
        except: pass

    def index_suture(self, cumul: str, chat_id: str, inv: str, parent: str = None, message_id: str = None):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO suture_index (cumulative_hash, chat_id, invariant_hash, parent_hash, message_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)", (cumul, chat_id, inv, parent, message_id, int(time.time())))
                conn.commit()
        except: pass

    def save_cognitive_data(self, cumul: str, sig: str = None, thought: str = None, tool_io: dict = None, message_id: str = None, model_id: str = None):        
        try:
            with self._get_connection() as conn:
                if sig:
                    conn.execute("INSERT OR REPLACE INTO cognitive_signatures (cumulative_hash, thought_signature, message_id, model_id, updated_at) VALUES (?, ?, ?, ?, ?)", (cumul, sig, message_id, model_id, int(time.time())))
                # thought_archive retiré (write-only, jamais lu — table purgée au prochain rebuild-echo)
                if tool_io: conn.execute("INSERT OR REPLACE INTO tool_journal (cumulative_hash, io_json, updated_at) VALUES (?, ?, ?)", (cumul, json.dumps(tool_io).decode('utf-8'), int(time.time())))
                if model_id:
                    conn.execute("INSERT OR REPLACE INTO session_state (id, last_model_id, updated_at) VALUES (1, ?, ?)", (model_id, int(time.time())))
                conn.commit()
        except: pass

    def get_signature_by_id(self, message_id: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE message_id = ?", (message_id,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def save_signature_by_id(self, message_id: str, signature: str):
        try:
            with self._get_connection() as conn:
                # Cette méthode est un fallback si le cumulative hash n'est pas encore connu
                # On utilise un hash factice ou on met à jour par ID si la ligne existe
                conn.execute("UPDATE cognitive_signatures SET thought_signature = ? WHERE message_id = ?", (signature, message_id))
                conn.commit()
        except: pass

    def get_last_active_model(self) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT last_model_id FROM session_state WHERE id = 1").fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_thought_signature(self, cumul: str) -> Optional[str]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT thought_signature FROM cognitive_signatures WHERE cumulative_hash = ?", (cumul,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_tool_io(self, cumul: str) -> Optional[dict]:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT io_json FROM tool_journal WHERE cumulative_hash = ?", (cumul,)).fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return None

    def save_auth_data(self, key: str, value: str):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO auth_data (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def save_setting(self, key: str, value: str):
        """Persiste une préférence Pipe dans echo_settings (identity.db)."""
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                conn.execute("INSERT OR REPLACE INTO echo_settings (key, value, updated_at) VALUES (?, ?, ?)", (key, value, int(time.time())))
                conn.commit()
        except: pass

    def get_setting(self, key: str) -> Optional[str]:
        """Lit une préférence Pipe depuis echo_settings (identity.db)."""
        try:
            with self._get_connection() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS echo_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)")
                row = conn.execute("SELECT value FROM echo_settings WHERE key = ?", (key,)).fetchone()
                return row[0] if row else None
        except: pass
        return None

    def get_agy_endpoint(self) -> tuple[int, str]:
        """Analyse les verrous temporels de chaque URL. Retourne (index, url_saine)."""
        from echo_constants import AGY_BASE_URLS
        from datetime import datetime, timezone
        
        now = datetime.now(timezone.utc).isoformat()
        for idx, url in enumerate(AGY_BASE_URLS):
            reset_time = self.get_setting(f"agy_locked_url_{idx}")
            if not reset_time or now > reset_time:
                return idx, url # URL saine
                
        # Si tout est bloqué, on privilégie l'URL de secours (Canary) par défaut
        # pour permettre à la boucle de backoff d'opérer sur l'environnement secondaire
        # plutôt que de provoquer un rebond immédiat vers Prod (0).
        fallback_idx = len(AGY_BASE_URLS) - 1
        return fallback_idx, AGY_BASE_URLS[fallback_idx]

    def lock_agy_endpoint(self, idx: int, reset_time_utc: str):
        """Verrouille une URL jusqu'à son reset_time."""
        self.save_setting(f"agy_locked_url_{idx}", reset_time_utc)

    def unlock_agy_endpoint(self, idx: int):
        """Déverrouille proactivement une URL sur un succès HTTP (Auto-Heal)."""
        self.save_setting(f"agy_locked_url_{idx}", "")

    def save_context_stats(self, stats: dict):
        try:
            with self._get_connection() as conn:
                conn.execute("INSERT OR REPLACE INTO context_stats (id, data, updated_at) VALUES (1, ?, ?)", (std_json.dumps(stats).decode('utf-8'), int(time.time())))
                conn.commit()
        except: pass

    def get_last_context_stats(self) -> dict:
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT data FROM context_stats WHERE id = 1").fetchone()
                if row: return std_json.loads(row[0])
        except: pass
        return {}

    def move_to_vault(self, file_id: str, filename: str) -> bool:
        old_path = resolve_upload_file_path(self.user_id, file_id, chat_id=self.chat_id)
        if not old_path: return False
        new_path = os.path.join(get_echo_session_path(self.user_id, self.chat_id, "files"), os.path.basename(old_path))
        try:
            if not os.path.exists(new_path):
                if ECHO_UPLOADS_TRANSIT_DIR in old_path:
                    shutil.move(old_path, new_path)
                else:
                    shutil.copy2(old_path, new_path)
            return True
        except: return False

    def save_thread_step(self, sub_sid: str, chat_id: str, role_id: str, step_index: int, role: str, content: List[dict], signature: Optional[str] = None):
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO cognitive_threads (sub_sid, chat_id, role_id, step_index, role, content_json, thought_signature, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (sub_sid, chat_id, role_id, step_index, role, json.dumps(content).decode('utf-8'), signature, int(time.time()))
                )
                conn.commit()
        except: pass

    def get_thread_history(self, sub_sid: str) -> List[dict]:
        history = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT role, content_json, thought_signature FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,)
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[1])
                    item = {"role": row[0], "parts": parts}
                    if row[2]: # Si on a une thoughtSignature, on l'injecte dans la première part
                        if parts and isinstance(parts, list):
                            if "functionCall" in parts[0] or "text" in parts[0]:
                                parts[0]["thoughtSignature"] = row[2]
                    history.append(item)
        except: pass
        return history

    def get_thread_steps_enriched(self, sub_sid: str) -> List[dict]:
        """Retourne les steps enrichis d'un thread (role, role_id, parts, timestamp).
        Utilisé par le Sub-Agent Monitor pour reconstruire l'arbre d'appels."""
        steps = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT step_index, role, role_id, content_json, updated_at "
                    "FROM cognitive_threads WHERE sub_sid = ? ORDER BY step_index ASC",
                    (sub_sid,)
                ).fetchall()
                for row in rows:
                    parts = json.loads(row[3])
                    steps.append({
                        "index": row[0],
                        "role": row[1],
                        "role_id": row[2],
                        "parts": parts,
                        "timestamp": row[4]
                    })
        except: pass
        return steps


    def list_threads(self, chat_id: str) -> List[dict]:
        threads = []
        try:
            with self._get_connection() as conn:
                # On récupère le dernier message de chaque thread pour avoir un résumé
                rows = conn.execute(
                    "SELECT sub_sid, role_id, MAX(step_index), content_json, updated_at FROM cognitive_threads WHERE chat_id = ? GROUP BY sub_sid ORDER BY updated_at DESC",
                    (chat_id,)
                ).fetchall()
                for row in rows:
                    content = json.loads(row[3])
                    summary = content[0].get("text", "")[:100] if content and "text" in content[0] else "Appel de fonction..."
                    threads.append({
                        "sub_sid": row[0],
                        "role_id": row[1],
                        "last_step": row[2],
                        "summary": summary,
                        "updated_at": row[4]
                    })
        except: pass
        return threads

    def delete_thread(self, sub_sid: str):
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM cognitive_threads WHERE sub_sid = ?", (sub_sid,))
                conn.commit()
        except: pass

    # ==========================================================================
    # STRATEGIC PLANNER — Registre des Plans
    # ==========================================================================

    def save_plan_record(self, plan_id: str, filename: str, goal: str,
                         status: str, author_model: str = None):
        """Enregistre un nouveau plan dans le registre de contrôle du chat."""
        ts = time.time()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO plans "
                    "(plan_id, filename, goal, status, author_model, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (plan_id, filename, goal, status, author_model, ts, ts)
                )
                conn.commit()
        except: pass

    def update_plan_record_status(self, plan_id: str, status: str):
        """Met à jour uniquement le statut d'un plan (préserve created_at)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE plans SET status = ?, updated_at = ? WHERE plan_id = ?",
                    (status, time.time(), plan_id)
                )
                conn.commit()
        except: pass

    def delete_plan_record(self, plan_id: str):
        """Supprime un plan du registre de contrôle."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM plans WHERE plan_id = ?", (plan_id,))
                conn.commit()
        except: pass

    def get_plans(self) -> List[dict]:
        """Retourne tous les plans du chat (pour injection dans registre_plan)."""
        plans = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT plan_id, filename, goal, status, author_model, created_at "
                    "FROM plans ORDER BY created_at DESC"
                ).fetchall()
                for row in rows:
                    plans.append({
                        "plan_id": row[0], "filename": row[1], "goal": row[2],
                        "status": row[3], "author_model": row[4], "created_at": row[5]
                    })
        except: pass
        return plans

    # --- ECHO CODEX ---

    def save_codex_record(self, filename: str, language: str, lines: int,
                          last_commit: str, commit_msg: str):
        """Enregistre ou met à jour un document Codex dans le registre."""
        ts = time.time()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO codex_docs (filename, language, lines, last_commit, commit_msg, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(filename) DO UPDATE SET language=?, lines=?, last_commit=?, commit_msg=?, updated_at=?",
                    (filename, language, lines, last_commit, commit_msg, ts, ts,
                     language, lines, last_commit, commit_msg, ts)
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] save_codex_record error: {e}")

    def delete_codex_record(self, filename: str):
        """Supprime un document du registre Codex."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM codex_docs WHERE filename = ?", (filename,))
                conn.commit()
        except: pass

    def get_codex_docs(self) -> List[dict]:
        """Retourne tous les documents Codex du chat (pour injection dans registre_codex)."""
        docs = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    "SELECT filename, language, lines, last_commit, commit_msg "
                    "FROM codex_docs ORDER BY updated_at DESC"
                ).fetchall()
                for row in rows:
                    docs.append({
                        "filename": row[0], "language": row[1], "lines": row[2],
                        "last_commit": row[3], "commit_msg": row[4]
                    })
        except: pass
        return docs

    def clear_codex_records(self):
        """Purge tous les documents Codex du chat (appelé par reset_all)."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM codex_docs")
                conn.commit()
        except: pass

    # ==========================================================================
    # REGISTRE UNIFIÉ — echo_resources (v8.0)
    # ==========================================================================

    def save_resource(self, id: str, name: str, resource_type: str, status: str,
                      mime: str = None, summary: str = None, storage_path: str = None,
                      git_tracked: bool = False, message_id: str = None,
                      plan_goal: str = None, author_model: str = None,
                      language: str = None, lines: int = None,
                      last_commit: str = None, commit_msg: str = None):
        """Crée ou met à jour une ressource dans le registre unifié.
        Utilise INSERT ... ON CONFLICT pour préserver created_at lors des mises à jour.
        """
        ts = time.time()
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "INSERT INTO echo_resources "
                    "(id, name, resource_type, mime, status, summary, storage_path, "
                    "git_tracked, message_id, plan_goal, author_model, language, lines, "
                    "last_commit, commit_msg, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "name=excluded.name, resource_type=excluded.resource_type, "
                    "mime=excluded.mime, status=excluded.status, summary=excluded.summary, "
                    "storage_path=excluded.storage_path, git_tracked=excluded.git_tracked, "
                    "message_id=COALESCE(excluded.message_id, echo_resources.message_id), "
                    "plan_goal=excluded.plan_goal, author_model=excluded.author_model, "
                    "language=excluded.language, lines=excluded.lines, "
                    "last_commit=excluded.last_commit, commit_msg=excluded.commit_msg, "
                    "updated_at=excluded.updated_at",
                    (id, name, resource_type, mime, status, summary, storage_path,
                     1 if git_tracked else 0, message_id, plan_goal, author_model,
                     language, lines, last_commit, commit_msg, ts, ts)
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] save_resource error: {e}")

    def update_resource_status(self, id: str, status: str):
        """Met à jour le statut d'une ressource (préserve les autres champs)."""
        try:
            with self._get_connection() as conn:
                conn.execute(
                    "UPDATE echo_resources SET status = ?, updated_at = ? WHERE id = ?",
                    (status, time.time(), id)
                )
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] update_resource_status error: {e}")

    def update_resource_fields(self, id: str, **fields):
        """Met à jour un ou plusieurs champs d'une ressource.
        Les colonnes autorisées sont filtrées pour éviter l'injection SQL.
        """
        allowed = {"name", "status", "mime", "summary", "storage_path", "git_tracked",
                   "message_id", "plan_goal", "author_model", "language", "lines",
                   "last_commit", "commit_msg"}
        updates = {k: v for k, v in fields.items() if k in allowed}
        if not updates:
            return
        updates["updated_at"] = time.time()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [id]
        try:
            with self._get_connection() as conn:
                conn.execute(f"UPDATE echo_resources SET {set_clause} WHERE id = ?", values)
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] update_resource_fields error: {e}")

    def delete_resource(self, id: str):
        """Supprime une ressource par ID."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM echo_resources WHERE id = ?", (id,))
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] delete_resource error: {e}")

    def get_resource(self, id: str) -> Optional[dict]:
        """Retourne une ressource par ID, ou None si inexistante."""
        try:
            with self._get_connection() as conn:
                row = conn.execute(
                    "SELECT id, name, resource_type, mime, status, summary, storage_path, "
                    "git_tracked, message_id, plan_goal, author_model, language, lines, "
                    "last_commit, commit_msg, created_at, updated_at "
                    "FROM echo_resources WHERE id = ?", (id,)
                ).fetchone()
                if row:
                    return self._row_to_resource(row)
        except Exception as e:
            print(f"[EchoStateManager] get_resource error: {e}")
        return None

    def get_resources(self, resource_type: str = None, status: str = None,
                      search: str = None, created_after: float = None,
                      message_ids: List[str] = None) -> List[dict]:
        """Liste les ressources avec filtres combinés (AND).
        
        Args:
            resource_type: Filtre par type ('codex', 'plan', 'media', 'binary', 'weburl').
            status: Filtre par statut.
            search: Recherche LIKE sur le champ name.
            created_after: Timestamp minimum (pour le mécanisme de watermark/delta).
            message_ids: Liste de message_id pour le filtrage par messages actifs.
        """
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

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        resources = []
        try:
            with self._get_connection() as conn:
                rows = conn.execute(
                    f"SELECT id, name, resource_type, mime, status, summary, storage_path, "
                    f"git_tracked, message_id, plan_goal, author_model, language, lines, "
                    f"last_commit, commit_msg, created_at, updated_at "
                    f"FROM echo_resources{where} ORDER BY updated_at DESC", params
                ).fetchall()
                for row in rows:
                    resources.append(self._row_to_resource(row))
        except Exception as e:
            print(f"[EchoStateManager] get_resources error: {e}")
        return resources

    def clear_resources_by_type(self, resource_type: str):
        """Supprime toutes les ressources d'un type donné."""
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM echo_resources WHERE resource_type = ?", (resource_type,))
                conn.commit()
        except Exception as e:
            print(f"[EchoStateManager] clear_resources_by_type error: {e}")

    def get_next_codex_name(self, name: str) -> str:
        """Retourne un nom unique pour le Codex. Si `name` existe déjà, ajoute un suffixe incrémental.
        Ex: script.py → script_1.py → script_2.py
        """
        try:
            with self._get_connection() as conn:
                # Vérifie si le nom exact existe
                existing = conn.execute(
                    "SELECT id FROM echo_resources WHERE id = ? AND resource_type = 'codex'", (name,)
                ).fetchone()
                if not existing:
                    return name

                # Collision : chercher le prochain suffixe libre
                base, ext = os.path.splitext(name)
                idx = 1
                while True:
                    candidate = f"{base}_{idx}{ext}"
                    exists = conn.execute(
                        "SELECT id FROM echo_resources WHERE id = ? AND resource_type = 'codex'", (candidate,)
                    ).fetchone()
                    if not exists:
                        return candidate
                    idx += 1
        except Exception as e:
            print(f"[EchoStateManager] get_next_codex_name error: {e}")
        return name

    @staticmethod
    def _row_to_resource(row) -> dict:
        """Convertit un tuple SQLite en dict ressource."""
        return {
            "id": row[0], "name": row[1], "resource_type": row[2],
            "mime": row[3], "status": row[4], "summary": row[5],
            "storage_path": row[6], "git_tracked": bool(row[7]),
            "message_id": row[8], "plan_goal": row[9], "author_model": row[10],
            "language": row[11], "lines": row[12], "last_commit": row[13],
            "commit_msg": row[14], "created_at": row[15], "updated_at": row[16]
        }

    def get_active_branch_shadows(self, chat_id: str, limit: int = 20) -> List[dict]:
        """Remonte la généalogie de la branche active via suture_index pour une distillation bit-perfect."""
        shadows = []
        try:
            with self._get_connection() as conn:
                # 1. On identifie le dernier message scellé dans la suture pour ce chat
                row = conn.execute(
                    "SELECT cumulative_hash, parent_hash, message_id FROM suture_index WHERE chat_id = ? ORDER BY timestamp DESC LIMIT 1",
                    (chat_id,)
                ).fetchone()
                
                if not row: return []
                
                chain = []
                curr_cumul, curr_parent, curr_mid = row
                
                while curr_mid and len(chain) < limit:
                    chain.append(curr_mid)
                    if not curr_parent: break
                    
                    # Remontée vers le parent via son Cumulative Hash
                    parent_row = conn.execute(
                        "SELECT cumulative_hash, parent_hash, message_id FROM suture_index WHERE cumulative_hash = ?",
                        (curr_parent,)
                    ).fetchone()
                    
                    if not parent_row: break
                    curr_cumul, curr_parent, curr_mid = parent_row
                
                # 2. Récupération des ombres (on inverse pour l'ordre chronologique)
                for mid in reversed(chain):
                    s_row = conn.execute("SELECT role, full_parts_json FROM message_shadows WHERE message_id = ?", (mid,)).fetchone()
                    if s_row:
                        shadows.append({"role": s_row[0], "parts": json.loads(s_row[1])})
        except Exception as e:
            print(f"[EchoStateManager] Error in genealogy: {e}")
        return shadows

    def is_message_embedded(self, message_id: str) -> bool:
        if not message_id: return False
        try:
            with self._get_connection() as conn:
                row = conn.execute("SELECT is_embedded FROM message_shadows WHERE message_id = ?", (message_id,)).fetchone()
                return bool(row[0]) if row else False
        except: pass
        return False

    def mark_message_embedded(self, message_id: str):
        if not message_id: return
        try:
            with self._get_connection() as conn:
                cursor = conn.execute("UPDATE message_shadows SET is_embedded = 1 WHERE message_id = ?", (message_id,))
                if cursor.rowcount == 0:
                    conn.execute("""
                        INSERT INTO message_shadows (message_id, chat_id, role, full_parts_json, updated_at, is_embedded) 
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (message_id, self.chat_id or "unknown", "assistant", "[]", int(time.time()), 1))
                conn.commit()
        except: pass

