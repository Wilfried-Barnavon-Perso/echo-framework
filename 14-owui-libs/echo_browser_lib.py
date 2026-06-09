"""
title: ECHO Browser Lib
author: ECHO Framework
version: 1.0
description: 1.0: Déportation de la logique de requêtage du navigateur et de la déclaration des outils Gemini.
"""

import httpx
import logging
from typing import Dict, Any, Callable

# On suppose que echo_constants est disponible dans le chemin PYTHONPATH (/app/backend/echo_libs)
from echo_constants import NAVIGATION_ENGINE_URL

logger = logging.getLogger(__name__)

BROWSER_TOOLS_SCHEMA = [
    {
        "name": "action_navigate",
        "description": "Accède à une URL absolue (doit commencer par http:// ou https://).",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "L'URL complète de la page cible."}
            },
            "required": ["url"]
        }
    },
    {
        "name": "action_click",
        "description": "Déplace la souris et clique sur un élément de la page via son index.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "L'ID numérique de l'élément (indiqué entre crochets sur la carte du DOM)."}
            },
            "required": ["index"]
        }
    },
    {
        "name": "action_type",
        "description": "Remplit un champ de texte (input/textarea) identifié par son index.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "L'ID numérique du champ."},
                "text": {"type": "string", "description": "Le texte à insérer."}
            },
            "required": ["index", "text"]
        }
    },
    {
        "name": "action_hover",
        "description": "Survole un élément avec la souris pour dévoiler des menus déroulants.",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "L'ID numérique de l'élément à survoler."}
            },
            "required": ["index"]
        }
    },
    {
        "name": "action_scroll",
        "description": "Fait défiler la page dans la direction souhaitée.",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down", "top", "bottom"]}
            },
            "required": ["direction"]
        }
    },
    {
        "name": "action_read_page",
        "description": "Lit le texte complet de la page pour rechercher des informations introuvables dans la carte du DOM.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "action_get_url",
        "description": "Extrait l'URL absolue d'un élément (ex: lien, image).",
        "parameters": {
            "type": "object",
            "properties": {
                "index": {"type": "integer", "description": "L'ID de l'élément."}
            },
            "required": ["index"]
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
    def __init__(self, timeout: int, session_id: str, user_id: str):
        self.timeout = timeout
        self.session_id = session_id
        self.user_id = user_id

    async def _action(self, action: str, params: dict = None) -> dict:
        return await req_to_browser(self.timeout, "/action", {"session_id": self.session_id, "action": action, "params": params or {}}, self.user_id)

    async def highlight(self) -> dict:
        return await self._action("highlight")

    async def ping(self) -> dict:
        return await self._action("ping")

    async def start_session(self, idle_timeout: int, mode: str) -> dict:
        return await req_to_browser(self.timeout, "/start_session", {
            "session_id": self.session_id, 
            "idle_timeout": idle_timeout, 
            "mode": mode
        }, self.user_id)
        
    async def reset_session(self) -> dict:
        return await self._action("reset")

    async def action_navigate(self, url: str) -> dict:
        return await self._action("goto", {"url": url})

    async def action_click(self, index: int) -> dict:
        return await self._action("click", {"index": index})

    async def action_type(self, index: int, text: str) -> dict:
        return await self._action("type", {"index": index, "text": text})

    async def action_hover(self, index: int) -> dict:
        return await self._action("hover", {"index": index})

    async def action_scroll(self, direction: str) -> dict:
        return await self._action("scroll", {"direction": direction})

    async def action_read_page(self) -> dict:
        return await self._action("read")

    async def action_get_url(self, index: int) -> dict:
        return await self._action("get_attribute", {"index": index, "attribute": "href"})
        
    def get_registry(self) -> Dict[str, Callable]:
        """Retourne le mapping name -> callable pour l'interception de Gemini."""
        return {
            "action_navigate": self.action_navigate,
            "action_click": self.action_click,
            "action_type": self.action_type,
            "action_hover": self.action_hover,
            "action_scroll": self.action_scroll,
            "action_read_page": self.action_read_page,
            "action_get_url": self.action_get_url
        }
