"""
title: ECHO Browser Lib
author: ECHO Framework
version: 6.18.0.1
description: Composant système interne : ECHO Browser Lib.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.0.1: Extraction du helper synchrone SSOT cleanup_expired_browserviews_sync (partagé ECHO-UI et Admin Manager) et délégation asyncio.to_thread.
# 6.16.0.1: Suppression de iter_screencast_stream SSE et ajout de build_browser_ws_url et resolve_chat_browser_session_id.
# 6.15.1.1: Passage par défaut de min_age_sec à BROWSER_TRANSIENT_GRACE_SEC (1800.0s) dans cleanup_expired_browserviews et application systématique du délai de grâce y compris sur force_discard_sids.
# 6.15.0.1: Ajout de min_age_sec dans cleanup_expired_browserviews pour préserver les captures récentes lors des purges passives sans affecter force_discard_sids.
# 6.13.1.1: Extension de cleanup_expired_browserviews aux clips MP4 (purge couplée N1 + orphelins), ajout de enforce_transient_clip_budget (purge FIFO N2) et iter_screencast_stream SSE.

import asyncio
import json
import os
import time
import httpx
import logging
from typing import Dict, Callable, Optional

# On suppose que echo_constants est disponible dans le chemin PYTHONPATH (/app/backend/echo_libs)
from echo_constants import (
    BROWSER_CLIP_EXT,
    BROWSER_MAX_TRANSIENT_CLIPS_MB,
    BROWSER_MAX_TRANSIENT_CLIPS_PER_CHAT,
    BROWSER_TRANSIENT_GRACE_SEC,
    BROWSER_VIEWS_DIR_NAME,
    BROWSER_VIEW_STATUS,
    NAVIGATION_ENGINE_URL,
    DEFAULT_VISION_GRID_STEP,
    EPHEMERAL_RENDER_ENDPOINT,
    EPHEMERAL_RENDER_VIEWPORT_W,
    EPHEMERAL_RENDER_VIEWPORT_H,
    EPHEMERAL_RENDER_SETTLE_MS,
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
