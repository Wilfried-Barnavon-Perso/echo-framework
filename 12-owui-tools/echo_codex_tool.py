"""
title: ECHO Codex Editor
author: Wilfried BARNAVON
version: 1.7
description: Composant système interne : ECHO Codex Editor.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 1.6: Nettoyage du code mort (suppression de la Valve KEY_SWITCH_THRESHOLD inutilisée).
# 1.5: Augmentation du CODEX_EDIT_TIMEOUT à 600s et max_retries=0 pour call_cascade.
# 1.4: [précédent]
# 1.3: Restauration des descriptions de paramètres sémantiques pour create_codex.
# 1.2: Registre Unifié V2 — save_codex_record → save_resource,
# delete_codex_record → delete_resource.
# 1.7: Nettoyage du code : suppression des imports inutilisés (PEP8).

# ECHO CONFIG NAME : ECHO Codex

import sys
from pydantic import BaseModel, Field
from typing import Optional, Any

sys.path.append("/app/backend/echo_libs")
from echo_utils import (
    wrap_tool_output, wrap_cascade_output, EchoEvents,
    EchoGeminiClient, EchoStateManager,
)
from echo_constants import (
    ECHO_API_MAX_RETRIES, TEMP_DEFAULT,
    TOP_P_DEFAULT, MAX_TOKENS_DEFAULT, CODEX_EDIT_SYSTEM_PROMPT,
    CODEX_SUMMARIZE_PROMPT, FILE_INGESTION_STATUS
)
from echo_codex_git import CodexRepo


class Tools:
    class Valves(BaseModel):
        MAX_RETRIES: int = Field(default=ECHO_API_MAX_RETRIES)
        CODEX_EDIT_TIMEOUT: int = Field(default=600, description="Timeout sub-chat édition (secondes).")

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
        """Création de fichier dans le Codex avec versioning Git. Validation Registre (query_registry) requise.
        :param filename: Nom du fichier cible.
        :param content: Le contenu texte intégral à injecter dans le fichier.
        :param language: (Optionnel) Langage du fichier.
        :param commit_message: (Optionnel) Message Git.
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        if repo.read_file(filename):
            return wrap_tool_output(text=f"❌ Le fichier `{filename}` existe déjà. IMPLIQUE `edit_codex`.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
            text=f"Fichier `{filename}` créé avec succès.\n- Langage : {lang}\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
        Modification d'un fichier Codex.
        [CONTRAINTE CRITIQUE : Les paramètres `new_content` et `instructions` sont mutuellement exclusifs. Le Modèle DOIT obligatoirement fournir l'un OU l'autre, et ne doit JAMAIS utiliser les deux simultanément.]
        :param filename: Fichier cible (existant).
        :param new_content: (Optionnel) Remplacement intégral immédiat (exclut instructions).
        :param instructions: (Optionnel) Directives d'édition (exclut new_content).
        """
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        # Vérification existence
        existing = repo.read_file(filename)
        if not existing:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable. IMPLIQUE `create_codex` ou vérification Registre.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
                text=f"Fichier `{filename}` modifié.\n- Lignes : {line_count}\n- Commit : `{commit_hash[:12]}`", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
                max_retries=0,
            )

            if not data:
                await events.status("❌ Édition échouée — aucun modèle disponible.", done=True)
                return wrap_tool_output(text="❌ Échec : aucun modèle disponible pour l'édition.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

            modified = self._extract_llm_text(data)
            if not modified:
                await events.status("❌ L'agent éditeur n'a produit aucun contenu.", done=True)
                return wrap_tool_output(text="❌ L'agent éditeur n'a pas retourné de contenu exploitable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
                reason=reason, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        return wrap_tool_output(text="❌ Ni `new_content` ni `instructions` fournis.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def delete_codex(
        self,
        filename: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """Suppression d'un fichier du Codex. Validation Registre requise."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        commit_hash = repo.delete_file(filename, f"Delete {filename}")
        if not commit_hash:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        state.delete_resource(filename)
        await events.status(f"🗑️ {filename} supprimé (commit {commit_hash[:7]}).", done=True)
        return wrap_tool_output(text=f"Fichier `{filename}` supprimé.\n- Commit : `{commit_hash[:12]}`", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
        """Lecture du contenu d'un fichier Codex. Paramètres optionnels de plage (start_line/end_line).
        :param filename: Fichier cible.
        :param start_line: (Optionnel) Ligne de début (1-indexed).
        :param end_line: (Optionnel) Ligne de fin (inclusive).
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = repo.read_file(filename, start_line, end_line)
        if not result:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        lang = CodexRepo.detect_language(filename)
        range_info = f"lignes {result['range'][0]}-{result['range'][1]}" if result["range"] else "complet"

        return wrap_tool_output(
            text=f"**{filename}** ({lang}, {result['total_lines']} lignes total, {range_info})\n\n```{lang}\n{result['content']}\n```", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
        """Recherche d'un pattern dans un fichier Codex (littéral ou regex).
        :param query: Motif de recherche.
        :param is_regex: (Bool) Interprétation regex du motif.
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        matches = repo.search_in_file(filename, query, is_regex)
        if not matches:
            return wrap_tool_output(text=f"Aucun résultat pour `{query}` dans `{filename}`.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        lines_text = "\n".join(
            f"L{m['line_number']:>4}: {m['line_content']}"
            for m in matches
        )
        return wrap_tool_output(
            text=f"**{len(matches)} résultat(s)** pour `{query}` dans `{filename}` :\n\n```\n{lines_text}\n```", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def summarize_codex(
        self,
        filename: str,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """Résumé technique structuré d'un fichier Codex par distillation Gemini."""
        events = EchoEvents(__event_emitter__, __event_call__)
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        result = repo.read_file(filename)
        if not result:
            return wrap_tool_output(text=f"❌ Fichier `{filename}` introuvable.", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

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
            text=f"## Synthèse — {filename} ({lang}, {result['total_lines']} lignes)\n\n{summary}", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def list_codex(
        self,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """Liste tous les fichiers du Codex de la session courante."""
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        files = repo.list_files()
        if not files:
            return wrap_tool_output(text="Le Codex de cette conversation est vide.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        lines = ["| Fichier | Langage | Lignes | Taille |"]
        lines.append("|---|---|---|---|")
        for f in files:
            size_kb = f["size_bytes"] / 1024
            lines.append(f"| `{f['filename']}` | {f['lang']} | {f['lines']} | {size_kb:.1f} Ko |")

        stats = repo.get_repo_stats()
        footer = f"\n**{stats['total_files']} fichiers**, {stats['total_commits']} commits"
        if stats["last_commit_message"]:
            footer += f", dernier : _{stats['last_commit_message']}_"

        return wrap_tool_output(text="\n".join(lines) + footer, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

    async def history_codex(
        self,
        filename: Optional[str] = None,
        limit: int = 20,
        __user__: dict = {},
        __metadata__: dict = {},
        __event_emitter__: Any = None,
        __event_call__: Any = None,
    ) -> str:
        """
        Affiche l'historique Git du Codex (global ou par fichier).
        :param filename: (Optionnel) Nom du fichier pour filtrer l'historique.
        :param limit: (Optionnel) Nombre maximum de commits à retourner. Le Modèle doit limiter (Maximum conseillé: 100) pour éviter la surcharge cognitive.
        """
        uid, cid, repo, state = self._get_context(__user__, __metadata__)
        if not repo:
            return wrap_tool_output(text="❌ Contexte manquant (chat_id).", status={"status": "error"}, user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        log = repo.get_log(filename=filename, limit=limit)
        if not log:
            scope = f"de `{filename}`" if filename else "du Codex"
            return wrap_tool_output(text=f"Aucun historique {scope}.", user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)

        import time as time_mod
        lines = []
        for entry in log:
            dt = time_mod.strftime("%Y-%m-%d %H:%M", time_mod.localtime(entry["timestamp"]))
            lines.append(f"- `{entry['hash']}` — {entry['message']} ({dt})")

        scope = f"de `{filename}`" if filename else "global"
        return wrap_tool_output(
            text=f"## Historique {scope} ({len(log)} commits)\n\n" + "\n".join(lines), user_id=__user__.get("id", "system") if __user__ else "system", chat_id=__metadata__.get("chat_id") if __metadata__ else None, metadata=__metadata__)
