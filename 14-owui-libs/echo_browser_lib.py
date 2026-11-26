# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Browser Lib
author: ECHO Framework
version: 6.23.0.1
description: Composant système interne : ECHO Browser Lib.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.23.0.1: Migration de BrowserToolDispatcher, deploy_browser_navigation_monitor et helpers DOM/sémaphore depuis echo_subagent_hub pour supprimer la dépendance circulaire.
# 6.18.0.1: Extraction du helper synchrone SSOT cleanup_expired_browserviews_sync (partagé ECHO-UI et Admin Manager) et délégation asyncio.to_thread.
# 6.16.0.1: Suppression de iter_screencast_stream SSE et ajout de build_browser_ws_url et resolve_chat_browser_session_id.
# 6.15.1.1: Passage par défaut de min_age_sec à BROWSER_TRANSIENT_GRACE_SEC (1800.0s) dans cleanup_expired_browserviews et application systématique du délai de grâce y compris sur force_discard_sids.
# 6.15.0.1: Ajout de min_age_sec dans cleanup_expired_browserviews pour préserver les captures récentes lors des purges passives sans affecter force_discard_sids.

import asyncio
import json
import os
import threading
import time
import uuid
import httpx
import logging
from typing import Any, Callable, Dict, List, Optional

# On suppose que echo_constants est disponible dans le chemin PYTHONPATH (/app/backend/echo_libs)
from echo_constants import (
    BROWSER_CLIP_EXT,
    BROWSER_CLIP_MIME,
    BROWSER_MAX_TRANSIENT_CLIPS_MB,
    BROWSER_MAX_TRANSIENT_CLIPS_PER_CHAT,
    BROWSER_TRANSIENT_GRACE_SEC,
    BROWSER_VIEWS_DIR_NAME,
    BROWSER_VIEW_STATUS,
    ECHO_MAX_CONCURRENT_BROWSERS,
    ECHO_MAX_PARALLEL_TOOLS_PER_TURN,
    NAVIGATION_ENGINE_URL,
    DEFAULT_VISION_GRID_STEP,
    EPHEMERAL_RENDER_ENDPOINT,
    EPHEMERAL_RENDER_VIEWPORT_W,
    EPHEMERAL_RENDER_VIEWPORT_H,
    EPHEMERAL_RENDER_SETTLE_MS,
    PRUNE_CONTENT_THRESHOLD,
)
from echo_paths import get_echo_session_path, sanitize_echo_id
from echo_state_manager import EchoStateManager

logger = logging.getLogger(__name__)

BROWSER_TOOLS_SCHEMA = [
    {
        "name": "action_interact_a11y",
        "description": "Interagit avec un élément de l'arbre A11y (via role, text ou label). Le Modèle PEUT appeler cet outil plusieurs fois dans le même tour pour effectuer des actions groupées.",
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["role", "label", "text"], "description": "La méthode de ciblage (role=ex:button/radio, label=attribut aria-label, text=texte brut visible)."},
                "value": {"type": "string", "description": "La valeur associée à la méthode de ciblage (ex: 'button', 'Je suis d\\'accord')."},
                "name": {"type": "string", "description": "(Optionnel) Si method='role', permet de filtrer par le nom du rôle (ex: 'Accepter') pour cibler précisément un bouton ou lien."},
                "action_type": {"type": "string", "enum": ["click", "type", "select", "hover", "download", "save_target"], "description": "Le type d'interaction (select=sélectionne l'option text_to_type dans une liste, download force un clic et attend le fichier, save_target extrait l'URL du lien/image et la télécharge furtivement)."},
                "text_to_type": {"type": "string", "description": "(Optionnel) Le texte à insérer si action_type='type', ou l'option à sélectionner si action_type='select'."},
                "clear_before": {"type": "boolean", "description": "(Optionnel) Efface le champ avant de taper (défaut: true)."}
            },
            "required": ["method", "value", "action_type"]
        }
    },
    {
        "name": "action_interact_dom",
        "description": "Interagit via l'index du DOM Map ou les coordonnées Vision X/Y. Le Modèle PEUT appeler cet outil plusieurs fois dans le même tour pour des actions groupées.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["click", "click_current", "type", "select", "hover", "download", "save_target"], "description": "Le type d'interaction (click_current=pression sur place sans bouger, select=sélectionne l'option, download force un clic et attend le fichier, save_target extrait l'URL et la télécharge furtivement)."},
                "index": {"type": "integer", "description": "L'ID numérique de l'élément (indiqué entre crochets sur la carte du DOM). À utiliser en priorité absolue."},
                "x": {"type": "integer", "description": "Coordonnée X en pixels (à n'utiliser QUE si l'index est introuvable ou en cas de vérification humaine, suite à une action_inspect_page avec target='vision')."},
                "y": {"type": "integer", "description": "Coordonnée Y en pixels (à n'utiliser QUE si l'index est introuvable)."},
                "text_to_type": {"type": "string", "description": "(Optionnel) Le texte à insérer si action_type='type' ou 'select'."},
                "clear_before": {"type": "boolean", "description": "(Optionnel) Efface le champ avant de taper (défaut: true)."}
            },
            "required": ["action_type"]
        }
    },
    {
        "name": "action_inspect_page",
        "description": "Extrait des informations de la page (a11y_tree, dom_map, vision, etc.). Le Modèle PEUT appeler cet outil plusieurs fois en parallèle avec des 'target' différentes dans le même tour.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["a11y_tree", "dom_map", "vision", "read_text", "read_html", "search_dom", "url"], "description": "L'information à extraire."},
                "index": {"type": "integer", "description": "(Optionnel) L'ID de l'élément si target='url'."},
                "value": {"type": "string", "description": "(Optionnel) Le texte court à rechercher si target='search_dom'. Cherche dans TOUTE la page, y fait défiler et marque l'élément 'found: true' dans la carte DOM."},
                "vision_grid": {"type": "boolean", "description": "(Optionnel) True pour calquer une grille orthonormée si target='vision' (requis pour résoudre des vérifications humaines)."}
            },
            "required": ["target"]
        }
    },
    {
        "name": "action_browser_control",
        "description": "Pilote globalement le navigateur (navigation, défilement, clavier, attente).",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "enum": ["navigate", "scroll", "press_key", "pause", "refresh", "reset", "tab_new", "tab_switch", "tab_close"], "description": "La commande globale à exécuter."},
                "value": {"type": "string", "description": "(Optionnel) L'URL absolue pour navigate/tab_new, la direction ('up','down','top','bottom') pour scroll, la touche ('Enter','Tab') pour press_key, le délai en secondes pour pause, ou l'index pour tab_switch."}
            },
            "required": ["command"]
        }
    },
    {
        "name": "action_zoom_in",
        "description": "Agrandit réellement la zone (×1 à ×4). Les graduations donnent les coordonnées ABSOLUES de la page. Viseur rouge = centre, anneau cyan = souris.",
        "parameters": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer", "description": "Coordonnée X du coin supérieur gauche."},
                "y1": {"type": "integer", "description": "Coordonnée Y du coin supérieur gauche."},
                "x2": {"type": "integer", "description": "Coordonnée X du coin inférieur droit."},
                "y2": {"type": "integer", "description": "Coordonnée Y du coin inférieur droit."}
            },
            "required": ["x1", "y1", "x2", "y2"]
        }
    },
    {
        "name": "action_zoom_out",
        "description": "Annule le zoom actuel et revient à la vue globale de l'écran.",
        "parameters": {"type": "object", "properties": {}}
    }
]

