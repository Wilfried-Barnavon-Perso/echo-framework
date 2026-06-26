"""
title: ECHO Strategic Planner
author: ECHO Framework
version: 1.4
description: 1.4: Refonte des system prompts (BUILD/UPDATE) avec balises XML, exemples yaml et ton impersonnel.
             1.0: Outil de planification stratégique — construction, modification et gestion
             de plans d'action via un agent planificateur LLM (cascade PRO→FLASH→LITE).
             Plans persistés en Markdown dans le vault, registre de contrôle SQLite par chat,
             injection automatique dans registre_plan (environnement_contexte).
             1.1: Docstrings enrichies — instruction de reformulation read_plan, directive
             tools_summary renforcée dans build_plan, contrainte auto-suppression delete_plan,
             log diagnostic __tools__.
             1.2: Centralisation politique modèle Pipe. Suppression _cascade_call() et
             _get_thinking_level() locaux → call_cascade() centralisé.
             1.3: Registre Unifié V2 — Plans stockés dans le Codex (Git) au lieu du
             dossier plans/. save_plan_record → save_resource. Suppression _get_plan_dir
             et _get_plan_path.
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
from echo_codex_git import CodexRepo
from echo_constants import (
    MODEL_LITE, MODEL_FLASH, MODEL_PRO, MODEL_ROUTING,
    TEMP_DEFAULT, TOP_P_DEFAULT, MAX_TOKENS_DEFAULT,
    THINKING_LEVEL_PRO, THINKING_LEVEL_FLASH, THINKING_LEVEL_LITE,
    PLAN_STATUS, PLAN_TASK_STATUS, PLAN_EXECUTABLE_STATUSES,
)


# ==============================================================================
# PROMPTS SYSTÈME POUR L'AGENT PLANIFICATEUR
# ==============================================================================

SYSTEM_PROMPT_BUILD = """<persona>
Le Modèle est un architecte expert en planification stratégique.
</persona>

<mission>
Le Modèle doit rédiger un plan d'action stratégique structuré en Markdown, focalisé exclusivement sur la résolution logique de l'objectif.
</mission>

<rules>
1. PROFONDEUR : La profondeur maximale des sous-tâches est strictement limitée à {max_depth} niveaux.
2. SYNTAXE : Chaque tâche DOIT impérativement commencer par `- [ ] ` (notation Markdown).
3. CONTENU : Le plan DOIT être actionnable, sans ambiguïté, identifier les contraintes/risques réels et définir des critères de succès mesurables.
4. OUTILS : Le Modèle a l'INTERDICTION d'inventer des outils. Il DOIT utiliser UNIQUEMENT ceux fournis dans la balise <available_tools>, en ajoutant la syntaxe `→ nom_exact_outil` à la fin de la ligne de la tâche correspondante.
</rules>

<available_tools>
{tools_summary}
</available_tools>

