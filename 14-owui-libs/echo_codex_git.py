"""
title: ECHO Codex Git Engine
author: Wilfried BARNAVON
version: 5.231.2.1
description: Composant système interne : ECHO Codex Git Engine.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.231.2.1: Filtrage SSoT strict dans get_user_codex_chats excluant les conversations orphelines supprimées de webui.db.
# 5.230.0.1: Délégation factorisée de _secure_path vers normalize_codex_rel_path (echo_paths) avec décodage URL défensif (unquote).
# 5.229.7.2: Résolution des SHA courts (resolve_commit) et traversée hiérarchique de Tree (lookup_path) pour read/restore/diff/log.
# 5.229.7.1: Support Multi-Chat sélectif (découverte SSoT, UI hiérarchique, Read-Only strict et tooling transverse).
# 5.228.8.2: Correction de commit_file (préservation LF stricte newline="" et textes Unicode hors plage Latin-1) et auto-réparation du décodage UTF-8/Latin-1 dans read_file.
# Couche pure, testable, sans dépendance OWUI/LLM/events.
# desc.

import os
import re
import shutil
from typing import Optional, List, Any

from dulwich.repo import Repo
from dulwich.objects import Tree, Commit
from dulwich import porcelain

import echo_constants
from echo_constants import (
    CODEX_LANG_MAP,
    CODEX_DEFAULT_LANG,
    ECHO_SYNC_EXCLUDE_LIST,
    is_binary_file,
    ECHO_USERS_ROOT,
    ECHO_BASE_DATA_DIR,
)
from echo_paths import get_echo_session_path, normalize_codex_rel_path


class CodexRepo:
    """Gestionnaire Git (dulwich) pour un dépôt Codex user/chat.
    Un dépôt isolé par couple (user_id, chat_id).
    Toutes les opérations sont synchrones et sans effet de bord réseau."""

    def __init__(self, user_id: str, chat_id: str, workspace: str = "main"):
        base_codex = get_echo_session_path(user_id, chat_id, "codex")
        self.workspace = workspace
        self.repo_path = os.path.join(base_codex, workspace)
        os.makedirs(self.repo_path, exist_ok=True)
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

    def _secure_path(self, path: str) -> str:
        """Sécurise un chemin relatif, décode les séquences URL éventuelles, retire les préfixes de workspace et empêche le path traversal."""
        raw = (path or "").replace("\\", "/").strip("/")
        if ".." in raw.split("/"):
            raise ValueError("Path traversal non autorisé.")
        ws = getattr(self, "workspace", "") or ""
        rp = getattr(self, "repo_path", "") or ""
        cleaned = normalize_codex_rel_path(raw, workspace=ws, repo_path=rp)
        if cleaned is None:
            if not raw:
                return ""
            raise ValueError("Path traversal non autorisé.")
        return cleaned

    def create_directory(self, path: str) -> None:
        """Crée un répertoire vide (non tracké par Git mais visible dans l'UI)."""
        safe_name = self._secure_path(path)
        dirpath = os.path.join(self.repo_path, safe_name)

        if os.path.exists(dirpath) and not os.path.isdir(dirpath):
            raise FileExistsError(
                f"Impossible de créer le dossier '{path}'. Un fichier porte déjà ce nom.")

        os.makedirs(dirpath, exist_ok=True)

    def commit_file(self, filename: str, content: str, message: str,
                    author: str = "ECHO Codex", is_binary: Optional[bool] = None) -> Optional[str]:
        """Crée ou met à jour un fichier, avec création automatique des sous-dossiers. Retourne le hash du commit."""
        safe_name = self._secure_path(filename)
        filepath = os.path.join(self.repo_path, safe_name)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        latin1_bytes = None
        if isinstance(content, str):
            try:
                latin1_bytes = content.encode("latin-1")
            except UnicodeEncodeError:
                latin1_bytes = None
                is_binary = False

            if is_binary is None:
                probe = b"\x00" if "\x00" in content else b""
                is_binary, _ = is_binary_file(safe_name, probe)
        elif isinstance(content, (bytes, bytearray)):
            if is_binary is None:
                is_binary, _ = is_binary_file(safe_name, bytes(content[:8192]))

        if is_binary and latin1_bytes is not None:
            with open(filepath, "wb") as f:
                f.write(latin1_bytes)
        elif is_binary and isinstance(content, (bytes, bytearray)):
            with open(filepath, "wb") as f:
                f.write(content)
        else:
            with open(filepath, "w", encoding="utf-8", newline="") as f:
                f.write(content if isinstance(content, str) else content.decode("utf-8", errors="replace"))

        porcelain.add(self.repo_path, paths=[safe_name])
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=f"{author} <codex@echo.local>".encode("utf-8"),
            committer=f"{author} <codex@echo.local>".encode("utf-8"),
        )
        return commit_sha.decode("ascii") if isinstance(
            commit_sha, bytes) else str(commit_sha)

    def read_file(self, filename: str, start_line: int = None,
                  end_line: int = None) -> Optional[dict]:
        """Lit un fichier. Retourne {content, total_lines, range, is_binary, mime, size_bytes} ou None."""
        safe_name = self._secure_path(filename)
        filepath = os.path.join(self.repo_path, safe_name)
        if not os.path.isfile(filepath):
            return None

        with open(filepath, "rb") as f:
            raw_bytes = f.read()

        is_bin, mime = is_binary_file(filepath, raw_bytes)
        if is_bin:
            return {
                "content": raw_bytes.decode("latin-1"),
                "total_lines": (len(raw_bytes) + 15) // 16,
                "range": None,
                "is_binary": True,
                "mime": mime,
                "size_bytes": len(raw_bytes),
            }

        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            text_content = raw_bytes.decode("latin-1")
        lines = text_content.splitlines(True)
        total = len(lines)

        if start_line is not None and end_line is not None:
            # 1-indexed, inclusif
            s = max(1, start_line) - 1
            e = min(total, end_line)
            content = "".join(lines[s:e])
            return {
                "content": content,
                "total_lines": total,
                "range": [s + 1, e],
                "is_binary": False,
                "mime": mime,
                "size_bytes": len(raw_bytes),
            }

        return {
            "content": text_content,
            "total_lines": total,
            "range": None,
            "is_binary": False,
            "mime": mime,
            "size_bytes": len(raw_bytes),
        }

    def delete_file(self, path: str, message: str) -> Optional[tuple[str, list[str]]]:
        """Supprime un fichier ou un dossier vide et commit. Retourne le (hash, liste_fichiers_supprimes) ou None."""
        safe_name = self._secure_path(path)
        target_path = os.path.join(self.repo_path, safe_name)
        if not os.path.exists(target_path):
            return None

        import shutil
        paths_to_rm = []
        if os.path.isdir(target_path):
            for root, _, files in os.walk(target_path):
                for f in files:
                    rel_path = os.path.relpath(os.path.join(root, f), self.repo_path)
                    paths_to_rm.append(rel_path.replace('\\', '/'))
            shutil.rmtree(target_path)
        else:
            os.remove(target_path)
            paths_to_rm = [safe_name.replace('\\', '/')]

        if paths_to_rm:
            try:
                porcelain.rm(self.repo_path, paths=paths_to_rm)
            except BaseException:
                pass
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=b"ECHO Codex <codex@echo.local>",
            committer=b"ECHO Codex <codex@echo.local>",
        )
        commit_str = commit_sha.decode("ascii") if isinstance(
            commit_sha, bytes) else str(commit_sha)
        return commit_str, paths_to_rm

    def rename_file(self, old_name: str, new_name: str, message: str,
                    author: str = "ECHO Codex") -> Optional[str]:
        """Renomme un fichier ou un dossier dans le dépôt. Retourne le hash du commit ou None."""
        safe_old = self._secure_path(old_name)
        safe_new = self._secure_path(new_name)
        old_path = os.path.join(self.repo_path, safe_old)
        new_path = os.path.join(self.repo_path, safe_new)
        os.makedirs(os.path.dirname(new_path), exist_ok=True)

        if not os.path.exists(old_path) or safe_old == safe_new:
            return None
        if os.path.exists(new_path):
            return None  # Nom déjà pris

        paths_to_rm = []
        if os.path.isdir(old_path):
            for root, _, files in os.walk(old_path):
                for f in files:
                    rel_path = os.path.relpath(os.path.join(root, f), self.repo_path)
                    paths_to_rm.append(rel_path.replace('\\', '/'))
        else:
            paths_to_rm = [safe_old.replace('\\', '/')]

        os.rename(old_path, new_path)

        paths_to_add = []
        if os.path.isdir(new_path):
            for root, _, files in os.walk(new_path):
                for f in files:
                    rel_path = os.path.relpath(os.path.join(root, f), self.repo_path)
                    paths_to_add.append(rel_path.replace('\\', '/'))
        else:
            paths_to_add = [safe_new.replace('\\', '/')]

        if paths_to_rm:
            try:
                porcelain.rm(self.repo_path, paths=paths_to_rm)
            except BaseException:
                pass
                
        if paths_to_add:
            try:
                porcelain.add(self.repo_path, paths=paths_to_add)
            except BaseException:
                pass
        commit_sha = porcelain.commit(
            self.repo_path,
            message=message.encode("utf-8"),
            author=f"{author} <codex@echo.local>".encode("utf-8"),
            committer=f"{author} <codex@echo.local>".encode("utf-8"),
        )
        return commit_sha.decode("ascii") if isinstance(
            commit_sha, bytes) else str(commit_sha)

    def list_directory(self, target_rel_path: str = "") -> List[dict]:
        """Retourne uniquement le contenu immédiat (profondeur 1) d'un dossier.
        Pour le workspace 'main' (tracké), analyse l'index Git.
        Pour la 'sandbox', utilise os.scandir."""
        files = []
        target_abs_path = os.path.join(self.repo_path, target_rel_path)
        
        if not os.path.exists(target_abs_path) or not os.path.isdir(target_abs_path):
            return files

        is_sandbox = os.path.basename(self.repo_path.rstrip("/\\")) == "sandbox"

        if is_sandbox:
            for entry in os.scandir(target_abs_path):
                if entry.name.startswith(".") or entry.name in ECHO_SYNC_EXCLUDE_LIST:
                    continue
                
                rel_path = os.path.relpath(entry.path, self.repo_path).replace("\\", "/")
                is_dir = entry.is_dir()
                try:
                    stat = entry.stat()
                    size = stat.st_size if not is_dir else 0
                    mtime = stat.st_mtime
                except Exception:
                    size = 0
                    mtime = 0

                line_count = 0
                is_bin = False
                if not is_dir:
                    try:
                        is_bin, _ = is_binary_file(entry.path)
                        if is_bin:
                            line_count = (size + 15) // 16
                        else:
                            with open(entry.path, "r", encoding="utf-8", errors="replace") as fh:
                                line_count = sum(1 for _ in fh)
                    except Exception:
                        pass

                files.append({
                    "filename": rel_path,
                    "type": "directory" if is_dir else "file",
                    "lang": "folder" if is_dir else self.detect_language(entry.name),
                    "lines": line_count,
                    "size_bytes": size,
                    "mtime": mtime,
                    "is_binary": is_bin,
                })
        else:
            # Workspace MAIN (tracké Git)
            try:
                index = self.repo.open_index()
                tracked_files = {f.decode("utf-8") for f in index}
            except Exception:
                tracked_files = set()

            # Normaliser target_rel_path
            prefix = "" if not target_rel_path or target_rel_path == "." else target_rel_path.rstrip("/") + "/"
            
            # Pour extraire les dossiers de profondeur 1, on utilise un set pour dédupliquer
            dirs_found = set()
            
            for tracked_file in tracked_files:
                if not tracked_file.startswith(prefix):
                    continue
                    
                # Reste du chemin après le prefix
                remainder = tracked_file[len(prefix):]
                if not remainder:
                    continue
                    
                parts = remainder.split("/")
                
                # Fichier immédiat
                if len(parts) == 1:
                    filepath = os.path.join(self.repo_path, tracked_file)
                    if not os.path.isfile(filepath):
                        continue
                    is_bin = False
                    try:
                        size = os.path.getsize(filepath)
                        mtime = os.path.getmtime(filepath)
                        is_bin, _ = is_binary_file(filepath)
                        if is_bin:
                            line_count = (size + 15) // 16
                        else:
                            with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
                                line_count = sum(1 for _ in fh)
                    except BaseException:
                        line_count = 0
                        size = 0
                        mtime = 0

                    files.append({
                        "filename": tracked_file,
                        "type": "file",
                        "lang": self.detect_language(tracked_file),
                        "lines": line_count,
                        "size_bytes": size,
                        "mtime": mtime,
                        "is_binary": is_bin,
                    })
                else:
                    # Dossier immédiat
                    immediate_dir = prefix + parts[0]
                    if immediate_dir not in dirs_found:
                        dirs_found.add(immediate_dir)
                        dir_abs_path = os.path.join(self.repo_path, immediate_dir)
                        try:
                            mtime = os.path.getmtime(dir_abs_path)
                        except Exception:
                            mtime = 0
                        
                        files.append({
                            "filename": immediate_dir,
                            "type": "directory",
                            "lang": "folder",
                            "lines": 0,
                            "size_bytes": 0,
                            "mtime": mtime,
                        })

            # Inclure également les dossiers physiques vides créés via l'UI (non indexés par Git)
            try:
                for entry in os.scandir(target_abs_path):
                    if not entry.is_dir() or entry.name.startswith(".") or entry.name in ECHO_SYNC_EXCLUDE_LIST:
                        continue
                    immediate_dir = prefix + entry.name
                    if immediate_dir not in dirs_found:
                        dirs_found.add(immediate_dir)
                        try:
                            mtime = entry.stat().st_mtime
                        except Exception:
                            mtime = 0
                        files.append({
                            "filename": immediate_dir,
                            "type": "directory",
                            "lang": "folder",
                            "lines": 0,
                            "size_bytes": 0,
                            "mtime": mtime,
                        })
            except Exception:
                pass

        return sorted(files, key=lambda x: x["mtime"], reverse=True)

    def list_files(self) -> List[dict]:
        """Amorce le chargement Lazy Loading en ne retournant que la racine (profondeur 1)."""
        return self.list_directory("")

    def _get_text_content(self, filename: str, commit_hash: str = None) -> str:
        """Helper interne : retourne le contenu d'un fichier (HEAD ou commit), ou une chaîne vide."""
        if commit_hash:
            return self.get_file_at_commit(filename, commit_hash) or ""
        else:
            result = self.read_file(filename)
            return result["content"] if result else ""

    def _find_lines(self, content: str, pattern: str, is_regex: bool = False) -> List[str]:
        """Helper interne : retourne uniquement les lignes contenant le pattern (ou matchant la regex)."""
        lines = content.splitlines()
        if not is_regex:
            return [l for l in lines if pattern in l]
        try:
            return [l for l in lines if re.search(pattern, l)]
        except re.error:
            # Fallback automatique si la regex est invalide
            return [l for l in lines if pattern in l]

    def search_in_file(self, filename: str, pattern: str,
                       is_regex: bool = False, commit_hash: str = None) -> List[dict]:
        """Permet de rechercher une occurrence textuelle ou une expression régulière au sein d'un fichier du Codex.
        Supporte l'investigation spatio-temporelle : si un identifiant de commit est fourni, la recherche s'opère sur la révision historique correspondante au lieu du fichier actif.
        """
        content = self._get_text_content(filename, commit_hash)
        if not content:
            return []

        matches = []
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            try:
                match = re.search(pattern, line) if is_regex else (pattern in line)
            except re.error:
                match = (pattern in line)
                
            if match:
                matches.append({"line_number": i, "line_content": line})
                if len(matches) >= 50:
                    break
        return matches

    def trace_history(self, query: str, target_paths: List[str] = None, limit: int = 50) -> List[dict]:
        """Traverse l'historique de versionnement du Codex pour trouver les itérations où `query` a été ajouté ou supprimé."""
        entries = []
        blob_cache = {} 
        try:
            walker = self.repo.get_walker(max_entries=limit * 5)
            for walk_entry in walker:
                commit = walk_entry.commit
                c_id = commit.id.decode("ascii")
                p_id = commit.parents[0].decode("ascii") if commit.parents else None
                
                all_files = [f["filename"] for f in self.list_files() if f["type"] == "file"]
                if target_paths:
                    files_to_check = [f for f in all_files if any(f.startswith(p) for p in target_paths)]
                else:
                    files_to_check = all_files
                
                commit_diffs = []
                for f in files_to_check:
                    cache_key_curr = f"{c_id}:{f}"
                    cache_key_prev = f"{p_id}:{f}" if p_id else None
                    
                    curr_content = blob_cache.pop(cache_key_curr, None)
                    if curr_content is None:
                        curr_content = self._get_text_content(f, c_id)
                        
                    prev_content = self._get_text_content(f, p_id) if p_id else ""
                    if p_id:
                        blob_cache[cache_key_prev] = prev_content
                        
                    curr_lines = self._find_lines(curr_content, query, is_regex=False)
                    prev_lines = self._find_lines(prev_content, query, is_regex=False)
                    
                    if curr_lines != prev_lines:
                        added = [l for l in curr_lines if l not in prev_lines]
                        removed = [l for l in prev_lines if l not in curr_lines]
                        if added or removed:
                            commit_diffs.append({"file": f, "added": added, "removed": removed})
                        
                if commit_diffs:
                    msg = commit.message.decode("utf-8", errors="replace").strip()
                    entries.append({"hash": c_id[:12], "message": msg, "diffs": commit_diffs})
                    
                if len(entries) >= limit:
                    break
        except Exception:
            pass
        return entries

    # =========================================================================
    # HISTORIQUE GIT
    # =========================================================================

    def get_log(self, filename: str = None, limit: int = 20) -> List[dict]:
        """Retourne l'historique des commits. Si filename, filtré pour ce fichier."""
        entries = []
        try:
            walker = self.repo.get_walker(
                max_entries=limit * 3)  # Marge pour le filtrage
            for walk_entry in walker:
                commit = walk_entry.commit
                msg = commit.message.decode("utf-8", errors="replace").strip()
                ts = commit.commit_time
                sha = commit.id.decode("ascii")

                if filename:
                    # Filtrage : vérifier si le fichier est modifié dans ce
                    # commit
                    safe_name = self._secure_path(filename)
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

    def resolve_commit(self, commit_ref: str) -> Optional[Any]:
        """Résout une référence de commit (SHA-1 complet 40 caractères, SHA court 7..39 caractères)."""
        if not commit_ref:
            return None
        ref_str = str(commit_ref).strip()
        if len(ref_str) == 40:
            try:
                obj = self.repo[ref_str.encode("ascii")]
                if isinstance(obj, Commit):
                    return obj
            except KeyError:
                pass
        try:
            walker = self.repo.get_walker()
            for entry in walker:
                sha = entry.commit.id.decode("ascii")
                if sha.startswith(ref_str):
                    return entry.commit
        except Exception:
            pass
        return None

    def _file_in_commit(self, commit, filename: str) -> bool:
        """Vérifie si un fichier existe dans l'arbre d'un commit (y compris dans les sous-dossiers)."""
        try:
            safe_name = self._secure_path(filename)
            tree = self.repo[commit.tree]
            _, _ = tree.lookup_path(self.repo.get_object, safe_name.encode("utf-8"))
            return True
        except (KeyError, Exception):
            return False

    def get_diff(self, commit_a: str, commit_b: str = None) -> str:
        """Diff entre deux commits. Si commit_b=None, diff commit_a vs son parent."""
        try:
            from dulwich.patch import write_tree_diff
            import io

            obj_a = self.resolve_commit(commit_a)
            if not obj_a:
                return f"Erreur diff: commit '{commit_a}' introuvable."

            if commit_b:
                obj_b = self.resolve_commit(commit_b)
                if not obj_b:
                    return f"Erreur diff: commit '{commit_b}' introuvable."
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

    def get_file_at_commit(
            self,
            filename: str,
            commit_hash: str) -> Optional[str]:
        """Lit le contenu d'un fichier à un commit donné (checkout virtuel via SHA complet ou court)."""
        try:
            commit = self.resolve_commit(commit_hash)
            if not commit:
                return None

            safe_name = self._secure_path(filename)
            root_tree = self.repo[commit.tree]

            try:
                _, sha = root_tree.lookup_path(self.repo.get_object, safe_name.encode("utf-8"))
            except KeyError:
                return None

            blob = self.repo[sha]
            is_bin, _ = is_binary_file(safe_name, blob.data)
            if is_bin:
                return blob.data.decode("latin-1")
            try:
                return blob.data.decode("utf-8")
            except UnicodeDecodeError:
                return blob.data.decode("latin-1")
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

    def restore_repo_to_timestamp(self, cutoff_ts: int) -> dict:
        """Restaure le dépôt Git 'main' (working tree, index et HEAD) au dernier commit dont commit_time <= cutoff_ts.
        Retourne {'restored_commit': sha_or_none, 'tracked_files': set_of_rel_paths}."""
        try:
            commits = self.get_log(limit=500)
            if not commits:
                return {"restored_commit": None, "tracked_files": set()}

            # Sélection du commit le plus récent antérieur ou égal à cutoff_ts
            target_commit = None
            for c in commits:
                if int(c.get("timestamp", 0)) <= int(cutoff_ts):
                    target_commit = c
                    break

            # Nettoyage préalable de l'arbre de travail (hors .git)
            for entry in os.listdir(self.repo_path):
                if entry == ".git":
                    continue
                full_p = os.path.join(self.repo_path, entry)
                if os.path.isdir(full_p) and not os.path.islink(full_p):
                    shutil.rmtree(full_p)
                else:
                    os.remove(full_p)

            if not target_commit:
                # Aucun commit antérieur ou égal au point de fork : réinitialisation de l'index et des refs
                idx_path = self.repo.index_path()
                if os.path.exists(idx_path):
                    os.remove(idx_path)
                for ref in list(self.repo.refs.keys()):
                    if ref.startswith(b"refs/heads/"):
                        del self.repo.refs[ref]
                return {"restored_commit": None, "tracked_files": set()}

            from dulwich.index import build_index_from_tree
            sha_bytes = target_commit["hash_full"].encode("ascii")
            commit_obj = self.repo.object_store[sha_bytes]

            # Reconstruction de l'index et du working tree depuis l'arbre du commit cible
            build_index_from_tree(
                self.repo_path,
                self.repo.index_path(),
                self.repo.object_store,
                commit_obj.tree,
            )

            # Réalignement des références Git sur le commit cible
            for ref in list(self.repo.refs.keys()):
                if ref.startswith(b"refs/heads/"):
                    self.repo.refs[ref] = sha_bytes
            self.repo.refs[b"HEAD"] = sha_bytes

            index = self.repo.open_index()
            tracked = {f.decode("utf-8") for f in index}
            return {"restored_commit": target_commit["hash_full"], "tracked_files": tracked}
        except Exception as e:
            return {"restored_commit": None, "tracked_files": set(), "error": str(e)}

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
        except BaseException:
            pass

        return {
            "total_files": len(files),
            "total_commits": total_commits,
            "total_size_bytes": total_size,
            "last_commit_hash": log[0]["hash"] if log else None,
            "last_commit_message": log[0]["message"] if log else None,
        }

    def get_latest_state(self) -> str:
        """Retourne un identifiant d'état rapide (hash Git ou mtime) pour le ping."""
        is_sandbox = os.path.basename(self.repo_path.rstrip("/\\")) == "sandbox"
        if is_sandbox:
            try:
                max_mtime = os.path.getmtime(self.repo_path)
                for root, dirs, files in os.walk(self.repo_path):
                    for name in dirs + files:
                        mtime = os.path.getmtime(os.path.join(root, name))
                        if mtime > max_mtime:
                            max_mtime = mtime
                return str(max_mtime)
            except Exception:
                return "0"
        else:
            log = self.get_log(limit=1)
            return log[0]["hash"] if log else "empty"

    # =========================================================================
    # UTILITAIRES
    # =========================================================================

    @staticmethod
    def detect_language(filename: str) -> str:
        """Détecte le langage Monaco depuis l'extension du fichier."""
        ext = os.path.splitext(filename)[1].lower()
        return CODEX_LANG_MAP.get(ext, CODEX_DEFAULT_LANG)

    # =========================================================================
    # MULTI-CHAT & DÉCOUVERTE SOUVERAINE
    # =========================================================================

    @classmethod
    def validate_user_chat(cls, user_id: str, chat_id: str) -> bool:
        """Valide qu'un chat_id appartient bien à l'utilisateur et ne présente aucun risque de traversée."""
        if not user_id or not chat_id:
            return False
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        safe_cid = "".join(x for x in str(chat_id) if x.isalnum() or x in "-_")
        if not safe_uid or not safe_cid or safe_cid != str(chat_id).strip():
            return False
        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        chat_dir = os.path.join(users_root, safe_uid, "chats", safe_cid)
        return os.path.exists(chat_dir)

    @classmethod
    def get_user_codex_chats(cls, user_id: str, current_chat_id: Optional[str] = None) -> dict:
        """Découvre et résout les conversations de l'utilisateur ayant un Codex.
        Exclut strictement current_chat_id de la liste others.
        Retourne:
        {
            "current": {"id": current_chat_id, "title": <titre_actuel>},
            "others": [
                {"id": cid, "title": <titre>},  # Strictement != current_chat_id
            ]
        }
        """
        safe_uid = "".join(x for x in str(user_id) if x.isalnum() or x in "-_")
        users_root = getattr(echo_constants, "ECHO_USERS_ROOT", ECHO_USERS_ROOT)
        base_data_dir = getattr(echo_constants, "ECHO_BASE_DATA_DIR", ECHO_BASE_DATA_DIR)
        user_chats_dir = os.path.join(users_root, safe_uid, "chats")
        cur_cid = str(current_chat_id or "").strip()

        candidate_cids = set()
        if os.path.exists(user_chats_dir):
            with os.scandir(user_chats_dir) as entries:
                for entry in entries:
                    if entry.is_dir() and not entry.name.startswith("."):
                        codex_path = os.path.join(entry.path, "codex")
                        if os.path.exists(codex_path):
                            has_content = False
                            try:
                                for ws in ("main", "sandbox"):
                                    ws_dir = os.path.join(codex_path, ws)
                                    if os.path.exists(ws_dir):
                                        with os.scandir(ws_dir) as ws_entries:
                                            if any(ws_entries):
                                                has_content = True
                                                break
                            except Exception:
                                has_content = True
                            if has_content:
                                candidate_cids.add(entry.name)

        if cur_cid:
            candidate_cids.add(cur_cid)

        # Résolution atomique des titres depuis /app/backend/data/webui.db
        titles_map = {}
        valid_chat_ids = set()
        db_verified = False
        webui_db = os.path.join(base_data_dir, "webui.db")
        if os.path.exists(webui_db) and candidate_cids:
            import sqlite3
            try:
                conn = sqlite3.connect(f"file:{webui_db}?mode=ro", uri=True)
                cursor = conn.cursor()
                placeholders = ",".join("?" for _ in candidate_cids)
                cursor.execute(
                    f"SELECT id, title FROM chat WHERE user_id = ? AND id IN ({placeholders})",
                    [safe_uid] + list(candidate_cids)
                )
                for row in cursor.fetchall():
                    cid_db = str(row[0])
                    valid_chat_ids.add(cid_db)
                    title_db = (row[1] or "").strip()
                    titles_map[cid_db] = title_db or f"Chat {cid_db[:8]}"
                conn.close()
                db_verified = True
            except Exception:
                db_verified = False

        cur_title = titles_map.get(cur_cid, "Conversation Actuelle")
        others = []
        for cid in sorted(candidate_cids):
            if cid == cur_cid:
                continue  # EXCLUSION STRICTE DU CHAT ACTUEL

            # Si webui.db a été vérifié avec succès, exclure les conversations orphelines (supprimées de webui.db)
            if db_verified and cid not in valid_chat_ids:
                continue

            others.append({
                "id": cid,
                "title": titles_map.get(cid, f"Chat {cid[:8]}")
            })

        return {
            "current": {"id": cur_cid, "title": cur_title},
            "others": sorted(others, key=lambda x: x["title"].lower())
        }
