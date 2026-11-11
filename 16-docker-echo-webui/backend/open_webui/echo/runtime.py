"""
title: ECHO V6 Direct Disk Plugin Runtime
author: Wilfried BARNAVON
version: 6.0.0.1
description: Chargeur direct sur disque des Pipes, Filters, Tools, Actions et Events ECHO avec hot-reload st_mtime_ns.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.0.0.1: Chargeur direct sur disque des plugins ECHO avec hot-reload st_mtime_ns et stacktraces natives.

from __future__ import annotations

import importlib.util
import logging
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

log = logging.getLogger(__name__)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_FRONTMATTER_RE = re.compile(r"^\s*([a-z_]+):\s*(.*)\s*$", re.IGNORECASE)

FUNCTION_SUBDIRS: dict[str, str] = {
    "pipe": "owui-pipes",
    "filter": "owui-filters",
    "action": "owui-actions",
    "event": "owui-events",
}
TOOLS_SUBDIR = "owui-tools"
LIBS_SUBDIR = "owui-libs"


@dataclass
class _CachedEchoPlugin:
    mtime_ns: int
    file_path: str
    content: str
    instance: Any
    kind: str
    frontmatter: dict[str, str]
    module: ModuleType


# Cache mémoire indexé sur (category, plugin_id) -> _CachedEchoPlugin
_RUNTIME_CACHE: dict[tuple[str, str], _CachedEchoPlugin] = {}


def get_echo_plugin_root() -> Path:
    return Path(os.getenv("ECHO_PLUGIN_ROOT", "/opt/ECHO"))


def ensure_echo_sys_path() -> None:
    """Injecte de façon idempotente les bibliothèques partagées ECHO en tête de sys.path."""
    root = get_echo_plugin_root()
    candidates = [
        str(root / LIBS_SUBDIR),
        "/app/backend/echo_libs",
    ]
    for path_str in reversed(candidates):
        if os.path.isdir(path_str) and path_str not in sys.path:
            sys.path.insert(0, path_str)


def parse_echo_frontmatter(content: str) -> dict[str, str]:
    """Extrait le frontmatter docstring triple-quotes standard ECHO/Open WebUI."""
    lines = content.splitlines()
    if not lines or lines[0].strip() != '"""':
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if '"""' in line:
            break
        match = _FRONTMATTER_RE.match(line)
        if match:
            key, val = match.groups()
            meta[key.strip().lower()] = val.strip()
    return meta


def resolve_echo_tool_path(tool_id: str) -> Path | None:
    if not tool_id or not _SAFE_ID_RE.match(tool_id):
        return None
    candidate = get_echo_plugin_root() / TOOLS_SUBDIR / f"{tool_id}.py"
    return candidate if candidate.is_file() else None


def resolve_echo_function_path(function_id: str) -> tuple[Path, str] | None:
    if not function_id or not _SAFE_ID_RE.match(function_id):
        return None
    root = get_echo_plugin_root()
    for ftype, subdir in FUNCTION_SUBDIRS.items():
        candidate = root / subdir / f"{function_id}.py"
        if candidate.is_file():
            return candidate, ftype
    return None


