"""
title: ECHO Universal API Client
author: Wilfried BARNAVON
version: 1.4
description: 1.4: Switched complex objects to JSON strings to avoid 400 errors with strict Gemini REST schemas.
"""

import requests
import orjson as json
import sys
import socket
import ipaddress
from urllib.parse import urlparse
from pydantic import BaseModel, Field
from typing import Dict, Optional, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output
from echo_constants import ECHO_ALLOWED_DOMAINS

class Tools:
    def __init__(self):
        pass

    def _is_safe_url(self, url: str) -> bool:
        """Vérifie si l'URL pointe vers un réseau privé (SSRF protection)."""
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if not hostname:
                return False
            
            # Vérification du domaine autorisé
            if ECHO_ALLOWED_DOMAINS != "*":
                if hostname not in ECHO_ALLOWED_DOMAINS.split(','):
                    return False

            # Résolution DNS pour bloquer RFC 1918
            ip_addr = socket.gethostbyname(hostname)
            ip = ipaddress.ip_address(ip_addr)
            
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
                return False
                
            return True
        except Exception:
            return False

    def call_api(
        self,
        url: str,
        method: str = "GET",
        headers_json: Optional[str] = None,
        body_json: Optional[str] = None,
    ) -> str:
        """
        Effectue un appel API HTTP universel.
        :param url: L'URL cible de la requête.
        :param method: La méthode HTTP à utiliser (GET, POST, PUT, DELETE). Par défaut 'GET'.
        :param headers_json: Dictionnaire optionnel des headers HTTP au format chaîne JSON (ex: '{"Authorization": "Bearer..."}').
        :param body_json: Corps de la requête au format chaîne JSON. Sera automatiquement converti en dictionnaire.
        """
        if not self._is_safe_url(url):
             return wrap_tool_output(text="❌ Accès réseau non autorisé (SSRF ou domaine non whitelisté).", status={"status": "error", "domain": url})

        # Parsing des paramètres JSON via orjson
        actual_headers = {}
        if headers_json:
            try:
                actual_headers = json.loads(headers_json)
            except Exception as e:
                return wrap_tool_output(text=f"❌ Erreur format headers_json : {str(e)}", status={"status": "error"})

        actual_body = None
        if body_json:
            try:
                actual_body = json.loads(body_json)
            except Exception as e:
                return wrap_tool_output(text=f"❌ Erreur format body_json : {str(e)}", status={"status": "error"})

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=actual_headers,
                json=actual_body,
                timeout=15
            )
            
            status_meta = {"status": response.status_code, "url": url, "method": method}
            
            try:
                res_json = response.json()
                text_out = json.dumps(res_json, option=json.OPT_INDENT_2).decode('utf-8')
            except:
                text_out = response.text[:5000]

            if response.status_code >= 400:
                text_out = f"❌ Erreur API {response.status_code}\n\n{text_out}"
            
            return wrap_tool_output(text=text_out, status=status_meta)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Exception API : {str(e)}", status={"status": "error", "error": str(e)})
