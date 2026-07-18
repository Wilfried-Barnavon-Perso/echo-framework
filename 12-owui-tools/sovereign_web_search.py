"""
title: ECHO Sovereign Web Search
author: Wilfried BARNAVON
version: 1.16
description: Composant système interne : ECHO Sovereign Web Search.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.14: Fix - Ajout de la règle anti-spam dans la docstring de search_web pour interdire le burst (Parallel Function Calling) par le modèle principal.
# 1.13: Optim - Nouveaux paramètres SearxNG, résolution du paradoxe logique (docstrings) et suppression algorithmique des appâts de boucle.
# 1.12: Optim - Rééquilibrage cognitif des docstrings de recherche pour prioriser delegate_deep_research sur les requêtes complexes.
# 1.11: Optim - Ajout de la suggestion de changement de méthode en cas de blocage SearXNG.
# 1.10: Optim - Intégration sous contrainte de delegate_web_browsing dans le Deep Research Agent.
# 1.15: Ajout des arguments manquant (__metadata__, __user__) dans l'interface pour garantir l'injection.
# 1.16: Nettoyage du code : suppression des imports inutilisés (PEP8).

import httpx
import sys
import uuid
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import EchoEvents, wrap_tool_output, EchoStateManager
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
        __event_emitter__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """
        Permet au Modèle d'obtenir des définitions de concepts, biographies ou dates via DuckDuckGo (type Wikipédia). Actualité ou événements récents proscrits. Les requêtes doivent utiliser des mots-clés stricts.
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
                    return wrap_tool_output(text=output, status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                
                return wrap_tool_output(text="⚠️ Aucune réponse instantanée. IMPLIQUE `search_web`.", status={"status": "no_result"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur DDG: {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def search_web(
        self,
        query: str,
        categories: Optional[Literal['general', 'it', 'science', 'images', 'videos', 'social media', 'news', 'music', 'map']] = None,
        time_range: Optional[Literal['day', 'week', 'month', 'year']] = None,
        language: Literal['fr-FR', 'en-US', 'all'] = "fr-FR",
        safesearch: int = 1,
        engines: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """
        Permet au Modèle d'effectuer une recherche web simple (one-shot). Retourne des extraits textuels (snippets) limités. Strictement réservé à l'extraction de faits rapides ou d'URLs. Ne permet pas de lire le contenu complet des pages.
        ANTI-SPAM : Le Modèle a l'INTERDICTION d'exécuter plus de 2 appels à cet outil simultanément lors d'un même tour (Parallel Function Calling). Pour une recherche exhaustive, complexe ou nécessitant de multiples requêtes, le Modèle DOIT utiliser l'outil `delegate_deep_research`.
        
        :param query: La recherche textuelle.
        :param categories: Catégorie spécifique de recherche.
        :param time_range: Filtre temporel.
        :param language: Code langue, ex: 'fr-FR', 'en-US', 'all' (défaut: 'fr-FR').
        :param safesearch: 0:Désactivé, 1:Modéré, 2:Strict (défaut: 1).
        :param engines: Moteurs séparés par des virgules sans espace (ex: 'google,duckduckgo,wikipedia').
        """
        events = EchoEvents(__event_emitter__)
        u_valves = __user__.get("valves", self.UserValves()) if __user__ else self.UserValves()
        await events.status(f"🔍 SearxNG Deep Search : {query}...")
        
        params = {
            "q": query,
            "format": "json",
            "language": language if language != "all" else "",
            "safesearch": safesearch
        }
        if categories: params["categories"] = categories
        if time_range: params["time_range"] = time_range
        if engines: params["engines"] = engines.replace(" ", "")
        
        url = f"{ECHO_SEARXNG_BASE_URL}/search"
        headers = {"User-Agent": ECHO_USER_AGENT}
        
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, data=params, headers=headers)
                if resp.status_code != 200:
                    return wrap_tool_output(text=f"❌ SearxNG indisponible ({resp.status_code}). Vérifiez le conteneur Docker.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                
                data = resp.json()
                results = data.get("results", [])[:u_valves.MAX_RESULTS]
                is_sub_agent = __metadata__.get("_is_deep_research_agent", False)
                
                if not results:
                    if not is_sub_agent:
                        return wrap_tool_output(text="⚠️ Aucun résultat. Les extraits textuels courts sont insuffisants, IMPLIQUE `delegate_deep_research`.", status={"status": "no_result"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                    else:
                        return wrap_tool_output(text="⚠️ Requête sans réponse. Altération des mots-clés requise.", status={"status": "no_result"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
                
                formatted_results = []
                for i, r in enumerate(results, 1):
                    title = r.get("title", "Sans titre")
                    snippet = r.get("content", "").replace("<b>", "").replace("</b>", "")
                    link = r.get("url", "#")
                    engine = r.get("engine", "web")
                    formatted_results.append(f"{i}. **[{title}]({link})** (via {engine})\n   _{snippet}_")
                
                output = "## 🔎 Résultats de recherche Web\n\n" + "\n\n".join(formatted_results)
                
                # Capture des suggestions de recherche si présentes (masqué pour le modèle principal pour éviter les boucles)
                suggestions = data.get("suggestions", [])
                if is_sub_agent and suggestions:
                    output += f"\n\n💡 **Suggestions :** {', '.join(suggestions[:5])}"

                await events.status("Recherche terminée.", done=True)
                return wrap_tool_output(text=output, status={"status": "success"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        except Exception as e:
            return wrap_tool_output(text=f"❌ Erreur SearxNG: {str(e)}", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def delegate_deep_research(
        self,
        query: str,
        engines: Optional[str] = Field(default=None, description="Ciblage éventuel des moteurs pour l'agent (ex: 'scholar,wikipedia')"),
        target_model_key: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Permet au Modèle de déléguer la recherche web complexe à un Agent Autonome multi-tours. L'agent naviguera en profondeur pour lire le contenu intégral des pages, procéder à des investigations vastes et croiser de multiples sources.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        u_valves = __user__.get("valves", self.UserValves()) if __user__ else self.UserValves()
        
        await events.status(f"🕵️‍♂️ Initialisation de la recherche profonde : {query}...")
        
        # Récupération de l'agent engine via sys.modules
        _agent_mod = sys.modules.get("tool_agent_engine_tool")
        if not _agent_mod or not hasattr(_agent_mod, "Tools"):
            return wrap_tool_output(
                text="❌ `agent_engine_tool` introuvable. IMPLIQUE notification utilisateur pour activation.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
        
        # Instanciation du Delegate
        delegate = _agent_mod.Tools()
        # On passe les valves de l'utilisateur
        delegate.user_valves = u_valves
        
        sid = f"thread_deepresearch_{uuid.uuid4().hex[:8]}"
        
        STATIC_SYSTEM_PROMPT = (
            "<persona>\n"
            "Le Modèle est un expert en recherche web approfondie, rigoureuse et autonome.\n"
            "</persona>\n\n"
            "<mission>\n"
            "Le Modèle doit explorer le sujet de manière exhaustive, croiser de multiples sources et combler proactivement les angles morts pour garantir une complétude absolue.\n"
            "</mission>\n\n"
            "<rules>\n"
            "1. ITÉRATION : Le Modèle DOIT poursuivre sa recherche tant que son analyse globale n'est pas complète et factuellement vérifiée.\n"
            "2. OUTILS : Le Modèle DOIT privilégier 'search_web' et 'search_instant_answer'.\n"
            "3. CARTOGRAPHIE : Si l'outil 'search_maps' est mobilisé, le Modèle DOIT obligatoirement définir l'argument 'print_map=False'.\n"
            "4. ANTI-SPAM : Le Modèle a l'INTERDICTION d'exécuter plus de 2 appels à 'search_web' simultanément lors d'un même tour. Il DOIT agréger ses mots-clés en requêtes denses.\n"
            "5. NAVIGATION ('delegate_web_browsing') : Cet outil est STRICTEMENT réservé à l'extraction sur une URL absolue précise obtenue précédemment. INTERDICTION FORMELLE de l'utiliser sur un moteur de recherche. L'argument 'max_iterations=20' est OBLIGATOIRE.\n"
            "</rules>\n\n"
            "<output_format>\n"
            "Le Modèle doit produire une synthèse finale structurée en Markdown, en citant rigoureusement chaque source consultée.\n"
            "</output_format>"
        )
        
        allowed = ["search_web", "search_instant_answer", "search_maps", "wait_timer", "delegate_web_browsing"]
        
        # Injection du flag de suppression d'UI pour maps et flag d'agent de recherche
        child_metadata = {**(__metadata__ or {}), "_echo_suppress_map_ui": True, "_is_deep_research_agent": True}
        
        await events.status("🕵️‍♂️ L'agent de recherche explore le web...")
        
        # Injection de la contrainte de moteurs de recherche dans la tâche
        augmented_query = query
        if engines:
            augmented_query += f"\n\n[INSTRUCTION SYSTÈME : Vous DEVEZ restreindre vos recherches aux moteurs suivants via l'argument 'engines' de vos outils : {engines}]"
        
        # Exécution
        result = await delegate.delegate_to_agent(
            task=augmented_query,
            system_prompt=STATIC_SYSTEM_PROMPT,
            skill_id=None,
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
