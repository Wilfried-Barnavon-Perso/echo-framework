"""
title: ECHO Strategic Planner
author: ECHO Framework
version: 1.2
description: 1.0: Outil de planification stratégique — construction, modification et gestion
             de plans d'action via un agent planificateur LLM (cascade PRO→FLASH→LITE).
             Plans persistés en Markdown dans le vault, registre de contrôle SQLite par chat,
             injection automatique dans registre_plan (environnement_contexte).
             1.1: Docstrings enrichies — instruction de reformulation read_plan, directive
             tools_summary renforcée dans build_plan, contrainte auto-suppression delete_plan,
             log diagnostic __tools__.
             1.2: Centralisation politique modèle Pipe. Suppression _cascade_call() et
             _get_thinking_level() locaux → call_cascade() centralisé.
"""

import sys
import os
import re
import glob
import time
import unicodedata
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import Optional, Any, Literal

# Importation ECHO Standard
sys.path.append("/app/backend/echo_libs")
from echo_utils import wrap_tool_output, wrap_cascade_output, EchoEvents, EchoGeminiClient, EchoStateManager, clamp_model
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO, MODEL_ROUTING,
    ECHO_USERS_ROOT,
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT, MAX_TOKENS_DEFAULT,
    THINKING_LEVEL_PRO, THINKING_LEVEL_FLASH, THINKING_LEVEL_LITE,
    PLAN_STATUS, PLAN_TASK_STATUS, PLAN_EXECUTABLE_STATUSES,
)


# ==============================================================================
# PROMPTS SYSTÈME POUR L'AGENT PLANIFICATEUR
# ==============================================================================

SYSTEM_PROMPT_BUILD = """Tu es l'Architecte de Plans Stratégiques ECHO.

## Ta mission
Rédiger un plan d'action structuré en Markdown pour atteindre l'objectif donné.

## Règles strictes
1. Le plan est centré sur L'OBJECTIF, pas sur les outils.
2. Tu as accès à la liste exhaustive des outils ECHO ci-dessous.
   Quand un outil est pertinent pour une étape, utilise UNIQUEMENT un nom
   de la liste ci-dessous avec la notation → `nom_exact_outil` en fin de ligne.
   N'invente JAMAIS de noms d'outils. Si aucun outil ne correspond, n'en mentionne pas.
3. Profondeur maximale des sous-tâches : {max_depth} niveaux.
4. Chaque tâche commence par `- [ ]` (notation Markdown).
5. Le plan doit être ACTIONNABLE : pas de formulations vagues.
6. Identifie les contraintes et risques réels.
7. Définis des critères de succès mesurables.

## Format de sortie OBLIGATOIRE (respecter exactement)
---
plan_id: {plan_id}
chat_id: {chat_id}
created_at: {iso_date}
goal: "{goal}"
author_model: {author_model}
status: draft
---

## 🎯 Objectif
(Reformulation précise de l'objectif)

## 📋 Plan d'action
- [ ] Étape 1 : ...
  - [ ] Sous-tâche 1.1 : ... (→ `outil` si pertinent)
- [ ] Étape 2 : ...

## ⚠️ Contraintes & Risques
...

## ✅ Critères de succès
...

## Outils ECHO disponibles pour ce plan
{tools_summary}
"""

