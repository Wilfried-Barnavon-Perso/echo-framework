# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Terminals
author: Wilfried BARNAVON
version: 6.24.6.1
description: Bibliothèque partagée d'initialisation de session, validation Vault SSH, client Worker terminal/SSH et synchronisation du registre echo_resources.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.24.6.1: Création du module partagé echo_terminals.py (ensure_echo_session_environment, validation Vault SSH, client HTTP Worker et réconciliation echo_resources).

import asyncio
import json
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx

from echo_codex_git import CodexRepo
from echo_constants import (
    ECHO_CODEX_WORKSPACES,
    ECHO_CODING_WORKER_BASE_URL,
    ECHO_GLOBAL_DOMAINS,
    ECHO_MAX_CODE_EXECUTION_TIMEOUT,
    ECHO_SESSION_DOMAINS,
    RESOURCE_TYPES,
    TERMINAL_DEFAULT_READ_LINES,
    TERMINAL_MAX_READ_LINES,
    TERMINAL_MAX_WAIT_MS,
    TERMINAL_SESSION_STATUS,
    is_codex_workspace_versioned,
)
from echo_db_schema import DB_KIND_IDENTITY, DB_KIND_SESSION, ensure_db
from echo_http import _get_global_client
from echo_paths import (
    get_echo_global_path,
    get_echo_identity_db_path,
    get_echo_session_path,
)
from echo_state_manager import EchoStateManager


TERMINAL_LOCKED_PAS_TEMPLATE = (
    "🔒 Session terminale '{name}' ({session_id}) verrouillée en lecture seule. "
    "Pour que le Modèle interagisse, L'Utilisateur DOIT déverrouiller la session '{name}' dans l'interface."
)

_TERMINAL_MIME = "application/x-echo-terminal"


def format_terminal_locked_message(name: str, session_id: str) -> str:
    """Formate le message PAS imposé lorsqu'une session terminale est verrouillée en lecture seule."""
    safe_name = (name or "").strip() or session_id or "terminal"
    safe_sid = (session_id or "").strip() or "unknown"
    return TERMINAL_LOCKED_PAS_TEMPLATE.format(name=safe_name, session_id=safe_sid)


def _safe_ensure_db(path: str, kind: str) -> None:
    """Applique ensure_db de façon idempotente avec journalisation non bloquante."""
    try:
        rep = ensure_db(path, kind)
        if rep.get("status") in ("locked", "error"):
            print(f"[ECHO-ENV] ensure_db {kind} {rep}")
        elif rep.get("status") == "migrated":
            print(f"[ECHO-ENV] SQLite v3 migrated ({kind}): {rep.get('actions')}")
    except Exception as exc:
        print(f"[ECHO-ENV] ensure_db {kind} exception: {exc}")


def ensure_echo_session_environment(user_id: str, chat_id: Optional[str] = None) -> None:
    """Crée les domaines globaux/session, dépôts Git Codex et schémas SQLite v3 de manière idempotente."""
    if not user_id:
        return

    for domain in ECHO_GLOBAL_DOMAINS:
        try:
            domain_path = get_echo_global_path(user_id, domain)
            os.makedirs(domain_path, exist_ok=True)
            if domain == "files":
                try:
                    os.chmod(domain_path, 0o711)
                except OSError:
                    pass
        except Exception:
            pass

    _safe_ensure_db(get_echo_identity_db_path(user_id), DB_KIND_IDENTITY)

    if chat_id:
        for domain in ECHO_SESSION_DOMAINS:
            if domain == "db":
                continue
            try:
                domain_path = get_echo_session_path(user_id, chat_id, domain)
                os.makedirs(domain_path, exist_ok=True)
                if domain == "files":
                    try:
                        os.chmod(domain_path, 0o711)
                    except OSError:
                        pass
                if domain == "codex":
                    for ws in ECHO_CODEX_WORKSPACES.keys():
                        os.makedirs(os.path.join(domain_path, ws), exist_ok=True)
                        if is_codex_workspace_versioned(ws):
                            CodexRepo(user_id, chat_id, workspace=ws)
            except Exception:
                pass

        _safe_ensure_db(get_echo_session_path(user_id, chat_id, "db"), DB_KIND_SESSION)


