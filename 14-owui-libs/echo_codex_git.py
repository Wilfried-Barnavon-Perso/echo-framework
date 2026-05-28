"""
title: ECHO Codex Git Engine
author: Wilfried BARNAVON
version: 1.1
description: 1.0: Wrapper dulwich pour la gestion de dépôts Git par user/chat.
             Couche pure, testable, sans dépendance OWUI/LLM/events.
             1.1: Fix bytes.fromhex → encode('ascii') pour object_store dulwich.
"""

import os
import re
import shutil
import time
from typing import Optional, List

from dulwich.repo import Repo
from dulwich.objects import Blob, Tree, Commit
from dulwich import porcelain

from echo_constants import ECHO_USERS_ROOT, CODEX_DIR_NAME, CODEX_LANG_MAP, CODEX_DEFAULT_LANG


class CodexRepo:
    """Gestionnaire Git (dulwich) pour un dépôt Codex user/chat.
    Un dépôt isolé par couple (user_id, chat_id).
    Toutes les opérations sont synchrones et sans effet de bord réseau."""

    def __init__(self, user_id: str, chat_id: str):
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        self.repo_path = os.path.join(ECHO_USERS_ROOT, safe_uid, CODEX_DIR_NAME, safe_cid)
        self.repo = self._ensure_repo()

    def _ensure_repo(self) -> Repo:
        """Initialise le dépôt s'il n'existe pas, sinon l'ouvre."""
        if os.path.exists(os.path.join(self.repo_path, ".git")):
            return Repo(self.repo_path)
        os.makedirs(self.repo_path, exist_ok=True)
        return Repo.init(self.repo_path)

    # =========================================================================
    # CRUD FICHIERS
    # =========================================================================

    def commit_file(self, filename: str, content: str, message: str,
                    author: str = "ECHO Codex") -> str:
        """Écrit un fichier, l'ajoute au staging et commit.
        Crée ou met à jour le fichier. Retourne le hash du commit (hex)."""
        # Sécurisation du nom de fichier (pas de traversée de répertoire)
        safe_name = os.path.basename(filename)
        filepath = os.path.join(self.repo_path, safe_name)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        porcelain.add(self.repo_path, paths=[safe_name])
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=f"{author} <codex@echo.local>".encode("utf-8"),
            committer=f"{author} <codex@echo.local>".encode("utf-8"),
        )
        return commit_sha.decode("ascii") if isinstance(commit_sha, bytes) else str(commit_sha)

    def read_file(self, filename: str, start_line: int = None,
                  end_line: int = None) -> Optional[dict]:
        """Lit un fichier. Retourne {content, total_lines, range} ou None."""
        safe_name = os.path.basename(filename)
        filepath = os.path.join(self.repo_path, safe_name)
        if not os.path.exists(filepath):
            return None

        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        total = len(lines)

        if start_line is not None and end_line is not None:
            # 1-indexed, inclusif
            s = max(1, start_line) - 1
            e = min(total, end_line)
            content = "".join(lines[s:e])
            return {"content": content, "total_lines": total, "range": [s + 1, e]}

        return {"content": "".join(lines), "total_lines": total, "range": None}

    def delete_file(self, filename: str, message: str) -> Optional[str]:
        """Supprime un fichier et commit. Retourne le hash ou None."""
        safe_name = os.path.basename(filename)
        filepath = os.path.join(self.repo_path, safe_name)
        if not os.path.exists(filepath):
            return None

        porcelain.rm(self.repo_path, paths=[safe_name])
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=b"ECHO Codex <codex@echo.local>",
            committer=b"ECHO Codex <codex@echo.local>",
        )
        return commit_sha.decode("ascii") if isinstance(commit_sha, bytes) else str(commit_sha)

    def list_files(self) -> List[dict]:
        """Liste tous les fichiers trackés dans le working tree."""
        files = []
        if not os.path.exists(self.repo_path):
            return files

        for entry in os.listdir(self.repo_path):
            if entry.startswith("."):
                continue
            filepath = os.path.join(self.repo_path, entry)
            if not os.path.isfile(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                    line_count = sum(1 for _ in f)
                size = os.path.getsize(filepath)
            except:
                line_count = 0
                size = 0

            files.append({
                "filename": entry,
                "lang": self.detect_language(entry),
                "lines": line_count,
                "size_bytes": size,
            })
        return sorted(files, key=lambda x: x["filename"])

    def search_in_file(self, filename: str, pattern: str,
                       is_regex: bool = False) -> List[dict]:
        """Recherche un pattern dans un fichier. Retourne [{line_number, line_content}]."""
        result = self.read_file(filename)
        if not result:
            return []

        matches = []
        lines = result["content"].splitlines()
        for i, line in enumerate(lines, 1):
            try:
                if is_regex:
                    if re.search(pattern, line):
                        matches.append({"line_number": i, "line_content": line})
                else:
                    if pattern in line:
                        matches.append({"line_number": i, "line_content": line})
            except re.error:
                # Regex invalide, fallback en recherche littérale
                if pattern in line:
                    matches.append({"line_number": i, "line_content": line})

            if len(matches) >= 50:  # Cap pour éviter les résultats massifs
                break
        return matches

    # =========================================================================
    # HISTORIQUE GIT
    # =========================================================================

    def get_log(self, filename: str = None, limit: int = 20) -> List[dict]:
        """Retourne l'historique des commits. Si filename, filtré pour ce fichier."""
        entries = []
        try:
            walker = self.repo.get_walker(max_entries=limit * 3)  # Marge pour le filtrage
            for walk_entry in walker:
                commit = walk_entry.commit
                msg = commit.message.decode("utf-8", errors="replace").strip()
                ts = commit.commit_time
                sha = commit.id.decode("ascii")

                if filename:
                    # Filtrage : vérifier si le fichier est modifié dans ce commit
                    safe_name = os.path.basename(filename)
                    if not self._file_in_commit(commit, safe_name):
                        continue

                entries.append({
                    "hash": sha[:12],
                    "hash_full": sha,
                    "message": msg,
                    "author": commit.author.decode("utf-8", errors="replace"),
                    "timestamp": ts,
                })

                if len(entries) >= limit:
                    break
        except Exception:
            pass
        return entries

    def _file_in_commit(self, commit, filename: str) -> bool:
        """Vérifie si un fichier existe dans l'arbre d'un commit."""
        try:
            tree = self.repo.object_store[commit.tree]
            for item in tree.items():
                if item.path.decode("utf-8") == filename:
                    return True
        except:
            pass
        return False

    def get_diff(self, commit_a: str, commit_b: str = None) -> str:
        """Diff entre deux commits. Si commit_b=None, diff commit_a vs son parent."""
        try:
            from dulwich.diff_tree import tree_changes
            from dulwich.patch import write_tree_diff
            import io

            obj_a = self.repo.object_store[commit_a.encode("ascii")]

            if commit_b:
                obj_b = self.repo.object_store[commit_b.encode("ascii")]
                tree_b = obj_b.tree
            else:
                # Parent du commit_a
                if obj_a.parents:
                    parent = self.repo.object_store[obj_a.parents[0]]
                    tree_b = parent.tree
                else:
                    tree_b = None  # Premier commit, pas de parent

            buf = io.BytesIO()
            write_tree_diff(buf, self.repo.object_store,
                            tree_b if tree_b else Tree().id,
                            obj_a.tree)
            return buf.getvalue().decode("utf-8", errors="replace")
        except Exception as e:
            return f"Erreur diff: {e}"

    def get_file_at_commit(self, filename: str, commit_hash: str) -> Optional[str]:
        """Lit le contenu d'un fichier à un commit donné (checkout virtuel)."""
        try:
            safe_name = os.path.basename(filename)
            commit = self.repo.object_store[commit_hash.encode("ascii")]
            tree = self.repo.object_store[commit.tree]

            for item in tree.items():
                if item.path.decode("utf-8") == safe_name:
                    blob = self.repo.object_store[item.sha]
                    return blob.data.decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def get_file_history_index(self, filename: str) -> List[dict]:
        """Retourne la liste ordonnée des commits touchant un fichier (pour navigation ◀ ▶).
        Index 0 = commit le plus ancien, dernier index = HEAD."""
        log = self.get_log(filename=filename, limit=500)
        log.reverse()  # Ordre chronologique (ancien → récent)
        return log

    # =========================================================================
    # ADMINISTRATION
    # =========================================================================

    def reset_all(self) -> bool:
        """Supprime entièrement le dépôt. Irréversible."""
        try:
            if os.path.exists(self.repo_path):
                shutil.rmtree(self.repo_path)
                self.repo = None
            return True
        except Exception:
            return False

    def get_repo_stats(self) -> dict:
        """Statistiques du dépôt."""
        files = self.list_files()
        log = self.get_log(limit=1)
        total_size = sum(f["size_bytes"] for f in files)
        total_commits = 0
        try:
            total_commits = len(list(self.repo.get_walker()))
        except:
            pass

        return {
            "total_files": len(files),
            "total_commits": total_commits,
            "total_size_bytes": total_size,
            "last_commit_hash": log[0]["hash"] if log else None,
            "last_commit_message": log[0]["message"] if log else None,
        }

    # =========================================================================
    # UTILITAIRES
    # =========================================================================

    @staticmethod
    def detect_language(filename: str) -> str:
        """Détecte le langage Monaco depuis l'extension du fichier."""
        ext = os.path.splitext(filename)[1].lower()
        return CODEX_LANG_MAP.get(ext, CODEX_DEFAULT_LANG)
