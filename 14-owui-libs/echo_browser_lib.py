"""
title: ECHO Browser Lib
author: ECHO Framework
version: 1.10
description: Composant système interne : ECHO Browser Lib.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.10: Optim - Refonte des descriptions d'outils pour autoriser les appels parallèles (suppression de la notion de niveaux stricts).
# 1.9: Ajout de l'action_type `download` pour supporter le téléchargement de fichiers via Playwright.
# 1.7: Ajout du paramètre optionnel `name` dans `action_interact_a11y` pour le ciblage précis des rôles.
# 1.6: Refonte de l'API avec intégration de l'arbre a11y_tree et hiérarchie stricte.
# 1.5: Unification de l'API en 4 piliers (interact_a11y, interact_dom, inspect_page, browser_control).

import httpx
import logging
from typing import Dict, Any, Callable

# On suppose que echo_constants est disponible dans le chemin PYTHONPATH (/app/backend/echo_libs)
from echo_constants import NAVIGATION_ENGINE_URL

logger = logging.getLogger(__name__)

BROWSER_TOOLS_SCHEMA = [
    {
        "name": "action_interact_a11y",
        "description": "Interagit avec un élément de l'arbre A11y (via role, text ou label). Tu peux appeler cet outil plusieurs fois dans le même tour pour effectuer des actions groupées.",
        "parameters": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["role", "label", "text"], "description": "La méthode de ciblage (role=ex:button/radio, label=attribut aria-label, text=texte brut visible)."},
                "value": {"type": "string", "description": "La valeur associée à la méthode de ciblage (ex: 'button', 'Je suis d\\'accord')."},
                "name": {"type": "string", "description": "(Optionnel) Si method='role', permet de filtrer par le nom du rôle (ex: 'Accepter') pour cibler précisément un bouton ou lien."},
                "action_type": {"type": "string", "enum": ["click", "type", "hover", "download", "save_target"], "description": "Le type d'interaction (download force un clic et attend le fichier, save_target extrait l'URL du lien/image et la télécharge furtivement)."},
                "text_to_type": {"type": "string", "description": "(Optionnel) Le texte à insérer si action_type='type'."}
            },
            "required": ["method", "value", "action_type"]
        }
    },
    {
        "name": "action_interact_dom",
        "description": "Interagit via l'index du DOM Map ou les coordonnées Vision X/Y. Tu peux appeler cet outil plusieurs fois dans le même tour pour des actions groupées.",
        "parameters": {
            "type": "object",
            "properties": {
                "action_type": {"type": "string", "enum": ["click", "type", "hover", "download", "save_target"], "description": "Le type d'interaction (download force un clic et attend le fichier, save_target extrait l'URL et la télécharge furtivement)."},
                "index": {"type": "integer", "description": "L'ID numérique de l'élément (indiqué entre crochets sur la carte du DOM). À utiliser en priorité absolue."},
                "x": {"type": "integer", "description": "Coordonnée X en pixels (à n'utiliser QUE si l'index est introuvable, suite à une action_inspect_page avec target='vision')."},
                "y": {"type": "integer", "description": "Coordonnée Y en pixels (à n'utiliser QUE si l'index est introuvable)."},
                "text_to_type": {"type": "string", "description": "(Optionnel) Le texte à insérer si action_type='type'."}
            },
            "required": ["action_type"]
        }
    },
    {
        "name": "action_inspect_page",
        "description": "Extrait des informations de la page (a11y_tree, dom_map, vision, etc.). Tu peux appeler cet outil plusieurs fois en parallèle avec des 'target' différentes dans le même tour.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "enum": ["a11y_tree", "dom_map", "vision", "read_text", "read_html", "search_dom", "url"], "description": "L'information à extraire."},
                "index": {"type": "integer", "description": "(Optionnel) L'ID de l'élément si target='url'."},
                "value": {"type": "string", "description": "(Optionnel) Le texte court à rechercher si target='search_dom'."},
                "vision_grid": {"type": "boolean", "description": "(Optionnel) True pour calquer une grille orthonormée si target='vision'."}
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
    }
]

async def req_to_browser(timeout: int, endpoint: str, data: dict = None, user_id: str = "anonymous") -> dict:
    """Effectue une requête POST asynchrone vers le Browser Agent."""
    url = f"{NAVIGATION_ENGINE_URL}{endpoint}"
    headers = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": str(user_id)}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=data or {}, headers=headers)
            return resp.json()
    except Exception as e:
        return {"status": "error", "message": f"Worker inaccessible : {str(e)}"}

class EchoBrowserLib:
    """Encapsule les actions réseau pour le sous-agent navigateur."""
    def __init__(self, timeout: int, session_id: str, user_id: str, vision_grid_step: int = 100):
        self.timeout = timeout
        self.session_id = session_id
        self.user_id = user_id
        self.vision_grid_step = vision_grid_step

    async def _action(self, action: str, params: dict = None) -> dict:
        params = params or {}
        if action == "inspect_page" and params.get("target") == "vision" and params.get("vision_grid"):
            params["vision_grid_step"] = self.vision_grid_step
        return await req_to_browser(self.timeout, "/action", {"session_id": self.session_id, "action": action, "params": params}, self.user_id)

    async def highlight(self) -> dict:
        return await self._action("inspect_page", {"target": "vision", "vision_grid": False})

    async def vision_grid(self) -> dict:
        return await self._action("inspect_page", {"target": "vision", "vision_grid": True})

    async def ping(self) -> dict:
        return await self._action("ping")

    async def start_session(self, idle_timeout: int, mode: str) -> dict:
        return await req_to_browser(self.timeout, "/start_session", {
            "session_id": self.session_id, 
            "idle_timeout": idle_timeout, 
            "mode": mode
        }, self.user_id)

    async def start_screencast(self) -> dict:
        return await req_to_browser(self.timeout, "/screencast/start", {"session_id": self.session_id}, self.user_id)

    async def stop_screencast(self, hd_b64: str = None) -> dict:
        return await req_to_browser(self.timeout, "/screencast/stop", {"session_id": self.session_id, "hd_b64": hd_b64}, self.user_id)
        
    async def reset_session(self) -> dict:
        return await self._action("browser_control", {"command": "reset"})

    async def action_interact_a11y(self, method: str, value: str, action_type: str, name: str = None, text_to_type: str = None, download_file_id: str = None) -> dict:
        return await self._action("interact_a11y", {"method": method, "value": value, "name": name, "action_type": action_type, "text_to_type": text_to_type, "download_file_id": download_file_id})

    async def action_interact_dom(self, action_type: str, index: int = None, x: int = None, y: int = None, text_to_type: str = "", download_file_id: str = None) -> dict:
        return await self._action("interact_dom", {"action_type": action_type, "index": index, "x": x, "y": y, "text_to_type": text_to_type, "download_file_id": download_file_id})

    async def action_inspect_page(self, target: str, index: int = None, value: str = "", vision_grid: bool = False) -> dict:
        if target == "vision":
            # Cette fonction est interceptée par l'orchestrateur, on flag la demande de grille
            return {"status": "success", "message": "Capture d'écran demandée.", "_trigger_vision": True, "grid": vision_grid}
        return await self._action("inspect_page", {"target": target, "index": index, "value": value, "vision_grid": vision_grid})

    async def action_browser_control(self, command: str, value: str = "") -> dict:
        return await self._action("browser_control", {"command": command, "value": str(value) if value is not None else ""})
        
    def get_registry(self) -> Dict[str, Callable]:
        """Retourne le mapping name -> callable pour l'interception de Gemini."""
        return {
            "action_interact_a11y": self.action_interact_a11y,
            "action_interact_dom": self.action_interact_dom,
            "action_inspect_page": self.action_inspect_page,
            "action_browser_control": self.action_browser_control
        }