<output_format>
Le Modèle doit retourner EXACTEMENT le frontmatter YAML suivi du plan, selon l'exemple suivant.
<example>
---
plan_id: {plan_id}
chat_id: {chat_id}
created_at: {iso_date}
goal: "{goal}"
author_model: {author_model}
status: draft
---
## 🎯 Objectif
(Reformulation claire et analytique de l'objectif)
## 📋 Plan d'action
- [ ] Étape 1 : Analyse initiale
  - [ ] Sous-tâche 1.1 : Lire les fichiers cibles (→ `echo_codex_tool`)
## ⚠️ Contraintes & Risques
- Risque identifié X...
## ✅ Critères de succès
- Critère mesurable 1...
</example>
</output_format>"""

SYSTEM_PROMPT_UPDATE = """<persona>
Le Modèle est un architecte expert en planification stratégique.
</persona>

<mission>
Le Modèle doit modifier le plan existant selon les instructions fournies, sans en altérer la structure globale.
</mission>

<rules>
1. SCOPE : Le Modèle DOIT appliquer UNIQUEMENT les modifications demandées. Il ne doit RIEN modifier d'autre (étapes non mentionnées, frontmatter non ciblé).
2. STATUT : Si les instructions impliquent un changement de statut, Le Modèle DOIT mettre à jour le champ `status:` du frontmatter YAML.
3. SYNTAXE : Si les instructions cochent/décochent des tâches, Le Modèle DOIT utiliser strictement cette notation :
   - [ ] = en attente | [/] = en cours | [x] = terminée | [!] = échouée | [-] = ignorée
</rules>

<output_format>
Le Modèle DOIT retourner le plan COMPLET modifié (incluant le frontmatter YAML et ses délimiteurs ---).
Le Modèle a l'INTERDICTION d'ajouter du texte ou des commentaires en dehors du plan Markdown.
</output_format>"""


class Tools:
    class Valves(BaseModel):
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
    def _read_plan_from_codex(uid: str, chat_id: str, plan_id: str) -> Optional[dict]:
        """Lit un plan depuis le Codex via glob sur {plan_id}_*.md dans le repo Git."""
        repo = CodexRepo(uid, chat_id)
        files = repo.list_files()
        for f_dict in files:
            f_name = f_dict["filename"]
            if f_name.startswith(f"{plan_id}_") and f_name.endswith(".md"):
                data = repo.read_file(f_name)
                if data:
                    data["filename"] = f_name
                return data
        return None

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
        """Création/Mise à jour d'un plan d'action avec organisation de la liste des tâches. Agent PLANNER dédié.
        :param goal: Objectif final mesurable.
        :param context: Contraintes et périmètre.
        :param planner_model: (Optionnel) Enum des modèles (echo_constants).
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
        plan_content = plan_content.replace("{author_model}", model_key_used)

        # Persistance dans le Codex (Git)
        repo = CodexRepo(user_id, chat_id)
        repo.commit_file(filename, plan_content, f"Plan {plan_id}: {goal[:60]}")

        # Enregistrement dans le registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.save_resource(
            id=plan_id, name=goal[:80], resource_type='plan', status='draft',
            mime='text/markdown', plan_goal=goal[:200], author_model=model_key_used,
            git_tracked=True, storage_path=f"codex/{filename}",
        )

        await events.status(f"✅ Plan `{plan_id}` créé (draft) par {model_key_used}.", done=True)

        return wrap_cascade_output(
            text=f"### Plan stratégique créé — `{plan_id}` (draft)\n\n"
                 f"**Modèle :** {model_key_used}\n"
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
        """Lecture du contenu complet d'un plan stratégique existant. (plan_id)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable dans le Codex.")

        content = result["content"]

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
        """Modification d'un plan existant selon des instructions en langage naturel via l'Agent PLANNER dédié.
        :param plan_id: Identifiant strict Registre.
        :param instructions: Modifications attendues.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable dans le Codex.")

        # 1. Lecture du plan actuel
        current_content = result["content"]
        plan_filename = result["filename"]

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

        # 4. Écriture dans le Codex (Git)
        repo = CodexRepo(user_id, chat_id)
        repo.commit_file(plan_filename, new_content, f"Update plan {plan_id}")

        # 5. Synchronisation du statut dans le registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        new_status = self._extract_frontmatter_status(new_content)
        if new_status and new_status in PLAN_STATUS:
            state.update_resource_status(plan_id, new_status)

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
        """Supprime définitivement un plan stratégique (fichier + registre)."""
        events = EchoEvents(__event_emitter__, __event_call__)
        user_id = __user__.get("id", "system") if __user__ else "system"
        chat_id = (__metadata__ or {}).get("chat_id")

        if not chat_id:
            return wrap_tool_output(text="❌ Erreur: Aucun chat_id détecté.")

        result = self._read_plan_from_codex(user_id, chat_id, plan_id)
        if not result:
            return wrap_tool_output(text=f"❌ Plan `{plan_id}` introuvable dans le Codex.")

        plan_filename = result["filename"]

        # 1. Suppression dans le Codex (Git)
        repo = CodexRepo(user_id, chat_id)
        repo.delete_file(plan_filename, f"Delete plan {plan_id}")

        # 2. Nettoyage du registre unifié
        state = EchoStateManager(user_id=user_id, chat_id=chat_id)
        state.delete_resource(plan_id)

        await events.status(f"🗑️ Plan `{plan_id}` supprimé.", done=True)

        return wrap_tool_output(
            text=f"✅ Plan `{plan_id}` (`{plan_filename}`) supprimé définitivement."
        )
