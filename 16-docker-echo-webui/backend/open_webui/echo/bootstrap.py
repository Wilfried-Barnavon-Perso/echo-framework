"""
title: ECHO V6 Declarative Lifespan Bootstrap
author: Wilfried BARNAVON
version: 6.5.2.1
description: Provisioning déclaratif au démarrage FastAPI remplaçant intégralement config-owui.sh.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.2.1: Correction de l'import initialize_runtime_config depuis open_webui.main.
# 6.0.0.1: Provisioning déclaratif lifespan remplaçant config-owui.sh.

from __future__ import annotations

import base64
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from open_webui.echo.runtime import (
    FUNCTION_SUBDIRS,
    TOOLS_SUBDIR,
    ensure_echo_sys_path,
    get_echo_plugin_root,
    load_echo_function_from_disk,
    load_echo_tool_from_disk,
)
from open_webui.models.access_grants import AccessGrants
from open_webui.models.auths import Auths
from open_webui.models.config import Config
from open_webui.models.functions import FunctionForm, FunctionMeta, Functions
from open_webui.models.models import ModelMeta, ModelModel, ModelParams, Models
from open_webui.models.tools import ToolForm, ToolMeta, Tools
from open_webui.models.users import Users
from open_webui.utils.auth import get_password_hash
from open_webui.utils.tools import get_tool_specs

log = logging.getLogger(__name__)

DEFAULT_ADMIN_EMAIL = "admin@echo.local"
DEFAULT_ADMIN_NAME = "ECHO Architect"
PUBLIC_READ_GRANT = [{"principal_type": "user", "principal_id": "*", "permission": "read"}]


def _list_plugin_ids(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(
        p.stem
        for p in directory.glob("*.py")
        if p.is_file() and not p.name.startswith("_")
    )


async def _ensure_owner_admin_user() -> str:
    """Garantit l'existence d'un administrateur propriétaire sans compte de service factice."""
    admin_email = (os.getenv("WEBUI_ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL).strip().lower()
    admin_name = (os.getenv("WEBUI_ADMIN_NAME") or DEFAULT_ADMIN_NAME).strip()
    admin_password = os.getenv("WEBUI_ADMIN_PASSWORD") or os.getenv("ECHO_ADMIN_PASSWORD") or ""

    user = await Users.get_user_by_email(admin_email)
    if user is not None:
        if user.role != "admin":
            await Users.update_user_role_by_id(user.id, "admin")
        return user.id

    first_admin = await Users.get_super_admin_user()
    if first_admin is not None and not admin_password:
        return first_admin.id

    pwd = admin_password or uuid.uuid4().hex
    hashed = await get_password_hash(pwd)
    created = await Auths.insert_new_auth(
        email=admin_email,
        password=hashed,
        name=admin_name,
        role="admin",
    )
    if created is None:
        raise RuntimeError(f"[ECHO-BOOTSTRAP] Impossible de créer l'administrateur initial ({admin_email})")
    log.info("[ECHO-BOOTSTRAP] Compte administrateur initialisé : %s (%s)", admin_email, created.id)
    return created.id


async def _sync_tools(root: Path, owner_id: str) -> list[str]:
    tools_dir = root / TOOLS_SUBDIR
    discovered_ids = _list_plugin_ids(tools_dir)
    if not tools_dir.is_dir():
        log.warning("[ECHO-BOOTSTRAP] Dossier tools introuvable (%s), synchronisation tools ignorée.", tools_dir)
        return []

    existing_tools = await Tools.get_tools()
    existing_map = {t.id: t for t in existing_tools}

    # Purge des outils ghosts absents du disque
    for old_id in set(existing_map.keys()) - set(discovered_ids):
        await Tools.delete_tool_by_id(old_id)
        log.info("[ECHO-BOOTSTRAP] Tool ghost purgé : %s", old_id)

    synced_ids: list[str] = []
    for tool_id in discovered_ids:
        loaded = await load_echo_tool_from_disk(tool_id, sync_db_specs_on_reload=False)
        if loaded is None:
            continue
        instance, frontmatter, _mod, content, _reloaded = loaded
        name = frontmatter.get("title") or tool_id
        description = frontmatter.get("description") or name
        meta = ToolMeta(
            description=description,
            manifest=frontmatter,
            has_user_valves=hasattr(instance, "UserValves"),
        )
        specs = get_tool_specs(instance)

        if tool_id not in existing_map:
            form = ToolForm(
                id=tool_id,
                name=name,
                content=content,
                meta=meta,
                access_grants=PUBLIC_READ_GRANT,
                commit_message="ECHO V6 declarative bootstrap",
            )
            await Tools.insert_new_tool(owner_id, form, specs, module=instance)
        else:
            await Tools.update_tool_by_id(
                tool_id,
                {
                    "name": name,
                    "content": content,
                    "specs": specs,
                    "meta": meta.model_dump(),
                    "is_active": True,
                },
                user_id=owner_id,
                module=instance,
            )
        await AccessGrants.set_access_grants("tool", tool_id, PUBLIC_READ_GRANT)
        synced_ids.append(tool_id)

    return synced_ids


async def _sync_functions(root: Path, owner_id: str) -> dict[str, list[str]]:
    by_type: dict[str, list[str]] = {k: [] for k in FUNCTION_SUBDIRS}
    any_dir_present = any((root / sub).is_dir() for sub in FUNCTION_SUBDIRS.values())
    if not any_dir_present:
        log.warning("[ECHO-BOOTSTRAP] Aucun dossier owui-{pipes,filters,actions,events} monté sous %s.", root)
        return by_type

    discovered: dict[str, str] = {}
    for expected_type, subdir in FUNCTION_SUBDIRS.items():
        for fid in _list_plugin_ids(root / subdir):
            discovered[fid] = expected_type

    existing_funcs = await Functions.get_functions()
    existing_map = {f.id: f for f in existing_funcs}

    # Purge des fonctions ghosts absentes du disque (ex: anciens filtres V5 supprimés)
    for old_id in set(existing_map.keys()) - set(discovered.keys()):
        await Functions.delete_function_by_id(old_id)
        log.info("[ECHO-BOOTSTRAP] Function ghost purgée : %s", old_id)

    for func_id, _dir_type in sorted(discovered.items()):
        loaded = await load_echo_function_from_disk(func_id)
        if loaded is None:
            continue
        instance, ftype, frontmatter, _mod, content, _reloaded = loaded
        name = frontmatter.get("title") or func_id
        description = frontmatter.get("description") or name
        meta = FunctionMeta(description=description, manifest=frontmatter)
        # Invariant ECHO : pipes, filters et actions sont globaux ; events ont is_global=False
        is_global = ftype != "event"

        if func_id not in existing_map:
            form = FunctionForm(
                id=func_id,
                name=name,
                content=content,
                meta=meta,
                commit_message="ECHO V6 declarative bootstrap",
            )
            await Functions.insert_new_function(owner_id, ftype, form, module=instance)
        await Functions.update_function_by_id(
            func_id,
            {
                "name": name,
                "type": ftype,
                "content": content,
                "meta": meta.model_dump(),
                "is_active": True,
                "is_global": is_global,
            },
            user_id=owner_id,
            module=instance,
        )
        by_type.setdefault(ftype, []).append(func_id)

    return by_type


async def _sync_master_model(
    root: Path,
    owner_id: str,
    tool_ids: list[str],
    by_type: dict[str, list[str]],
) -> str | None:
    config_dir = root / "config"
    model_cfg_path = config_dir / "model-config.json"
    if not model_cfg_path.is_file():
        log.warning("[ECHO-BOOTSTRAP] Fichier %s introuvable, sync modèle ignorée.", model_cfg_path)
        return None

    raw_cfg = json.loads(model_cfg_path.read_text(encoding="utf-8"))
    entry = raw_cfg[0] if isinstance(raw_cfg, list) and raw_cfg else raw_cfg
    if not isinstance(entry, dict) or not entry.get("id"):
        return None

    model_id = str(entry["id"])
    params_dict = dict(entry.get("params") or {})

    # Injection du Kernel Statique (system-prompt.md)
    sys_prompt_candidates = [
        config_dir / "system-prompt.md",
        Path("/app/backend/data/system-prompt.md"),
    ]
    for sp_path in sys_prompt_candidates:
        if sp_path.is_file():
            params_dict["system"] = sp_path.read_text(encoding="utf-8")
            break

    meta_dict = dict(entry.get("meta") or {})
    meta_dict.pop("_comment_resources", None)
    filter_ids = sorted(by_type.get("filter", []))
    action_ids = sorted(by_type.get("action", []))
    meta_dict["toolIds"] = sorted(tool_ids)
    meta_dict["filterIds"] = filter_ids
    meta_dict["defaultFilterIds"] = list(filter_ids)
    meta_dict["actionIds"] = action_ids

    # Encodage Data-URI de l'avatar local si disponible
    img_filename = entry.get("local_image_filename") or "logo-echo.png"
    img_candidates = [
        root / "echo-images" / img_filename,
        root / "_assets" / "images" / img_filename,
    ]
    for img_path in img_candidates:
        if img_path.is_file():
            b64 = base64.b64encode(img_path.read_bytes()).decode("ascii")
            meta_dict["profile_image_url"] = f"data:image/png;base64,{b64}"
            break

    now = int(time.time())
    model_obj = ModelModel(
        id=model_id,
        user_id=owner_id,
        base_model_id=entry.get("base_model_id"),
        name=entry.get("name", "Écho"),
        params=ModelParams.model_validate(params_dict),
        meta=ModelMeta.model_validate(meta_dict),
        access_grants=[],
        is_active=True,
        updated_at=now,
        created_at=now,
    )
    await Models.sync_models(owner_id, [model_obj])
    await AccessGrants.set_access_grants(
        "model",
        model_id,
        entry.get("access_grants") or PUBLIC_READ_GRANT,
    )
    return model_id


async def _sync_global_configs(root: Path, app: FastAPI) -> int:
    config_dir = root / "config"
    updates: dict[str, Any] = {}

    settings_path = config_dir / "webui-settings.json"
    if settings_path.is_file():
        raw_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        if isinstance(raw_settings, dict):
            for k, v in raw_settings.items():
                if k != "version":
                    updates[k] = v

    banners_path = config_dir / "webui-banners.json"
    if banners_path.is_file():
        raw_banners = json.loads(banners_path.read_text(encoding="utf-8"))
        banners_list = raw_banners.get("banners", []) if isinstance(raw_banners, dict) else raw_banners
        if isinstance(banners_list, list):
            now_ts = int(time.time())
            normalized = [
                {**b, "timestamp": b.get("timestamp") or now_ts}
                for b in banners_list
                if isinstance(b, dict)
            ]
            updates["ui.banners"] = normalized

    if updates:
        await Config.upsert(updates)
        from open_webui.main import initialize_runtime_config

        await initialize_runtime_config(app)
    return len(updates)


async def bootstrap_echo_runtime(app: FastAPI) -> dict[str, Any]:
    """Point d'entrée unique exécuté dans le lifespan de FastAPI."""
    ensure_echo_sys_path()
    root = get_echo_plugin_root()
    if not root.is_dir():
        log.info("[ECHO-BOOTSTRAP] Racine %s absente (mode test/hors conteneur), bootstrap ignoré.", root)
        return {"skipped": True, "root": str(root)}

    owner_id = await _ensure_owner_admin_user()
    tool_ids = await _sync_tools(root, owner_id)
    by_type = await _sync_functions(root, owner_id)
    model_id = await _sync_master_model(root, owner_id, tool_ids, by_type)
    cfg_count = await _sync_global_configs(root, app)

    summary = {
        "skipped": False,
        "owner_id": owner_id,
        "tools_count": len(tool_ids),
        "functions_by_type": {k: len(v) for k, v in by_type.items()},
        "model_id": model_id,
        "config_keys_synced": cfg_count,
    }
    app.state.echo_bootstrap_summary = summary
    log.info("[ECHO-BOOTSTRAP] Provisioning déclaratif terminé : %s", summary)
    return summary