def _parse_ssh_port(raw_port: Any) -> Optional[int]:
    """Parse et valide un port TCP SSH dans [1, 65535]."""
    if raw_port is None or raw_port == "":
        return 22
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        return None
    return port if 1 <= port <= 65535 else None


def load_and_validate_ssh_vault_account(
    user_id: str,
    account_id: str,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Charge et valide un compte SSH depuis l'Identity Vault sans exposer de secrets hors retour interne."""
    clean_acc = (account_id or "").strip()
    if not clean_acc:
        return None, "❌ Le paramètre 'account_id' est obligatoire pour une cible SSH."

    raw_creds = EchoStateManager(user_id=user_id).get_vault_credential("ssh", clean_acc)
    if not raw_creds:
        return (
            None,
            f"❌ Identifiants SSH introuvables pour '{clean_acc}'. "
            "Vérifier les comptes disponibles dans l'Identity Vault (service='ssh').",
        )

    try:
        creds = json.loads(raw_creds)
    except Exception:
        return None, f"❌ Configuration SSH invalide (JSON corrompu) pour '{clean_acc}'."

    if not isinstance(creds, dict):
        return None, f"❌ Configuration SSH invalide pour '{clean_acc}'."

    host = str(creds.get("host") or "").strip()
    user = str(creds.get("user") or "").strip()
    if not host or not user:
        return None, f"❌ Configuration SSH incomplète pour '{clean_acc}' ('host' et 'user' requis)."

    port = _parse_ssh_port(creds.get("port"))
    if port is None:
        return None, f"❌ Port SSH invalide pour '{clean_acc}' : {creds.get('port')} (attendu 1..65535)."

    password = str(creds.get("password") or "")
    private_key = str(creds.get("private_key") or "")
    certificate = str(creds.get("certificate") or "")
    description = str(creds.get("description") or "").strip()

    if not private_key.strip() and not password:
        return (
            None,
            f"❌ Configuration SSH incomplète pour '{clean_acc}' : fournir 'private_key' ou 'password'.",
        )

    if certificate.strip() and not private_key.strip():
        return (
            None,
            f"❌ Configuration SSH invalide pour '{clean_acc}' : 'certificate' exige 'private_key'.",
        )

    return (
        {
            "account_id": clean_acc,
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "private_key": private_key,
            "certificate": certificate,
            "description": description,
        },
        None,
    )


def list_user_ssh_vault_targets_sync(user_id: str) -> List[Dict[str, Any]]:
    """Liste les cibles SSH configurées dans identity.db (sans secret et sans créer de BDD fantôme)."""
    if not user_id or not os.path.exists(get_echo_identity_db_path(user_id)):
        return []

    rows = EchoStateManager(user_id=user_id).list_vault_credentials("ssh")
    targets: List[Dict[str, Any]] = []
    for account_id, raw_creds in rows:
        try:
            creds = json.loads(raw_creds) if raw_creds else {}
        except Exception:
            creds = {}
        if not isinstance(creds, dict):
            creds = {}
        port = _parse_ssh_port(creds.get("port")) or 22
        targets.append(
            {
                "account_id": str(account_id),
                "host": str(creds.get("host") or "").strip(),
                "port": port,
                "user": str(creds.get("user") or "").strip(),
                "description": str(creds.get("description") or "").strip(),
                "has_certificate": bool(str(creds.get("certificate") or "").strip()),
            }
        )
    targets.sort(key=lambda item: item["account_id"].lower())
    return targets


def _build_terminal_resource_fields(session_data: Dict[str, Any]) -> Dict[str, Any]:
    """Dérive les champs canoniques echo_resources depuis un TerminalSessionDict."""
    mode = str(session_data.get("mode") or "local").strip().lower()
    if mode not in ("local", "ssh"):
        mode = "local"
    account_id = str(session_data.get("account_id") or "").strip()
    target = str(session_data.get("target") or ("local:/sandbox" if mode == "local" else "")).strip()
    name = str(session_data.get("name") or account_id or "Sandbox").strip()
    alive = bool(session_data.get("alive", True))
    agent_locked = bool(session_data.get("agent_locked", False))

    if not alive:
        status = TERMINAL_SESSION_STATUS["CLOSED"]
    elif agent_locked:
        status = TERMINAL_SESSION_STATUS["ACTIVE_LOCKED"]
    else:
        status = TERMINAL_SESSION_STATUS["ACTIVE_UNLOCKED"]

    if mode == "local":
        storage_path = "local:/sandbox"
    elif account_id and target:
        storage_path = f"ssh://{account_id} ({target})"
    elif account_id:
        storage_path = f"ssh://{account_id}"
    else:
        storage_path = f"ssh://{target}" if target else "ssh://"

    metadata = {
        "mode": mode,
        "account_id": account_id,
        "target": target,
        "agent_locked": agent_locked,
        "alive": alive,
    }
    return {
        "name": name,
        "status": status,
        "storage_path": storage_path,
        "metadata": metadata,
    }


def sync_terminal_resource_sync(
    user_id: str,
    chat_id: str,
    session_data: Dict[str, Any],
    message_id: Optional[str] = None,
) -> None:
    """Synchronise une session terminale active ou terminée dans echo_resources (session.db)."""
    if not user_id or not chat_id or not isinstance(session_data, dict):
        return
    session_id = str(session_data.get("id") or "").strip()
    if not session_id:
        return

    fields = _build_terminal_resource_fields(session_data)
    EchoStateManager(user_id=user_id, chat_id=chat_id).save_resource(
        id=session_id,
        name=fields["name"],
        resource_type=RESOURCE_TYPES["terminal"],
        item_type="terminal",
        status=fields["status"],
        mime=_TERMINAL_MIME,
        storage_path=fields["storage_path"],
        message_id=message_id,
        metadata=fields["metadata"],
    )


def mark_terminal_resource_closed_sync(user_id: str, chat_id: str, session_id: str) -> None:
    """Passe une session terminale en statut 'closed' dans echo_resources sans créer de BDD fantôme."""
    clean_sid = (session_id or "").strip()
    if not user_id or not chat_id or not clean_sid:
        return
    session_db_path = get_echo_session_path(user_id, chat_id, "db")
    if not os.path.exists(session_db_path):
        return

    sm = EchoStateManager(user_id=user_id, chat_id=chat_id)
    existing = sm.get_resource(clean_sid)
    if not existing or existing.get("resource_type") != RESOURCE_TYPES["terminal"]:
        return

    raw_meta = existing.get("metadata")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    meta["alive"] = False
    sm.update_resource_fields(
        clean_sid,
        status=TERMINAL_SESSION_STATUS["CLOSED"],
        metadata=json.dumps(meta, ensure_ascii=False),
    )


def _reconcile_terminal_db_sync(
    user_id: str,
    chat_id: str,
    live_sessions: List[Dict[str, Any]],
    worker_reachable: bool,
) -> None:
    """Réconcilie echo_resources (lecture seule d'abord, écriture uniquement si écart réel)."""
    session_db_path = get_echo_session_path(user_id, chat_id, "db")
    if not live_sessions and not os.path.exists(session_db_path):
        return

    sm = EchoStateManager(user_id=user_id, chat_id=chat_id)
    existing_rows: List[Dict[str, Any]] = []
    if os.path.exists(session_db_path):
        existing_rows = sm.get_resources(resource_type=RESOURCE_TYPES["terminal"])

    if not worker_reachable:
        return

    existing_by_id = {
        str(row.get("id")): row
        for row in existing_rows
        if isinstance(row, dict) and row.get("id")
    }
    live_by_id = {
        str(sess.get("id")): sess
        for sess in live_sessions
        if isinstance(sess, dict) and sess.get("id")
    }

    active_statuses = {
        TERMINAL_SESSION_STATUS["ACTIVE_UNLOCKED"],
        TERMINAL_SESSION_STATUS["ACTIVE_LOCKED"],
    }

    for sid, row in existing_by_id.items():
        if row.get("status") in active_statuses and sid not in live_by_id:
            mark_terminal_resource_closed_sync(user_id, chat_id, sid)

    for sid, sess in live_by_id.items():
        expected = _build_terminal_resource_fields(sess)
        row = existing_by_id.get(sid)
        if row is None:
            sync_terminal_resource_sync(user_id, chat_id, sess)
            continue
        row_meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        exp_meta = expected["metadata"]
        drift = (
            row.get("status") != expected["status"]
            or row.get("name") != expected["name"]
            or row.get("storage_path") != expected["storage_path"]
            or bool(row_meta.get("agent_locked")) != bool(exp_meta["agent_locked"])
            or bool(row_meta.get("alive")) != bool(exp_meta["alive"])
            or row_meta.get("mode") != exp_meta["mode"]
            or row_meta.get("account_id") != exp_meta["account_id"]
            or row_meta.get("target") != exp_meta["target"]
        )
        if drift:
            sync_terminal_resource_sync(user_id, chat_id, sess)


def _worker_headers(user_id: str, chat_id: str) -> Dict[str, str]:
    """Construit les en-têtes d'identification interne vers echo-coding-worker."""
    headers: Dict[str, str] = {}
    if user_id:
        headers["X-User-Id"] = str(user_id)
    if chat_id:
        headers["X-Session-Id"] = str(chat_id)
    return headers


def _extract_error_detail(response: httpx.Response) -> str:
    """Extrait un message d'erreur compact depuis une réponse HTTP du Worker."""
    try:
        payload = response.json()
        if isinstance(payload, dict):
            msg = payload.get("error") or payload.get("detail") or payload.get("message")
            if msg:
                return str(msg)
    except Exception:
        pass
    return (response.text or "")[:300] or f"HTTP {response.status_code}"


def _build_locked_error_dict(response: httpx.Response, fallback_sid: str = "") -> Dict[str, Any]:
    """Traduit une réponse HTTP 423 du Worker en erreur normalisée terminal_locked."""
    sid = fallback_sid
    name = fallback_sid or "terminal"
    try:
        payload = response.json()
        if isinstance(payload, dict):
            sid = str(payload.get("session_id") or sid)
            name = str(payload.get("name") or name)
    except Exception:
        pass
    return {
        "status": "error",
        "error_code": "terminal_locked",
        "session_id": sid,
        "name": name,
        "agent_locked": True,
        "error": format_terminal_locked_message(name, sid),
    }


async def _fetch_worker_terminals(
    user_id: str,
    chat_id: str,
) -> Tuple[List[Dict[str, Any]], bool, Optional[str]]:
    """Interroge GET /api/terminals et retourne (sessions_actives, worker_reachable, err_msg)."""
    try:
        client = await _get_global_client()
        response = await client.get(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals",
            params={"user_id": user_id, "chat_id": chat_id},
            headers=_worker_headers(user_id, chat_id),
            timeout=10.0,
        )
        if response.status_code != 200:
            return [], False, f"Erreur Worker Terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}"
        data = response.json()
        items = data.get("items") if isinstance(data, dict) else []
        if not isinstance(items, list):
            items = []
        return [s for s in items if isinstance(s, dict)], True, None
    except httpx.RequestError as exc:
        return [], False, f"Service Coding Worker injoignable : {exc}"
    except Exception as exc:
        return [], False, f"Erreur lecture terminaux : {exc}"


async def reconcile_chat_terminals_registry(user_id: str, chat_id: str) -> List[Dict[str, Any]]:
    """Interroge le Worker et réconcilie echo_resources sans jamais matérialiser de BDD fantôme."""
    live_sessions, worker_reachable, _ = await _fetch_worker_terminals(user_id, chat_id)
    await asyncio.to_thread(
        _reconcile_terminal_db_sync,
        user_id,
        chat_id,
        live_sessions,
        worker_reachable,
    )
    return live_sessions


async def worker_exec_ssh_oneshot(
    user_id: str,
    chat_id: str,
    account_id: str,
    command: str,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    """Exécute une commande SSH one-shot via POST /ssh après validation Vault."""
    clean_acc = (account_id or "").strip()
    clean_cmd = (command or "").strip()
    if not clean_acc or not clean_cmd:
        return {
            "status": "error",
            "account_id": clean_acc,
            "exit_code": -1,
            "error": "❌ Les paramètres 'account_id' et 'command' sont obligatoires.",
        }

    if not (1 <= timeout_sec <= ECHO_MAX_CODE_EXECUTION_TIMEOUT):
        return {
            "status": "error",
            "account_id": clean_acc,
            "exit_code": -1,
            "error": (
                f"❌ timeout_sec ({timeout_sec}s) doit être compris "
                f"entre 1 et {ECHO_MAX_CODE_EXECUTION_TIMEOUT}s."
            ),
        }

    creds, err_msg = await asyncio.to_thread(load_and_validate_ssh_vault_account, user_id, clean_acc)
    if err_msg or not creds:
        return {
            "status": "error",
            "account_id": clean_acc,
            "exit_code": -1,
            "error": err_msg or f"❌ Identifiants SSH introuvables pour '{clean_acc}'.",
        }

    target = f"{creds['user']}@{creds['host']}:{creds['port']}"
    try:
        client = await _get_global_client()
        response = await client.post(
            f"{ECHO_CODING_WORKER_BASE_URL}/ssh",
            json={
                "user_id": user_id,
                "chat_id": chat_id,
                "host": creds["host"],
                "port": creds["port"],
                "user": creds["user"],
                "password": creds["password"],
                "private_key": creds["private_key"],
                "certificate": creds["certificate"],
                "command": clean_cmd,
                "timeout": timeout_sec,
            },
            headers=_worker_headers(user_id, chat_id),
            timeout=float(timeout_sec) + 10.0,
        )
        if response.status_code != 200:
            return {
                "status": "error",
                "account_id": clean_acc,
                "target": target,
                "exit_code": -1,
                "error": f"Erreur Worker SSH (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }

        res_data = response.json()
        return {
            "status": "success" if res_data.get("status") == "success" else "error",
            "account_id": clean_acc,
            "target": target,
            "exit_code": res_data.get("exit_code", -1),
            "output": res_data.get("output") or "",
            "error": res_data.get("error") or "",
        }
    except httpx.RequestError as exc:
        return {
            "status": "error",
            "account_id": clean_acc,
            "target": target,
            "exit_code": -1,
            "error": f"Service Coding Worker injoignable : {exc}",
        }
    except Exception as exc:
        return {
            "status": "error",
            "account_id": clean_acc,
            "target": target,
            "exit_code": -1,
            "error": f"Erreur Client SSH : {exc}",
        }


async def worker_open_terminal(
    user_id: str,
    chat_id: str,
    mode: str = "local",
    account_id: Optional[str] = None,
    name: Optional[str] = None,
    agent_locked: Optional[bool] = None,
    message_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Ouvre une session terminale PTY (locale ou SSH) et la synchronise dans echo_resources."""
    clean_mode = (mode or "local").strip().lower()
    if clean_mode not in ("local", "ssh"):
        return {"status": "error", "error": f"❌ Mode terminal invalide : {mode} (attendu 'local' ou 'ssh')."}
    if not user_id or not chat_id:
        return {"status": "error", "error": "❌ Contexte de session manquant (user_id / chat_id)."}

    await asyncio.to_thread(ensure_echo_session_environment, user_id, chat_id)

    ssh_config: Optional[Dict[str, Any]] = None
    clean_acc = (account_id or "").strip()
    if clean_mode == "ssh":
        creds, err_msg = await asyncio.to_thread(load_and_validate_ssh_vault_account, user_id, clean_acc)
        if err_msg or not creds:
            return {"status": "error", "error": err_msg or "❌ Identifiants SSH invalides."}
        clean_acc = creds["account_id"]
        ssh_config = {
            "host": creds["host"],
            "port": creds["port"],
            "user": creds["user"],
            "password": creds["password"],
            "private_key": creds["private_key"],
            "certificate": creds["certificate"],
        }
        effective_name = (name or "").strip() or clean_acc
        effective_locked = True
    else:
        effective_name = (name or "").strip() or "Sandbox"
        effective_locked = bool(agent_locked) if agent_locked is not None else False

    payload: Dict[str, Any] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "mode": clean_mode,
        "name": effective_name,
        "account_id": clean_acc if clean_mode == "ssh" else "",
        "agent_locked": effective_locked,
    }
    if ssh_config is not None:
        payload["ssh_config"] = ssh_config

    try:
        client = await _get_global_client()
        response = await client.post(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals",
            json=payload,
            headers=_worker_headers(user_id, chat_id),
            timeout=20.0,
        )
        if response.status_code != 200:
            return {
                "status": "error",
                "error": f"Erreur création terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }

        session_data = response.json()
        await asyncio.to_thread(sync_terminal_resource_sync, user_id, chat_id, session_data, message_id)
        return {"status": "success", "session": session_data}
    except httpx.RequestError as exc:
        return {"status": "error", "error": f"Service Coding Worker injoignable : {exc}"}
    except Exception as exc:
        return {"status": "error", "error": f"Erreur ouverture terminal : {exc}"}


async def worker_list_terminals(user_id: str, chat_id: str) -> Dict[str, Any]:
    """Liste les terminaux actifs d'une session et réconcilie le registre echo_resources."""
    if not user_id or not chat_id:
        return {"status": "error", "items": [], "count": 0, "error": "❌ Contexte de session manquant."}

    live_sessions, worker_reachable, err_msg = await _fetch_worker_terminals(user_id, chat_id)
    await asyncio.to_thread(
        _reconcile_terminal_db_sync,
        user_id,
        chat_id,
        live_sessions,
        worker_reachable,
    )
    if not worker_reachable:
        return {
            "status": "error",
            "items": [],
            "count": 0,
            "error": err_msg or "Service Coding Worker injoignable.",
        }
    return {"status": "success", "items": live_sessions, "count": len(live_sessions)}


async def worker_read_terminal(
    session_id: str,
    tail_lines: int = TERMINAL_DEFAULT_READ_LINES,
    since_cursor: int = 0,
) -> Dict[str, Any]:
    """Lit la sortie nettoyée (sans ANSI) d'une session terminale interactive."""
    clean_sid = (session_id or "").strip()
    if not clean_sid:
        return {"status": "error", "error": "❌ Le paramètre 'session_id' est obligatoire."}

    safe_tail = max(1, min(int(tail_lines or TERMINAL_DEFAULT_READ_LINES), TERMINAL_MAX_READ_LINES))
    safe_cursor = max(0, int(since_cursor or 0))

    try:
        client = await _get_global_client()
        response = await client.get(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals/{clean_sid}/buffer",
            params={"tail_lines": safe_tail, "since_cursor": safe_cursor},
            timeout=15.0,
        )
        if response.status_code == 404:
            return {
                "status": "error",
                "error_code": "not_found",
                "session_id": clean_sid,
                "error": f"❌ Session terminale '{clean_sid}' introuvable.",
            }
        if response.status_code != 200:
            return {
                "status": "error",
                "session_id": clean_sid,
                "error": f"Erreur lecture terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }
        data = response.json()
        return {"status": "success", **data}
    except httpx.RequestError as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Service Coding Worker injoignable : {exc}"}
    except Exception as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Erreur lecture terminal : {exc}"}


async def worker_write_terminal(
    session_id: str,
    input_text: Optional[str] = None,
    actor: str = "agent",
    append_newline: bool = True,
    signal: Optional[str] = None,
    wait_ms: int = 400,
) -> Dict[str, Any]:
    """Envoie une entrée ou un signal à un terminal (traduit HTTP 423 en terminal_locked PAS)."""
    clean_sid = (session_id or "").strip()
    if not clean_sid:
        return {"status": "error", "error": "❌ Le paramètre 'session_id' est obligatoire."}

    safe_wait = max(0, min(int(wait_ms if wait_ms is not None else 400), TERMINAL_MAX_WAIT_MS))
    payload: Dict[str, Any] = {
        "actor": (actor or "agent").strip().lower(),
        "input": input_text,
        "append_newline": bool(append_newline),
        "signal": signal,
        "wait_ms": safe_wait,
    }

    try:
        client = await _get_global_client()
        response = await client.post(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals/{clean_sid}/input",
            json=payload,
            timeout=max(15.0, (safe_wait / 1000.0) + 10.0),
        )
        if response.status_code == 423:
            return _build_locked_error_dict(response, clean_sid)
        if response.status_code == 404:
            return {
                "status": "error",
                "error_code": "not_found",
                "session_id": clean_sid,
                "error": f"❌ Session terminale '{clean_sid}' introuvable.",
            }
        if response.status_code == 409:
            return {
                "status": "error",
                "error_code": "terminal_closed",
                "session_id": clean_sid,
                "error": _extract_error_detail(response),
            }
        if response.status_code != 200:
            return {
                "status": "error",
                "session_id": clean_sid,
                "error": f"Erreur écriture terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }
        return response.json()
    except httpx.RequestError as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Service Coding Worker injoignable : {exc}"}
    except Exception as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Erreur écriture terminal : {exc}"}


async def worker_set_terminal_lock(
    user_id: str,
    chat_id: str,
    session_id: str,
    agent_locked: bool,
) -> Dict[str, Any]:
    """Met à jour le verrou agent_locked sur le Worker et synchronise echo_resources."""
    clean_sid = (session_id or "").strip()
    if not clean_sid:
        return {"status": "error", "error": "❌ Le paramètre 'session_id' est obligatoire."}

    try:
        client = await _get_global_client()
        response = await client.patch(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals/{clean_sid}/lock",
            json={"agent_locked": bool(agent_locked)},
            headers=_worker_headers(user_id, chat_id),
            timeout=10.0,
        )
        if response.status_code == 404:
            return {
                "status": "error",
                "error_code": "not_found",
                "session_id": clean_sid,
                "error": f"❌ Session terminale '{clean_sid}' introuvable.",
            }
        if response.status_code != 200:
            return {
                "status": "error",
                "session_id": clean_sid,
                "error": f"Erreur verrou terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }
        session_data = response.json()
        await asyncio.to_thread(sync_terminal_resource_sync, user_id, chat_id, session_data)
        return {"status": "success", "session": session_data}
    except httpx.RequestError as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Service Coding Worker injoignable : {exc}"}
    except Exception as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Erreur verrou terminal : {exc}"}


async def worker_close_terminal(
    user_id: str,
    chat_id: str,
    session_id: str,
    actor: str = "agent",
) -> Dict[str, Any]:
    """Termine une session terminale (gère 423 si actor='agent') et marque closed dans echo_resources."""
    clean_sid = (session_id or "").strip()
    if not clean_sid:
        return {"status": "error", "error": "❌ Le paramètre 'session_id' est obligatoire."}

    clean_actor = (actor or "agent").strip().lower()
    try:
        client = await _get_global_client()
        response = await client.delete(
            f"{ECHO_CODING_WORKER_BASE_URL}/api/terminals/{clean_sid}",
            params={"actor": clean_actor},
            headers=_worker_headers(user_id, chat_id),
            timeout=15.0,
        )
        if response.status_code == 423:
            return _build_locked_error_dict(response, clean_sid)
        if response.status_code == 404:
            await asyncio.to_thread(mark_terminal_resource_closed_sync, user_id, chat_id, clean_sid)
            return {
                "status": "terminated",
                "id": clean_sid,
                "already_closed": True,
            }
        if response.status_code != 200:
            return {
                "status": "error",
                "session_id": clean_sid,
                "error": f"Erreur fermeture terminal (HTTP {response.status_code}) : {_extract_error_detail(response)}",
            }

        await asyncio.to_thread(mark_terminal_resource_closed_sync, user_id, chat_id, clean_sid)
        data = response.json()
        return {
            "status": data.get("status", "terminated"),
            "id": data.get("id", clean_sid),
            "already_closed": False,
        }
    except httpx.RequestError as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Service Coding Worker injoignable : {exc}"}
    except Exception as exc:
        return {"status": "error", "session_id": clean_sid, "error": f"Erreur fermeture terminal : {exc}"}
