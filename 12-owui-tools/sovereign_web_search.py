# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
"""
title: ECHO Sovereign Web Search
author: Wilfried BARNAVON
version: 6.18.2.1
description: Composant système interne : ECHO Sovereign Web Search.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.2.1: Support JSON complet (answers/WeatherAnswer, infoboxes, engines, publishedDate, unresponsive_engines), fix language='all' et engines à espaces, ajout catégories files/movies/weather.
# 6.7.7.1: Passage de delegate_deep_research sur tranches autonomes par tours (ECHO_AUTONOMOUS_MAX_TURNS=15, ECHO_AUTONOMOUS_TIMEOUT_SEC=300) et suppression de DEEP_RESEARCH_MAX_CALLS_DEFAULT.
# 6.7.1.2: Conservation de l'historique thread deep_research pour observabilité UI (suppression close auto) et role_name explicite.
# 6.7.1.1: Mise en conformité PAS des prompts et directives.
# 6.6.30.1: Câblage de delegate_deep_research sur EchoSubagentController (ECHO_AUTONOMOUS_ROUND_MAX_CALLS).

import httpx
import sys
import uuid
from typing import Optional, Any, Literal
from pydantic import BaseModel, Field

# Importations ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_events import EchoEvents
from echo_core import wrap_tool_output, echo_tool
from echo_constants import ECHO_AUTONOMOUS_MAX_TURNS, ECHO_AUTONOMOUS_TIMEOUT_SEC, ECHO_SEARXNG_BASE_URL, ECHO_USER_AGENT
from echo_prompts import SYS_SEARCH_STATIC
from echo_subagent_hub import EchoSubagentController


def _clean_snippet(text: Any) -> str:
    return str(text or "").replace("<b>", "").replace("</b>", "").strip()


def _extract_answer_text(ans: Any) -> tuple[str, Optional[str]]:
    """Extrait le texte et le lien optionnel d'une entrée answers SearXNG (Answer, Translations, WeatherAnswer)."""
    if not isinstance(ans, dict):
        return _clean_snippet(ans), None
    link = ans.get("url")
    txt = _clean_snippet(ans.get("answer"))
    if txt:
        return txt, link
    if isinstance(ans.get("translations"), list):
        parts = [_clean_snippet(t.get("text")) for t in ans["translations"] if isinstance(t, dict) and t.get("text")]
        return "; ".join(p for p in parts if p), link
    cur = ans.get("current")
    if isinstance(cur, dict):
        summary = _clean_snippet(cur.get("summary") or f"{cur.get('location', '')}: {cur.get('temperature', '')} ({cur.get('condition', '')})")
        fc_parts = []
        for fc in (ans.get("forecasts") or [])[:3]:
            if isinstance(fc, dict) and fc.get("summary"):
                dt = str(fc.get("datetime") or "")[:16]
                fc_parts.append(f"{dt}: {_clean_snippet(fc['summary'])}" if dt else _clean_snippet(fc["summary"]))
        svc = _clean_snippet(ans.get("service"))
        if svc:
            summary += f" [via {svc}]"
        if fc_parts:
            summary += " | Prévisions : " + " ; ".join(fc_parts)
        return summary, link
    return "", link


