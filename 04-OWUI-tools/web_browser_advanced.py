"""
title: ECHO Web Browser
author: Wilfried BARNAVON
version: 3.2
description: 3.2: Navigation web persistante, vision augmentée (Set-of-Mark), clics intelligents et support clavier. (Fonctions restaurées).
"""

# ECHO CONFIG NAME : ECHO Web Browser

import requests
from pydantic import BaseModel, Field

class Tools:
    class Valves(BaseModel):
        agent_url: str = Field(default="http://browser-agent:5002", description="URL du container Browser Agent")
        debug_mode: bool = Field(default=False, description="Debug logs")
    
    def __init__(self):
        self.valves = self.Valves()

    def _req(self, endpoint, data=None, user_id="anonymous"):
        try:
            url = f"{self.valves.agent_url}{endpoint}"
            headers = {"Content-Type": "application/json", "X-OpenWebUI-User-Id": str(user_id)}
            
            if self.valves.debug_mode:
                print(f"[BROWSER v3.2] {endpoint} | User: {user_id}")

            if data is not None or endpoint in ["/start_session", "/stop_session"]:
                r = requests.post(url, json=data or {}, headers=headers, timeout=60)
            else:
                r = requests.get(url, headers=headers, timeout=10)
            
            return r.json()
        except Exception as e:
            return {"error": str(e)}

    def start_browser_session(self, __user__: dict = {}) -> str:
        """
        [REQUIRED FIRST] Starts a new isolated browser session for the current user.
        Returns a 'session_id' that MUST be used in all subsequent commands.
        """
        res = self._req("/start_session", {}, __user__.get("id", "anonymous"))
        return f"Session démarrée ID: {res.get('session_id')}." if "session_id" in res else f"Erreur: {res}"

    def stop_browser_session(self, session_id: str, __user__: dict = {}) -> str:
        """
        [REQUIRED LAST] Closes the browser session and frees memory. 
        Always call this when the navigation task is complete.
        """
        self._req("/stop_session", {"session_id": session_id}, __user__.get("id", "anonymous"))
        return "Session fermée."

    def navigate(self, session_id: str, url: str, __user__: dict = {}) -> str:
        """
        Navigates to a specific URL in the active session.
        Handles redirects automatically.
        """
        res = self._req("/action", {"session_id": session_id, "action": "goto", "params": {"url": url}}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Page chargée : {res.get('title')}"

    def read_page(self, session_id: str, __user__: dict = {}) -> str:
        """
        Extracts the main text content of the current page.
        Returns Markdown format (Links, Headers, Text) for better understanding.
        """
        res = self._req("/action", {"session_id": session_id, "action": "read"}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return f"URL: {res.get('url')}\n\nCONTENU (Markdown):\n{res.get('content', '')[:15000]}..."

    def highlight_elements(self, session_id: str, __user__: dict = {}) -> str:
        """
        [VISION AUGMENTED] Injects numeric markers (red tags) on all interactive elements.
        Use this BEFORE 'click_element' to identify elements by their ID number.
        Returns the count of marked elements.
        """
        res = self._req("/action", {"session_id": session_id, "action": "highlight"}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Vision activée : {res.get('count')} éléments marqués. Utilisez ces numéros pour cliquer."

    def click_element(self, session_id: str, selector: str, __user__: dict = {}) -> str:
        """
        Clicks on an element. Automatically scrolls to it first.
        
        The 'selector' argument accepts 3 formats (in order of reliability):
        1. [BEST] Numeric Index: The number displayed by 'highlight_elements' (e.g., '12').
        2. [GOOD] Exact Text: The visible text of the button/link (e.g., 'Login').
        3. [HARD] CSS Selector: Standard CSS selector (e.g., '.btn-primary').
        """
        res = self._req("/action", {"session_id": session_id, "action": "click", "params": {"selector": selector}}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return "Clic effectué."

    def type_text(self, session_id: str, selector: str, text: str, __user__: dict = {}) -> str:
        """
        Types text into an input field.
        'selector' can be a CSS selector, an element ID, or a numeric index from 'highlight_elements'.
        """
        res = self._req("/action", {"session_id": session_id, "action": "type", "params": {"selector": selector, "text": text}}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return f"'{text}' saisi."

    def press_key(self, session_id: str, key: str = "ENTER", __user__: dict = {}) -> str:
        """
        [NEW] Presses a keyboard key. Useful for submitting searches/forms without a button.
        Common keys: ENTER, TAB, ESCAPE, BACKSPACE.
        """
        res = self._req("/action", {"session_id": session_id, "action": "key", "params": {"key": key}}, __user__.get("id"))
        if "error" in res: return f"Erreur: {res['error']}"
        return f"Touche {key} envoyée."

    def quick_read(self, url: str, __user__: dict = {}) -> str:
        """
        [ONE-SHOT] Opens a URL, reads the content, and closes the session immediately.
        Use this for simple reading tasks where no interaction (clicking/login) is required.
        """
        user_id = __user__.get("id", "anonymous")
        
        # 1. Start Temp Session
        start = self._req("/start_session", {}, user_id)
        if "session_id" not in start: 
            return f"Erreur init: {start}"
        sid = start["session_id"]
        
        # 2. Go & Read
        self._req("/action", {"session_id": sid, "action": "goto", "params": {"url": url}}, user_id)
        read = self._req("/action", {"session_id": sid, "action": "read"}, user_id)
        
        # 3. Cleanup
        self._req("/stop_session", {"session_id": sid}, user_id)
        
        if "content" in read:
            # Note: Le contenu est déjà converti en Markdown par le serveur si html2text est présent
            return f"Lecture rapide de {url}:\n{read['content'][:15000]}"
        return f"Lecture échouée: {read.get('error', 'Inconnue')}"