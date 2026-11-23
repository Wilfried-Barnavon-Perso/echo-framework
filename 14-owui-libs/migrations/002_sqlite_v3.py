# -*- coding: utf-8 -*-
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: Migration 002 - Schéma SQLite v3
author: Wilfried BARNAVON
version: 6.21.0.1
description: Migre les bases SQLite identity.db et session.db de tous les utilisateurs vers le schéma v3.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.21.0.1: Création de la migration déclarative SQLite v3 pour identity.db et session.db via echo_db_schema.migrate_all.

import os
from typing import Any


def run(dry_run: bool = True) -> dict[str, Any]:
    """Exécute la migration SQLite v3 via `echo_db_schema.migrate_all`."""
    data_dir = os.environ.get("ECHO_DATA_DIR") or os.environ.get("DATA_DIR") or "/app/backend/data"
    users_root = os.path.join(data_dir, "users")

    if not os.path.isdir(users_root):
        return {"dry_run": bool(dry_run), "total": 0, "counts": {}}

    from echo_db_schema import migrate_all

    rep = migrate_all(users_root=users_root, dry_run=bool(dry_run))
    counts = rep.get("counts", {})
    if counts.get("error", 0) > 0 or counts.get("locked", 0) > 0:
        raise RuntimeError(f"SQLite v3 migration check failed: {counts}")

    return {k: v for k, v in rep.items() if k != "details"}
