import aiosqlite
import json
import time
import hashlib
from typing import Optional, Any
import os
import asyncio

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

async def get_cache(tool_name: str, arguments: dict) -> Optional[Any]:
    arg_str = json.dumps(arguments, sort_keys=True)
    tool_hash = hashlib.md5(f"{tool_name}_{arg_str}".encode()).hexdigest()
    
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

async def set_cache(tool_name: str, arguments: dict, result: Any, ttl_seconds: int = 3600):
    arg_str = json.dumps(arguments, sort_keys=True)
    tool_hash = hashlib.md5(f"{tool_name}_{arg_str}".encode()).hexdigest()
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
