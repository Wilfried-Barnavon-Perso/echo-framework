# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Declarative Migrations Runner
author: Wilfried BARNAVON
version: 6.21.0.1
description: Exécuteur idempotent à pré-vol obligatoire des migrations ponctuelles 14-owui-libs/migrations/.

Contrat obligatoire de tout fichier `14-owui-libs/migrations/NNN_<slug>.py` :
1. Nommage strict `^[0-9]{3}_[a-zA-Z0-9_]+\\.py$` (exécution en ordre lexicographique strict).
2. Signature obligatoire `def run(dry_run: bool = True) -> dict[str, Any]:` (vérifiée via `inspect.signature`).
3. Pré-vol `run(dry_run=True)` systématique avant tout appel `run(dry_run=False)` : toute exception
   levée pendant le pré-vol bloque strictement `run(dry_run=False)`, journalise le traceback complet
   via `logger.error`, n'ajoute pas l'identifiant à `newly_applied` et interrompt la séquence (`fail-fast`).
4. Idempotence intrinsèque et persistance de la liste des identifiants appliqués dans `webui.db::config`
   sous la clé `APPLIED_MIGRATIONS_CONFIG_KEY = "echo.applied_migrations"`.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.0.1: Création du chargeur déclaratif idempotent à pré-vol dry_run=True obligatoire pour 14-owui-libs/migrations/.

import argparse
import importlib.util
import inspect
import json
import logging
import os
import re
import sqlite3
import sys
from typing import Any, Iterable, Optional

logger = logging.getLogger("echo.migrations")

APPLIED_MIGRATIONS_CONFIG_KEY = "echo.applied_migrations"
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
MIGRATION_FILENAME_RE = re.compile(r"^([0-9]{3}_[a-zA-Z0-9_]+)\.py$")


