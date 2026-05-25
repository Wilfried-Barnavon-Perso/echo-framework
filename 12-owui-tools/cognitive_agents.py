"""
title: ECHO Cognitive Agents
author: ECHO Framework
version: 5.8
description: 5.7: Résolution du conflit de nom get_all_skills (shadowing).
             5.8: Centralisation des niveaux de réflexion (THINKING_LEVEL_*) — suppression
             valves FLASH_THINKING et PRO_THINKING. Remplacement par constantes echo_constants.
"""

import sys
import orjson as json
import asyncio
import re
import uuid
import time
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, EchoEvents, EchoGeminiClient, EchoStateManager
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO, MODEL_ROUTING,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT, TEMP_DISTILLATION, TOP_P_DISTILLATION,
    MODEL_DISTILLATION,
    THINKING_LEVEL_PRO, THINKING_LEVEL_FLASH, THINKING_LEVEL_LITE
)
from echo_skills import get_all_skills, get_skill_content, save_skill

class Tools:
    class Valves(BaseModel):
        # Les niveaux de réflexion sont des constantes ECHO (echo_constants.py v4.8) — plus de valves.
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD, description="Nombre d'erreurs 429/503 avant de basculer sur la clé de secours.")
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES, description="Nombre de tentatives maximum.")
        COGNITIVE_TIMEOUT: int = Field(default=120, description="Délai d'attente maximum (secondes) pour la délégation cognitive.")
        DEBUG_COUNCIL: bool = Field(default=False, description="Si activé, conserve les traces des réflexions internes et affiche plus de détails.")

    class UserValves(BaseModel):
        ITERATION_DEFAULT: int = Field(default=3, description="Nombre de tours par défaut pour une réflexion itérative.")
        ITERATION_MAX: int = Field(default=10, description="Limite haute du nombre de tours.")
        DISTILLATION_DEPTH: int = Field(default=20, description="Nombre de messages de l'historique à distiller par défaut.")

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    async def list_sub_chats(
        self,
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None
    ) -> str:
        """
        Liste les fils de discussion (sous-chats) cognitifs actifs pour ce chat.
        Permet à l'orchestrateur de choisir quel fil de réflexion reprendre.
        
        :return: Liste des sous-chats avec leur sub_sid, rôle et résumé.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")
            
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        threads = state.list_threads(__chat_id__)
        
        if not threads:
            return wrap_tool_output(text="ℹ️ Aucun sous-chat actif pour cette conversation.")
            
        res = "### SOUS-CHATS COGNITIFS ACTIFS\n"
        for t in threads:
            res += f"- **ID:** `{t['sub_sid']}` | **Rôle:** {t['role_id']} | **Étapes:** {t['last_step']}\n"
            res += f"  > *Dernier échange:* {t['summary']}\n"
            
        return wrap_tool_output(text=res)

    async def forge_skill(
        self,
        skill_id: str,
        name: str,
        description: str,
        instructions: str,
        __user__: Optional[dict] = None
    ) -> str:
        """
        Créez ou modifiez une expertise cognitive (SKILL) au format SKILL.md.
        Pour un résultat optimal, concevez un profil riche : définissez un ton (ex: incisif), une méthodologie (ex: premiers principes), et des contraintes de sortie strictes. 
        Plus le Skill est détaillé, plus l'expert sera pertinent dans sa réflexion.
        
        :param skill_id: Identifiant unique du skill (ex: 'lead_dev_rust', 'expert_cyber').
        :param name: Nom lisible du rôle (ex: 'Lead Developer Rust').
        :param description: Brève description de l'expertise pour la découverte.
        :param instructions: Instructions système détaillées définissant le comportement du rôle.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        success = save_skill(user_id, skill_id, name, description, instructions)
        
        if success:
            return wrap_tool_output(text=f"✅ Skill '{name}' ({skill_id}) forgé avec succès.")
        return wrap_tool_output(text=f"❌ Échec de la forge du skill '{skill_id}'.")

    async def list_skills(
        self,
        __user__: Optional[dict] = None
    ) -> str:
        """
        Consultez la liste des expertises (SKILLS) disponibles pour le Conseil.
        Permet de découvrir les rôles déjà forgés et leurs descriptions.
        """
        user_id = __user__.get("id", "system") if __user__ else "system"
        skills = get_all_skills(user_id)
        
        if not skills:
            return wrap_tool_output(text="ℹ️ Aucune expertise (Skill) n'est actuellement forgée.")
            
        res = "### EXPERTISES DISPONIBLES (CONSEIL)\n"
        for s in skills:
            res += f"- **ID:** `{s['id']}` | **Nom:** {s['name']}\n"
            res += f"  > *Description:* {s['description']}\n"
            
        return wrap_tool_output(text=res)

    async def delegate_reasoning(
        self,
        context: str,
        prompt: str,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"],
        system_instruction: Optional[str] = None,
        __user__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Délégation cognitive sans état (stateless). Chaque appel est indépendant et ne conserve aucune mémoire des échanges précédents.
        Le paramètre 'context' est obligatoire pour injecter sémantiquement les faits, la mémoire ou les données nécessaires à la réflexion.
        
        Utilisez MODEL_LITE pour la distillation rapide et l'extraction de données.
        Utilisez MODEL_FLASH pour les tâches intermédiaires, le formatage ou la logique standard.
        Utilisez MODEL_PRO pour l'architecture complexe, le debug profond ou la planification stratégique.
        
        :param context: Contexte sémantique (Markdown) de référence pour la tâche.
        :param prompt: L'instruction ou la tâche spécifique à exécuter.
        :param target_model: Le modèle à utiliser (MODEL_LITE, MODEL_FLASH, MODEL_PRO).
        :param system_instruction: (Optionnel) Comportement strict ou format de sortie attendu.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        
        # Résolution du modèle via le Registre Souverain
        actual_model = MODEL_ROUTING.get(target_model, MODEL_PRO)
        
        # Résolution du niveau de réflexion via constantes ECHO
        thinking_level = THINKING_LEVEL_PRO
        if target_model == "MODEL_FLASH":
            thinking_level = THINKING_LEVEL_FLASH
        elif target_model == "MODEL_LITE":
            thinking_level = THINKING_LEVEL_LITE

        await events.status(f"🧠 Délégation Cognitive ({target_model}) pour {user_id}...")
        
        # Construction du prompt sémantique
        combined_prompt = f"### CONTEXTE\n{context}\n\n### TÂCHE\n{prompt}"

        # Appel au client agnostique (Purifié)
        res_json = await EchoGeminiClient.call(
            target_model=actual_model,
            payload={
                "contents": [{"role": "user", "parts": [{"text": combined_prompt}]}],
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                    "maxOutputTokens": 16000,
                    "thinkingConfig": {"includeThoughts": False, "thinkingLevel": thinking_level.lower()}
                },
                "systemInstruction": {"parts": [{"text": system_instruction}]} if system_instruction else None
            },
            user_id=user_id,
            events=events,
            threshold=self.valves.KEY_SWITCH_THRESHOLD,
            max_retries=self.valves.MAX_RETRIES,
            timeout=self.valves.COGNITIVE_TIMEOUT
        )
        
        # Extraction normalisée (le client déballe déjà les enveloppes Code Assist)
        candidates = res_json.get("candidates", [])
        if candidates and candidates[0].get("content"):
            full_text = "".join([p.get("text", "") for p in candidates[0]["content"].get("parts", [])])
            await events.status(f"Délégation terminée ({target_model}).", done=True)
            return wrap_tool_output(text=full_text)
        
        return wrap_tool_output(text="❌ Erreur: Réponse Gemini vide.")

    async def consult_council(
        self,
        role_name: str,
        prompt: str,
        sub_sid: Optional[str] = None,
        target_model: Literal["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"] = "MODEL_PRO",
        history_depth: Optional[int] = Field(default=None, description="Nombre de messages de la branche active de la conversation à fournir à l'expert. Surcharge la UserValve par défaut si renseigné."),
        distillation_focus: Optional[str] = Field(default=None, description="Sujet, question ou point d'attention absolu sur lequel l'expert doit se concentrer lors de sa lecture."),
        __user__: Optional[dict] = None,
        __chat_id__: Optional[str] = None,
        __event_emitter__: Any = None,
        __event_call__: Any = None
    ) -> str:
        """
        Consultez un expert spécifique (Skill) du Conseil. Gère la continuité via sub_sid.
        Si sub_sid est fourni, reprend la discussion là où elle s'était arrêtée pour ce rôle.
        Sinon, crée un nouveau fil de réflexion.
        
        :param role_name: Identifiant du Skill/Rôle à consulter (ex: 'ceo', 'lead_dev').
        :param prompt: Votre question ou instruction pour cet expert.
        :param sub_sid: (Optionnel) ID du sous-chat à reprendre.
        :param target_model: Modèle cible pour cet expert (MODEL_PRO recommandé).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        if not __chat_id__:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        # 1. Résolution de la sous-session
        sid = sub_sid if sub_sid else f"thread_{role_name}_{uuid.uuid4().hex[:8]}"
        state = EchoStateManager(user_id=user_id, chat_id=__chat_id__)
        
        # 2. Chargement du Skill (Prompt Système)
        skill_content = get_skill_content(user_id, role_name)
        if not skill_content:
            await events.status(f"⚠️ Skill '{role_name}' non trouvé. Tentative de création par défaut...")
            return wrap_tool_output(text=f"❌ Erreur: Le rôle '{role_name}' n'existe pas. Utilisez forge_skill d'abord.")

        await events.status(f"🧠 Consultation du {role_name} (SID: {sid})...")

        # 3. Distillation du contexte (branche active uniquement)
        depth = history_depth if history_depth is not None else self.user_valves.DISTILLATION_DEPTH
        distillate = await self._distill_context(state, __chat_id__, role_name, user_id, events, depth, distillation_focus)
        
        # 4. Boucle de réflexion itérative
        final_answer = await self._iterative_loop(
            state=state,
            sub_sid=sid,
            chat_id=__chat_id__,
            role_id=role_name,
            system_instruction=skill_content,
            initial_prompt=prompt,
            context_distillate=distillate,
            target_model=target_model,
            user_id=user_id,
            events=events
        )

        return wrap_tool_output(text=f"### RÉPONSE DU {role_name.upper()} (ID: `{sid}`)\n\n{final_answer}")

    async def _distill_context(self, state: EchoStateManager, chat_id: str, role: str, user_id: str, events: EchoEvents, depth: int, focus: Optional[str] = None) -> str:
        """Méthode interne pour préparer un résumé focalisé de la branche active du chat."""
        try:
            # Récupération généalogique via Suture Index (Branche active uniquement)
            rows = state.get_active_branch_shadows(chat_id, limit=depth)
            
            if not rows: return "Aucun historique disponible sur cette branche."
            
            raw_history = ""
            for r in rows:
                text = "".join([p.get("text", "") for p in r.get("parts", []) if "text" in p])
                raw_history += f"{r['role'].upper()}: {text}\n---\n"
            
            distill_prompt = f"Tu es un agent de distillation. Résume l'historique suivant sous l'angle critique pour un rôle de '{role}'. Ne garde que les faits techniques, les décisions et les contraintes pertinentes.\n"
            if focus:
                distill_prompt += f"PRIORITÉ ABSOLUE : Concentre-toi sur le sujet suivant : {focus}\n"
            
            distill_prompt += f"\n### HISTORIQUE\n{raw_history}"
            
            res = await EchoGeminiClient.call(
                target_model=MODEL_DISTILLATION,
                payload={
                    "contents": [{"role": "user", "parts": [{"text": distill_prompt}]}],
                    "generationConfig": {"temperature": TEMP_DISTILLATION, "topP": TOP_P_DISTILLATION}
                },
                user_id=user_id,
                events=None # Discret
            )
            
            candidates = res.get("candidates", [])
            if candidates:
                return "".join([p.get("text", "") for p in candidates[0]["content"]["parts"]])
        except Exception as e:
            print(f"Distillation error: {e}")
        return "Erreur lors de la distillation du contexte."

    async def _iterative_loop(self, state, sub_sid, chat_id, role_id, system_instruction, initial_prompt, context_distillate, target_model, user_id, events) -> str:
        """Moteur itératif avec gestion des thoughtSignatures (Gemini 3.1)."""
        actual_model = MODEL_ROUTING.get(target_model, MODEL_PRO)
        # Niveaux de réflexion via constantes ECHO (echo_constants.py v4.8)
        thinking_level = THINKING_LEVEL_PRO if target_model == "MODEL_PRO" else THINKING_LEVEL_FLASH
        
        # Injection de la contrainte technique d'arrêt
        stop_tag = "<FINAL_ANSWER>"
        stop_instruction = f"\n\nIMPORTANT : Lorsque vous avez terminé votre analyse et que vous êtes prêt à livrer votre conclusion finale, vous DEVEZ impérativement terminer votre message par la balise exacte : {stop_tag}"
        full_system_instruction = f"{system_instruction}{stop_instruction}"

        # Récupération de l'historique du thread
        history = state.get_thread_history(sub_sid)
        step_index = len(history)
        
        # Si c'est le début, on injecte le distillat
        if step_index == 0:
            full_prompt = f"### CONTEXTE DISTILLÉ\n{context_distillate}\n\n### MISSION\n{initial_prompt}"
            history.append({"role": "user", "parts": [{"text": full_prompt}]})
            state.save_thread_step(sub_sid, chat_id, role_id, 0, "user", history[0]["parts"])
            step_index = 1
        else:
            # Suite de discussion
            history.append({"role": "user", "parts": [{"text": initial_prompt}]})
            state.save_thread_step(sub_sid, chat_id, role_id, step_index, "user", history[-1]["parts"])
            step_index += 1

        iteration = 0
        max_iters = self.user_valves.ITERATION_MAX
        
        while iteration < max_iters:
            iteration += 1
            await events.status(f"🧠 Réflexion du {role_id} (Tour {iteration}/{max_iters})...")
            
            # Appel Gemini avec includeThoughts: False (La signature suffit pour maintenir l'état CoT)
            res = await EchoGeminiClient.call(
                target_model=actual_model,
                payload={
                    "contents": history,
                    "systemInstruction": {"parts": [{"text": full_system_instruction}]},
                    "generationConfig": {
                        "temperature": TEMP_DEFAULT,
                        "topP": TOP_P_DEFAULT,
                        "thinkingConfig": {"includeThoughts": False, "thinkingLevel": thinking_level.lower()}
                    }
                },
                user_id=user_id,
                events=events,
                timeout=self.valves.COGNITIVE_TIMEOUT
            )
            
            candidate = res.get("candidates", [{}])[0]
            content = candidate.get("content", {})
            parts = content.get("parts", [])
            
            if not parts: break
            
            # Extraction de la signature (Gemini 3.1)
            signature = None
            for p in parts:
                if "thoughtSignature" in p:
                    signature = p["thoughtSignature"]
                    break
            
            # Sauvegarde de l'étape du modèle
            state.save_thread_step(sub_sid, chat_id, role_id, step_index, "model", parts, signature)
            history.append({"role": "model", "parts": parts})
            step_index += 1
            
            # Analyse de la réponse
            full_text = "".join([p.get("text", "") for p in parts if "text" in p])
            
            # Condition d'arrêt : Présence de la balise finale ou limite atteinte
            if stop_tag in full_text or iteration >= self.user_valves.ITERATION_DEFAULT:
                return full_text.replace(stop_tag, "").strip()
            
            # Si pas de clôture, on relance (auto-poursuite)
            history.append({"role": "user", "parts": [{"text": "Continuez votre analyse pour arriver à la conclusion finale."}]})
            state.save_thread_step(sub_sid, chat_id, role_id, step_index, "user", history[-1]["parts"])
            step_index += 1

        return "Erreur: Limite d'itérations atteinte sans réponse finale."
