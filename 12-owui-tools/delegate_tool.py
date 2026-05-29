"""
title: ECHO Delegate Sub-Agent
author: ECHO Framework
version: 1.3
description: 1.0: Sous-agent ECHO stateful avec accès aux outils et montée cognitive
             identique au Pipe (new_cognitive_level fantôme en mode AUTO/AUTO_PRO).
             Budget d'appels de fonctions configurable par UserValve (par invocation).
             Stateful via EchoStateManager (cognitive_threads). Remplace delegate_reasoning
             (cognitive_agents.py ≤ 5.13, supprimé en 5.14).
             4 functions calls :
               - delegate_to_subagent    : délégation principale
               - list_subagent_sessions  : liste des threads actifs du chat courant
               - close_subagent_session  : fermeture définitive d'un thread
               - summarize_subagent_session : résumé distillé (≤ 8192 tokens)
             1.1: Fix critique — préservation des thoughtSignatures Gemini 3.x.
             Les parts du modèle sont désormais conservées BRUTES (pattern _iterative_loop)
             au lieu d'être reconstruites, ce qui perdait le champ thoughtSignature
             présent dans les functionCall parts et causait des 400 Bad Request systématiques
             sur tous les appels post-tool-execution.
             Fix secondaire : suppression du budget_info (text) du message user contenant
             les functionResponse — mélange interdit par l'API Gemini.
             1.2: Fix injection outils sous-agent.
             __tools__ est None dans le contexte Pipe (OWUI ne l'injecte pas dans ce path).
             Solution dual-source : specs via __metadata__['_echo_body_tools'] (injecté par Pipe,
             format OpenAI, fiable), callables via __tools__ quand disponible. Diagnostic log
             systématique pour confirmer l'état de __tools__ au runtime. Fallback gracieux vers
             QUESTION: si callable absent (specs seules).
             1.3: Résolution complète des callables via sys.modules.
             OWUI n'injecte __tools__ ni dans les outils ni dans le Pipe (kwargs=[]). Il
             charge cependant tous les modules tool_* dans sys.modules avant d'invoquer le Pipe.
             La fonction _resolve_sub_tools_from_sys_modules() scanne sys.modules, instancie
             chaque classe Tools avec user_valves injectées depuis __user__, et récupère les
             méthodes async. Résultat : sub_tools peuplé avec specs (OpenAI→RAW) + callables
             réels. Exécution directe possible sans QUESTION: ni dépendance OWUI interne.
"""

import sys
import orjson as json
import asyncio
import uuid
import re
import time
import inspect
from pydantic import BaseModel, Field
from typing import Optional, Any, List

sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    wrap_tool_output, wrap_cascade_output,
    EchoEvents, EchoGeminiClient, EchoStateManager,
    resolve_model_policy,
)
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO, MODEL_ROUTING, MODEL_HIERARCHY,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT, MAX_TOKENS_DEFAULT,
    TEMP_DISTILLATION, TOP_P_DISTILLATION,
    DELEGATE_SUBAGENT_BLACKLIST, DELEGATE_SYSTEM_APPENDIX,
)

# Identifiant de rôle pour les threads delegate dans cognitive_threads
_DELEGATE_ROLE_ID = "delegate"

