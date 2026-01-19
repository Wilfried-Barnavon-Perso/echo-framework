"""
title: Advanced Web Browser (User Isolation Compatible)
author: Wilfried BARNAVON
version: 2.0
description: 2.0: Navigateur persistant capable de cliquer, remplir des formulaires et lire le contenu.
"""

import requests
import json
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        agent_url: str = Field(default="http://browser-agent:5002", description="URL du container Browser Agent")
        debug_mode: bool = Field(default=False, description="Activer les logs détaillés.")

    def __init__(self):
        self.valves = self.Valves()

    def _req(self, endpoint, data=None, user_id="anonymous"):
        """Helper pour les requêtes HTTP vers l'agent avec propagation de l'identité."""
        try:
            url = f"{self.valves.agent_url}{endpoint}"
            
            # Propagation de l'identité utilisateur vers le service Docker
            headers = {
                "Content-Type": "application/json",
                "X-OpenWebUI-User-Id": str(user_id)
            }
            
            if self.valves.debug_mode:
                print(f"[BROWSER v137.0] REQ {endpoint} | User: {user_id}")
            
            # FORCE POST si data est fourni ou si c'est start_session/stop_session
            # L'API browser_api.py attend du POST pour toutes ces actions
            if data is not None or endpoint in ["/start_session", "/stop_session"]:
                r = requests.post(url, json=data or {}, headers=headers, timeout=60)
            else:
                r = requests.get(url, headers=headers, timeout=10)
                
            try:
                return r.json()
            except:
                return {"error": f"Invalid JSON response (Status {r.status_code}): {r.text[:200]}"}
                
        except Exception as e:
            return {"error": str(e)}

    def start_browser_session(self, __user__: dict = {}) -> str:
        """Démarre une nouvelle session de navigation propre."""
        user_id = __user__.get("id", "anonymous")
        # Fix: envoi d'un dict vide {} pour forcer le mode POST dans _req
        res = self._req("/start_session", {}, user_id)
        if "session_id" in res:
            return f"Session démarrée. ID: {res['session_id']}"
        return f"Erreur démarrage: {res}"

    def stop_browser_session(self, session_id: str, __user__: dict = {}) -> str:
        """Ferme une session de navigation."""
        user_id = __user__.get("id", "anonymous")
        res = self._req("/stop_session", {"session_id": session_id}, user_id)
        return str(res)

    def navigate(self, session_id: str, url: str, __user__: dict = {}) -> str:
        """Charge une URL dans une session existante."""
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "goto", "params": {"url": url}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Page chargée. Titre: {res.get('title')}"

    def read_page(self, session_id: str, __user__: dict = {}) -> str:
        """Lit le contenu textuel actuel de la page."""
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "read"}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        content = res.get("content", "")[:8000] # Limite context window
        return f"URL: {res.get('url')}\n\nCONTENU:\n{content}..."

    def click_element(self, session_id: str, selector: str, __user__: dict = {}) -> str:
        """Clique sur un élément identifié par un sélecteur CSS."""
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "click", "params": {"selector": selector}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return "Clic effectué."

    def type_text(self, session_id: str, selector: str, text: str, __user__: dict = {}) -> str:
        """Ecrit du texte dans un champ identifié par un sélecteur CSS."""
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "type", "params": {"selector": selector, "text": text}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Texte '{text}' saisi."
    
    def quick_read(self, url: str, __user__: dict = {}) -> str:
        """Mode rapide: Ouvre, lit et ferme (sans session persistante)."""
        user_id = __user__.get("id", "anonymous")
        
        # Fix: Force POST pour start_session
        start = self._req("/start_session", {}, user_id)
        if "session_id" not in start: 
            return f"Erreur init: {start}"
        sid = start["session_id"]
        
        self._req("/action", {"session_id": sid, "action": "goto", "params": {"url": url}}, user_id)
        read = self._req("/action", {"session_id": sid, "action": "read"}, user_id)
        
        self._req("/stop_session", {"session_id": sid}, user_id)
        
        if "content" in read:
            return f"Lecture rapide de {url}:\n{read['content'][:8000]}"
        return f"Lecture échouée: {read.get('error', 'Inconnue')}"