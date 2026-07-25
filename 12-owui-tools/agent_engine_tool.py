"""
title: ECHO Agent Engine
author: ECHO Framework
version: 1.15
description: Composant système interne : ECHO Agent Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.13: Ajout des cas d'usage dans la docstring (protection du contexte de l'Orchestrateur).
# 1.12: Précision docstring sur l'héritage du système prompt de l'orchestrateur.
# 1.11: Correction injection PRAF (évite doublon si héritage du Kernel). Suppression acronyme PRAF.
# 1.10: Consolidation de l'injection universelle (date + PRAF ajusté) via <directives_globales>.
# 1.9: Injection universelle du contexte temporel (date iso) à la fin du base_system des agents délégués.
# 1.14: Ajout des arguments manquant (__metadata__, __user__) dans l'interface pour garantir l'injection.
# 1.15: Nettoyage du code : suppression des imports inutilisés (PEP8).

import sys
import uuid
import re
import time
import inspect
from pydantic import BaseModel, Field
from typing import Optional, Any, List

sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    wrap_tool_output, EchoEvents,
    EchoGeminiClient, EchoStateManager, clamp_model,
    estimate_token_size, smart_truncate_history
)
from echo_constants import (
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    DELEGATE_AGENT_BLACKLIST,
    DELEGATE_SYSTEM_APPENDIX, CONTEXT_TRUNCATE_THRESHOLD,
    ECHO_MAX_CONTEXT_SIZE, get_generation_config
)
from echo_skills import get_skill_content, parse_skill_metadata

# Identifiant de rôle pour les threads delegate dans cognitive_threads
_DELEGATE_ROLE_ID = "delegate"



class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(
            default=ECHO_API_KEY_THRESHOLD,
            description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours."
        )
        MAX_RETRIES: int = Field(
            default=ECHO_API_MAX_RETRIES,
            description="Nombre de tentatives maximum."
        )

    class UserValves(BaseModel):
        MAX_AGENT_FUNCTION_CALLS: int = Field(
            default=25, ge=5, le=50,
            description=(
                "Budget d'appels de fonctions par invocation d'agent. "
                "Compte les décisions d'appel de l'agent uniquement — "
                "les opérations internes des outils appelés (ex: itérations d'un conseil) "
                "ne sont pas comptées."
            )
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # 1. DÉLÉGATION PRINCIPALE
    # ==========================================================================

    async def delegate_to_agent(
        self,
        task: str,
        system_prompt: Optional[str] = None,
        skill_id: Optional[str] = None,
        sub_sid: Optional[str] = None,
        with_context_distillate: bool = False,
        target_model_key: Optional[str] = None,
        # --- Paramètres internes (non exposés au LLM) ---
        allowed_tools: Optional[List[str]] = None,
        max_calls_override: Optional[int] = None,
        __tools__: Optional[list] = None,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Délégation experte par agent autonome.
        CAS D'USAGE PRINCIPAUX :
        1. Exécuter des tâches isolées et spécialisées via des Skills dédiés.
        2. Protéger et ne pas encombrer inutilement le contexte de l'Orchestrateur (offload cognitif pour les opérations verbeuses ou répétitives).
        
        INFO ORCHESTRATEUR : si le système_prompt est vide, ainsi que le skill, alors c'est le système prompt de l'Orchestrateur qui est transmis.
        
        :param task: Mission ou réponse (si reprise).
        :param system_prompt: (Optionnel) Directives comportementales additionnelles.
        :param skill_id: (Optionnel) Identifiant de Skill (se combine avec system_prompt si les deux sont fournis).
        :param sub_sid: (Optionnel) ID de session pour reprise.
        :param with_context_distillate: (Bool) Injection du résumé de branche.
        :param target_model_key: (Optionnel) Enum des modèles (echo_constants).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = (__user__ or {}).get("id", "system")
        chat_id = __chat_id__ or ""
        max_calls = max_calls_override or self.user_valves.MAX_AGENT_FUNCTION_CALLS

        if not user_id or user_id == "system":
            return wrap_tool_output(
                text="❌ Contexte utilisateur manquant.",
                status={"status": "error", "message": "user_id requis"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if not skill_id and not system_prompt:
            # Fallback : Transfert du system_prompt d'ECHO au sous-agent
            try:
                with open("/app/backend/data/system-prompt.md", "r", encoding="utf-8") as f:
                    system_prompt = f.read()
            except Exception:
                system_prompt = "Tu es une extension cognitive experte du framework ECHO."

        # 1. Résolution de la persona (Skill optionnel)
        role_name = None
        if skill_id:
            skill_content = get_skill_content(user_id, skill_id)
            if not skill_content:
                return wrap_tool_output(
                    text=f"❌ Skill '{skill_id}' introuvable. IMPLIQUE `forge_skill`.",
                    status={"status": "error", "message": f"Skill '{skill_id}' not found"}
                , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
            
            # Extraction du nom lisible pour l'UI
            skill_meta = parse_skill_metadata(skill_content)
            role_name = skill_meta.get("name", skill_id)

            # Le Skill définit la persona, le system_prompt l'enrichit contextuellement
            base_system = f"{skill_content}\n\n{system_prompt}" if system_prompt else skill_content
        else:
            base_system = system_prompt

        import datetime
        current_time = datetime.datetime.now().isoformat()
        
        if not base_system or ("<directives_globales>" not in base_system and "<context_temporel>" not in base_system):
            if base_system and "PRAF" in base_system:
                # Si le système hérite déjà du Kernel (contenant PRAF), on n'injecte que la date
                appendix = (
                    f"\n\n<directives_globales>\n"
                    f"- Ancrage Temporel : {current_time}\n"
                    f"</directives_globales>"
                )
            else:
                # Sinon, on injecte la date et une consigne de rigueur factuelle sans jargon
                appendix = (
                    f"\n\n<directives_globales>\n"
                    f"- Ancrage Temporel : {current_time}\n"
                    f"- Rigueur Factuelle : Le Modèle DOIT asseoir son raisonnement sur des certitudes.\n"
                    f"- Budget Maîtrisé : Si des outils de recherche sont disponibles, leur utilisation est ABSOLUMENT réservée à la levée d'un doute critique, la mise à jour temporelle d'une connaissance ou la validation d'un pivot factuel.\n"
                    f"</directives_globales>"
                )
            
            base_system = f"{base_system}{appendix}" if base_system else appendix


        # 2. Résolution du sub_sid
        if sub_sid:
            sid = sub_sid
        elif skill_id:
            sid = f"thread_{skill_id}_{uuid.uuid4().hex[:8]}"
        else:
            sid = f"dlg_{uuid.uuid4().hex[:10]}"
        is_resume = sub_sid is not None

        # 3. Résolution du modèle — orchestrateur > politique par défaut
        #    clamp_model = min(demandé, plafond_politique), centralisé dans echo_utils
        requested = target_model_key or "MODEL_FLASH"
        current_model_key = clamp_model(requested, __metadata__ or {}, user_id)

        # -----------------------------------------------------------------------
        # 3. Résolution des outils disponibles pour le sous-agent
        # -----------------------------------------------------------------------
        # Contrainte architecturale OWUI :
        #   OWUI injecte __tools__ (le dict des outils avec callables) uniquement
        #   dans le Pipe principal. Les tool callables (comme delegate_to_agent)
        #   ne reçoivent PAS __tools__ et ne peuvent PAS modifier __metadata__ de
        #   façon visible par les autres outils (chaque outil reçoit un __metadata__
        #   indépendant).
        #
        # Solution (ECHO v5.166.2) :
        #   Le Pipe stocke __tools__ dans _TOOLS_CACHE[chat_id] (module-level).
        #   agent_engine_tool lit ce cache via sys.modules["function_pipe_engine"].
        #   Cette lecture est un accès direct à un attribut de module, PAS un scan.
        #
        # Priorité des sources :
        #   1. _TOOLS_CACHE[chat_id]            ← callables OWUI originaux (optimal)
        #   2. __metadata__["_echo_tools_dict"] ← fallback symétrique (normalement vide)
        #   3. _resolve_sub_tools_from_sys_modules ← dernier recours si specs disponibles
        # -----------------------------------------------------------------------
        import logging as _dlog
        import sys as _sys
        _log = _dlog.getLogger("echo.delegate")

        # Source 1 : cache module-level du Pipe (contient les callables OWUI originaux)
        _pipe_mod = _sys.modules.get("function_pipe_engine")
        _tools_cache: dict = (getattr(_pipe_mod, "_TOOLS_CACHE", {}) or {}) if _pipe_mod else {}
        _tools_dict: dict = _tools_cache.get(chat_id, {})

        # Source 2 : fallback __metadata__ (normalement vide — OWUI ne le partage pas)
        if not _tools_dict:
            _tools_dict = (__metadata__ or {}).get("_echo_tools_dict", {})

        # Specs OpenAI (pour le dernier recours sys.modules scan)
        _body_tools_specs: list = (__metadata__ or {}).get("_echo_body_tools", [])

        sub_tools: dict = {}
        if _tools_dict:
            # Filtrage de la blacklist : certains outils ne doivent pas être délégués
            # (ex: delegate_to_agent lui-même pour éviter les récursions infinies)
            sub_tools = {
                k: v for k, v in _tools_dict.items()
                if k not in DELEGATE_AGENT_BLACKLIST and (not allowed_tools or k in allowed_tools)
            }
        elif _body_tools_specs:
            # Source 3 : reconstruction depuis sys.modules (valves par défaut)
            sub_tools = _resolve_sub_tools_from_sys_modules(_body_tools_specs, __user__)
            if allowed_tools:
                sub_tools = {k: v for k, v in sub_tools.items() if k in allowed_tools}

        _callable_count = sum(1 for v in sub_tools.values() if v.get("callable") is not None)
        _log.debug(
            "delegate [%s]: cache_keys=%d, _tools_dict=%d, sub_tools=%d, callables=%d",
            sid, len(_tools_cache), len(_tools_dict), len(sub_tools), _callable_count,
        )

        # 4. System prompt final (appendice cadre d'exécution)
        final_system = base_system + DELEGATE_SYSTEM_APPENDIX.format(
            sub_sid=sid, max_calls=max_calls
        )

        # 5. Chargement de l'historique thread (EchoStateManager sur chat_id)
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        history = state.get_thread_history(sid)

        # 6. Distillat de contexte principal (optionnel, non-bloquant)
        if with_context_distillate and not is_resume:
            try:
                await events.status(f"🧠 [{sid}] Distillation du contexte principal...")
                distillate = await _distill_main_context(state, chat_id, user_id)
                if distillate:
                    final_system += f"\n\n## CONTEXTE DU CHAT PRINCIPAL (distillé)\n{distillate}"
            except Exception:
                pass  # Non-bloquant : la tâche continue sans distillat

        # 7. Ajout du message task dans l'historique
        step_index = len(history)
        prefix = "RÉPONSE DE L'ORCHESTRATEUR : " if is_resume else ""
        task_parts = [{"text": f"{prefix}{task}"}]
        history.append({"role": "user", "parts": task_parts})
        state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_index, "user", task_parts)

        label = f"🎓 Expert [{role_name}]" if role_name else f"🤖 Agent [{sid}]"
        await events.status(f"{label} démarré ({current_model_key})...")

        # 8. Boucle agentique
        return await _run_agent_loop(
            sid=sid,
            chat_id=chat_id,
            user_id=user_id,
            history=history,
            state=state,
            sub_tools=sub_tools,
            body_tools_specs=_body_tools_specs,
            final_system=final_system,
            current_model_key=current_model_key,
            max_calls=max_calls,
            events=events,
            valves=self.valves,
            __user__=__user__,
            __chat_id__=__chat_id__,
            __metadata__=__metadata__,
            __event_emitter__=__event_emitter__,
            __event_call__=__event_call__,
        )

    # ==========================================================================
    # 2. LISTE DES SESSIONS
    # ==========================================================================

    async def list_agent_sessions(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """Liste des sessions d'agents actives."""
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)

        if not threads:
            return wrap_tool_output(
                text="ℹ️ Aucune session d'agent active pour cette conversation.",
                status={"status": "success", "sessions": []}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        md = "### 🤖 SESSIONS D'AGENTS ACTIVES\n\n"
        for t in threads:
            sid = t["sub_sid"]
            # Classification par préfixe
            if sid.startswith("thread_council_"):
                t_type, t_icon = "council", "🏛️"
            elif sid.startswith("thread_supervisor_"):
                t_type, t_icon = "supervisor", "📋"
            elif sid.startswith("thread_"):
                t_type, t_icon = "expert", "🎓"
            else:
                t_type, t_icon = "agent", "🤖"
            ts = time.strftime("%H:%M:%S", time.localtime(t.get("updated_at", 0)))
            md += (
                f"- {t_icon} **SID:** `{sid}` | **Type:** {t_type} | "
                f"**Étapes:** {t['last_step'] + 1} | **Modifié:** {ts}\n"
                f"  > *{t['summary']}*\n\n"
            )

        return wrap_tool_output(
            text=md,
            status={"status": "success", "sessions": [t["sub_sid"] for t in threads]}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    # ==========================================================================
    # 3. FERMETURE DE SESSION
    # ==========================================================================

    async def close_agent_session(
        self,
        sub_sid: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Any = None,
        __metadata__: dict = {},
    ) -> str:
        """Ferme définitivement une session d'agent et purge son historique (irréversible).
        :param sub_sid: Identifiant strict de la session.
        """
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Garde-fou : vérifier que le thread appartient au chat courant
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        sids_in_chat = {t["sub_sid"] for t in threads}

        if sub_sid not in sids_in_chat:
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat ou accès refusé.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state.delete_thread(sub_sid)
        return wrap_tool_output(
            text=f"✅ Session `{sub_sid}` fermée et purgée.",
            status={"status": "success", "sid": sub_sid}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    # ==========================================================================
    # 4. RÉSUMÉ DE SESSION
    # ==========================================================================

    async def summarize_agent_session(
        self,
        sub_sid: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
    ) -> str:
        """Résumé structuré d'une session d'agent.
        :param sub_sid: Identifiant strict de la session.
        """
        events = EchoEvents(__event_emitter__)
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Garde-fou d'appartenance
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        sids_in_chat = {t["sub_sid"] for t in threads}

        if sub_sid not in sids_in_chat:
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        await events.status(f"🧠 Résumé de la session [{sub_sid}]...")
        history = state.get_thread_history(sub_sid)

        if not history:
            return wrap_tool_output(
                text=f"ℹ️ Session `{sub_sid}` vide.",
                status={"status": "success", "sid": sub_sid}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Construction du texte brut de l'historique
        raw = ""
        for step in history:
            role = step.get("role", "user").upper()
            for part in step.get("parts", []):
                if "text" in part and not part.get("thought"):
                    raw += f"{role}: {part['text'][:800]}\n---\n"
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    raw += f"{role} [OUTIL→]: {fc.get('name', '?')} args={str(fc.get('args', {}))[:200]}\n---\n"
                elif "functionResponse" in part:
                    fr = part["functionResponse"]
                    resp = str(fr.get("response", {}))[:300]
                    raw += f"{role} [←OUTIL]: {fr.get('name', '?')} → {resp}\n---\n"

        distill_prompt = (
            "Résume cette session d'agent ECHO en 4 sections :\n"
            "1. **Tâche initiale** — Quelle était la mission ?\n"
            "2. **Outils utilisés** — Lesquels et dans quel ordre ?\n"
            "3. **Résultats clés** — Qu'a-t-on découvert ou produit ?\n"
            "4. **Conclusion** — Réponse finale ou état actuel.\n\n"
            f"### HISTORIQUE\n{raw}"
        )

        data, model_used, _ = await EchoGeminiClient.call_cascade(
            target_model_key="MODEL_FLASH",
            payload={
                "contents": [{"role": "user", "parts": [{"text": distill_prompt}]}],
                "generationConfig": {**get_generation_config("MODEL_DISTILLATION"), "maxOutputTokens": 8192},
            },
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            include_thoughts=False,
        )

        if not data:
            return wrap_tool_output(
                text="❌ Échec de la génération du résumé.",
                status={"status": "error"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        summary = ""
        candidates = data.get("candidates", [])
        if candidates and candidates[0].get("content"):
            for p in candidates[0]["content"].get("parts", []):
                if "text" in p:
                    summary += p["text"]

        await events.status("✅ Résumé généré.", done=True)
        return wrap_tool_output(
            text=f"### Résumé de la session `{sub_sid}`\n\n{summary}",
            status={"status": "success", "sid": sub_sid, "steps": len(history)}
        , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)


# ==============================================================================
# HELPERS PRIVÉS (fonctions module-level pour clarté)
# ==============================================================================

async def _run_agent_loop(
    sid: str,
    chat_id: str,
    user_id: str,
    history: List[dict],
    state: EchoStateManager,
    sub_tools: dict,
    body_tools_specs: list,
    final_system: str,
    current_model_key: str,
    max_calls: int,
    events: EchoEvents,
    valves,
    __user__, __chat_id__, __metadata__,
    __event_emitter__, __event_call__,
) -> str:
    """Boucle agentique principale de l'agent."""
    calls_used = 0

    while True:
        # 1. Construction des function_declarations (source A : sub_tools, B : body_tools_specs)
        fn_decls = _build_function_declarations(sub_tools, body_tools_specs)

        # 1.5. Défense passive : Troncature de l'historique de l'agent
        current_size = estimate_token_size(history) + estimate_token_size(final_system)
        max_tokens = ECHO_MAX_CONTEXT_SIZE
        if current_size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD:
            try:
                await events.toast(f"⚠️ Agent [{sid}] : saturation contextuelle ({int(current_size/max_tokens*100)}%). Troncature silencieuse active.", "warning")
            except AttributeError:
                if __event_emitter__: await __event_emitter__({"type": "toast", "data": {"title": "ECHO Agent", "message": f"⚠️ Agent [{sid}] : saturation contextuelle. Troncature silencieuse active.", "type": "warning"}})
            while current_size > max_tokens * CONTEXT_TRUNCATE_THRESHOLD and len(history) > 3:
                removed = smart_truncate_history(history, 0)
                if not removed:
                    break
                current_size -= removed

        # 2. Payload Gemini complet
        payload: dict = {
            "contents": history,
            "systemInstruction": {"parts": [{"text": final_system}]},
            "generationConfig": get_generation_config(current_model_key)
        }
        if fn_decls:
            payload["tools"] = [{"function_declarations": fn_decls}]
            payload["tool_config"] = {"function_calling_config": {"mode": "AUTO"}}

        # 3. Appel API via call_cascade (clamping + thinkingConfig + cascade descendante)
        try:
            data, model_used, reason = await EchoGeminiClient.call_cascade(
                target_model_key=current_model_key,
                payload=payload,
                user_id=user_id,
                metadata=__metadata__,
                events=events,
                threshold=valves.KEY_SWITCH_THRESHOLD,
                max_retries=valves.MAX_RETRIES,
                timeout=120,
                include_thoughts=False,
            )
        except Exception as e:
            return wrap_tool_output(
                text=f"❌ Erreur API dans l'agent [{sid}] : {str(e)}",
                status={"status": "error", "sid": sid, "message": str(e)}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if not data:
            return wrap_tool_output(
                text=f"❌ Cascade épuisée pour l'agent [{sid}].",
                status={"status": "error", "sid": sid, "message": "cascade exhausted"}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # 4. Extraction des parts BRUTES de la réponse
        # Conservation des parts BRUTES (thoughtSignature Gemini 3.x) :
        # on ne reconstruit PAS les parts — les parts brutes conservent le
        # thoughtSignature Gemini 3.x. Le perdre cause un 400 Bad Request
        # systématique sur TOUS les appels suivant une exécution d'outil.
        candidates = data.get("candidates", [])
        if not candidates or not candidates[0].get("content"):
            return wrap_tool_output(
                text=f"❌ Réponse vide du modèle pour l'agent [{sid}].",
                status={"status": "error", "sid": sid}
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Parts brutes — NE PAS EXTRAIRE de sous-dict, conserver intégralement
        raw_parts = candidates[0]["content"].get("parts", [])

        # Tri des parts par type
        tools_raw = [p for p in raw_parts if "functionCall" in p]
        text_raw  = [p for p in raw_parts if "text" in p and not p.get("thought")]

        real_fc   = [p["functionCall"] for p in tools_raw]
        text      = "".join(p.get("text", "") for p in text_raw).strip()

        step_idx = len(history)

        # Helper inline : extrait la première thoughtSignature présente dans une liste de parts
        def _sig(raw_list):
            return next((p["thoughtSignature"] for p in raw_list if "thoughtSignature" in p), None)


        # =====================================================================
        # CAS 2 : Question de clarification (QUESTION: en dernière ligne)
        # =====================================================================
        if not real_fc and text:
            question_match = re.search(r"QUESTION:\s*(.+?)$", text, re.MULTILINE)
            if question_match:
                question = question_match.group(1).strip()
                progress = text[:text.rfind("QUESTION:")].strip()

                # Parts brutes du modèle (thoughtSignature préservée)
                model_raw = raw_parts if raw_parts else [{"text": text}]
                history.append({"role": "model", "parts": model_raw})
                state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", model_raw, _sig(model_raw))

                return wrap_tool_output(
                    text=f"⚠️ Agent [{sid}] bloqué. IMPLIQUE clarification utilisateur.",
                    status={
                        "status": "pending_question",
                        "sid": sid,
                        "question": question,
                        "progress": progress or "(en cours)",
                        "calls_used": calls_used,
                    }
                , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # =====================================================================
        # CAS 3 : Réponse finale (texte pur, pas de tool call)
        # =====================================================================
        if not real_fc:
            model_raw = raw_parts if raw_parts else [{"text": "(Réponse vide)"}]
            history.append({"role": "model", "parts": model_raw})
            state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", model_raw, _sig(model_raw))

            final_text = text or "(Réponse vide)"
            await events.status(
                f"✅ Agent [{sid}] terminé ({calls_used}/{max_calls} appels).",
                done=True
            )
            return wrap_tool_output(
                text=final_text,
                status={
                    "status": "success",
                    "sid": sid,
                    "calls_used": calls_used,
                    "model_used": model_used,
                }
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # =====================================================================
        # CAS 4 : Circuit breaker — budget dépassé avant exécution
        # =====================================================================
        if calls_used + len(real_fc) > max_calls:
            budget_warning = (
                f"⚠️ Budget de {max_calls} appels épuisé ({calls_used} utilisés). "
                "Produis immédiatement ta meilleure réponse finale avec les informations "
                "disponibles — aucun outil supplémentaire ne peut être appelé."
            )
            history.append({"role": "user", "parts": [{"text": budget_warning}]})

            # Dernier appel sans outils (conclusion forcée)
            forced_text = "(Conclusion forcée — budget épuisé)"
            try:
                forced_payload: dict = {
                    "contents": history,
                    "systemInstruction": {"parts": [{"text": final_system}]},
                    "generationConfig": get_generation_config("MODEL_FLASH"),
                }
                data2, _, _ = await EchoGeminiClient.call_cascade(
                    target_model_key=current_model_key,
                    payload=forced_payload,
                    user_id=user_id,
                    metadata=__metadata__,
                    events=events,
                    threshold=valves.KEY_SWITCH_THRESHOLD,
                    max_retries=valves.MAX_RETRIES,
                    timeout=120,
                    include_thoughts=False,
                )
                if data2:
                    cands2 = data2.get("candidates", [])
                    if cands2 and cands2[0].get("content"):
                        forced_text = "".join(
                            p.get("text", "")
                            for p in cands2[0]["content"].get("parts", [])
                            if "text" in p and not p.get("thought")
                        ) or forced_text
            except Exception:
                pass

            await events.status(
                f"⚠️ Agent [{sid}] — budget épuisé ({max_calls}/{max_calls} appels).",
                done=True
            )
            return wrap_tool_output(
                text=forced_text,
                status={
                    "status": "success",
                    "sid": sid,
                    "warning": "budget_exhausted",
                    "calls_used": calls_used,
                    "model_used": model_used,
                }
            , user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # =====================================================================
        # CAS 5 : Exécution des outils (appels linéaires ou parallèles)
        # =====================================================================
        # Règle Gemini 3.x : le thoughtSignature est dans la 1ère functionCall part
        # (appels parallèles). On conserve les parts BRUTES — jamais reconstruites.
        history.append({"role": "model", "parts": raw_parts})
        state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", raw_parts, _sig(raw_parts))

        # Exécution de chaque outil (potentiellement en parallèle dans les parts)
        response_parts = []
        for fc in real_fc:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})
            # L'ID Gemini identifie chaque appel outil individuel — INDISPENSABLE pour les
            # appels parallèles au même outil (ex: 2× consult_council).
            # Sans ID correspondant dans functionResponse, l'API retourne 400.
            fn_id = fc.get("id")  # Peut être None pour les appels non-parallèles

            callable_fn = (sub_tools.get(fn_name) or {}).get("callable")
            if callable_fn is None:
                # Callable absent : soit outil inconnu, soit specs-only (__tools__ non injecté).
                # Le sous-agent doit utiliser QUESTION: pour déléguer à l'orchestrateur.
                result = {
                    "status": "error",
                    "message": (
                        f"L'outil '{fn_name}' est déclaré mais son exécution n'est pas disponible "
                        "dans ce contexte de sous-agent. "
                        "Utilise QUESTION: pour demander à l'orchestrateur de l'exécuter."
                    )
                }
            else:
                try:
                    # Paramètres infrastructure — passage explicite (binding OWUI non garanti)
                    infra_kwargs = {
                        "__user__": __user__,
                        "__chat_id__": __chat_id__,
                        "__metadata__": __metadata__,
                        "__event_emitter__": __event_emitter__,
                        "__event_call__": __event_call__,
                    }
                    # Filtrage des params infra acceptés par le callable.
                    # Les callables OWUI (depuis _echo_tools_dict) sont des functools.partial
                    # avec __user__, __event_emitter__, etc. déjà frozen — on les exclut
                    # pour éviter "got multiple values for argument".
                    try:
                        sig = inspect.signature(callable_fn)
                        already_frozen = getattr(callable_fn, "keywords", {})
                        accepted_infra = {
                            k: v for k, v in infra_kwargs.items()
                            if k in sig.parameters and k not in already_frozen
                        }
                    except (ValueError, TypeError):
                        accepted_infra = {}  # Partial OWUI — ne pas passer d'infra

                    result = await callable_fn(**fn_args, **accepted_infra)
                except Exception as e:
                    result = {"status": "error", "message": str(e)}

            calls_used += 1
            await events.status(f"🔧 [{sid}] {fn_name} ({calls_used}/{max_calls})")

            # Construction du functionResponse pour l'historique Gemini.
            # ATTENTION : Gemini rejette les payloads trop lourds (HTTP 400).
            # Les outils cognitifs (consult_council) peuvent retourner des milliers
            # de tokens. On tronque et nettoie avant d'insérer dans l'historique.
            _MAX_TOOL_RESPONSE_CHARS = 6000  # Seuil empirique sûr pour Gemini
            if isinstance(result, dict):
                _resp = dict(result)
                # Supprimer les champs volumineux inutiles pour le LLM du sous-agent
                _resp.pop("echo_tool_multiparts", None)
                # Tronquer le texte si trop long
                _text = _resp.get("text", "")
                if isinstance(_text, str) and len(_text) > _MAX_TOOL_RESPONSE_CHARS:
                    _resp["text"] = _text[:_MAX_TOOL_RESPONSE_CHARS] + "\n[…réponse tronquée pour compatibilité API]"
            else:
                _resp = {"result": str(result)[:_MAX_TOOL_RESPONSE_CHARS]}

            # Construction du functionResponse avec son ID Gemini.
            # L'ID est obligatoire pour les appels parallèles au même outil
            # (Gemini l'utilise pour associer chaque réponse à son appel).
            _fr: dict = {"name": fn_name, "response": _resp}
            if fn_id:
                _fr["id"] = fn_id
            response_parts.append({"functionResponse": _fr})

        # Règle API : le message user après une functionCall doit contenir
        # UNIQUEMENT des functionResponse parts — pas de text mélangé.
        # Le budget est géré par l'infrastructure (circuit breaker CAS 4),
        # pas par le LLM sous-agent via l'historique.
        history.append({"role": "user", "parts": response_parts})
        state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx + 1, "user", response_parts)


def _resolve_sub_tools_from_sys_modules(body_tools_specs: list, __user__: Optional[dict], __metadata__: dict = None) -> dict:
    """
    Construit sub_tools en combinant specs OpenAI (body_tools_specs) et callables récupérés
    directement depuis sys.modules.

    OWUI charge tous les modules tool_* dans sys.modules avant d'invoquer le Pipe. Ces modules
    restent accessibles dans le même processus Python. On peut donc instancier chaque classe
    Tools, injecter user_valves depuis __user__['valves'], et récupérer les méthodes async
    comme callables.

    Limitation : les instances créées sont fraîches (Valves defaults). Les user_valves sont
    injectées depuis __user__['valves'] pour minimiser la perte de configuration.
    """
    import sys, asyncio as _asyncio

    # 1. Construire fn_name → callable depuis sys.modules
    fn_to_callable: dict = {}
    user_valves_data: dict = (__user__ or {}).get("valves", {}) or {}

    for mod_name, module in list(sys.modules.items()):
        if not mod_name.startswith("tool_") or module is None:
            continue
        try:
            tools_class = getattr(module, "Tools", None)
            if tools_class is None:
                continue
            instance = tools_class()
            # Injection user_valves depuis __user__['valves'] pour préserver la config utilisateur
            if user_valves_data and hasattr(instance, "user_valves") and hasattr(instance, "UserValves"):
                try:
                    instance.user_valves = instance.UserValves(**{
                        k: v for k, v in user_valves_data.items()
                        if k in instance.UserValves.model_fields
                    })
                except Exception:
                    pass  # Non-bloquant : user_valves defaults si échec
            # Scanner les méthodes async publiques
            for attr_name in dir(instance):
                if attr_name.startswith("_"):
                    continue
                try:
                    attr = getattr(instance, attr_name)
                    if _asyncio.iscoroutinefunction(attr):
                        fn_to_callable[attr_name] = attr
                except Exception:
                    continue
        except Exception:
            continue

    # 2. Construire sub_tools : specs (OpenAI → RAW) + callables
    sub_tools: dict = {}
    for t in (body_tools_specs or []):
        if not isinstance(t, dict) or t.get("type") != "function":
            continue
        f = t.get("function", {})
        fn_name = f.get("name", "")
        if not fn_name or fn_name in DELEGATE_AGENT_BLACKLIST:
            continue
        sub_tools[fn_name] = {
            "spec": {
                "name": fn_name,
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {"type": "object", "properties": {}}),
            },
            "callable": fn_to_callable.get(fn_name),  # None si non trouvé dans sys.modules
        }

    return sub_tools


def _build_function_declarations(sub_tools: dict, body_tools_specs: list = None) -> List[dict]:
    """
    Construit les function_declarations Gemini depuis 2 sources, par ordre de priorité :

    Source A — sub_tools (dict OWUI) : {fn_name: {callable, spec:{name, description, parameters}}}
      → Source primaire quand __tools__ est injecté par OWUI.
      → Contient les callables → exécution directe possible.

    Source B — body_tools_specs (list OpenAI) : [{type:'function', function:{name,description,parameters}}]
      → Fallback quand sub_tools est vide (__tools__ non injecté dans le contexte Pipe).
      → Specs seules, pas de callable → exécution via QUESTION: si le sous-agent en a besoin.

    Le filtrage DELEGATE_AGENT_BLACKLIST est appliqué dans les deux cas.
    """
    decls = []

    if sub_tools:
        # Source A : format RAW OWUI
        for name, tool_data in sub_tools.items():
            if not isinstance(tool_data, dict):
                continue
            spec = tool_data.get("spec") or tool_data.get("function") or {}
            decl = {
                "name": name,
                "description": spec.get("description", ""),
                "parameters": spec.get("parameters", {"type": "object", "properties": {}}),
            }
            decls.append(decl)

    elif body_tools_specs:
        # Source B : format OpenAI (fallback)
        for t in (body_tools_specs or []):
            if not isinstance(t, dict) or t.get("type") != "function":
                continue
            f = t.get("function", {})
            fn_name = f.get("name", "")
            if not fn_name or fn_name in DELEGATE_AGENT_BLACKLIST:
                continue
            decl = {
                "name": fn_name,
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {"type": "object", "properties": {}}),
            }
            decls.append(decl)

    return decls




async def _distill_main_context(
    state: EchoStateManager,
    chat_id: str,
    user_id: str,
) -> str:
    """Distille les N derniers messages de la branche active du chat principal (via shadows)."""
    try:
        rows = state.get_active_branch_shadows(chat_id, limit=10)
        if not rows:
            return ""
        raw = ""
        for r in rows:
            text = "".join(p.get("text", "") for p in r.get("parts", []) if "text" in p)
            if text:
                raw += f"{r['role'].upper()}: {text[:500]}\n---\n"
        if not raw:
            return ""
        result = await EchoGeminiClient.call_distillation(
            f"Résume factuellement cet historique en 5 points clés pour un agent :\n\n{raw}",
            {"id": user_id},
            {},
        )
        return result or ""
    except Exception:
        return ""