_browser_http_client = None

def _get_browser_client(timeout: int) -> httpx.AsyncClient:
    global _browser_http_client
    if _browser_http_client is None or _browser_http_client.is_closed:
        _browser_http_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100)
        )
    return _browser_http_client

async def req_to_browser(timeout: int, endpoint: str, data: dict = None, user_id: str = "anonymous") -> dict:
    """Effectue une requête POST asynchrone vers le Browser Agent."""
    url = f"{NAVIGATION_ENGINE_URL}{endpoint}"
    headers = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": str(user_id)}
    client = _get_browser_client(timeout)
    try:
        # Le timeout doit être passé par requête pour overrider le timeout d'initialisation du Singleton
        resp = await client.post(url, json=data or {}, headers=headers, timeout=timeout)
        return resp.json()
    except Exception as e:
        return {"status": "error", "message": f"Worker inaccessible : {str(e)}"}


async def get_active_browser_sessions(
    user_id: str,
    chat_id: Optional[str] = None,
    timeout: float = 5.0,
) -> Optional[set[str]]:
    """Interroge GET /sessions sur le Browser Worker et retourne l'ensemble des session_id actifs (ou None si injoignable)."""
    url = f"{NAVIGATION_ENGINE_URL}/sessions"
    params = {}
    if user_id:
        params["user_id"] = str(user_id)
    if chat_id:
        params["chat_id"] = str(chat_id)
    client = _get_browser_client(int(max(1, timeout)))
    try:
        resp = await client.get(url, params=params, timeout=timeout)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("status") == "success":
            return set(data.get("active_sessions") or [])
    except Exception:
        return None
    return None


def _collect_clip_names(file_id: str, meta: dict) -> list[str]:
    """Collecte les noms de fichiers clips candidats (dédupliqués, ordre préservé)."""
    candidates: list[str] = []
    raw_clip = os.path.basename(str((meta or {}).get("clip_filename") or ""))
    if raw_clip:
        candidates.append(raw_clip)
    default_clip = f"{sanitize_echo_id(file_id)}{BROWSER_CLIP_EXT}"
    if default_clip and default_clip not in candidates:
        candidates.append(default_clip)
    return candidates


def enforce_transient_clip_budget(
    user_id: str,
    chat_id: str,
    max_clips: int = BROWSER_MAX_TRANSIENT_CLIPS_PER_CHAT,
    max_mb: int = BROWSER_MAX_TRANSIENT_CLIPS_MB,
) -> dict:
    """Évince les plus anciens clips MP4 éphémères au-delà du budget (nombre ou taille) sans toucher aux frames ni aux retained."""
    if not chat_id:
        return {"evicted": 0}
    db_path = get_echo_session_path(user_id, chat_id, "db")
    bv_dir = get_echo_session_path(user_id, chat_id, BROWSER_VIEWS_DIR_NAME)
    if not os.path.isfile(db_path) or not os.path.isdir(bv_dir):
        return {"evicted": 0}

    state = EchoStateManager(user_id=user_id, chat_id=chat_id)
    bv_res = state.get_resources(resource_type="browserview") or []
    items = []
    total_bytes = 0

    for r in bv_res:
        raw_meta = r.get("metadata")
        meta: dict = {}
        if isinstance(raw_meta, dict):
            meta = dict(raw_meta)
        elif isinstance(raw_meta, str) and raw_meta:
            try:
                parsed = json.loads(raw_meta)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                meta = {}

        is_retained = (
            r.get("status") == BROWSER_VIEW_STATUS["RETAINED"]
            or meta.get("retained") is True
        )
        if is_retained:
            continue

        clip_fp = None
        for cname in _collect_clip_names(r["id"], meta):
            cand = os.path.join(bv_dir, cname)
            if os.path.isfile(cand):
                clip_fp = cand
                break
        if not clip_fp:
            continue

        try:
            size = os.path.getsize(clip_fp)
        except OSError:
            continue
        created_at = r.get("created_at", 0) or 0
        items.append((created_at, str(r["id"]), r, meta, clip_fp, size))
        total_bytes += size

    items.sort(key=lambda it: (it[0], it[1]))
    max_bytes = max_mb * 1024 * 1024
    evicted = 0

    while items and (len(items) > max_clips or total_bytes > max_bytes):
        _, _, res, meta, fp, size = items.pop(0)
        try:
            if os.path.isfile(fp):
                os.unlink(fp)
        except Exception:
            pass
        meta["clip_filename"] = None
        meta["clip_duration_ms"] = 0
        meta["clip_size_bytes"] = 0
        meta["clip_purged"] = True
        try:
            state.update_resource_fields(
                res["id"],
                metadata=json.dumps(meta, ensure_ascii=False),
            )
        except Exception:
            pass
        total_bytes -= size
        evicted += 1

    return {"evicted": evicted}