def _format_searxng_payload(data: dict, max_results: int, is_sub_agent: bool) -> Optional[str]:
    """Formate les réponses directes, infoboxes et résultats fusionnés SearXNG en Markdown."""
    sections = []

    # 1. Réponses directes (calculs, devises, conversions, traductions, météo)
    answer_lines = []
    for ans in data.get("answers", [])[:3]:
        txt, link = _extract_answer_text(ans)
        if txt:
            answer_lines.append(f"- {txt}" + (f" ([Source]({link}))" if link else ""))
    if answer_lines:
        sections.append("### ⚡ Réponses directes\n" + "\n".join(answer_lines))

    # 2. Infoboxes (Wikipédia, Wikidata)
    infobox_blocks = []
    for ib in data.get("infoboxes", [])[:2]:
        if not isinstance(ib, dict):
            continue
        title = _clean_snippet(ib.get("infobox") or "Fiche synthétique")
        urls = ib.get("urls") if isinstance(ib.get("urls"), list) else []
        first_url = urls[0].get("url") if urls and isinstance(urls[0], dict) else None
        link = ib.get("id") or first_url or "#"
        engine = ib.get("engine") or "infobox"
        extract = _clean_snippet(ib.get("content"))
        attrs = []
        for attr in (ib.get("attributes") or [])[:6]:
            if isinstance(attr, dict) and attr.get("label") and attr.get("value"):
                attrs.append(f"{_clean_snippet(attr['label'])}: {_clean_snippet(attr['value'])}")
        block = f"- **[{title}]({link})** (via {engine})"
        if extract:
            block += f"\n  _{extract}_"
        if attrs:
            block += f"\n  `{' | '.join(attrs)}`"
        infobox_blocks.append(block)
    if infobox_blocks:
        sections.append("### 📌 Fiches de référence\n" + "\n\n".join(infobox_blocks))

    # 3. Résultats web ordonnés et dédupliqués
    results = data.get("results", [])[:max_results]
    formatted_results = []
    for i, r in enumerate(results, 1):
        title = _clean_snippet(r.get("title")) or "Sans titre"
        snippet = _clean_snippet(r.get("content"))
        link = r.get("url", "#")
        engines_list = r.get("engines")
        if isinstance(engines_list, list) and engines_list:
            via = ", ".join(str(e) for e in engines_list)
        else:
            via = str(r.get("engine") or "web")
        pub_raw = r.get("publishedDate")
        date_str = f" — {str(pub_raw)[:10]}" if pub_raw else ""
        entry = f"{i}. **[{title}]({link})** (via {via}{date_str})"
        if snippet:
            entry += f"\n   _{snippet}_"
        formatted_results.append(entry)
    if formatted_results:
        sections.append("\n\n".join(formatted_results))

    if not sections:
        return None

    output = "## 🔎 Résultats de recherche Web\n\n" + "\n\n".join(sections)
    suggestions = data.get("suggestions", [])
    if is_sub_agent and suggestions:
        output += f"\n\n💡 **Suggestions :** {', '.join(str(s) for s in suggestions[:5])}"
    return output