SYSTEM_PROMPT_UPDATE = """Tu es l'Architecte de Plans Stratégiques ECHO.

## Ta mission
Modifier le plan existant selon les instructions fournies.
Applique UNIQUEMENT les modifications demandées.
Ne modifie RIEN d'autre (structure, étapes non mentionnées, frontmatter non ciblé).

Si les instructions changent le statut, mets à jour le champ `status:` du frontmatter.
Si les instructions cochent/décochent des tâches, utilise la notation ECHO :
- [ ] = en attente | [/] = en cours | [x] = terminée | [!] = échouée | [-] = ignorée

Retourne le plan COMPLET modifié (frontmatter YAML inclus, délimiteurs --- inclus).
Ne retourne RIEN d'autre que le plan Markdown complet."""


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(
            default=ECHO_API_KEY_THRESHOLD,
            description="Nombre d'erreurs 429/503 avant bascule sur la clé de secours."
        )
        MAX_RETRIES: int = Field(
            default=ECHO_API_MAX_RETRIES,
            description="Nombre de tentatives maximum."
        )
        PLANNER_TIMEOUT: int = Field(
            default=180,
            description="Timeout (secondes) pour l'appel LLM planificateur."
        )

    class UserValves(BaseModel):
        MAX_PLAN_DEPTH: int = Field(
            default=3, ge=1, le=5,
            description="Profondeur max des sous-tâches dans un plan (1=plat, 3=recommandé, 5=projets complexes)."
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # ==========================================================================
    # HELPERS PRIVÉS
    # ==========================================================================

    @staticmethod
    def _slugify(text: str, max_length: int = 40) -> str:
        """Convertit un texte en slug ASCII pour les noms de fichiers."""
        # Normalisation Unicode → ASCII
        text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
        text = text.lower().strip()
        # Remplacement des caractères non-alphanum par des tirets
        text = re.sub(r"[^a-z0-9]+", "-", text)
        # Nettoyage des tirets multiples et des extrémités
        text = re.sub(r"-+", "-", text).strip("-")
        return text[:max_length] if text else "plan"

    @staticmethod
    def _get_plan_dir(uid: str, chat_id: str) -> str:
        """Retourne le répertoire des plans pour un chat donné. Le crée si nécessaire."""
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        plan_dir = os.path.join(ECHO_USERS_ROOT, safe_uid, "plans", safe_cid)
        os.makedirs(plan_dir, exist_ok=True)
        return plan_dir

    @staticmethod
    def _get_plan_path(uid: str, chat_id: str, plan_id: str) -> Optional[str]:
        """Résout le chemin physique d'un plan via glob sur {plan_id}_*.md."""
        safe_uid = "".join(x for x in str(uid) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        plan_dir = os.path.join(ECHO_USERS_ROOT, safe_uid, "plans", safe_cid)
        matches = glob.glob(os.path.join(plan_dir, f"{plan_id}_*.md"))
        return matches[0] if matches else None

    @staticmethod
    def _build_tools_summary(tools_dict: Optional[dict]) -> str:
        """Extrait les noms et descriptions des outils disponibles depuis __tools__."""
        if not tools_dict:
            return "Aucune information sur les outils disponibles."
        lines = []
        for func_name, tool_info in tools_dict.items():
            spec = tool_info.get("spec", {}) if isinstance(tool_info, dict) else {}
            desc = spec.get("description", "Pas de description.")
            # Première ligne significative de la description
            short_desc = desc.split("\n")[0][:120]
            lines.append(f"- `{func_name}` — {short_desc}")
        return "\n".join(lines) if lines else "Aucun outil disponible."

    @staticmethod
    def _extract_frontmatter_status(content: str) -> Optional[str]:
        """Extrait le champ status: du frontmatter YAML d'un plan."""
        match = re.search(r"^status:\s*(\w+)", content, re.MULTILINE)
        return match.group(1) if match else None

    @staticmethod
    def _get_thinking_level(model_key: str) -> str:
        """DEPRECATED — géré par call_cascade. Conservé pour compatibilité."""
        if model_key == "MODEL_PRO":
            return THINKING_LEVEL_PRO
        elif model_key == "MODEL_FLASH":
            return THINKING_LEVEL_FLASH
        return THINKING_LEVEL_LITE

    @staticmethod
    def _extract_llm_text(res_json: dict) -> Optional[str]:
        """Extrait le texte de la réponse Gemini (après déballage enveloppe CA)."""
        candidates = res_json.get("candidates", [])
        if candidates and candidates[0].get("content"):
            parts = candidates[0]["content"].get("parts", [])
            # Filtrer les parties pensée (thought=True), ne garder que le texte final
            text_parts = [p.get("text", "") for p in parts if not p.get("thought")]
            return "".join(text_parts)
        return None

    # ==========================================================================
    # FUNCTION CALLS PUBLIQUES
    # ==========================================================================

    async def build_plan(
        self,
        goal: str,
        context: str,
        planner_model: Literal["MODEL_PRO", "MODEL_FLASH", "MODEL_LITE"] = "MODEL_PRO",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __tools__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Crée un plan stratégique persistant pour atteindre un objectif complexe.

        ⚠️ NE PAS appeler précipitamment.

        **Protocole avant création :**
        Un plan est un contrat d'exécution. Avant d'appeler build_plan, engager une
        discussion de clarification avec l'utilisateur :

        1. L'objectif est-il précis ? Un but vague produit un plan vague.
           → Poser des questions : périmètre, format de sortie attendu, contraintes.
        2. Les contraintes sont-elles connues ? Sources privilégiées ? Interdictions ?
        3. Les critères de succès sont-ils mesurables ?
        4. Proposer une structure préliminaire AVANT d'appeler cet outil.

        Appeler build_plan uniquement quand l'objectif et les contraintes sont clairs.

        **Statut initial :** Le plan est créé en statut `draft` (non exécutable).
        L'utilisateur doit valider → appeler update_plan pour passer à `ready`.

        **Lecture :** Le plan est lisible via `read_plan(plan_id)`.
        Son existence et son état apparaissent dans registre_plan (environnement_contexte).

        **Modèle :** Le modèle effectif dépend de la politique du Pipe. Cascade descendante
        automatique en cas d'indisponibilité. Le modèle utilisé est consigné dans le frontmatter.

        :param planner_model: Modèle préféré (MODEL_PRO recommandé). Ajusté automatiquement.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        await events.status("🗂️ Préparation du plan stratégique...")

        # Génération des identifiants
        plan_id = str(int(time.time()))
        slug = self._slugify(goal)
        filename = f"{plan_id}_{slug}.md"
        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        tools_summary = self._build_tools_summary(__tools__)
        if "Aucun" in tools_summary:
            import logging
            logging.getLogger("echo.planner").warning(
                f"build_plan: __tools__ injecté avec type={type(__tools__)}, "
                f"contenu={list(__tools__.keys()) if isinstance(__tools__, dict) else __tools__}"
            )
        max_depth = self.user_valves.MAX_PLAN_DEPTH

        # Construction du prompt système avec les variables injectées
        system_prompt = SYSTEM_PROMPT_BUILD.format(
            max_depth=max_depth,
            plan_id=plan_id,
            chat_id=chat_id,
            iso_date=iso_date,
            goal=goal,
            author_model="{author_model}",  # Placeholder — remplacé après cascade
            tools_summary=tools_summary,
        )

        # Prompt utilisateur
        user_prompt = f"## Objectif\n{goal}\n\n## Contexte et contraintes\n{context}"

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": TEMP_DEFAULT,
                "topP": TOP_P_DEFAULT,
                "maxOutputTokens": MAX_TOKENS_DEFAULT,
            },
            "systemInstruction": {"parts": [{"text": system_prompt}]},
        }

        # Appel cascade
        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=planner_model,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("❌ Échec — tous les modèles sont indisponibles.", done=True)
            return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour la planification.")

        # Extraction du texte généré
        plan_content = self._extract_llm_text(res_json)
        if not plan_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return wrap_tool_output(text="❌ Erreur : le planificateur n'a produit aucun contenu.")

        # Remplacement du placeholder author_model dans le frontmatter
        actual_model = MODEL_ROUTING.get(model_key_used, model_key_used)
        plan_content = plan_content.replace("{author_model}", actual_model)

        # Persistance fichier
        plan_dir = self._get_plan_dir(user_id, chat_id)
        plan_path = os.path.join(plan_dir, filename)
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(plan_content)

        # Enregistrement dans le registre SQLite
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.save_plan_record(
            plan_id=plan_id,
            filename=filename,
            goal=goal[:200],
            status="draft",
            author_model=actual_model,
        )

        await events.status(f"✅ Plan `{plan_id}` créé (draft) par {model_key_used}.", done=True)

        return wrap_cascade_output(
            text=f"### Plan stratégique créé — `{plan_id}` (draft)\n\n"
                 f"**Modèle :** {model_key_used} ({actual_model})\n"
                 f"**Fichier :** `{filename}`\n\n"
                 f"---\n\n{plan_content}",
            model_requested=planner_model,
            model_used=model_key_used,
            reason=reason
        )

    async def read_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Lit le contenu complet d'un plan stratégique existant.

        IMPORTANT : Le contenu retourné par cet outil est encapsulé dans un bloc
        technique invisible pour l'utilisateur. Tu DOIS reformuler le plan dans ta
        réponse pour que l'utilisateur puisse le lire. Reproduis fidèlement le contenu
        (objectif, étapes, statuts) dans un format Markdown lisible.

        Le plan_id est présent dans registre_plan (environnement_contexte).
        Retourne le Markdown complet incluant le frontmatter YAML et toutes les sections.

        :param plan_id: Identifiant du plan (visible dans registre_plan).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        plan_path = self._get_plan_path(user_id, chat_id, plan_id)
        if not plan_path or not os.path.exists(plan_path):
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable.")

        with open(plan_path, "r", encoding="utf-8") as f:
            content = f.read()

        await events.status(f"📖 Plan `{plan_id}` lu.", done=True)
        return wrap_tool_output(text=content)

    async def update_plan(
        self,
        plan_id: str,
        instructions: str,
        planner_model: Literal["MODEL_PRO", "MODEL_FLASH", "MODEL_LITE"] = "MODEL_FLASH",
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Modifie un plan existant selon des instructions en langage naturel.

        L'agent planificateur relit le plan intégralement, applique les modifications
        demandées, et réécrit le fichier complet. Le plan mis à jour est retourné.

        **Exemples d'instructions :**
        - "Marquer l'étape 'Extraire le contenu' comme terminée"
        - "Ajouter une sous-étape 'Valider les sources' après l'étape 2"
        - "Passer le statut global à 'executing'"
        - "Restructurer la section contraintes"
        - "Marquer le plan comme réussi (success)"

        **Synchronisation :** Si le statut du frontmatter change, le registre_plan
        dans environnement_contexte est mis à jour automatiquement au tour suivant.

        :param plan_id: Identifiant du plan (visible dans registre_plan).
        :param instructions: Description des modifications souhaitées.
        :param planner_model: Modèle de départ pour la cascade (défaut: MODEL_FLASH).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        plan_path = self._get_plan_path(user_id, chat_id, plan_id)
        if not plan_path or not os.path.exists(plan_path):
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable.")

        # 1. Lecture du plan actuel
        with open(plan_path, "r", encoding="utf-8") as f:
            current_content = f.read()

        await events.status(f"📝 Modification du plan `{plan_id}`...")

        # 2. Construction du prompt pour l'agent modificateur
        user_prompt = (
            f"## Plan actuel\n{current_content}\n\n"
            f"## Modifications demandées\n{instructions}"
        )

        payload = {
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": TEMP_DEFAULT,
                "topP": TOP_P_DEFAULT,
                "maxOutputTokens": MAX_TOKENS_DEFAULT,
            },
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT_UPDATE}]},
        }

        # 3. Appel cascade
        res_json, model_key_used, reason = await EchoGeminiClient.call_cascade(
            target_model_key=planner_model,
            payload=payload,
            user_id=user_id,
            metadata=__metadata__,
            events=events,
            timeout=self.valves.PLANNER_TIMEOUT,
            chat_id=chat_id,
            include_thoughts=False,
        )

        if not res_json:
            await events.status("❌ Échec — tous les modèles sont indisponibles.", done=True)
            return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour la modification.")

        new_content = self._extract_llm_text(res_json)
        if not new_content:
            await events.status("❌ Réponse vide du planificateur.", done=True)
            return wrap_tool_output(text="❌ Erreur : le planificateur n'a produit aucun contenu.")

        # 4. Écriture du fichier mis à jour
        with open(plan_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # 5. Synchronisation du statut SQLite si changé
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        new_status = self._extract_frontmatter_status(new_content)
        if new_status and new_status in PLAN_STATUS:
            state.update_plan_record_status(plan_id, new_status)

        await events.status(
            f"✅ Plan `{plan_id}` modifié par {model_key_used}.",
            done=True
        )

        return wrap_cascade_output(
            text=f"### Plan `{plan_id}` mis à jour\n\n"
                 f"**Modèle :** {model_key_used}\n"
                 f"**Statut :** {new_status or '(inchangé)'}\n\n"
                 f"---\n\n{new_content}",
            model_requested=planner_model,
            model_used=model_key_used,
            reason=reason
        )

    async def delete_plan(
        self,
        plan_id: str,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Optional[Any] = None,
        __event_call__: Optional[Any] = None,
    ) -> dict:
        """
        Supprime définitivement un plan stratégique (fichier + registre).

        CONTRAINTE : Un plan ne peut PAS ordonner sa propre suppression. Si tu es
        en train d'exécuter un plan et qu'une étape prévoit la suppression de ce
        même plan, tu DOIS refuser et demander confirmation explicite à l'utilisateur.
        Seul l'utilisateur ou un contexte extérieur au plan actif peut déclencher
        cette action.

        La suppression est irréversible. Le plan disparaît du registre_plan
        dans environnement_contexte au tour suivant.

        :param plan_id: Identifiant du plan à supprimer (présent dans registre_plan).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        plan_path = self._get_plan_path(user_id, chat_id, plan_id)
        if not plan_path or not os.path.exists(plan_path):
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable.")

        filename = os.path.basename(plan_path)

        # 1. Suppression du fichier
        os.remove(plan_path)

        # 2. Nettoyage du registre SQLite
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.delete_plan_record(plan_id)

        await events.status(f"🗑️ Plan `{plan_id}` supprimé.", done=True)

        return wrap_tool_output(
            text=f"✅ Plan `{plan_id}` (`{filename}`) supprimé définitivement."
        )
