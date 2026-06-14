"""
title: ECHO Sovereign Web Search
author: Wilfried BARNAVON
version: 1.7
description: 1.7: Précision dans la docstring de delegate_deep_research sur le comportement conditionnel du navigateur (dernier recours).
             1.6: Renommage de search_deep_web en search_web pour dissiper la confusion avec le "Dark Web".
             1.5: Clarification sémantique des docstrings des outils de recherche pour optimiser le routage.
             1.4: Amélioration de la docstring de delegate_deep_research pour clarifier son usage.
             1.3: Ajout de l'outil delegate_deep_research pour la recherche autonome multi-tours.
             1.2: Refonte de search_instant_answer (force les mots-clés intemporels en anglais).
"""

import httpx
import sys
import os
import re
import uuid
from typing import Optional, Any, List
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoAuth, EchoStateManager
from echo_constants import ECHO_USER_AGENT, ECHO_SEARXNG_BASE_URL, DEEP_RESEARCH_MAX_CALLS_DEFAULT

class Tools:
    class Valves(BaseModel):
        pass

    class UserValves(BaseModel):
        MAX_RESULTS: int = Field(default=8, description="Nombre de résultats web à extraire.")
        DEEP_RESEARCH_MAX_CALLS: int = Field(
            default=DEEP_RESEARCH_MAX_CALLS_DEFAULT,
            description="Budget max d'appels de fonctions pour l'agent de recherche profonde."
        )

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
        Idéal pour : Vérifier une date, un chiffre ou une définition factuelle rapide. Ne retourne pas de liste de sites web.
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
                
                return wrap_tool_output(text="⚠️ Pas de réponse instantanée trouvée. Essayez 'search_web'.", status={"status": "no_result"})
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur DDG: {str(e)}", status={"status": "error"})

    async def search_web(
        self,
        query: str,
        time_range: Optional[str] = None, # None, day, week, month, year
        __user__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Recherche web classique via SearxNG (agrégateur). Retourne une liste de liens et de courts extraits.
        Idéal pour : L'actualité récente, trouver des sources spécifiques, lire les titres de forums ou récupérer rapidement des liens de référence. C'est une recherche en une seule passe (One-Shot).
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

    async def delegate_deep_research(
        self,
        query: str,
        target_model_key: str = "MODEL_PRO",
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Agent autonome de recherche profonde (Deep Research Agent).
        Idéal pour : Déléguer une recherche web sur un sujet complexe, large ou profond nécessitant une synthèse exhaustive.
        L'agent travaillera en arrière-plan (multi-tours, croisement de sources) et te retournera un rapport consolidé complet.
        À utiliser quand le sujet demande de l'investigation. Note : Il privilégiera les requêtes web classiques et n'invoquera le navigateur autonome qu'en dernier recours absolu si les autres méthodes ne donnent pas satisfaction.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        u_valves = __user__.get("valves", self.UserValves()) if __user__ else self.UserValves()
        
        await events.status(f"🕵️‍♂️ Initialisation de la recherche profonde : {query}...")
        
        # Récupération de l'agent engine via sys.modules
        _agent_mod = sys.modules.get("tool_agent_engine_tool")
        if not _agent_mod or not hasattr(_agent_mod, "Tools"):
            return wrap_tool_output(
                text="❌ L'outil agent_engine_tool est introuvable. Activez-le dans l'interface.",
                status={"status": "error"}
            )
        
        # Instanciation du Delegate
        delegate = _agent_mod.Tools()
        # On passe les valves de l'utilisateur
        delegate.user_valves = u_valves
        
        sid = f"thread_deepresearch_{uuid.uuid4().hex[:8]}"
        
        STATIC_SYSTEM_PROMPT = (
            "Tu es un chercheur web expert, rigoureux et autonome.\n"
            "Ta mission est d'explorer le sujet demandé de manière exhaustive, de croiser les sources "
            "et d'identifier les angles morts pour garantir la complétude du champ de recherche.\n\n"
            "RÈGLES ABSOLUES :\n"
            "1. Poursuis ta recherche tant que tu n'as pas une vision complète et vérifiée.\n"
            "2. Privilégie 'search_web' et 'search_instant_answer' pour l'agrégation de données.\n"
            "3. N'utilise le navigateur web ('delegate_web_browsing') qu'en dernier recours absolu "
            "si une source l'exige impérativement.\n"
            "4. Si tu utilises 'search_maps', tu DOIS passer l'argument print_map=False pour ne pas afficher de carte.\n"
            "5. Produis une synthèse finale riche, structurée en Markdown, et citant précisément tes sources."
        )
        
        allowed = ["search_web", "search_instant_answer", "delegate_web_browsing", "search_maps"]
        
        # Injection du flag de suppression d'UI pour maps
        child_metadata = {**(__metadata__ or {}), "_echo_suppress_map_ui": True}
        
        await events.status("🕵️‍♂️ L'agent de recherche explore le web...")
        
        # Exécution
        result = await delegate.delegate_to_agent(
            task=query,
            system_prompt=STATIC_SYSTEM_PROMPT,
            role_name=None,
            sub_sid=sid,
            with_context_distillate=False,
            target_model_key=target_model_key,
            allowed_tools=allowed,
            max_calls_override=u_valves.DEEP_RESEARCH_MAX_CALLS,
            __tools__=None,
            __user__=__user__,
            __chat_id__=__chat_id__,
            __metadata__=child_metadata,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__
        )
        
        # Nettoyage de la DB (thread éphémère)
        try:
            EchoStateManager(user_id, __chat_id__).delete_thread(sid)
        except Exception:
            pass
            
        await events.status("✅ Synthèse de recherche finalisée.", done=True)
        return result
