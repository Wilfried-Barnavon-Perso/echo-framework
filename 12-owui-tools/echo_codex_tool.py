"""
title: ECHO Codex Editor
author: Wilfried BARNAVON
version: 1.2
description: 1.2: Registre Unifié V2 — save_codex_record → save_resource,
             delete_codex_record → delete_resource.
             1.1: Correction docstring summarize_codex (distillation cloud Gemini).
             1.0: Éditeur multi-langage avec Git intégré. 9 fonctions Tool pour le LLM :
             create, edit (direct + agent), read (plage lignes), search, summarize (distillation),
             list, delete, history. Sub-chat MODEL_FLASH pour édition assistée via call_cascade.
"""

# ECHO CONFIG NAME : ECHO Codex

import sys
import orjson as json
from pydantic import BaseModel, Field
from typing import Optional, Any, List

sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    wrap_tool_output, wrap_cascade_output, EchoEvents,
    EchoGeminiClient, EchoStateManager, split_thought_process,
)
from echo_constants import (
    ECHO_API_KEY_THRESHOLD, ECHO_API_MAX_RETRIES,
    TEMP_DEFAULT, TOP_P_DEFAULT, MAX_TOKENS_DEFAULT,
    CODEX_EDIT_SYSTEM_PROMPT, CODEX_SUMMARIZE_PROMPT, CODEX_DEFAULT_LANG,
    FILE_INGESTION_STATUS
)
from echo_codex_git import CodexRepo