def _import_module_from_disk(module_name: str, file_path: Path) -> ModuleType:
    """Charge un fichier Python directement depuis son chemin réel (stacktraces exactes)."""
    ensure_echo_sys_path()
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Impossible de créer ModuleSpec pour {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def _instantiate_function_object(module: ModuleType) -> tuple[Any, str]:
    if hasattr(module, "Pipe"):
        return module.Pipe(), "pipe"
    if hasattr(module, "Filter"):
        return module.Filter(), "filter"
    if hasattr(module, "Action"):
        return module.Action(), "action"
    if hasattr(module, "Event"):
        return module.Event(), "event"
    raise RuntimeError(f"Aucune classe Pipe/Filter/Action/Event trouvée dans {module.__file__}")


def _sync_owui_state_cache(
    request: Any,
    category: str,
    plugin_id: str,
    entry: _CachedEchoPlugin,
) -> None:
    if request is None or not hasattr(request, "app"):
        return
    state = request.app.state
    if category == "tool":
        sys.modules[f"tool_{plugin_id}"] = entry.module
        if not hasattr(state, "TOOLS"):
            state.TOOLS = {}
        if not hasattr(state, "TOOL_CONTENTS"):
            state.TOOL_CONTENTS = {}
        state.TOOLS[plugin_id] = entry.instance
        state.TOOL_CONTENTS[plugin_id] = entry.content
    else:
        sys.modules[f"function_{plugin_id}"] = entry.module
        if not hasattr(state, "FUNCTIONS"):
            state.FUNCTIONS = {}
        if not hasattr(state, "FUNCTION_CONTENTS"):
            state.FUNCTION_CONTENTS = {}
        state.FUNCTIONS[plugin_id] = entry.instance
        state.FUNCTION_CONTENTS[plugin_id] = entry.content


async def load_echo_tool_from_disk(
    tool_id: str,
    request: Any = None,
    sync_db_specs_on_reload: bool = True,
) -> tuple[Any, dict[str, str], ModuleType, str, bool] | None:
    """
    Charge un Tool ECHO depuis /opt/ECHO/owui-tools/{tool_id}.py avec cache st_mtime_ns.
    Retourne (instance, frontmatter, module, content, reloaded) ou None si absent du disque.
    """
    file_path = resolve_echo_tool_path(tool_id)
    if file_path is None:
        return None

    stat_res = file_path.stat()
    mtime_ns = stat_res.st_mtime_ns
    cache_key = ("tool", tool_id)
    cached = _RUNTIME_CACHE.get(cache_key)

    if cached is not None and cached.mtime_ns == mtime_ns and cached.file_path == str(file_path):
        _sync_owui_state_cache(request, "tool", tool_id, cached)
        return cached.instance, cached.frontmatter, cached.module, cached.content, False

    was_cached = cached is not None
    content = file_path.read_text(encoding="utf-8")
    frontmatter = parse_echo_frontmatter(content)
    module_name = f"echo_runtime_tool_{tool_id}"
    module = _import_module_from_disk(module_name, file_path)

    if not hasattr(module, "Tools"):
        raise RuntimeError(f"Aucune classe Tools trouvée dans {file_path}")
    instance = module.Tools()

    entry = _CachedEchoPlugin(
        mtime_ns=mtime_ns,
        file_path=str(file_path),
        content=content,
        instance=instance,
        kind="tool",
        frontmatter=frontmatter,
        module=module,
    )
    _RUNTIME_CACHE[cache_key] = entry
    _sync_owui_state_cache(request, "tool", tool_id, entry)
    log.info("[ECHO-RUNTIME] Tool '%s' chargé depuis %s (hot_reload=%s)", tool_id, file_path, was_cached)

    if was_cached and sync_db_specs_on_reload:
        try:
            from open_webui.models.tools import Tools
            from open_webui.utils.tools import get_tool_specs

            existing = await Tools.get_tool_by_id(tool_id)
            if existing is not None:
                specs = get_tool_specs(instance)
                await Tools.update_tool_by_id(
                    tool_id,
                    {"content": content, "specs": specs},
                    module=instance,
                )
        except Exception as exc:
            log.warning("[ECHO-RUNTIME] Échec resync specs DB pour '%s': %s", tool_id, exc)

    return instance, frontmatter, module, content, True


async def load_echo_function_from_disk(
    function_id: str,
    request: Any = None,
) -> tuple[Any, str, dict[str, str], ModuleType, str, bool] | None:
    """
    Charge une Function ECHO (pipe, filter, action, event) depuis /opt/ECHO/owui-* avec cache st_mtime_ns.
    Retourne (instance, function_type, frontmatter, module, content, reloaded) ou None si absente du disque.
    """
    resolved = resolve_echo_function_path(function_id)
    if resolved is None:
        return None
    file_path, _expected_dir_kind = resolved

    stat_res = file_path.stat()
    mtime_ns = stat_res.st_mtime_ns
    cache_key = ("function", function_id)
    cached = _RUNTIME_CACHE.get(cache_key)

    if cached is not None and cached.mtime_ns == mtime_ns and cached.file_path == str(file_path):
        _sync_owui_state_cache(request, "function", function_id, cached)
        return cached.instance, cached.kind, cached.frontmatter, cached.module, cached.content, False

    was_cached = cached is not None
    content = file_path.read_text(encoding="utf-8")
    frontmatter = parse_echo_frontmatter(content)
    module_name = f"echo_runtime_func_{function_id}"
    module = _import_module_from_disk(module_name, file_path)
    instance, ftype = _instantiate_function_object(module)

    entry = _CachedEchoPlugin(
        mtime_ns=mtime_ns,
        file_path=str(file_path),
        content=content,
        instance=instance,
        kind=ftype,
        frontmatter=frontmatter,
        module=module,
    )
    _RUNTIME_CACHE[cache_key] = entry
    _sync_owui_state_cache(request, "function", function_id, entry)
    log.info(
        "[ECHO-RUNTIME] Function '%s' (%s) chargée depuis %s (hot_reload=%s)",
        function_id,
        ftype,
        file_path,
        was_cached,
    )
    return instance, ftype, frontmatter, module, content, True


def clear_echo_runtime_cache() -> None:
    """Purge le cache mémoire runtime (utile pour les tests unitaires)."""
    _RUNTIME_CACHE.clear()
