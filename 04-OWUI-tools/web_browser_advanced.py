"""
title: Advanced Web Browser (User Isolation & Vision Compatible)
author: Wilfried BARNAVON
version: 2.2
description: 2.2: Documentation 'LLM-Optimized'. Navigateur persistant avec Vision Augmentée (Highlight) et Smart Click (Texte/Index).
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
                print(f"[BROWSER v2.2] REQ {endpoint} | User: {user_id}")
            
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
        """
        [REQUIRED FIRST] Starts a new, clean browser session isolated for the current user.
        Returns a session_id that MUST be passed to all subsequent navigation commands.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/start_session", {}, user_id)
        if "session_id" in res:
            return f"Session démarrée. ID: {res['session_id']}"
        return f"Erreur démarrage: {res}"

    def stop_browser_session(self, session_id: str, __user__: dict = {}) -> str:
        """
        [REQUIRED LAST] Closes the browser session and frees memory. 
        Always call this when the navigation task is complete.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/stop_session", {"session_id": session_id}, user_id)
        return str(res)

    def navigate(self, session_id: str, url: str, __user__: dict = {}) -> str:
        """
        Navigates to a specific URL in the active session.
        Use this to load a page before reading or clicking.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "goto", "params": {"url": url}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Page chargée. Titre: {res.get('title')}"

    def read_page(self, session_id: str, __user__: dict = {}) -> str:
        """
        Extracts the main text content of the current page.
        Use this to understand the page structure and find information.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "read"}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        content = res.get("content", "")[:8000] 
        return f"URL: {res.get('url')}\n\nCONTENU:\n{content}..."

    def highlight_elements(self, session_id: str, __user__: dict = {}) -> str:
        """
        [VISION AUGMENTED] Injects numeric markers (red tags) on all interactive elements (buttons, links, inputs).
        Use this BEFORE 'click_element' to easily identify elements by their ID number.
        Returns the count of marked elements.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "highlight"}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return res.get("message", "Vision augmentée activée.")

    def click_element(self, session_id: str, selector: str, __user__: dict = {}) -> str:
        """
        Clicks on an element on the current page.
        
        The 'selector' argument accepts 3 formats (in order of reliability):
        1. [BEST] Numeric Index: The number displayed by 'highlight_elements' (e.g., '12' or '#12').
        2. [GOOD] Exact Text: The visible text of the button/link (e.g., 'Log In', 'Submit').
        3. [HARD] CSS Selector: Standard CSS selector (e.g., '.btn-primary', '#login-form > input').
        
        Recommendation: Run 'highlight_elements' first, then click using the number.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "click", "params": {"selector": selector}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return "Clic effectué."

    def type_text(self, session_id: str, selector: str, text: str, __user__: dict = {}) -> str:
        """
        Types text into an input field.
        'selector' can be a CSS selector, an element ID, or a numeric index from 'highlight_elements'.
        """
        user_id = __user__.get("id", "anonymous")
        res = self._req("/action", {"session_id": session_id, "action": "type", "params": {"selector": selector, "text": text}}, user_id)
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Texte '{text}' saisi."
    
    def quick_read(self, url: str, __user__: dict = {}) -> str:
        """
        [ONE-SHOT] Opens a URL, reads the content, and closes the session immediately.
        Use this for simple reading tasks where no interaction (clicking/login) is required.
        """
        user_id = __user__.get("id", "anonymous")
        
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