class Tools:
    class Valves(BaseModel):
        KEY_SWITCH_THRESHOLD: int = Field(default=ECHO_API_KEY_THRESHOLD)
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES)
        CODEX_EDIT_TIMEOUT: int = Field(default=120, description="Timeout sub-chat édition (secondes).")

    class UserValves(BaseModel):
        CODEX_EDIT_MODEL: str = Field(
            default="MODEL_FLASH",
            description="Modèle pour l'édition assistée Codex (sub-chat).",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()

    # =========================================================================
    # HELPERS
    # =========================================================================

    def _get_context(self, __user__: dict, __metadata__: dict):
        """Extrait user_id, chat_id et initialise CodexRepo + StateManager."""
        uid = __user__.get("id", "anonymous") if __user__ else "anonymous"
        cid = (__metadata__ or {}).get("chat_id")
        if not cid:
            return None, None, None, None
        repo = CodexRepo(uid, cid)
        state = EchoStateManager(user_id=uid, chat_id=cid)
        return uid, cid, repo, state

    def _extract_llm_text(self, data: dict) -> str:
        """Extrait le texte des candidates (filtrage thought parts)."""
        candidates = data.get("candidates", [])
        if not candidates:
            return ""
        parts = candidates[0].get("content", {}).get("parts", [])
        text_parts = [p.get("text", "") for p in parts if not p.get("thought")]
        raw = "".join(text_parts).strip()
        # Nettoyage des blocs markdown enveloppants (```lang ... ```)
        if raw.startswith("```"):
            lines = raw.split("\n")
            if len(lines) >= 3 and lines[-1].strip() == "```":
                return "\n".join(lines[1:-1])
        return raw

    # =========================================================================
    # ÉCRITURE
    # =========================================================================

    async def create_codex(
        self,
        filename: str,
        content: str,
        language: str = None,
        commit_message: str = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Crée un nouveau fichier dans le Codex de cette conversation.
        Le fichier est versionné via Git. Le langage est auto-détecté depuis l'extension si non spécifié.
        :param filename: Nom du fichier avec extension (ex: "main.py", "notes.md").
        :param content: Contenu initial du fichier.
        :param language: Langage (optionnel, auto-détecté depuis l'extension).
        :param commit_message: Message de commit Git (optionnel, auto-généré si absent).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        await events.status(f"📝 Création de {filename}...", done=False)

        lang = language or CodexRepo.detect_language(filename)
        msg = commit_message or f"Create {filename}"
        line_count = content.count("\n") + 1

        commit_hash = repo.commit_file(filename, content, msg)
        state.save_resource(
            id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
            git_tracked=True, language=lang, lines=line_count,
            last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
        )

        await events.status(f"✅ {filename} créé ({line_count} lignes, commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(
            text=f"Fichier `{filename}` créé avec succès.\n- Langage : {lang}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`",
        )

    async def edit_codex(
        self,
        filename: str,
        new_content: str = None,
        instructions: str = None,
        commit_message: str = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Modifie un fichier Codex existant. Deux modes :
        - new_content fourni : remplacement direct du fichier, commit immédiat.
        - instructions fournies (sans new_content) : délégation à un agent éditeur spécialisé
          (sub-chat MODEL_FLASH) qui lit le fichier, applique les instructions et retourne le résultat.
        :param filename: Fichier cible (doit exister dans le Codex).
        :param new_content: Contenu modifié complet (mode direct).
        :param instructions: Instructions d'édition en langage naturel (mode agent).
        :param commit_message: Message de commit (optionnel, auto-généré si absent).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        # Vérification existence
        existing = repo.read_file(filename)
        if not existing:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable dans le Codex.", status={"status": "error"})

        lang = CodexRepo.detect_language(filename)

        # Mode 1 : Remplacement direct
        if new_content is not None:
            msg = commit_message or f"Edit {filename}"
            line_count = new_content.count("\n") + 1
            commit_hash = repo.commit_file(filename, new_content, msg)
            state.save_resource(
                id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                git_tracked=True, language=lang, lines=line_count,
                last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
            )

            await events.status(f"✅ {filename} modifié (commit {commit_hash[:7]}).", done=True)
            return wrap_tool_output(
                text=f"Fichier `{filename}` modifié.\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`",
            )

        # Mode 2 : Édition assistée via sub-chat
        if instructions:
            await events.status(f"🧠 Édition assistée de {filename}...", done=False)

            content = existing["content"]
            user_prompt = f"## Document complet\n```{lang}\n{content}\n```\n\n## Instructions\n{instructions}"

            payload = {
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": TEMP_DEFAULT,
                    "topP": TOP_P_DEFAULT,
                    "maxOutputTokens": MAX_TOKENS_DEFAULT,
                },
                "systemInstruction": {
                    "parts": [{"text": CODEX_EDIT_SYSTEM_PROMPT.format(filename=filename, language=lang)}]
                },
            }

            data, model_key, reason = await EchoGeminiClient.call_cascade(
                target_model_key=self.user_valves.CODEX_EDIT_MODEL,
                payload=payload,
                user_id=uid,
                metadata=__metadata__,
                events=events,
                timeout=self.valves.CODEX_EDIT_TIMEOUT,
                chat_id=cid,
                include_thoughts=False,
            )

            if not data:
                await events.status("❌ Édition échouée — aucun modèle disponible.", done=True)
                return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour l'édition.", status={"status": "error"})

            modified = self._extract_llm_text(data)
            if not modified:
                await events.status("❌ L'agent éditeur n'a produit aucun contenu.", done=True)
                return wrap_tool_output(text="❌ L'agent éditeur n'a pas retourné de contenu exploitable.", status={"status": "error"})

            msg = commit_message or f"AI edit: {instructions[:60]}"
            line_count = modified.count("\n") + 1
            commit_hash = repo.commit_file(filename, modified, msg)
            state.save_resource(
                id=filename, name=filename, resource_type='codex', status=FILE_INGESTION_STATUS['PUT_IN_CONTEXT'],
                git_tracked=True, language=lang, lines=line_count,
                last_commit=commit_hash[:12], commit_msg=msg, storage_path=f"codex/{filename}",
            )

            await events.status(f"✅ Édition assistée terminée (commit {commit_hash[:7]}).", done=True)
            return wrap_cascade_output(
                text=f"Fichier `{filename}` modifié par agent éditeur.\n- Instructions : {instructions}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`",
                model_requested=self.user_valves.CODEX_EDIT_MODEL,
                model_used=model_key or "unknown",
                reason=reason,
            )

        return wrap_tool_output(text="❌ Ni `new_content` ni `instructions` fournis.", status={"status": "error"})

    async def delete_codex(
        self,
        filename: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Supprime un fichier du Codex. Irréversible (mais l'historique Git est conservé pour restauration).
        :param filename: Fichier à supprimer.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        commit_hash = repo.delete_file(filename, f"Delete {filename}")
        if not commit_hash:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"})

        state.delete_resource(filename)
        await events.status(f"🗑️ {filename} supprimé (commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(text=f"Fichier `{filename}` supprimé.\n- Commit : `{commit_hash[:12]}`")

    # =========================================================================
    # LECTURE
    # =========================================================================

    async def read_codex(
        self,
        filename: str,
        start_line: int = None,
        end_line: int = None,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Lit le contenu d'un fichier Codex.
        Sans start_line/end_line : fichier complet.
        Avec : plage [start_line, end_line] (1-indexed, inclusif).
        Le nombre total de lignes est toujours indiqué.
        Pour les fichiers volumineux (>300 lignes), préférer summarize_codex pour une vue d'ensemble,
        puis cibler avec start_line/end_line.
        :param filename: Fichier à lire.
        :param start_line: Première ligne (optionnel, 1-indexed).
        :param end_line: Dernière ligne (optionnel, 1-indexed, inclusif).
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        result = repo.read_file(filename, start_line, end_line)
        if not result:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"})

        lang = CodexRepo.detect_language(filename)
        range_info = f"lignes {result['range'][0]}-{result['range'][1]}" if result["range"] else "complet"

        return wrap_tool_output(
            text=f"**{filename}** ({lang}, {result['total_lines']} lignes total, {range_info})\n\n```{lang}\n{result['content']}\n```",
        )

    async def search_codex(
        self,
        filename: str,
        query: str,
        is_regex: bool = False,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Recherche un pattern dans un fichier Codex.
        Retourne les lignes correspondantes avec leurs numéros.
        Utile pour cibler une zone avant read_codex(start_line, end_line).
        :param filename: Fichier cible.
        :param query: Pattern de recherche (texte littéral ou regex).
        :param is_regex: Si True, query est interprété comme regex.
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        matches = repo.search_in_file(filename, query, is_regex)
        if not matches:
            return wrap_tool_output(text=f"Aucun résultat pour `{query}` dans `{filename}`.")

        lines_text = "\n".join(
            f"L{m['line_number']:>4}: {m['line_content']}"
            for m in matches
        )
        return wrap_tool_output(
            text=f"**{len(matches)} résultat(s)** pour `{query}` dans `{filename}` :\n\n```\n{lines_text}\n```",
        )

    async def summarize_codex(
        self,
        filename: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Produit un résumé technique structuré du fichier (≤ 8000 tokens) via distillation (Gemini).
        Utile pour comprendre un fichier volumineux sans le charger intégralement dans le contexte.
        :param filename: Fichier à résumer.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        result = repo.read_file(filename)
        if not result:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"})

        lang = CodexRepo.detect_language(filename)
        await events.status(f"🔍 Distillation de {filename} ({result['total_lines']} lignes)...", done=False)

        prompt = CODEX_SUMMARIZE_PROMPT.format(filename=filename, language=lang)
        parts = [{"role": "user", "parts": [{"text": result["content"]}]}]

        summary = await EchoGeminiClient.call_distillation(
            prompt=prompt,
            __user__=__user__,
            __metadata__=__metadata__,
            is_json=False,
            parts=parts,
            max_tokens=8192,
        )

        await events.status(f"✅ Synthèse de {filename} terminée.", done=True)
        return wrap_tool_output(
            text=f"## Synthèse — {filename} ({lang}, {result['total_lines']} lignes)\n\n{summary}",
        )

    async def list_codex(
        self,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Liste tous les fichiers du Codex de cette conversation.
        Retourne : filename, langage, nombre de lignes, dernier commit.
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        files = repo.list_files()
        if not files:
            return wrap_tool_output(text="Le Codex de cette conversation est vide.")

        lines = [f"| Fichier | Langage | Lignes | Taille |"]
        lines.append("|---|---|---|---|")
        for f in files:
            size_kb = f["size_bytes"] / 1024
            lines.append(f"| `{f['filename']}` | {f['lang']} | {f['lines']} | {size_kb:.1f} Ko |")

        stats = repo.get_repo_stats()
        footer = f"\n**{stats['total_files']} fichiers**, {stats['total_commits']} commits"
        if stats["last_commit_message"]:
            footer += f", dernier : _{stats['last_commit_message']}_"

        return wrap_tool_output(text="\n".join(lines) + footer)

    async def history_codex(
        self,
        filename: str = None,
        limit: int = 20,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Affiche l'historique Git du Codex.
        Sans filename : historique global du repo. Avec : historique d'un fichier spécifique.
        :param filename: Fichier cible (optionnel, tout le repo si absent).
        :param limit: Nombre max de commits à afficher (défaut 20).
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"})

        log = repo.get_log(filename=filename, limit=limit)
        if not log:
            scope = f"de `{filename}`" if filename else "du Codex"
            return wrap_tool_output(text=f"Aucun historique {scope}.")

        import time as time_mod
        lines = []
        for entry in log:
            dt = time_mod.strftime("%Y-%m-%d %H:%M", time_mod.localtime(entry["timestamp"]))
            lines.append(f"- `{entry['hash']}` — {entry['message']} ({dt})")

        scope = f"de `{filename}`" if filename else "global"
        return wrap_tool_output(
            text=f"## Historique {scope} ({len(log)} commits)\n\n" + "\n".join(lines),
        )
