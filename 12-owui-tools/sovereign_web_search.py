"""
title: ECHO Sovereign Web Search
author: Wilfried BARNAVON
version: 1.18
description: Composant système interne : ECHO Sovereign Web Search.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.15: Ajout des arguments manquant (__metadata__, __user__) dans l'interface pour garantir l'injection.
# 1.16: Nettoyage du code : suppression des imports inutilisés (PEP8).
# 1.17: Optim - Ajout de la priorisation des informations récentes via consignes et utilisation proactive de time_range.

import httpx
import sys
import uuid
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_core import wrap_tool_output
from echo_state_manager import EchoStateManager
from echo_constants import ECHO_USER_AGENT, ECHO_SEARXNG_BASE_URL, DEEP_RESEARCH_MAX_CALLS_DEFAULT
from echo_prompts import SYS_SEARCH_STATIC

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
        Pour prioriser l'actualité ou les informations récentes, définissez TOUJOURS time_range (ex: 'year', 'month' ou 'week'). Par défaut aucune limite temporelle n'est appliquée.
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
        
        # La définition de STATIC_SYSTEM_PROMPT a été migrée vers echo_prompts.py
        
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
            system_prompt=SYS_SEARCH_STATIC,
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
