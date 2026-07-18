"""
title: ECHO Universal API Client
author: Wilfried BARNAVON
version: 1.7
description: Composant système interne : ECHO Universal API Client.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.4: Switched complex objects to JSON strings to avoid 400 errors with strict Gemini REST schemas.
# 1.6: Ajout des arguments manquant (__metadata__, __user__) dans l'interface pour garantir l'injection.
# 1.7: Nettoyage du code : suppression des imports inutilisés (PEP8).

import requests
import orjson as json
import sys
import socket
import ipaddress
from urllib.parse import urlparse
from typing import Optional, Literal

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
        method: Literal["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"] = "GET",
        headers: Optional[dict] = None,
        body: Optional[dict] = None,
        __user__: dict = {},
        __metadata__: dict = {},
    ) -> str:
        """
        Requête HTTP universelle. Optionnel : headers, body.
        :param url: URL cible sécurisée.
        :param method: (Optionnel) GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS (Défaut: GET).
        :param headers: (Optionnel) Dictionnaire Headers.
        :param body: (Optionnel) Corps de requête format Object.
        """
        if not self._is_safe_url(url):
             return wrap_tool_output(text="❌ Accès réseau non autorisé (SSRF ou domaine non whitelisté).", status={"status": "error", "domain": url}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        actual_headers = headers if headers else {}
        actual_body = body if body else None

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
            
            return wrap_tool_output(text=text_out, status=status_meta, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Exception API : {str(e)}", status={"status": "error", "error": str(e)}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