def discover_migrations(migrations_dir: str = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    """Découvre et trie lexicographiquement les scripts de migration conformes."""
    if not os.path.isdir(migrations_dir):
        return []

    found: list[tuple[str, str]] = []
    for entry in sorted(os.listdir(migrations_dir)):
        match = MIGRATION_FILENAME_RE.match(entry)
        if not match:
            continue
        full_path = os.path.join(migrations_dir, entry)
        if os.path.isfile(full_path):
            found.append((match.group(1), full_path))
    return found


def _load_and_validate_migration(mig_id: str, mig_path: str) -> Any:
    """Charge un module de migration et valide la signature de `run(dry_run: bool = True)`."""
    libs_dir = os.path.dirname(os.path.abspath(__file__))
    if libs_dir not in sys.path:
        sys.path.insert(0, libs_dir)

    spec = importlib.util.spec_from_file_location(f"echo_migration_{mig_id}", mig_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de charger le module de migration: {mig_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    run_fn = getattr(module, "run", None)
    if not callable(run_fn):
        raise TypeError(f"La migration '{mig_id}' doit exposer une fonction `run(dry_run: bool = True)`.")

    sig = inspect.signature(run_fn)
    param = sig.parameters.get("dry_run")
    if param is None or param.kind not in (
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.KEYWORD_ONLY,
    ):
        raise TypeError(
            f"Signature invalide pour '{mig_id}.run': paramètre `dry_run` requis en mot-clé."
        )
    if param.default is inspect.Parameter.empty:
        raise TypeError(
            f"Signature invalide pour '{mig_id}.run': `dry_run` doit avoir une valeur par défaut (`True`)."
        )
    return run_fn


def run_pending_migrations(
    applied_ids: Iterable[str] = (),
    migrations_dir: str = MIGRATIONS_DIR,
    dry_run_only: bool = False,
) -> dict[str, Any]:
    """Exécute les migrations en attente avec pré-vol `dry_run=True` obligatoire (fail-fast)."""
    applied_set = {str(item) for item in applied_ids if item}
    discovered_pairs = discover_migrations(migrations_dir)

    report: dict[str, Any] = {
        "discovered": [mig_id for mig_id, _ in discovered_pairs],
        "skipped": [],
        "newly_applied": [],
        "results": {},
        "error": None,
    }

    for mig_id, mig_path in discovered_pairs:
        if mig_id in applied_set:
            report["skipped"].append(mig_id)
            continue

        try:
            run_fn = _load_and_validate_migration(mig_id, mig_path)
        except Exception as exc:
            logger.error("Échec de chargement/validation pour %s: %s", mig_id, exc, exc_info=True)
            report["error"] = f"{mig_id} (load): {exc}"
            break

        try:
            preflight_result = run_fn(dry_run=True)
        except Exception as exc:
            logger.error("Échec du pré-vol (dry_run=True) pour %s: %s", mig_id, exc, exc_info=True)
            report["error"] = f"{mig_id} (dry_run): {exc}"
            break

        if dry_run_only:
            report["results"][mig_id] = {"dry_run": preflight_result}
            continue

        try:
            apply_result = run_fn(dry_run=False)
        except Exception as exc:
            logger.error("Échec d'exécution (dry_run=False) pour %s: %s", mig_id, exc, exc_info=True)
            report["error"] = f"{mig_id} (apply): {exc}"
            break

        report["results"][mig_id] = {
            "dry_run": preflight_result,
            "apply": apply_result,
        }
        report["newly_applied"].append(mig_id)

    return report


def _read_applied_migrations_ro(data_dir: str) -> list[str]:
    """Lit en lecture seule `webui.db::config['echo.applied_migrations']` si disponible."""
    db_path = os.path.join(data_dir, "webui.db")
    if not os.path.isfile(db_path) or os.path.getsize(db_path) == 0:
        return []

    conn: Optional[sqlite3.Connection] = None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
        row = conn.execute(
            "SELECT value FROM config WHERE key = ?",
            (APPLIED_MIGRATIONS_CONFIG_KEY,),
        ).fetchone()
        if not row or row[0] is None:
            return []
        raw_val = row[0]
        if isinstance(raw_val, (bytes, bytearray)):
            raw_val = raw_val.decode("utf-8", errors="replace")
        parsed = json.loads(raw_val) if isinstance(raw_val, str) else raw_val
        if isinstance(parsed, list):
            return [str(x) for x in parsed if x]
        return []
    except sqlite3.Error:
        return []
    except (ValueError, TypeError):
        return []
    finally:
        if conn is not None:
            conn.close()


def cli(argv: Optional[list[str]] = None) -> int:
    """Point d'entrée CLI pour lister ou simuler/exécuter les migrations ECHO."""
    parser = argparse.ArgumentParser(description="ECHO Declarative Migrations Runner")
    parser.add_argument("--list", action="store_true", help="Lister les migrations découvertes et appliquées.")
    parser.add_argument("--dry-run", action="store_true", help="Exécuter uniquement le pré-vol sans modification.")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ECHO_DATA_DIR") or os.environ.get("DATA_DIR") or "/app/backend/data",
        help="Répertoire des données ECHO contenant webui.db.",
    )
    args = parser.parse_args(argv)

    os.environ["ECHO_DATA_DIR"] = args.data_dir
    applied = _read_applied_migrations_ro(args.data_dir)

    if args.list:
        discovered = [mig_id for mig_id, _ in discover_migrations(MIGRATIONS_DIR)]
        applied_set = set(applied)
        payload = {
            "data_dir": args.data_dir,
            "discovered": discovered,
            "applied": applied,
            "pending": [m for m in discovered if m not in applied_set],
            "error": None,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    report = run_pending_migrations(
        applied_ids=applied,
        migrations_dir=MIGRATIONS_DIR,
        dry_run_only=args.dry_run,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report.get("error") is None else 1


if __name__ == "__main__":
    raise SystemExit(cli())
