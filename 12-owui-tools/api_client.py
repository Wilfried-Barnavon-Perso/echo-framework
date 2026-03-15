"""
title: ECHO Universal API Client
author: Wilfried BARNAVON
version: 1.3
description: 1.3: Enriched docstrings with parameters.
"""

import requests
import json
import sys
from pydantic import BaseModel, Field
from typing import Dict, Optional, Any

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output

class Tools:
    class Valves(BaseModel):
        allowed_domains: str = Field(default="*", description="Domaines autorisés (séparés par virgule) ou *")

    def __init__(self):
        self.valves = self.Valves()

    def call_api(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict] = None,
        body: Optional[Dict] = None,
    ) -> str:
        """
        Effectue un appel API HTTP universel.
        :param url: L'URL cible de la requête.
        :param method: La méthode HTTP à utiliser (GET, POST, PUT, DELETE). Par défaut 'GET'.
        :param headers: Dictionnaire optionnel des headers HTTP (ex: {'Authorization': 'Bearer ...'}). ECHO ajoutera un User-Agent si absent.
        :param body: Dictionnaire optionnel du corps de la requête. Sera automatiquement converti en JSON.
        """
        if self.valves.allowed_domains != "*" and url.split('/')[2] not in self.valves.allowed_domains.split(','):
             return wrap_tool_output(text="❌ Domaine non autorisé.", status={"status": "error", "domain": url})

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                headers=headers or {},
                json=body,
                timeout=15
            )
            
            status_meta = {"status": response.status_code, "url": url, "method": method}
            
            try:
                res_json = response.json()
                text_out = json.dumps(res_json, indent=2, ensure_ascii=False)
            except:
                text_out = response.text[:5000]

            if response.status_code >= 400:
                text_out = f"❌ Erreur API {response.status_code}\n\n{text_out}"
            
            return wrap_tool_output(text=text_out, status=status_meta)

        except Exception as e:
            return wrap_tool_output(text=f"❌ Exception API : {str(e)}", status={"status": "error", "error": str(e)})
