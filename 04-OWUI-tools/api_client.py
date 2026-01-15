"""
title: Universal API Client
author: Wilfried BARNAVON
version: v1.0
description: Permet d'effectuer des appels API REST (GET, POST, etc.) vers des services externes.
"""

import requests
import json
from pydantic import BaseModel, Field
from typing import Dict, Optional

class Tools:
    class Valves(BaseModel):
        allowed_domains: str = Field(default="*", description="Domaines autorisés (séparés par virgule) ou *")

    def __init__(self):
        self.valves = self.Valves()

    def call_api(self, url: str, method: str = "GET", headers: Optional[Dict] = None, body: Optional[Dict] = None) -> str:
        """
        Effectue un appel API HTTP.
        :param url: L'URL cible.
        :param method: La méthode HTTP (GET, POST, PUT, DELETE).
        :param headers: Dictionnaire des headers.
        :param body: Dictionnaire du corps de la requête (JSON).
        """
        if self.valves.allowed_domains != "*" and url.split('/')[2] not in self.valves.allowed_domains.split(','):
             return json.dumps({"error": "Domaine non autorisé par la configuration de sécurité."})

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                json=body,
                timeout=15
            )
            
            try:
                return json.dumps({
                    "status": response.status_code,
                    "data": response.json()
                }, indent=2)
            except:
                return json.dumps({
                    "status": response.status_code,
                    "text": response.text[:2000]
                })
        except Exception as e:
            return json.dumps({"error": str(e)})