# Description de new_cognitive_level — identique au Pipe (pipe_engine.py)
_NCL_DESCRIPTION = (
    "Ajuste le niveau cognitif d'ECHO. Tu DOIS appeler cet outil AVANT "
    "toute tâche non-triviale pour garantir la qualité de la réponse.\n\n"
    "## Règles de sélection\n"
    "- **MODEL_LITE** (Réflexe — défaut) : Salutations, remerciements, extractions simples, "
    "traduction courte, questions factuelles basiques.\n"
    "- **MODEL_FLASH** (Exécution — moteur agentique) : OBLIGATOIRE pour toute "
    "tâche non-triviale. Recherche web, écriture de code, analyse sémantique, "
    "synthèse de documents, orchestration d'outils, planification, réponses "
    "structurées, raisonnement multi-étapes.\n"
    "  → Escalader vers FLASH SYSTÉMATIQUEMENT dès que la tâche "
    "dépasse le simple réflexe. Ne pas rester en LITE par inertie.\n"
    "- **MODEL_PRO** (Expertise) : Pour les tâches de haute complexité où "
    "FLASH a échoué ou serait insuffisant. Architectures systèmes complexes, "
    "refactoring multi-fichiers avec contraintes imbriquées, logique formelle.\n"
    "  → Justifier le besoin de PRO. Redescendre vers FLASH ou LITE "
    "une fois la tâche complexe accomplie.\n\n"
    "## Corrélation contextuelle\n"
    "La saturation contextuelle est atténuée par le RAG éphémère. Vigilance "
    "accrue à haute charge (> 50%) — préférer alors FLASH ou PRO."
)


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
        MAX_SUBAGENT_FUNCTION_CALLS: int = Field(
            default=25, ge=5, le=50,
            description=(
                "Budget d'appels de fonctions par invocation de sous-agent. "
                "Compte les décisions d'appel du sous-agent uniquement — "
                "les opérations internes des outils appelés (ex: itérations d'un expert) "
                "ne sont pas comptées."
            )
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # 1. DÉLÉGATION PRINCIPALE
    # ==========================================================================

    async def delegate_to_subagent(
        self,
        task: str,
        system_prompt: str,
        sub_sid: Optional[str] = None,
        with_context_distillate: bool = False,
        __tools__: Optional[list] = None,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Délègue une tâche à un sous-agent ECHO autonome avec accès aux outils.

        Le sous-agent est stateful : son historique est persisté via sub_sid.
        Rappeler avec le même sub_sid pour reprendre ou répondre à une QUESTION.

        Le sous-agent hérite de la politique cognitive du Pipe (AUTO/AUTO_PRO/MODEL_*).
        En mode AUTO/AUTO_PRO, il démarre en MODEL_LITE et peut escalader via
        new_cognitive_level — identiquement au Pipe principal.

        Retours possibles :
          status "success"          → "result" contient la réponse finale
          status "pending_question" → "question" du sous-agent, relancer avec sub_sid + réponse dans task
          status "error"            → "message" d'erreur technique

        :param task: Mission initiale, ou réponse à une QUESTION (si reprise via sub_sid).
        :param system_prompt: Persona et règles du sous-agent (rédigés par l'orchestrateur).
                              Le framework ajoute automatiquement le cadre d'exécution (budget, SESSION_ID).
        :param sub_sid: None = nouveau thread (SID retourné dans status.sid).
                        Fourni = reprise du thread existant.
        :param with_context_distillate: Si True, injecter un résumé de la branche active du chat
                                         principal dans le system_prompt initial. Désactivé par défaut.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = (__user__ or {}).get("id", "system")
        chat_id = __chat_id__ or ""
        max_calls = self.user_valves.MAX_SUBAGENT_FUNCTION_CALLS

        if not user_id or user_id == "system":
            return wrap_tool_output(
                text="❌ Contexte utilisateur manquant.",
                status={"status": "error", "message": "user_id requis"}
            )

        # 1. Résolution du sub_sid
        sid = sub_sid if sub_sid else f"dlg_{uuid.uuid4().hex[:10]}"
        is_resume = sub_sid is not None

        # 2. Politique cognitive (identique au Pipe via resolve_model_policy)
        policy_mode, policy_ceiling = resolve_model_policy(__metadata__, user_id=user_id)
        is_auto = policy_mode in ("auto", "auto_pro")

        # Modèle de départ selon la politique
        current_model_key = "MODEL_LITE" if is_auto else policy_ceiling

        # -----------------------------------------------------------------------
        # 3. Résolution des outils disponibles pour le sous-agent
        # -----------------------------------------------------------------------
        # Contrainte architecturale OWUI :
        #   OWUI injecte __tools__ (le dict des outils avec callables) uniquement
        #   dans le Pipe principal. Les tool callables (comme delegate_to_subagent)
        #   ne reçoivent PAS __tools__ et ne peuvent PAS modifier __metadata__ de
        #   façon visible par les autres outils (chaque outil reçoit un __metadata__
        #   indépendant).
        #
        # Solution (ECHO v5.166.2) :
        #   Le Pipe stocke __tools__ dans _TOOLS_CACHE[chat_id] (module-level).
        #   delegate_tool lit ce cache via sys.modules["function_pipe_engine"].
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
            # (ex: delegate_to_subagent lui-même pour éviter les récursions infinies)
            sub_tools = {
                k: v for k, v in _tools_dict.items()
                if k not in DELEGATE_SUBAGENT_BLACKLIST
            }
        elif _body_tools_specs:
            # Source 3 : reconstruction depuis sys.modules (valves par défaut)
            sub_tools = _resolve_sub_tools_from_sys_modules(_body_tools_specs, __user__)

        _callable_count = sum(1 for v in sub_tools.values() if v.get("callable") is not None)
        _log.debug(
            "delegate [%s]: cache_keys=%d, _tools_dict=%d, sub_tools=%d, callables=%d",
            sid, len(_tools_cache), len(_tools_dict), len(sub_tools), _callable_count,
        )

        # 4. System prompt final (appendice cadre d'exécution)
        final_system = system_prompt + DELEGATE_SYSTEM_APPENDIX.format(
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

        await events.status(f"🤖 Sous-agent [{sid}] démarré ({current_model_key})...")

        # 8. Boucle agentique
        return await _run_subagent_loop(
            sid=sid,
            chat_id=chat_id,
            user_id=user_id,
            history=history,
            state=state,
            sub_tools=sub_tools,
            body_tools_specs=_body_tools_specs,
            final_system=final_system,
            current_model_key=current_model_key,
            policy_mode=policy_mode,
            policy_ceiling=policy_ceiling,
            is_auto=is_auto,
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

    async def list_subagent_sessions(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        Liste les sous-sessions delegate actives pour ce chat.
        Permet à l'orchestrateur de retrouver un sub_sid pour reprendre une tâche.

        :return: Markdown listant sub_sid, nombre d'étapes et résumé de chaque session.
        """
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)

        # Filtrer uniquement les threads delegate (préfixe dlg_)
        delegate_threads = [t for t in threads if t["sub_sid"].startswith("dlg_")]

        if not delegate_threads:
            return wrap_tool_output(
                text="ℹ️ Aucune sous-session delegate active pour cette conversation.",
                status={"status": "success", "sessions": []}
            )

        md = "### 🤖 SOUS-SESSIONS DELEGATE ACTIVES\n\n"
        for t in delegate_threads:
            ts = time.strftime("%H:%M:%S", time.localtime(t.get("updated_at", 0)))
            md += (
                f"- **SID:** `{t['sub_sid']}` | **Étapes:** {t['last_step'] + 1} | "
                f"**Modifié:** {ts}\n"
                f"  > *{t['summary']}*\n\n"
            )

        return wrap_tool_output(
            text=md,
            status={"status": "success", "sessions": [t["sub_sid"] for t in delegate_threads]}
        )

    # ==========================================================================
    # 3. FERMETURE DE SESSION
    # ==========================================================================

    async def close_subagent_session(
        self,
        sub_sid: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        Ferme définitivement une sous-session delegate et purge son historique (irréversible).
        Utiliser list_subagent_sessions pour retrouver le sub_sid.

        :param sub_sid: ID de la sous-session à fermer (format dlg_*).
        """
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        if not sub_sid.startswith("dlg_"):
            return wrap_tool_output(
                text=f"❌ `{sub_sid}` n'est pas une session delegate (préfixe `dlg_` requis).",
                status={"status": "error"}
            )

        # Garde-fou : vérifier que le thread appartient au chat courant
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        sids_in_chat = {t["sub_sid"] for t in threads}

        if sub_sid not in sids_in_chat:
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat ou accès refusé.",
                status={"status": "error"}
            )

        state.delete_thread(sub_sid)
        return wrap_tool_output(
            text=f"✅ Sous-session `{sub_sid}` fermée et purgée.",
            status={"status": "success", "sid": sub_sid}
        )

    # ==========================================================================
    # 4. RÉSUMÉ DE SESSION
    # ==========================================================================

    async def summarize_subagent_session(
        self,
        sub_sid: str,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        Génère un résumé structuré d'une sous-session delegate (≤ 8192 tokens).
        Limité aux sessions du chat courant.

        Structure du résumé : tâche initiale, outils utilisés, résultats clés, conclusion.

        :param sub_sid: ID de la sous-session à résumer (format dlg_*).
        """
        events = EchoEvents(__event_emitter__)
        user_id = (__user__ or {}).get("id", "system")
        if not __chat_id__:
            return wrap_tool_output(text="❌ Aucun chat_id détecté.", status={"status": "error"})

        # Garde-fou d'appartenance
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        sids_in_chat = {t["sub_sid"] for t in threads}

        if sub_sid not in sids_in_chat:
            return wrap_tool_output(
                text=f"❌ Session `{sub_sid}` introuvable dans ce chat.",
                status={"status": "error"}
            )

        await events.status(f"🧠 Résumé de la session [{sub_sid}]...")
        history = state.get_thread_history(sub_sid)

        if not history:
            return wrap_tool_output(
                text=f"ℹ️ Session `{sub_sid}` vide.",
                status={"status": "success", "sid": sub_sid}
            )

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
            "Résume cette sous-session de sous-agent ECHO en 4 sections :\n"
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
                "generationConfig": {
                    "temperature": TEMP_DISTILLATION,
                    "topP": TOP_P_DISTILLATION,
                    "maxOutputTokens": 8192,
                },
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
            )

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
        )


# ==============================================================================
# HELPERS PRIVÉS (fonctions module-level pour clarté)
# ==============================================================================

async def _run_subagent_loop(
    sid: str,
    chat_id: str,
    user_id: str,
    history: List[dict],
    state: EchoStateManager,
    sub_tools: dict,
    body_tools_specs: list,
    final_system: str,
    current_model_key: str,
    policy_mode: str,
    policy_ceiling: str,
    is_auto: bool,
    max_calls: int,
    events: EchoEvents,
    valves,
    __user__, __chat_id__, __metadata__,
    __event_emitter__, __event_call__,
) -> str:
    """Boucle agentique principale du sous-agent."""
    calls_used = 0

    while True:
        # 1. Construction des function_declarations (source A : sub_tools, B : body_tools_specs)
        fn_decls = _build_function_declarations(sub_tools, body_tools_specs)

        # Injection new_cognitive_level fantôme si mode AUTO/AUTO_PRO
        if is_auto:
            ncl_tool = _build_escalation_tool(current_model_key, policy_mode)
            if ncl_tool:
                fn_decls.append(ncl_tool)

        # 2. Payload Gemini complet
        payload: dict = {
            "contents": history,
            "systemInstruction": {"parts": [{"text": final_system}]},
            "generationConfig": {
                "temperature": TEMP_DEFAULT,
                "topP": TOP_P_DEFAULT,
                "maxOutputTokens": MAX_TOKENS_DEFAULT,
            },
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
                text=f"❌ Erreur API dans le sous-agent [{sid}] : {str(e)}",
                status={"status": "error", "sid": sid, "message": str(e)}
            )

        if not data:
            return wrap_tool_output(
                text=f"❌ Cascade épuisée pour le sous-agent [{sid}].",
                status={"status": "error", "sid": sid, "message": "cascade exhausted"}
            )

        # 4. Extraction des parts BRUTES de la réponse
        # Pattern identique à _iterative_loop (cognitive_agents.py) :
        # on ne reconstruit PAS les parts — les parts brutes conservent le
        # thoughtSignature Gemini 3.x. Le perdre cause un 400 Bad Request
        # systématique sur TOUS les appels suivant une exécution d'outil.
        candidates = data.get("candidates", [])
        if not candidates or not candidates[0].get("content"):
            return wrap_tool_output(
                text=f"❌ Réponse vide du modèle pour le sous-agent [{sid}].",
                status={"status": "error", "sid": sid}
            )

        # Parts brutes — NE PAS EXTRAIRE de sous-dict, conserver intégralement
        raw_parts = candidates[0]["content"].get("parts", [])

        # Tri des parts par type (inspection du contenu, pas extraction)
        ncl_raw   = [p for p in raw_parts if "functionCall" in p
                      and p["functionCall"].get("name") == "new_cognitive_level"]
        tools_raw = [p for p in raw_parts if "functionCall" in p
                      and p["functionCall"].get("name") != "new_cognitive_level"]
        text_raw  = [p for p in raw_parts if "text" in p and not p.get("thought")]

        # Dicts pour la logique métier (extraits une seule fois depuis les raw parts)
        ncl_calls = [p["functionCall"] for p in ncl_raw]
        real_fc   = [p["functionCall"] for p in tools_raw]
        text      = "".join(p.get("text", "") for p in text_raw).strip()

        step_idx = len(history)

        # Helper inline : extrait la première thoughtSignature présente dans une liste de parts
        def _sig(raw_list):
            return next((p["thoughtSignature"] for p in raw_list if "thoughtSignature" in p), None)

        # =====================================================================
        # CAS 1 : Escalade cognitive (new_cognitive_level)
        # =====================================================================
        if ncl_calls and not real_fc:
            ncl_args = ncl_calls[0].get("args", {})
            target_req = ncl_args.get("niveau_requis", "")

            # Parts BRUTES du modèle — thoughtSignature préservée
            history.append({"role": "model", "parts": ncl_raw})
            state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", ncl_raw, _sig(ncl_raw))

            # Validation du niveau demandé
            if target_req not in MODEL_HIERARCHY:
                resp_parts = [{"functionResponse": {
                    "name": "new_cognitive_level",
                    "response": {
                        "status": "error",
                        "message": f"Niveau '{target_req}' inconnu. Choisissez parmi MODEL_LITE, MODEL_FLASH, MODEL_PRO."
                    }
                }}]
                history.append({"role": "user", "parts": resp_parts})
                state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx + 1, "user", resp_parts)
                continue

            # Vérification des droits (policy AUTO ≠ AUTO_PRO)
            if policy_mode == "auto" and target_req == "MODEL_PRO":
                resp_parts = [{"functionResponse": {
                    "name": "new_cognitive_level",
                    "response": {
                        "status": "error",
                        "message": "Transfert vers MODEL_PRO refusé (policy AUTO). Traitez avec MODEL_FLASH."
                    }
                }}]
                history.append({"role": "user", "parts": resp_parts})
                state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx + 1, "user", resp_parts)
                continue  # Reboucle sans changer de modèle

            # Mutation du modèle courant (montée ou descente)
            current_model_key = target_req
            await events.status(f"🚀 Sous-agent [{sid}] → {target_req}")

            resp_parts = [{"functionResponse": {
                "name": "new_cognitive_level",
                "response": {"status": "ok", "model_now": target_req}
            }}]
            history.append({"role": "user", "parts": resp_parts})
            state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx + 1, "user", resp_parts)
            continue  # calls_used INCHANGÉ

        # =====================================================================
        # CAS 2 : Question de clarification (QUESTION: en dernière ligne)
        # =====================================================================
        if not real_fc and text:
            question_match = re.search(r"QUESTION:\s*(.+?)$", text, re.MULTILINE)
            if question_match:
                question = question_match.group(1).strip()
                progress = text[:text.rfind("QUESTION:")].strip()

                # Parts brutes du modèle (thoughtSignature préservée)
                model_raw = text_raw if text_raw else [{"text": text}]
                history.append({"role": "model", "parts": model_raw})
                state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", model_raw, _sig(model_raw))

                return wrap_tool_output(
                    text=f"Le sous-agent [{sid}] a besoin d'une clarification avant de continuer.",
                    status={
                        "status": "pending_question",
                        "sid": sid,
                        "question": question,
                        "progress": progress or "(en cours)",
                        "calls_used": calls_used,
                    }
                )

        # =====================================================================
        # CAS 3 : Réponse finale (texte pur, pas de tool call)
        # =====================================================================
        if not real_fc:
            model_raw = text_raw if text_raw else [{"text": "(Réponse vide)"}]
            history.append({"role": "model", "parts": model_raw})
            state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", model_raw, _sig(model_raw))

            final_text = text or "(Réponse vide)"
            await events.status(
                f"✅ Sous-agent [{sid}] terminé ({calls_used}/{max_calls} appels).",
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
            )

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
                    "generationConfig": {
                        "temperature": TEMP_DEFAULT,
                        "topP": TOP_P_DEFAULT,
                        "maxOutputTokens": MAX_TOKENS_DEFAULT,
                    },
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
                f"⚠️ Sous-agent [{sid}] — budget épuisé ({max_calls}/{max_calls} appels).",
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
            )

        # =====================================================================
        # CAS 5 : Exécution des outils (appels linéaires ou parallèles)
        # =====================================================================
        # Règle Gemini 3.x : le thoughtSignature est dans la 1ère functionCall part
        # (appels parallèles). On conserve les parts BRUTES — jamais reconstruites.
        history.append({"role": "model", "parts": tools_raw})
        state.save_thread_step(sid, chat_id, _DELEGATE_ROLE_ID, step_idx, "model", tools_raw, _sig(tools_raw))

        # Exécution de chaque outil (potentiellement en parallèle dans les parts)
        response_parts = []
        for fc in real_fc:
            fn_name = fc.get("name", "")
            fn_args = fc.get("args", {})
            # L'ID Gemini identifie chaque appel outil individuel — INDISPENSABLE pour les
            # appels parallèles au même outil (ex: 2× consult_expert_consultant).
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


def _resolve_sub_tools_from_sys_modules(body_tools_specs: list, __user__: Optional[dict]) -> dict:
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
        if not fn_name or fn_name in DELEGATE_SUBAGENT_BLACKLIST:
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

    Le filtrage DELEGATE_SUBAGENT_BLACKLIST est appliqué dans les deux cas.
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
            if not fn_name or fn_name in DELEGATE_SUBAGENT_BLACKLIST:
                continue
            decl = {
                "name": fn_name,
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {"type": "object", "properties": {}}),
            }
            decls.append(decl)

    return decls


def _build_escalation_tool(current_model_key: str, policy_mode: str) -> Optional[dict]:
    """
    Construit le spec new_cognitive_level fantôme (identique au Pipe, pipe_engine.py).
    Menu bidirectionnel : le sous-agent peut monter ET descendre.
    Retourne None si aucune escalade possible (ex: MODEL_LITE en mode 'auto' → seul FLASH).
    """
    menu = []
    if current_model_key == "MODEL_LITE":
        menu = ["MODEL_FLASH"]
        if policy_mode == "auto_pro":
            menu.append("MODEL_PRO")
    elif current_model_key == "MODEL_FLASH":
        menu = ["MODEL_LITE"]
        if policy_mode == "auto_pro":
            menu.append("MODEL_PRO")
    elif current_model_key == "MODEL_PRO":
        menu = ["MODEL_LITE", "MODEL_FLASH"]

    if not menu:
        return None

    return {
        "name": "new_cognitive_level",
        "description": _NCL_DESCRIPTION,
        "parameters": {
            "type": "object",
            "properties": {
                "niveau_requis": {
                    "type": "string",
                    "enum": menu,
                    "description": "Le niveau cognitif cible."
                },
                "plan_de_transfert": {
                    "type": "string",
                    "description": "Plan Markdown structuré (Objectif, Analyse, Stratégie, Contraintes)."
                },
                "raison": {
                    "type": "string",
                    "description": "Justification du changement de niveau."
                }
            },
            "required": ["niveau_requis", "plan_de_transfert"]
        }
    }


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
            f"Résume factuellement cet historique en 5 points clés pour un sous-agent :\n\n{raw}",
            {"id": user_id},
            {},
        )
        return result or ""
    except Exception:
        return ""