def cleanup_expired_browserviews_sync(
    user_id: str,
    chat_id: str,
    active_session_ids: Optional[set[str]] = None,
    force_discard_sids: Optional[set[str]] = None,
    min_age_sec: float = BROWSER_TRANSIENT_GRACE_SEC,
) -> dict:
    """Purge synchrone SSOT des captures et clips browserviews éphémères (partagée ECHO-UI & Admin Manager)."""
    if not chat_id:
        return {"purged": 0, "clips_evicted": 0}
    if active_session_ids is None and not force_discard_sids:
        return {"purged": 0, "clips_evicted": 0}
    db_path = get_echo_session_path(user_id, chat_id, "db")
    if not os.path.isfile(db_path):
        return {"purged": 0, "clips_evicted": 0}

    state = EchoStateManager(user_id=user_id, chat_id=chat_id)
    bv_res = state.get_resources(resource_type="browserview") or []
    legacy_media = [
        r for r in (state.get_resources(resource_type="media") or [])
        if str(r.get("name") or "").endswith("_frame.jpg")
    ]
    all_candidates = bv_res + legacy_media

    bv_dir = get_echo_session_path(user_id, chat_id, BROWSER_VIEWS_DIR_NAME)
    files_dir = get_echo_session_path(user_id, chat_id, "files")
    kept_filenames: set[str] = set()
    purged = 0
    now = time.time()

    for r in all_candidates:
        raw_meta = r.get("metadata")
        meta: dict = {}
        if isinstance(raw_meta, dict):
            meta = raw_meta
        elif isinstance(raw_meta, str) and raw_meta:
            try:
                parsed = json.loads(raw_meta)
                if isinstance(parsed, dict):
                    meta = parsed
            except Exception:
                meta = {}

        is_retained = (
            r.get("status") == BROWSER_VIEW_STATUS["RETAINED"]
            or meta.get("retained") is True
        )
        fname = os.path.basename(str(r.get("name") or "")) or f"{sanitize_echo_id(r['id'])}_frame.jpg"
        clip_names = _collect_clip_names(r["id"], meta)
        if is_retained:
            kept_filenames.add(fname)
            kept_filenames.update(clip_names)
            continue

        sess_id = meta.get("session_id") or chat_id
        is_forced = force_discard_sids is not None and sess_id in force_discard_sids
        is_passive_expired = active_session_ids is not None and sess_id not in active_session_ids
        should_purge = is_forced or is_passive_expired
        if (is_forced or is_passive_expired) and min_age_sec > 0:
            item_ts = float(meta.get("timestamp") or r.get("created_at") or 0)
            if item_ts > 0 and (now - item_ts) < min_age_sec:
                kept_filenames.add(fname)
                kept_filenames.update(clip_names)
                continue
        if not should_purge:
            kept_filenames.add(fname)
            kept_filenames.update(clip_names)
            continue

        for base_d in (bv_dir, files_dir):
            for cand_name in (fname, *clip_names):
                cand = os.path.join(base_d, cand_name)
                if os.path.isfile(cand):
                    try:
                        os.unlink(cand)
                    except Exception:
                        pass
        state.delete_resource(r["id"])
        purged += 1

    if os.path.isdir(bv_dir):
        try:
            for entry in os.listdir(bv_dir):
                if not entry.endswith(("_frame.jpg", BROWSER_CLIP_EXT)) or entry in kept_filenames:
                    continue
                fp = os.path.join(bv_dir, entry)
                if os.path.isfile(fp) and (now - os.path.getmtime(fp) > 60.0):
                    try:
                        os.unlink(fp)
                        purged += 1
                    except Exception:
                        pass
        except Exception:
            pass

    budget_res = enforce_transient_clip_budget(user_id, chat_id)
    return {"purged": purged, "clips_evicted": int((budget_res or {}).get("evicted", 0))}


async def cleanup_expired_browserviews(
    user_id: str,
    chat_id: str,
    active_session_ids: Optional[set[str]] = None,
    force_discard_sids: Optional[set[str]] = None,
    min_age_sec: float = BROWSER_TRANSIENT_GRACE_SEC,
) -> dict:
    """Purge les captures browserviews éphémères dont la session Playwright est expirée ou réinitialisée."""
    if not chat_id:
        return {"purged": 0, "clips_evicted": 0}
    db_path = get_echo_session_path(user_id, chat_id, "db")
    if not os.path.isfile(db_path):
        return {"purged": 0, "clips_evicted": 0}

    if active_session_ids is None:
        active_session_ids = await get_active_browser_sessions(user_id, chat_id)
    if active_session_ids is None and not force_discard_sids:
        return {"purged": 0, "clips_evicted": 0}

    return await asyncio.to_thread(
        cleanup_expired_browserviews_sync,
        user_id,
        chat_id,
        active_session_ids,
        force_discard_sids,
        min_age_sec,
    )


def build_browser_ws_url(session_id: str) -> str:
    """Convertit NAVIGATION_ENGINE_URL vers l'URL WebSocket /ws/screencast/{session_id}."""
    base = str(NAVIGATION_ENGINE_URL or "").rstrip("/")
    if base.startswith("https://"):
        ws_base = "wss://" + base[len("https://"):]
    elif base.startswith("http://"):
        ws_base = "ws://" + base[len("http://"):]
    else:
        ws_base = base
    return f"{ws_base}/ws/screencast/{session_id}"


async def resolve_chat_browser_session_id(user_id: str, chat_id: str) -> Optional[str]:
    """Interroge get_active_browser_sessions() pour retrouver un session_id actif de (user_id, chat_id)."""
    if not chat_id:
        return None
    sessions = await get_active_browser_sessions(user_id=user_id, chat_id=chat_id)
    if not sessions:
        return None
    return sorted(sessions)[0]


