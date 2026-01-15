"""
title: Advanced Web Browser
author: Wilfried BARNAVON
version: v1.1
description: Navigateur persistant capable de cliquer, remplir des formulaires et lire le contenu (via Browser Agent).
"""

import requests
import json
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        agent_url: str = Field(default="http://browser-agent:5002", description="URL du container Browser Agent")

    def __init__(self):
        self.valves = self.Valves()

    def _req(self, endpoint, data=None):
        """Helper pour les requêtes HTTP vers l'agent."""
        try:
            url = f"{self.valves.agent_url}{endpoint}"
            headers = {"Content-Type": "application/json"}
            
            # FORCE POST si data est fourni ou si c'est start_session/stop_session
            # L'API browser_api.py attend du POST pour toutes ces actions
            if data is not None or endpoint in ["/start_session", "/stop_session"]:
                r = requests.post(url, json=data or {}, headers=headers, timeout=60)
            else:
                r = requests.get(url, timeout=10)
                
            try:
                return r.json()
            except:
                return {"error": f"Invalid JSON response (Status {r.status_code}): {r.text[:200]}"}
                
        except Exception as e:
            return {"error": str(e)}

    def start_browser_session(self) -> str:
        """Démarre une nouvelle session de navigation propre."""
        # Fix: envoi d'un dict vide {} pour forcer le mode POST dans _req
        res = self._req("/start_session", {})
        if "session_id" in res:
            return f"Session démarrée. ID: {res['session_id']}"
        return f"Erreur démarrage: {res}"

    def stop_browser_session(self, session_id: str) -> str:
        """Ferme une session de navigation."""
        res = self._req("/stop_session", {"session_id": session_id})
        return str(res)

    def navigate(self, session_id: str, url: str) -> str:
        """Charge une URL dans une session existante."""
        res = self._req("/action", {"session_id": session_id, "action": "goto", "params": {"url": url}})
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Page chargée. Titre: {res.get('title')}"

    def read_page(self, session_id: str) -> str:
        """Lit le contenu textuel actuel de la page."""
        res = self._req("/action", {"session_id": session_id, "action": "read"})
        if "error" in res: return f"Erreur: {res['error']}"
        content = res.get("content", "")[:8000] # Limite context window
        return f"URL: {res.get('url')}\n\nCONTENU:\n{content}..."

    def click_element(self, session_id: str, selector: str) -> str:
        """Clique sur un élément identifié par un sélecteur CSS."""
        res = self._req("/action", {"session_id": session_id, "action": "click", "params": {"selector": selector}})
        if "error" in res: return f"Erreur: {res['error']}"
        return "Clic effectué."

    def type_text(self, session_id: str, selector: str, text: str) -> str:
        """Ecrit du texte dans un champ identifié par un sélecteur CSS."""
        res = self._req("/action", {"session_id": session_id, "action": "type", "params": {"selector": selector, "text": text}})
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Texte '{text}' saisi."
    
    def quick_read(self, url: str) -> str:
        """Mode rapide: Ouvre, lit et ferme (sans session persistante)."""
        # Fix: Force POST pour start_session
        start = self._req("/start_session", {})
        if "session_id" not in start: 
            return f"Erreur init: {start}"
        sid = start["session_id"]
        
        self._req("/action", {"session_id": sid, "action": "goto", "params": {"url": url}})
        read = self._req("/action", {"session_id": sid, "action": "read"})
        
        self._req("/stop_session", {"session_id": sid})
        
        if "content" in read:
            return f"Lecture rapide de {url}:\n{read['content'][:8000]}"
        return f"Lecture échouée: {read.get('error', 'Inconnue')}"