class Tools:
    class Valves(BaseModel):
        pass

    class UserValves(BaseModel):
        MAX_RESULTS: int = Field(default=8, description="Nombre de résultats web à extraire.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    @echo_tool
    async def search_instant_answer(
        self,
        query: str,
        __user__: dict = {},
        __event_emitter__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """Obtient une définition encyclopédique, biographie ou fait statique via le moteur de réponses instantanées (hors actualité)."""
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
                    return output
                
                return wrap_tool_output(text="⚠️ Aucune réponse instantanée disponible.", status={"status": "no_result"})
        except Exception as e:
            return f"❌ Erreur DDG: {str(e)}"

    @echo_tool
    async def search_web(
        self,
        query: str,
        categories: Optional[Literal[
            'general', 'it', 'science', 'images', 'videos', 'social media',
            'news', 'music', 'map', 'files', 'movies', 'weather'
        ]] = None,
        time_range: Optional[Literal['day', 'week', 'month', 'year']] = None,
        language: Literal['fr-FR', 'en-US', 'all'] = "fr-FR",
        safesearch: int = 1,
        engines: Optional[str] = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None
    ) -> str:
        """Exécute une recherche web rapide via le Méta-moteur de recherche et retourne des extraits et URLs (max 2 appels parallèles ; définir `time_range` pour le récent)."""
        events = EchoEvents(__event_emitter__)
        u_valves = __user__.get("valves", self.UserValves()) if __user__ else self.UserValves()
        await events.status(f"🔍 SearxNG Deep Search : {query}...")

        params = {
            "q": query,
            "format": "json",
            "language": language,
            "safesearch": safesearch
        }
        if categories:
            params["categories"] = categories
        if time_range:
            params["time_range"] = time_range
        if engines:
            cleaned_engines = ",".join(e.strip() for e in engines.split(",") if e.strip())
            if cleaned_engines:
                params["engines"] = cleaned_engines

        url = f"{ECHO_SEARXNG_BASE_URL}/search"
        headers = {"User-Agent": ECHO_USER_AGENT}

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, data=params, headers=headers)
                if resp.status_code != 200:
                    return f"❌ SearxNG indisponible ({resp.status_code}). Le Système DOIT vérifier le conteneur Docker."

                data = resp.json()
                is_sub_agent = __metadata__.get("_is_deep_research_agent", False)
                output = _format_searxng_payload(data, u_valves.MAX_RESULTS, is_sub_agent)
                if not output:
                    unresponsive = data.get("unresponsive_engines") or []
                    err_note = ""
                    if unresponsive:
                        pairs = [f"{item[0]} ({item[1]})" for item in unresponsive[:5] if isinstance(item, (list, tuple)) and len(item) >= 2]
                        if pairs:
                            err_note = f" Moteurs en échec : {', '.join(pairs)}."
                    base_msg = (
                        "⚠️ Requête sans réponse. Altération des mots-clés requise."
                        if is_sub_agent
                        else "⚠️ Aucun résultat trouvé pour cette requête."
                    )
                    await events.status("Aucun résultat.", done=True)
                    return wrap_tool_output(text=base_msg + err_note, status={"status": "no_result"})

                await events.status("Recherche terminée.", done=True)
                return output
        except Exception as e:
            return f"❌ Erreur SearxNG: {str(e)}"

    @echo_tool
    async def delegate_deep_research(
        self,
        query: str,
        engines: Optional[str] = Field(
            default=None,
            description="Moteurs cibles séparés par des virgules (ex: 'google scholar,semantic scholar,wikipedia')."
        ),
        target_model_key: Literal["MODEL_ZEPHYR", "MODEL_APOLLO", "MODEL_PROMETHEUS", "MODEL_ATHENA"] = "MODEL_PROMETHEUS",
        __user__: dict = {},
        __chat_id__: str = "",
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """Délègue une investigation web complexe et multi-sources à un sous-agent de recherche profonde multi-tours."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system")
        chat_id = __chat_id__ or (__metadata__ or {}).get("chat_id") or ""
        
        await events.status(f"🕵️‍♂️ Initialisation de la recherche profonde : {query}...")
        
        sid = f"thread_deepresearch_{uuid.uuid4().hex[:8]}"

        allowed = ["search_web", "search_instant_answer", "search_maps", "wait_timer", "delegate_web_browsing"]
        
        # Injection du flag de suppression d'UI pour maps et flag d'agent de recherche
        child_metadata = {**(__metadata__ or {}), "_echo_suppress_map_ui": True, "_is_deep_research_agent": True}
        
        await events.status("🕵️‍♂️ L'agent de recherche explore le web...")
        
        # Injection de la contrainte de moteurs de recherche dans la tâche
        augmented_query = query
        if engines:
            augmented_query += f"\n\n[INSTRUCTION SYSTÈME : Le Modèle DOIT restreindre ses recherches aux moteurs suivants via l'argument 'engines' des outils : {engines}]"
        
        # Exécution
        result = await EchoSubagentController.run_or_resume(
            user_id=user_id,
            chat_id=chat_id,
            task=augmented_query,
            system_prompt=SYS_SEARCH_STATIC,
            skill_id=None,
            sub_sid=sid,
            target_model_key=target_model_key,
            allowed_tools=allowed,
            slice_max_turns=ECHO_AUTONOMOUS_MAX_TURNS,
            slice_timeout_sec=ECHO_AUTONOMOUS_TIMEOUT_SEC,
            autonomous_slice=True,
            engine="deep_research",
            role_name="Recherche Profonde",
            __user__=__user__,
            __metadata__=child_metadata,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

        # Nettoyage de la session Playwright éventuelle uniquement (conservation du thread pour l'UI)
        try:
            from echo_browser_lib import EchoBrowserLib
            await EchoBrowserLib(10, sid, user_id).reset_session()
        except Exception:
            pass
            
        await events.status("✅ Synthèse de recherche finalisée.", done=True)
        return result