async def render_ephemeral_html(
    html: str,
    timeout_ms: int = 15000,
    settle_ms: int = EPHEMERAL_RENDER_SETTLE_MS,
    viewport_w: int = EPHEMERAL_RENDER_VIEWPORT_W,
    viewport_h: int = EPHEMERAL_RENDER_VIEWPORT_H,
    user_id: str = "system",
) -> dict:
    """Rendu HTML incognito en mémoire vive via le Browser Worker (Zéro-Disque)."""
    req_timeout = max(20, (int(timeout_ms) // 1000) + 10)
    return await req_to_browser(
        timeout=req_timeout,
        endpoint=EPHEMERAL_RENDER_ENDPOINT,
        data={
            "html": html,
            "timeout_ms": timeout_ms,
            "settle_ms": settle_ms,
            "viewport_w": viewport_w,
            "viewport_h": viewport_h,
        },
        user_id=user_id,
    )


class EchoBrowserLib:
    """Encapsule les actions réseau pour le sous-agent navigateur."""
    def __init__(self, timeout: int, session_id: str, user_id: str, vision_grid_step: int = DEFAULT_VISION_GRID_STEP, chat_id: str = None):
        self.timeout = timeout
        self.session_id = session_id
        self.user_id = user_id
        self.vision_grid_step = vision_grid_step
        self.chat_id = chat_id or session_id

    async def _action(self, action: str, params: dict = None) -> dict:
        params = params or {}
        if action == "inspect_page" and params.get("target") == "vision" and params.get("vision_grid"):
            params["vision_grid_step"] = self.vision_grid_step
        return await req_to_browser(self.timeout, "/action", {"session_id": self.session_id, "chat_id": self.chat_id, "action": action, "params": params}, self.user_id)

    async def highlight(self) -> dict:
        return await self._action("inspect_page", {"target": "vision", "vision_grid": False})

    async def vision_grid(self) -> dict:
        return await self._action("inspect_page", {"target": "vision", "vision_grid": True})

    async def ping(self) -> dict:
        return await self._action("ping")

    async def start_session(self, idle_timeout: int, mode: str) -> dict:
        return await req_to_browser(self.timeout, "/start_session", {
            "session_id": self.session_id, 
            "chat_id": self.chat_id,
            "idle_timeout": idle_timeout, 
            "mode": mode
        }, self.user_id)

    async def start_screencast(self) -> dict:
        return await req_to_browser(self.timeout, "/screencast/start", {"session_id": self.session_id}, self.user_id)

    async def stop_screencast(self) -> dict:
        return await req_to_browser(max(self.timeout, 30), "/screencast/stop", {"session_id": self.session_id}, self.user_id)
        
    async def reset_session(self) -> dict:
        return await self._action("browser_control", {"command": "reset"})

    async def action_interact_a11y(self, method: str, value: str, action_type: str, name: str = None, text_to_type: str = None, clear_before: bool = True, download_file_id: str = None) -> dict:
        return await self._action("interact_a11y", {"method": method, "value": value, "name": name, "action_type": action_type, "text_to_type": text_to_type, "clear_before": clear_before, "download_file_id": download_file_id})

    async def action_interact_dom(self, action_type: str, index: int = None, x: int = None, y: int = None, text_to_type: str = "", clear_before: bool = True, download_file_id: str = None) -> dict:
        return await self._action("interact_dom", {"action_type": action_type, "index": index, "x": x, "y": y, "text_to_type": text_to_type, "clear_before": clear_before, "download_file_id": download_file_id})

    async def vision_capture(self, grid: bool, zoom_box: dict = None) -> dict:
        """Capture grille/zoom sans réindexation (la carte DOM du modèle reste valide)."""
        return await self._action("inspect_page", {"target": "vision", "vision_grid": grid or bool(zoom_box),
                                                   "zoom_box": zoom_box, "reindex": False})

    async def action_inspect_page(self, target: str, index: int = None, value: str = "", vision_grid: bool = False) -> dict:
        if target == "vision":
            # Cette fonction est interceptée par l'orchestrateur, on flag la demande de grille
            return {"status": "success", "message": "Capture d'écran demandée.", "_trigger_vision": True, "grid": vision_grid}
        return await self._action("inspect_page", {"target": target, "index": index, "value": value, "vision_grid": vision_grid})

    async def action_browser_control(self, command: str, value: str = "") -> dict:
        return await self._action("browser_control", {"command": command, "value": str(value) if value is not None else ""})

    async def action_zoom_in(self, x1: int, y1: int, x2: int, y2: int) -> dict:
        return {
            "status": "success", 
            "_trigger_vision": True, 
            "grid": True, 
            "is_zoom": True, 
            "zoom_box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}
        }

    async def action_zoom_out(self) -> dict:
        return {
            "status": "success", 
            "_trigger_vision": True, 
            "grid": True,
            "is_zoom_out": True
        }
        
    def get_registry(self) -> Dict[str, Callable]:
        """Retourne le mapping name -> callable pour l'interception de Gemini."""
        return {
            "action_interact_a11y": self.action_interact_a11y,
            "action_interact_dom": self.action_interact_dom,
            "action_inspect_page": self.action_inspect_page,
            "action_browser_control": self.action_browser_control,
            "action_zoom_in": self.action_zoom_in,
            "action_zoom_out": self.action_zoom_out
        }


_BROWSER_LIB_LOCK = threading.RLock()
_BROWSER_SEMAPHORES: Dict[int, asyncio.Semaphore] = {}


def _get_browser_loop_id() -> int:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return 0


def get_global_browser_semaphore() -> asyncio.Semaphore:
    """Retourne le sémaphore global d'instances Playwright Chromium."""
    loop_id = _get_browser_loop_id()
    with _BROWSER_LIB_LOCK:
        sem = _BROWSER_SEMAPHORES.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(ECHO_MAX_CONCURRENT_BROWSERS)
            _BROWSER_SEMAPHORES[loop_id] = sem
        return sem


def prune_browser_heavy_context(
    history_list: List[dict], threshold: int, is_zooming: bool = False
) -> None:
    """Élagage proactif des payloads DOM/A11y/inlineData anciens dans l'historique navigateur."""
    heavy_keys = ("dom_map", "a11y_tree", "content", "html", "search_dom")
    for i, msg in enumerate(history_list):
        if "parts" in msg and not is_zooming and i < len(history_list) - 4:
            msg["parts"] = [
                p for p in msg["parts"] if "inlineData" not in p
            ]
        for part in msg.get("parts", []):
            if "functionResponse" in part:
                fr = part["functionResponse"]
                resp = fr.get("response", {})
                if isinstance(resp, dict):
                    for key in heavy_keys:
                        if key in resp:
                            val = resp[key]
                            if isinstance(val, str) and (
                                "[PURGED" in val or "[TRUNCATED" in val
                            ):
                                continue
                            if isinstance(val, str) and len(val) > threshold:
                                resp[key] = (
                                    val[:threshold]
                                    + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"
                                )
                            elif (
                                not isinstance(val, str)
                                and len(str(val)) > threshold
                            ):
                                resp[key] = "[PURGED_OBSOLETE_UI_STATE]"
            if "text" in part:
                text = part["text"]
                if (
                    isinstance(text, str)
                    and text.startswith("Voici les éléments interactifs actuels")
                    and "[TRUNCATED" not in text
                    and len(text) > threshold
                ):
                    part["text"] = (
                        text[:threshold] + "\n... [TRUNCATED_TO_PREVENT_BLOAT]"
                    )


def format_browser_dom_view(res_view: dict) -> str:
    """Formate la carte DOM et l'état de navigation pour injection dans l'historique navigateur."""
    dom_data = res_view.get("metadata", [])
    dom_text = json.dumps(dom_data, ensure_ascii=False)[:60000]
    vp = res_view.get("viewport")
    vp_text = (
        f"\nDimensions de l'écran (Viewport) : {vp['width']}x{vp['height']} pixels."
        if vp
        else ""
    )
    url = res_view.get("url") or ""
    url_text = f"\nURL active : {url}" if url else ""
    http_status = res_view.get("http_status")
    http_status_text = (res_view.get("http_status_text") or "").strip()
    http_line = ""
    if http_status is not None:
        status_str = f"{http_status} {http_status_text}".strip()
        http_line = (
            f"\n⚠️ Statut HTTP : {status_str}"
            if int(http_status) >= 400
            else f"\nStatut HTTP : {status_str}"
        )
    nav_error = res_view.get("nav_error")
    nav_warning = res_view.get("nav_warning")
    alert_lines = ""
    if nav_error:
        alert_lines += f"\n⚠️ Erreur de navigation : {nav_error}"
    if nav_warning:
        alert_lines += f"\n⚠️ Avertissement de navigation : {nav_warning}"
    return (
        f"Voici les éléments interactifs actuels (Carte DOM) :"
        f"{url_text}{http_line}{alert_lines}{vp_text}\n{dom_text}"
    )


async def deploy_browser_navigation_monitor(
    res_view: dict,
    chat_id: str,
    uid: str,
    show_browser_hud: bool = True,
    browser_session_id: Optional[str] = None,
    open_tab_on_deploy: bool = False,
) -> None:
    """Enregistre la capture dans browserviews/ et la diffuse via EchoWindowManager."""
    import pybase64 as base64
    from echo_paths import generate_echo_file_id
    from echo_window_manager import get_window_manager

    b64 = res_view.get("screenshot_b64", "")
    clip_b64 = res_view.get("clip_b64", "")
    clip_duration_ms = int(res_view.get("clip_duration_ms") or 0)
    clip_size_bytes = int(res_view.get("clip_size_bytes") or 0)
    eff_session_id = browser_session_id or chat_id
    file_id = ""
    filename = ""
    clip_filename = ""
    ts_now = int(time.time())

    if b64:
        try:
            file_id = generate_echo_file_id(uid, chat_id)
            filename = f"{file_id}_frame.jpg"
            state_manager = EchoStateManager(user_id=uid, chat_id=chat_id)
            vault_path = get_echo_session_path(
                uid, chat_id, BROWSER_VIEWS_DIR_NAME
            )
            os.makedirs(vault_path, exist_ok=True)
            filepath = os.path.join(vault_path, filename)

            def _write_bytes(data: bytes, path: str) -> None:
                with open(path, "wb") as f:
                    f.write(data)

            img_data = base64.b64decode(b64)
            await asyncio.to_thread(_write_bytes, img_data, filepath)

            if file_id and clip_b64:
                clip_data = base64.b64decode(clip_b64)
                clip_filename = f"{file_id}{BROWSER_CLIP_EXT}"
                clip_filepath = os.path.join(vault_path, clip_filename)
                await asyncio.to_thread(_write_bytes, clip_data, clip_filepath)
                clip_size_bytes = len(clip_data)

            state_manager.save_resource(
                id=file_id,
                name=filename,
                resource_type="browserview",
                status=BROWSER_VIEW_STATUS["TRANSIENT"],
                mime="image/jpeg",
                storage_path=f"{BROWSER_VIEWS_DIR_NAME}/{filename}",
                metadata={
                    "kind": "browser_frame",
                    "session_id": eff_session_id,
                    "url": res_view.get("url", ""),
                    "action": res_view.get("action", ""),
                    "http_status": res_view.get("http_status"),
                    "http_status_text": res_view.get("http_status_text") or "",
                    "timestamp": ts_now,
                    "retained": False,
                    "clip_filename": clip_filename or None,
                    "clip_mime": BROWSER_CLIP_MIME if clip_filename else None,
                    "clip_duration_ms": clip_duration_ms if clip_filename else 0,
                    "clip_size_bytes": clip_size_bytes if clip_filename else 0,
                },
            )
        except Exception as e:
            print(f"[BrowserMonitor] Échec persistance capture ({uid}/{chat_id}): {e}")
            file_id = ""
            clip_filename = ""
        try:
            await asyncio.to_thread(enforce_transient_clip_budget, uid, chat_id)
        except Exception as e:
            print(f"[BrowserMonitor] Échec purge quota clips ({uid}/{chat_id}): {e}")

    if not show_browser_hud:
        return

    wm = get_window_manager()
    if open_tab_on_deploy and file_id:
        await wm.open_tab(
            uid,
            chat_id,
            "echo-browser",
            {"sessionId": eff_session_id, "frameId": file_id, "auto": True},
        )
    safe_cid = sanitize_echo_id(chat_id)
    await wm.broadcast_browser_frame(
        uid,
        chat_id,
        {
            "session_id": eff_session_id,
            "file_id": file_id,
            "filename": filename,
            "url": res_view.get("url", ""),
            "action": res_view.get("action", ""),
            "http_status": res_view.get("http_status"),
            "http_status_text": res_view.get("http_status_text") or "",
            "timestamp": ts_now,
            "status": BROWSER_VIEW_STATUS["TRANSIENT"],
            "retained": False,
            "image_url": (
                f"/echo/v1/cognition/{safe_cid}/browser-frames/{file_id}/image"
                if file_id
                else ""
            ),
            "clip_url": (
                f"/echo/v1/cognition/{safe_cid}/browser-frames/{file_id}/clip"
                if clip_filename
                else ""
            ),
            "clip_duration_ms": clip_duration_ms if clip_filename else 0,
            "clip_size_bytes": clip_size_bytes if clip_filename else 0,
        },
    )


class BrowserToolDispatcher:
    """
    Dispatcher spécialisé pour le Navigateur Autonome Playwright.
    Peut être instancié directement par navigation_engine_tool.py OU automatiquement
    par EchoSubagentController lors d'un resume/kill/close à froid sur un thread_web_*.
    """

    def __init__(
        self,
        sid: str,
        browser_session_id: str,
        chat_id: str,
        user_id: str,
        events: Any,
        http_timeout: int = 120,
        browser_mode: str = "desktop",
        show_browser_hud: bool = True,
        use_multimodal_vision: bool = True,
        vision_grid_step: int = DEFAULT_VISION_GRID_STEP,
        prune_threshold: int = PRUNE_CONTENT_THRESHOLD,
        analyze_page_cb: Any = None,
        archive_page_cb: Any = None,
    ):
        self.sid = sid
        self.browser_session_id = browser_session_id or sid
        self.chat_id = chat_id
        self.user_id = user_id
        self.events = events
        self.http_timeout = http_timeout
        self.browser_mode = browser_mode
        self.show_browser_hud = show_browser_hud
        self.use_vision = use_multimodal_vision
        self.vision_grid_step = vision_grid_step
        self.prune_threshold = prune_threshold
        self.analyze_page_cb = analyze_page_cb
        self.archive_page_cb = archive_page_cb

        self.browser = EchoBrowserLib(
            self.http_timeout,
            self.browser_session_id,
            self.user_id,
            vision_grid_step=self.vision_grid_step,
            chat_id=self.chat_id,
        )
        self.registry = self.browser.get_registry()
        self.vision_requested = False
        self.is_zooming = False
        self.zoom_attempts = 0
        self.last_view: Optional[dict] = None
        self._sem_acquired = False
        self.last_url: str = ""
        self.last_http_status: Optional[int] = None
        self.last_http_status_text: str = ""
        self.navigation_statuses: List[dict] = []

        schema_extensions = [
            {
                "name": "action_analyze_page",
                "description": "Analyse intelligemment le contenu textuel complet de la page actuelle en tâche de fond sans saturer la mémoire de travail.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "L'objectif de l'analyse.",
                        }
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "action_archive_page",
                "description": "Archive silencieusement tout le texte de la page actuelle dans la mémoire à long terme (RAG).",
                "parameters": {"type": "object", "properties": {}},
            },
        ]
        self._fn_decls = list(BROWSER_TOOLS_SCHEMA) + schema_extensions

    def get_tool_declarations(self) -> List[dict]:
        return self._fn_decls

    def _record_http_telemetry(self, payload: Optional[dict]) -> None:
        if not isinstance(payload, dict):
            return
        url = str(payload.get("url") or "").strip()
        code = payload.get("http_status")
        text = str(payload.get("http_status_text") or "").strip()
        if url:
            self.last_url = url
        if code is not None:
            self.last_http_status = code
            self.last_http_status_text = text
            entry = {
                "url": url or self.last_url,
                "http_status": code,
                "http_status_text": text,
            }
            if not self.navigation_statuses or self.navigation_statuses[-1] != entry:
                self.navigation_statuses.append(entry)
                if len(self.navigation_statuses) > 25:
                    self.navigation_statuses = self.navigation_statuses[-25:]

    def get_telemetry_metadata(self) -> dict:
        if (
            not self.last_url
            and self.last_http_status is None
            and not self.navigation_statuses
        ):
            return {}
        return {
            "final_url": self.last_url,
            "http_status": self.last_http_status,
            "http_status_text": self.last_http_status_text,
            "navigation_statuses": list(self.navigation_statuses),
        }

    def get_thinking_override(
        self, is_slice_exhausted: bool, last_fn_name: str
    ) -> Optional[str]:
        return (
            "high"
            if (
                is_slice_exhausted
                or last_fn_name in ("action", "action_inspect_page")
            )
            else "low"
        )

    async def acquire_browser_slot(self) -> None:
        if not self._sem_acquired:
            sem = get_global_browser_semaphore()
            await sem.acquire()
            self._sem_acquired = True

    def release_browser_slot(self) -> None:
        if self._sem_acquired:
            self._sem_acquired = False
            try:
                get_global_browser_semaphore().release()
            except ValueError:
                pass

    async def ensure_engine_ready(
        self, start_url: Optional[str] = None, open_tab: bool = False
    ) -> Optional[dict]:
        res = await req_to_browser(
            self.http_timeout,
            "/action",
            {"session_id": self.browser_session_id, "action": "ping"},
            self.user_id,
        )
        if (
            res.get("message") == "RESTART_REQUIRED"
            or res.get("error_type") == "SESSION_NOT_FOUND"
        ):
            if self.events:
                await self.events.status(
                    "🌐 Moteur de navigation initialisation...", done=False
                )
            start_res = await req_to_browser(
                self.http_timeout,
                "/start_session",
                {
                    "session_id": self.browser_session_id,
                    "chat_id": self.chat_id or self.browser_session_id,
                    "idle_timeout": 900,
                    "mode": self.browser_mode,
                },
                self.user_id,
            )
            if start_res.get("status") != "success":
                return None

        if self.show_browser_hud:
            from echo_window_manager import get_window_manager

            get_window_manager().register_browser_session(
                self.user_id, self.chat_id, self.browser_session_id
            )

        await cleanup_expired_browserviews(self.user_id, self.chat_id, min_age_sec=BROWSER_TRANSIENT_GRACE_SEC)
        nav_res: Optional[dict] = None
        if start_url:
            if self.events:
                await self.events.status(
                    f"🌐 Navigation vers {start_url}...", done=False
                )
            nav_res = await self.browser.action_browser_control(
                command="navigate", value=start_url
            )
        if self.events:
            await self.events.status(
                f"📸 [{self.sid}] Extraction de la carte DOM...", done=False
            )
        res_view = await self.browser.highlight()
        if isinstance(res_view, dict) and isinstance(nav_res, dict):
            if nav_res.get("status") == "error":
                res_view["nav_error"] = nav_res.get("message")
            if nav_res.get("warning"):
                res_view["nav_warning"] = nav_res.get("warning")
            if res_view.get("http_status") is None and nav_res.get("http_status") is not None:
                res_view["http_status"] = nav_res.get("http_status")
                res_view["http_status_text"] = nav_res.get("http_status_text") or ""
        if isinstance(res_view, dict) and res_view.get("http_status") is not None:
            self._record_http_telemetry(res_view)
        else:
            self._record_http_telemetry(nav_res)
            self._record_http_telemetry(res_view)
        self.last_view = res_view
        await deploy_browser_navigation_monitor(
            res_view,
            self.chat_id,
            self.user_id,
            show_browser_hud=self.show_browser_hud,
            browser_session_id=self.browser_session_id,
            open_tab_on_deploy=open_tab,
        )
        return res_view

    async def on_before_slice(self) -> None:
        await self.acquire_browser_slot()
        if self.show_browser_hud:
            from echo_window_manager import get_window_manager

            get_window_manager().register_browser_session(
                self.user_id, self.chat_id, self.browser_session_id
            )

    async def on_after_slice(self, final_status: str) -> None:
        self.release_browser_slot()

    async def on_cleanup(self) -> None:
        await self.on_after_slice("killed")
        if self.show_browser_hud:
            try:
                from echo_window_manager import get_window_manager

                get_window_manager().unregister_browser_session(
                    self.user_id, self.chat_id, self.browser_session_id
                )
            except Exception:
                pass
        try:
            await self.browser.reset_session()
        except Exception:
            pass
        try:
            await cleanup_expired_browserviews(
                self.user_id,
                self.chat_id,
                force_discard_sids={self.browser_session_id, self.sid},
            )
        except Exception:
            pass

    async def execute_tool_calls(
        self,
        tool_calls: List[dict],
        remaining_slice_calls: int,
        current_model_key: str,
        history: List[dict],
    ) -> tuple[List[dict], int, int, Optional[str], str]:
        allowed_calls = tool_calls[:ECHO_MAX_PARALLEL_TOOLS_PER_TURN]
        excess_calls = tool_calls[ECHO_MAX_PARALLEL_TOOLS_PER_TURN:]

        response_parts: List[dict] = []
        last_view = None
        last_fn_name = "action"
        err_streak = 0

        try:
            await self.browser.start_screencast()
        except Exception:
            pass

        screencast_stopped = False

        async def _stop_and_collect_clip() -> dict:
            nonlocal screencast_stopped
            if screencast_stopped:
                return {}
            screencast_stopped = True
            sc_res: Any = {}
            try:
                sc_res = await self.browser.stop_screencast()
            except Exception:
                sc_res = {}
            return sc_res if isinstance(sc_res, dict) else {}

        def _attach_clip_fields(target_view: dict, sc_res: dict) -> None:
            if not isinstance(target_view, dict) or not isinstance(sc_res, dict):
                return
            for k in ("clip_b64", "clip_mime", "clip_duration_ms", "clip_size_bytes"):
                if sc_res.get(k):
                    target_view[k] = sc_res[k]

        def _propagate_http_fields(target_view: Any, src_res: Any) -> None:
            if isinstance(target_view, dict) and isinstance(src_res, dict):
                if target_view.get("http_status") is None and src_res.get("http_status") is not None:
                    target_view["http_status"] = src_res.get("http_status")
                    target_view["http_status_text"] = src_res.get("http_status_text") or ""
                if not target_view.get("url") and src_res.get("url"):
                    target_view["url"] = src_res.get("url")

        try:
            for index, fc in enumerate(allowed_calls):
                fn_name = fc.get("name", "")
                fn_args = dict(fc.get("args", {}) or {})
                fn_id = fc.get("id")
                is_last_tool = index == len(allowed_calls) - 1

                if fn_args.get("action_type") in ("download", "save_target"):
                    fn_args["download_file_id"] = f"DL_{uuid.uuid4().hex[:8]}"

                if self.events:
                    await self.events.status(
                        f"🖱️ [{self.sid}] {fn_name}({fn_args})", done=False
                    )

                _resp: dict = {"status": "error", "message": "Outil inconnu."}
                is_err = False

                if fn_name in self.registry:
                    try:
                        action_res = await self.registry[fn_name](**fn_args)
                        if action_res.get("_trigger_vision"):
                            self.vision_requested = True
                            grid = action_res.get("grid", False)
                            zb = action_res.get("zoom_box")
                            if action_res.get("is_zoom"):
                                self.is_zooming = True
                                self.zoom_attempts += 1
                                cx, cy = action_res.get("cx"), action_res.get("cy")
                                _resp = {
                                    "status": "success",
                                    "message": (
                                        f"Zoom appliqué (Essai {self.zoom_attempts}/3). L'image jointe est rognée sur la zone cible. "
                                        f"Un VISEUR ROUGE a été dessiné au centre absolu (X={cx}, Y={cy}). "
                                        f"Si le point central de ce viseur est STRICTEMENT sur la cible, le Sous-Agent DOIT utiliser "
                                        f"action_interact_dom(x={cx}, y={cy}, action_type='click')."
                                    ),
                                }
                            elif action_res.get("is_zoom_out"):
                                self.is_zooming = False
                                self.zoom_attempts = 0
                                _resp = {
                                    "status": "success",
                                    "message": "Zoom annulé. Retour à la vue globale.",
                                }
                            else:
                                _resp = {
                                    "status": "success",
                                    "message": "Capture d'écran demandée. Elle est jointe à ce message.",
                                }

                            if grid:
                                last_view = await self.browser.vision_capture(
                                    grid=True, zoom_box=zb
                                )
                            else:
                                last_view = await self.browser.highlight()

                            hud_view = (
                                await self.browser.highlight() if grid else last_view
                            )
                            _propagate_http_fields(last_view, action_res)
                            if hud_view is not last_view:
                                _propagate_http_fields(hud_view, action_res)
                            if is_last_tool:
                                sc_res = await _stop_and_collect_clip()
                                _attach_clip_fields(hud_view, sc_res)
                            await deploy_browser_navigation_monitor(
                                hud_view,
                                self.chat_id,
                                self.user_id,
                                show_browser_hud=self.show_browser_hud,
                                browser_session_id=self.browser_session_id,
                            )
                            self._record_http_telemetry(hud_view or last_view)
                            last_fn_name = fn_name
                        elif action_res.get("status") != "error":
                            if fn_name in (
                                "action_interact_dom",
                                "action_interact_a11y",
                                "action_browser_control",
                            ):
                                self.is_zooming = False
                                self.zoom_attempts = 0
                            if is_last_tool:
                                if (
                                    "metadata" in action_res
                                    and "screenshot_b64" in action_res
                                ):
                                    last_view = action_res
                                else:
                                    last_view = await self.browser.highlight()
                                _propagate_http_fields(last_view, action_res)
                                sc_res = await _stop_and_collect_clip()
                                _attach_clip_fields(last_view, sc_res)
                                await deploy_browser_navigation_monitor(
                                    last_view,
                                    self.chat_id,
                                    self.user_id,
                                    show_browser_hud=self.show_browser_hud,
                                    browser_session_id=self.browser_session_id,
                                )
                                _resp = {
                                    "status": "success",
                                    "dom_map": last_view.get("metadata", []),
                                }
                            else:
                                _resp = {
                                    "status": "success",
                                    "message": "Action exécutée avec succès.",
                                }
                            primary = last_view if (is_last_tool and isinstance(last_view, dict)) else {}
                            url_val = primary.get("url") or action_res.get("url")
                            if url_val:
                                _resp["url"] = url_val
                            http_code = primary.get("http_status")
                            http_text = primary.get("http_status_text") or ""
                            if http_code is None:
                                http_code = action_res.get("http_status")
                                http_text = action_res.get("http_status_text") or ""
                            if http_code is not None:
                                _resp["http_status"] = http_code
                                _resp["http_status_text"] = http_text
                            warn_val = primary.get("warning") or action_res.get("warning")
                            if warn_val:
                                _resp["warning"] = warn_val
                            self._record_http_telemetry(last_view if is_last_tool else action_res)
                            self._record_http_telemetry(action_res)
                            if action_res.get("status") == "downloading":
                                _resp["message"] = (
                                    f"📥 Téléchargement asynchrone démarré. Identifiant : {fn_args.get('download_file_id')}."
                                )
                            for k_extra in ("search_result", "content", "value"):
                                if k_extra in action_res:
                                    _resp[k_extra] = action_res[k_extra]
                            last_fn_name = fn_name
                        else:
                            _resp = {
                                "status": "error",
                                "error": action_res.get("message"),
                            }
                            is_err = True
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                elif fn_name == "action_analyze_page" and self.analyze_page_cb:
                    try:
                        current_url_resp = await self.browser.action_inspect_page(
                            target="url"
                        )
                        raw_res = await self.analyze_page_cb(
                            current_url_resp.get("url", ""),
                            fn_args.get("query", ""),
                        )
                        _resp = {"status": "success", "result": raw_res}
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                elif fn_name == "action_archive_page" and self.archive_page_cb:
                    try:
                        current_url_resp = await self.browser.action_inspect_page(
                            target="url"
                        )
                        raw_res = await self.archive_page_cb(
                            current_url_resp.get("url", "")
                        )
                        _resp = {"status": "success", "result": raw_res}
                    except Exception as e:
                        _resp = {"status": "error", "error": str(e)}
                        is_err = True
                else:
                    is_err = True

                if is_err:
                    err_streak += 1
                else:
                    err_streak = 0

                _fr: dict = {"name": fn_name, "response": _resp}
                if fn_id:
                    _fr["id"] = fn_id
                response_parts.append({"functionResponse": _fr})
        finally:
            if not screencast_stopped:
                await _stop_and_collect_clip()

        for fc in excess_calls:
            fn_id = fc.get("id")
            _efr: dict = {
                "name": fc.get("name", "unknown"),
                "response": {
                    "status": "exceeded_parallel_per_turn_limit",
                    "message": (
                        f"Limite par tour atteinte ({ECHO_MAX_PARALLEL_TOOLS_PER_TURN} appels simultanés max par tour). "
                        "Réémettre au tour suivant si nécessaire."
                    ),
                },
            }
            if fn_id:
                _efr["id"] = fn_id
            response_parts.append({"functionResponse": _efr})

        if last_view:
            self.last_view = last_view
        if (
            last_view
            and self.use_vision
            and self.vision_requested
            and last_view.get("screenshot_b64")
        ):
            target_fr = None
            for rp in reversed(response_parts):
                if (
                    "functionResponse" in rp
                    and rp["functionResponse"].get("name") == last_fn_name
                ):
                    target_fr = rp["functionResponse"]
                    break
            if not target_fr and response_parts:
                target_fr = response_parts[-1]["functionResponse"]
            if target_fr:
                target_fr["response"]["message"] = (
                    "Capture d'écran disponible. Le Sous-Agent DOIT analyser la capture."
                )
                response_parts.append({
                    "inlineData": {
                        "mimeType": "image/jpeg",
                        "data": last_view["screenshot_b64"],
                    }
                })
            self.vision_requested = False

        prune_browser_heavy_context(
            history, self.prune_threshold, self.is_zooming
        )
        return (
            response_parts,
            len(allowed_calls),
            err_streak,
            None,
            last_fn_name,
        )
