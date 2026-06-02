"""
title: ECHO Sovereign Web Search
author: Wilfried BARNAVON
version: 1.2
description: 1.2: Refonte de search_instant_answer (force les mots-clés intemporels en anglais).
"""

import httpx
import sys
import os
import re
from typing import Optional, Any, List
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoAuth
from echo_constants import ECHO_USER_AGENT, ECHO_SEARXNG_BASE_URL

class Tools:
    class Valves(BaseModel):
        pass

    class UserValves(BaseModel):
        MAX_RESULTS: int = Field(default=8, description="Nombre de résultats web à extraire.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    async def search_instant_answer(
        self,
        query: str,
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Récupère une réponse instantanée (faits, définitions, encyclopédie) via DuckDuckGo.
        RÈGLE STRICTE : Ne JAMAIS formuler de question complète. Fournissez UNIQUEMENT les mots-clés exacts.
        TRÈS IMPORTANT : L'entité cible DOIT être traduite en ANGLAIS pour garantir un résultat.
        Exemples intemporels valides : "Theory of relativity", "Photosynthesis", "Isaac Newton".
        """
        events = EchoEvents(__event_emitter__)
        await events.status(f"🦆 DuckDuckGo Instant Answer : {query}...")
        
        url = f"https://api.duckduckgo.com/?q={query}&format=json&no_html=1&skip_disambig=1"
        headers = {"User-Agent": ECHO_USER_AGENT}
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, headers=headers)
                data = resp.json()
                
                answer = data.get("AbstractText", "")
                source_url = data.get("AbstractURL", "")
                
                if not answer and data.get("Answer"):
                    answer = data.get("Answer")
                
                if answer:
                    output = f"**Source : DuckDuckGo / {data.get('DefinitionSource', 'Wikipédia')}**\n\n{answer}"
                    if source_url: output += f"\n\n[Lire la suite]({source_url})"
                    return wrap_tool_output(text=output, status={"status": "success"})
                
                return wrap_tool_output(text="⚠️ Pas de réponse instantanée trouvée. Essayez 'search_deep_web'.", status={"status": "no_result"})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur DDG: {str(e)}", status={"status": "error"})

    async def search_deep_web(
        self,
        query: str,
        time_range: Optional[str] = None, # None, day, week, month, year
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Recherche web approfondie via SearxNG (agrégateur de moteurs).
        Idéal pour : actualités, tutoriels, forums, recherches complexes.
        :param query: La requête de recherche.
        :param time_range: (Optionnel) Filtre temporel : 'day', 'week', 'month', 'year'.
        """
        events = EchoEvents(__event_emitter__)
        u_valves = __user__.get("valves", self.UserValves()) if __user__ else self.UserValves()
        await events.status(f"🔍 SearxNG Deep Search : {query}...")
        
        params = {
            "q": query,
            "format": "json",
            "language": "fr-FR",
            "safesearch": 1
        }
        if time_range: params["time_range"] = time_range
        
        url = f"{ECHO_SEARXNG_BASE_URL}/search"
        headers = {"User-Agent": ECHO_USER_AGENT}
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, data=params, headers=headers)
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ SearxNG indisponible ({resp.status_code}). Vérifiez le conteneur Docker.", status={"status": "error"})
                
                data = resp.json()
                results = data.get("results", [])[:u_valves.MAX_RESULTS]
                
                if not results:
                    return wrap_tool_output(text="⚠️ Aucun résultat trouvé pour cette recherche.", status={"status": "no_result"})
                
                formatted_results = []
                for i, r in enumerate(results, 1):
                    title = r.get("title", "Sans titre")
                    snippet = r.get("content", "").replace("<b>", "").replace("</b>", "")
                    link = r.get("url", "#")
                    engine = r.get("engine", "web")
                    formatted_results.append(f"{i}. **[{title}]({link})** (via {engine})\n   _{snippet}_")
                
                output = f"## 🔎 Résultats de recherche Web\n\n" + "\n\n".join(formatted_results)
                
                # Capture des suggestions de recherche si présentes
                suggestions = data.get("suggestions", [])
                if suggestions:
                    output += f"\n\n💡 **Suggestions :** {', '.join(suggestions[:5])}"

                await events.status("Recherche terminée.", done=True)
                return wrap_tool_output(text=output, status={"status": "success"})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur SearxNG: {str(e)}", status={"status": "error"})
