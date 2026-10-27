# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO - CACHE MANAGER
VERSION : 5.231.20.1
================================================================================
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.20.1: Isolation multi-user des clés de cache SQLite via user_id_var.
# 5.226.18.0: Alignement sur le versioning global ECHO.
import aiosqlite
import json
import time
import hashlib
from typing import Optional, Any
import os
from core.security import user_id_var

CACHE_DB_PATH = os.getenv("MCP_CACHE_DB_PATH", "/app/data/mcp_cache.db")


async def _init_cache_db():
    try:
        async with aiosqlite.connect(CACHE_DB_PATH) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS cache_entries (
                    tool_hash TEXT PRIMARY KEY,
                    result TEXT,
                    expires_at REAL
                )
            """)
            await db.commit()
    except Exception as e:
        print(f"[CacheManager] Erreur d'initialisation: {e}")


async def get_cache(tool_name: str, arguments: dict, user_id: Optional[str] = None) -> Optional[Any]:
    uid = user_id or user_id_var.get() or "anonymous"
    arg_str = json.dumps(arguments, sort_keys=True)
    tool_hash = hashlib.md5(f"{uid}_{tool_name}_{arg_str}".encode()).hexdigest()

    try:
        async with aiosqlite.connect(CACHE_DB_PATH) as db:
            async with db.execute("SELECT result, expires_at FROM cache_entries WHERE tool_hash = ?", (tool_hash,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    result, expires_at = row
                    if time.time() < expires_at:
                        return json.loads(result)
                    else:
                        # Cache expiré
                        await db.execute("DELETE FROM cache_entries WHERE tool_hash = ?", (tool_hash,))
                        await db.commit()
    except Exception as e:
        print(f"[CacheManager] Erreur de lecture: {e}")
    return None


async def set_cache(tool_name: str, arguments: dict, result: Any, ttl_seconds: int = 3600, user_id: Optional[str] = None):
    uid = user_id or user_id_var.get() or "anonymous"
    arg_str = json.dumps(arguments, sort_keys=True)
    tool_hash = hashlib.md5(f"{uid}_{tool_name}_{arg_str}".encode()).hexdigest()
    expires_at = time.time() + ttl_seconds
    
    try:
        async with aiosqlite.connect(CACHE_DB_PATH) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cache_entries (tool_hash, result, expires_at) VALUES (?, ?, ?)",
                (tool_hash, json.dumps(result), expires_at)
            )
            await db.commit()
    except Exception as e:
        print(f"[CacheManager] Erreur d'écriture: {e}")
