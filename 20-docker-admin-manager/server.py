# -*- coding: utf-8 -*-
"""
================================================================================
MODULE : ECHO ADMIN MANAGER SERVER
VERSION : 5.73 (Suppression de profil Rclone)
--- CHANGELOG 5.66 ---
- Correctif critique : load_maint_config() utilisait une copie superficielle (copy()). Les dicts imbriqués
  (retention, memory_ttl, consolidation) étaient des références partagées vers DEFAULT_MAINT_CONFIG,
  ce qui mutait silencieusement les défauts en mémoire à chaque sauvegarde. Remplacé par copy.deepcopy().
- Correctif : save_maint_config() swallowait toute exception sans log. Corrigé pour logger l'erreur
  et retourner un booléen de succès, permettant au flash de refléter l'état réel de l'opération.
- Correctif UI : Les checkboxes 'purge orphelins' n'avaient pas de liaison id/for entre input et label.
  Le clic sur le texte ne toggleait pas le switch. De plus, le data-bs-toggle="tooltip" sur le label
  interceptait le clic. Tooltip déplacé sur l'input, liaison id/for ajoutée.
AUTEUR : Wilfried BARNAVON
DATE MAJ : 2026-05-30

--- DESCRIPTION ARCHITECTURALE ---
Ce micro-service assure la régulation et le monitoring du framework ECHO.
Architecture ECHO-Native avec distinction entre stockage et sessions.

--- CHANGELOG 5.63 ---
- Nettoyage : Suppression du provider SFTP du wizard Rclone (doublon avec le SFTP natif paramiko).
--- CHANGELOG 5.62 ---
- UI : Refonte complète du style des tuiles CPU et RAM en Flexbox haut de gamme pour corriger le bug d'alignement du symbole %.
--- CHANGELOG 5.61 ---
- Correctif : Remplacement d'orjson par json standard pour restaurer la sauvegarde des configurations.
- UI : Ajout de la fonction JS manquante toggleWizardFields pour l'assistant Rclone.
--- CHANGELOG 5.60 ---
- Qualité : Audit complet du code. Suppression du code mort, correction des imports,
  remplacement des bare except:, suppression de shell=True, whitelist providers Rclone.
--- CHANGELOG 5.59 ---
- Backups : Externalisation native (SFTP et Rclone/Google Drive) avec politiques de rétention séparées pour le Cloud.
- UI : Fenêtre modale de configuration des exports avec champs dynamiques.
--- CHANGELOG 5.58 ---
- Sécurité : Exclusion stricte du sous-dossier `codex` de la Purge Temporelle du Vault.
--- CHANGELOG 5.57 ---
- UX/UI : Refonte du Dashboard en grille dynamique (Gridstack.js). Tuiles redimensionnables, déplaçables et auto-ajustables.
- Persistance : Sauvegarde de la disposition locale dans le localStorage du navigateur client avec possibilité de réinitialisation.
--- CHANGELOG 5.56 ---
- Sauvegarde : Inclusion de la base vectorielle Qdrant.
- Optimisation : Passage à la compression XZ (LZMA) pour des archives nettement plus petites.
- Rétrocompatibilité : Les anciennes archives tar.gz restent supportées à la restauration.
--- CHANGELOG 5.55 ---
- UX : Ajout de labels sémantiques explicites (Trivial, Mineur, Utile, Majeur, Axiome) au-dessus des champs de rétention TTL pour une meilleure lisibilité administrative.
--- CHANGELOG 5.51 ---
- UX : Ajout de champs de configuration dans le Dashboard pour personnaliser les durées de rétention (Purge Temporelle des Souvenirs) des souvenirs de la base vectorielle (Niveaux 1 à 5).
--- CHANGELOG 5.50 ---
- Optimisation : Centralisation du processus de Purge Temporelle des Souvenirs de la base vectorielle dans l'Admin Manager pour alléger le traitement temps-réel du filtre conversationnel.
--- CHANGELOG 5.41 ---
- Correctif : Restauration des constantes QDRANT_URL et COLLECTION_MEMORY.
- UX : Ajout du label "Logs" sur le bouton d'historique de maintenance.
--- CHANGELOG 5.40 ---
- Ajout : Synchronisation automatique de la base vectorielle des souvenirs (Qdrant) pour éliminer les souvenirs orphelins (utilisateurs ou chats supprimés).
- Ajout : Historique d'audit persistant (1 an de rétention) affichable directement depuis l'interface UI.
--- CHANGELOG 5.30 ---
- Correction : Fallback robuste pour la copie du mot de passe Admin (support HTTP/Non-Secure).
- Ajout : Route API /api/backups pour le rafraîchissement dynamique.
- Ajout : Route /download/<filename> pour la récupération des sauvegardes.
- Sécurité : Vérification d'existence du fichier avant restauration destructive.
- Harmonisation : Usage strict de OWUI_ADMIN_SECRET_PATH.
--- CHANGELOG 5.25 ---
- Correction : Suppression définitive du décorateur @app.after_request orphelin (ligne 78).
--- CHANGELOG 5.24 ---
- Vérité Sémantique : Le compteur "Sessions Actives" ne cible plus que les .db de chats.
- Correction du décalage : Exclusion de identity.db et des fichiers docs du compteur de sessions.
- Maintien du Volume Global : Le volume Espace Personnel reste inclusif (docs + bases).
- Parité UX intégrale maintenue (Horloge, Tooltips, Monitoring).
================================================================================
"""

from flask import Flask, request, jsonify, render_template_string, send_file, redirect, url_for, flash, session # pyright: ignore[reportMissingImports]
from typing import Optional, List, Dict, Tuple
import os
import subprocess
import datetime
import glob
import secrets
import json
import time
import threading
import shutil
import sqlite3
import math
import uuid
import copy
from werkzeug.utils import secure_filename # pyright: ignore[reportMissingImports]

# ==============================================================================
# SECTION 1 : GESTION DES DÉPENDANCES
# ==============================================================================
try:
    import docker
    import paramiko
    DOCKER_AVAILABLE = True
except ImportError:
    DOCKER_AVAILABLE = False

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    HAS_SCHEDULER = True
except ImportError:
    HAS_SCHEDULER = False

try:
    import schedule
    HAS_MAINT_SCHEDULER = True
except ImportError:
    HAS_MAINT_SCHEDULER = False

# ==============================================================================
# SECTION 2 : CONFIGURATION & CHEMINS
# ==============================================================================
app = Flask(__name__, static_folder='/app/static')
app.secret_key = secrets.token_hex(32)
app.config['JSON_AS_ASCII'] = False

TARGET_CONTAINER = os.environ.get('TARGET_CONTAINER', 'echo-open-webui')
BACKUP_DIR = "/backups"
HOST_GATEWAY = "host.docker.internal"
SETTINGS_FILE = os.path.join(BACKUP_DIR, "settings.json")

OWUI_DATA_ROOT = "/app/backend/data"
ECHO_USERS_ROOT = os.path.join(OWUI_DATA_ROOT, "users") 
UPLOADS_DIR = os.path.join(OWUI_DATA_ROOT, "uploads")
WEBUI_DB_PATH = os.path.join(OWUI_DATA_ROOT, "webui.db")
RCLONE_CONF_DIR = os.path.join(OWUI_DATA_ROOT, "rclone")
RCLONE_CONF_PATH = os.path.join(RCLONE_CONF_DIR, "rclone.conf")
OWUI_ADMIN_SECRET_PATH = "/app/secrets/.owui-admin-secret"

QDRANT_URL = "http://echo-qdrant:6333"
COLLECTION_MEMORY = "echo_memory"
COLLECTION_EPHEMERAL = "echo_ephemeral"

DIRS = {
    "uploads": UPLOADS_DIR,
    "echo_vault": ECHO_USERS_ROOT,
    "debug_logs": os.path.join(OWUI_DATA_ROOT, "debug_logs"),
    "rclone": RCLONE_CONF_DIR
}

DEFAULT_BACKUP_CONFIG = {
    "auto_backup": True, "auto_cleanup": True, "cleanup_mode": "count",
    "cleanup_value": 5, "backup_time": "03:00", "interval_days": 1,
    "ext_mode": "local", "ext_cleanup_mode": "count", "ext_cleanup_value": 5,
    "sftp_host": "", "sftp_port": "22", "sftp_user": "", "sftp_pass": "", "sftp_path": "/backups/echo",
    "rclone_remote": ""
}

DEFAULT_MAINT_CONFIG = {
    "cleanup_hour": "03:00", "last_run": "Never",
    "retention": { "uploads_days": 1095, "vault_days": 1095 },
    "memory_ttl": { "lvl1": 30, "lvl2": 60, "lvl3": 180, "lvl4": 365, "lvl5": 540 },
    "consolidation": {
        "enabled": True,
        "trigger_threshold": 10,   # Nb de points lvl1 par user déclenchant la consolidation
        "min_cluster_size": 3,     # Nb minimum de points similaires pour fusionner
        "similarity_threshold": 0.75  # Score cosinus minimal pour appartenir à un cluster
    },
    "purge_orphaned_chats": False,
    "purge_orphaned_users": False
}

# ==============================================================================
# SECTION 3 : HELPERS & LOGIQUE RÉCURSIVE
# ==============================================================================

def human_size(size):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size < 1024: return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} PB"

def get_cpu_model_name():
    try:
        with open('/proc/cpuinfo') as f:
            for line in f:
                if "model name" in line: return line.split(':')[1].strip()
    except Exception: return "N/A"

def get_dir_stats(path, filter_ext=None):
    if not os.path.exists(path): return {"count": 0, "size": 0, "size_fmt": "0 B"}
    t_size, t_count = 0, 0
    for root, _, files in os.walk(path):
        for f in files:
            if filter_ext and not f.endswith(filter_ext): continue
            try:
                t_size += os.path.getsize(os.path.join(root, f))
                t_count += 1
            except Exception: pass
    return {"count": t_count, "size": t_size, "size_fmt": human_size(t_size)}

def prune_recursive(path: str, days: int, sanctuary_files: Optional[List[str]] = None):
    if sanctuary_files is None: sanctuary_files = ["identity.db"]
    if not os.path.exists(path): return 0
    cutoff = time.time() - (days * 86400)
    removed = 0
    for root, dirs, files in os.walk(path):
        if "codex" in dirs: dirs.remove("codex") # Empêche la traversée du dossier codex
        for f in files:
            if f in sanctuary_files: continue
            fpath = os.path.join(root, f)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    os.remove(fpath); removed += 1
            except Exception: pass
    return removed

def get_echo_version():
    try:
        if os.path.exists('/app/ECHO_VERSION'):
            with open('/app/ECHO_VERSION', 'r', encoding='utf-8') as f: return f.read().strip()
    except Exception: pass
    return "v?.?"

# ==============================================================================
# SECTION 4 : ÉLAGAGE SÉMANTIQUE & SÉCURITÉ
# ==============================================================================

def load_maint_config() -> dict:
    """
    Charge la config de maintenance depuis le fichier JSON.
    Utilise deepcopy pour éviter toute mutation silencieuse du dict DEFAULT_MAINT_CONFIG
    via des références partagées lors d'écritures ultérieures sur les sous-dicts (retention, etc.).
    """
    c = copy.deepcopy(DEFAULT_MAINT_CONFIG)
    m_file = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
    if os.path.exists(m_file):
        try:
            with open(m_file, 'r') as f:
                saved = json.load(f)
                # Fusion profonde des sous-dicts pour ne pas écraser les clés absentes du fichier
                for key, val in saved.items():
                    if isinstance(val, dict) and isinstance(c.get(key), dict):
                        c[key].update(val)
                    else:
                        c[key] = val
        except Exception as e:
            print(f"[ECHO-MAINT] ⚠️ Impossible de lire la config maintenance : {e}")
    return c

def save_maint_config(c: dict) -> bool:
    """Sauvegarde la config de maintenance. Retourne True si OK, False si erreur."""
    try:
        target = os.path.join(OWUI_DATA_ROOT, "maintenance_config.json")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, 'w') as f:
            json.dump(c, f, indent=4)
        return True
    except Exception as e:
        print(f"[ECHO-MAINT] ❌ Impossible de sauvegarder la config maintenance : {e}")
        return False

MAINT_HISTORY_FILE = os.path.join(OWUI_DATA_ROOT, "maintenance_history.json")

def load_maint_history():
    if os.path.exists(MAINT_HISTORY_FILE):
        try:
            with open(MAINT_HISTORY_FILE, 'r') as f: return json.load(f)
        except Exception: pass
    return []

def save_maint_report(report_str):
    history = load_maint_history()
    new_entry = {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "report": report_str
    }
    history.insert(0, new_entry)
    
    # Auto-purge de l'historique (Rétention 1 an / 365 entrées max)
    cutoff = time.time() - (365 * 86400)
    history = [h for h in history if time.mktime(time.strptime(h["timestamp"], "%Y-%m-%d %H:%M:%S")) > cutoff]
    
    try:
        with open(MAINT_HISTORY_FILE, 'w') as f: json.dump(history[:500], f, indent=4)
    except Exception: pass

def run_semantic_pruning():
    """Purge Temporelle des Souvenirs (v5.51 + Qdrant Sync & TTL)."""
    print("🧬 [ECHO-LIFECYCLE] Démarrage...")
    report = []
    config = load_maint_config()
    
    # 1. API Safeguard (Garde-fou)
    if HAS_HTTPX:
        try:
            admin_token = ""
            if os.path.exists(OWUI_ADMIN_SECRET_PATH):
                with open(OWUI_ADMIN_SECRET_PATH, "r") as f:
                    admin_token = f.read().strip()
            owui_url = os.environ.get("WEBUI_URL", "http://echo-open-webui:8080")
            r_auth = httpx.get(f"{owui_url}/api/v1/users/", headers={"Authorization": f"Bearer {admin_token}"}, timeout=5)
            if r_auth.status_code != 200:
                err_msg = f"API Safeguard: Auth OWUI échouée (HTTP {r_auth.status_code}). Pruning annulé."
                print(f"❌ {err_msg}")
                save_maint_report(err_msg)
                return err_msg
        except Exception as e:
            err_msg = f"API Safeguard: OWUI injoignable ({str(e)}). Pruning annulé."
            print(f"❌ {err_msg}")
            save_maint_report(err_msg)
            return err_msg

    # 2. Orphelins (Dossiers Utilisateurs et Mémoire Qdrant)
    orphans = 0
    qdrant_synced = False
    if os.path.exists(ECHO_USERS_ROOT):
        try:
            conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
            valid_ids = {str(row[0]) for row in conn.execute("SELECT id FROM user").fetchall()}
            try: db_valid_chats = {str(row[0]) for row in conn.execute("SELECT id FROM chat").fetchall()}
            except Exception: db_valid_chats = set()
            conn.close()
            
            # --- A. Purge des dossiers de l'Espace Personnel ---
            if config.get("purge_orphaned_users", False):
                for folder in os.listdir(ECHO_USERS_ROOT):
                    if folder not in valid_ids and len(folder) > 30:
                        shutil.rmtree(os.path.join(ECHO_USERS_ROOT, folder))
                        orphans += 1
            
            if config.get("purge_orphaned_chats", False):
                for folder in valid_ids:
                    user_chats_dir = os.path.join(ECHO_USERS_ROOT, folder, "chats")
                    if os.path.exists(user_chats_dir):
                        for cdir in os.listdir(user_chats_dir):
                            if os.path.isdir(os.path.join(user_chats_dir, cdir)):
                                chat_id = cdir
                                if chat_id not in db_valid_chats:
                                    shutil.rmtree(os.path.join(user_chats_dir, cdir))
                                    orphans += 1
            
            # --- B. Purge Temporelle des Souvenirs & Garbage Collection (Qdrant) ---
            if HAS_HTTPX and valid_ids:
                try:
                    # Test de disponibilité Qdrant
                    r = httpx.get(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}", timeout=5)
                    if r.status_code == 200:
                        # 1) Utilisateurs orphelins
                        if config.get("purge_orphaned_users", False):
                            httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", 
                                       json={"filter": {"must_not": [{"key": "user_id", "match": {"any": list(valid_ids)}}]}}, 
                                       timeout=30)
                            httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_EPHEMERAL}/points/delete", 
                                       json={"filter": {"must_not": [{"key": "user_id", "match": {"any": list(valid_ids)}}]}}, 
                                       timeout=30)
                        
                        # 2) Chats orphelins & TTL
                        now = int(time.time())
                        ttl_cfg = config.get("memory_ttl", DEFAULT_MAINT_CONFIG["memory_ttl"])
                        ttl_map = {
                            1: int(ttl_cfg.get("lvl1", 30)) * 86400,
                            2: int(ttl_cfg.get("lvl2", 60)) * 86400,
                            3: int(ttl_cfg.get("lvl3", 180)) * 86400,
                            4: int(ttl_cfg.get("lvl4", 365)) * 86400,
                            5: int(ttl_cfg.get("lvl5", 540)) * 86400
                        }
                        
                        for uid in valid_ids:
                            str_uid = str(uid)
                            # Purge Chats
                            user_chats_dir = os.path.join(ECHO_USERS_ROOT, str_uid, "chats")
                            physical_chats = []
                            if os.path.exists(user_chats_dir):
                                physical_chats = [f for f in os.listdir(user_chats_dir) if os.path.isdir(os.path.join(user_chats_dir, f))]
                            
                            if config.get("purge_orphaned_chats", False):
                                if not physical_chats:
                                    payload = {"filter": {"must": [{"key": "user_id", "match": {"value": str_uid}}]}}
                                else:
                                    payload = {
                                        "filter": {
                                            "must": [{"key": "user_id", "match": {"value": str_uid}}],
                                            "must_not": [{"key": "chat_id", "match": {"any": physical_chats}}]
                                        }
                                    }
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", json=payload, timeout=30)
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_EPHEMERAL}/points/delete", json=payload, timeout=30)
                            
                            # Decay TTL
                            for level, seconds in ttl_map.items():
                                decay_payload = {
                                    "filter": {
                                        "must": [
                                            {"key": "user_id", "match": {"value": str_uid}},
                                            {"key": "memory_importance", "match": {"value": level}},
                                            {"key": "timestamp", "range": {"lt": now - seconds}}
                                        ]
                                    }
                                }
                                httpx.post(f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete", json=decay_payload, timeout=30)
                        
                        qdrant_synced = True
                except Exception as e:
                    print(f"[ECHO-LIFECYCLE] ❌ Erreur Qdrant : {e}")
        except Exception as e:
            print(f"[ECHO-LIFECYCLE] ❌ Erreur DB/Espace Personnel : {e}")
        
    report_str = f"Orphelins: {orphans}"
    if qdrant_synced:
        report_str += " | Qdrant: Synchro (Chats/Users/Purge Temporelle) | RAG Éphémère Purgé"
    report.append(report_str)

    # 2. Atrophie
    rem_u = prune_recursive(UPLOADS_DIR, config["retention"]["uploads_days"])
    report.append(f"Élagage: {rem_u}")

    # 3. Vacuum
    vax = 0
    for root, _, files in os.walk(ECHO_USERS_ROOT):
        for f in files:
            if f.endswith('.db'):
                try:
                    with sqlite3.connect(os.path.join(root, f), timeout=10.0) as db:
                        db.execute("VACUUM;")
                        vax += 1
                except Exception: pass
    report.append(f"Optimisés: {vax}")

    final_report = " | ".join(report)
    config["last_run"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_maint_config(config)
    save_maint_report(final_report)
    return final_report

# ==============================================================================
# SECTION 4b : CONSOLIDATION DES SOUVENIRS LVL1 → LVL2 (Centroïde Vectoriel)
# ==============================================================================

def _cosine_sim(v1: list, v2: list) -> float:
    """Produit scalaire entre deux vecteurs normalisés L2 = similarité cosinus."""
    return sum(a * b for a, b in zip(v1, v2))

def _centroid(vectors: list) -> list:
    """
    Centroïde L2-normalisé d'une liste de vecteurs déjà normalisés.
    Représente le centre de gravité sémantique du cluster.
    Après re-normalisation, le vecteur est valide pour la recherche cosinus dans Qdrant.
    """
    dim = len(vectors[0])
    c = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
    norm = math.sqrt(sum(x**2 for x in c))
    return [x / norm for x in c] if norm > 0 else c

def consolidate_memories_for_user(user_id: str, config: dict) -> dict:
    """
    Consolide les souvenirs lvl1 d'un utilisateur en souvenirs lvl2 par clustering sémantique.

    Stratégie : clustering greedy sur les vecteurs récupérés via scroll (with_vectors=True),
    puis fusion par centroïde L2-normalisé des vecteurs du cluster.
    - Zéro appel au worker bge-m3 (vecteurs déjà disponibles).
    - Zéro appel Gemini (pas de dépendance aux clés API utilisateur).
    - Zéro troncature : le summary concatène l'intégralité des textes originaux.
    - Idémpotente : cible uniquement memory_importance=1.

    Retourne : {clusters_found, points_merged, points_promoted, points_deleted}
    """
    if not HAS_HTTPX:
        return {"error": "httpx non disponible"}

    consol_cfg       = config.get("consolidation", DEFAULT_MAINT_CONFIG["consolidation"])
    trigger_threshold = int(consol_cfg.get("trigger_threshold", 10))
    min_cluster_size  = int(consol_cfg.get("min_cluster_size", 3))
    sim_threshold     = float(consol_cfg.get("similarity_threshold", 0.75))
    report = {"clusters_found": 0, "points_merged": 0, "points_promoted": 0, "points_deleted": 0}

    try:
        # 1. COUNT : guard — évite de charger les vecteurs si pas assez de lvl1
        count_resp = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/count",
            json={"filter": {"must": [
                {"key": "user_id",          "match": {"value": user_id}},
                {"key": "memory_importance", "match": {"value": 1}}
            ]}}, timeout=10
        )
        if count_resp.status_code != 200:
            return report
        if count_resp.json().get("result", {}).get("count", 0) < trigger_threshold:
            return report  # Pas assez de lvl1 pour consolider

        # 2. SCROLL with_vectors=True — récupère les vecteurs pour le clustering local
        scroll_resp = httpx.post(
            f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/scroll",
            json={
                "filter": {"must": [
                    {"key": "user_id",          "match": {"value": user_id}},
                    {"key": "memory_importance", "match": {"value": 1}}
                ]},
                "limit": 200,
                "with_payload": True,
                "with_vectors": True   # Clé : zéro appel embedding pour le clustering
            }, timeout=30
        )
        if scroll_resp.status_code != 200:
            return report
        points = scroll_resp.json().get("result", {}).get("points", [])
        if not points:
            return report

        # 3. CLUSTERING GREEDY
        # Chaque point non assigné devient la graine d'un nouveau cluster.
        # Tous ses voisins avec cos ≥ sim_threshold le rejoignent.
        assigned, clusters = set(), []
        for p in points:
            if p["id"] in assigned or not p.get("vector"):
                continue
            cluster = [p]
            assigned.add(p["id"])
            for q in points:
                if q["id"] in assigned or not q.get("vector"):
                    continue
                if _cosine_sim(p["vector"], q["vector"]) >= sim_threshold:
                    cluster.append(q)
                    assigned.add(q["id"])
            if len(cluster) >= min_cluster_size:
                clusters.append(cluster)

        report["clusters_found"] = len(clusters)
        if not clusters:
            return report

        # 4. FUSION PAR CENTROÏDE
        for cluster in clusters:
            # Vecteur fusionné = centroïde L2-normalisé des vecteurs du cluster
            fused_vector = _centroid([pt["vector"] for pt in cluster])

            # Summary = concaténation complète (zéro troncature)
            summaries    = [pt["payload"].get("summary", "") for pt in cluster if pt.get("payload")]
            fused_summary = " | ".join(s for s in summaries if s)
            tags          = list({t for pt in cluster for t in pt.get("payload", {}).get("tags", [])})[:5]
            new_memory_id = f"consolidated_{uuid.uuid4().hex[:8]}"
            new_id        = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{user_id}_{new_memory_id}"))

            # Upsert du nouveau point lvl2
            upsert_resp = httpx.put(
                f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points",
                json={"points": [{
                    "id": new_id,
                    "vector": fused_vector,
                    "payload": {
                        "user_id":           user_id,
                        "chat_id":           "consolidated",
                        "timestamp":         int(time.time()),
                        "memory_importance": 2,      # Promotion lvl1 → lvl2
                        "memory_id":         new_memory_id,
                        "tags":              tags,
                        "summary":           fused_summary
                    }
                }]}, timeout=30
            )
            if upsert_resp.status_code not in (200, 206):
                print(f"[ECHO-CONSOLIDATION] \u274c Upsert échoué : {upsert_resp.text[:200]}")
                continue

            # Suppression des anciens points par liste d'IDs
            # (API Qdrant : {"points": [id1, id2, ...]} et non pas {"filter": ...})
            del_resp = httpx.post(
                f"{QDRANT_URL}/collections/{COLLECTION_MEMORY}/points/delete",
                json={"points": [pt["id"] for pt in cluster]},
                timeout=30
            )
            if del_resp.status_code == 200:
                report["points_deleted"]  += len(cluster)
                report["points_promoted"] += 1
                report["points_merged"]   += len(cluster)
                print(f"[ECHO-CONSOLIDATION] \u2705 Cluster {new_memory_id} : "
                      f"{len(cluster)} lvl1 → 1 lvl2 (user={user_id[:8]})")

    except Exception as e:
        print(f"[ECHO-CONSOLIDATION] \u274c Erreur user={user_id[:8]}: {e}")

    return report

def setup_lifecycle_scheduler():
    if not HAS_MAINT_SCHEDULER: return
    try:
        config = load_maint_config()
        schedule.clear()
        schedule.every().day.at(config.get("cleanup_hour", "03:00")).do(run_semantic_pruning)
    except Exception: pass

def change_system_password(username, current_pwd, new_pwd):
    if not DOCKER_AVAILABLE: return False, "Module SSH absent"
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=username, password=current_pwd, timeout=10)
        chan = ssh.invoke_shell(); time.sleep(1)
        chan.send('passwd\n'); time.sleep(1)
        chan.send(f'{current_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(0.5)
        chan.send(f'{new_pwd}\n'); time.sleep(1)
        out = chan.recv(4096).decode('utf-8', errors='ignore'); ssh.close()
        if "updated successfully" in out or "mis à jour avec succès" in out: return True, "Succès"
        return False, "Refus système"
    except Exception as e: return False, str(e)

# ==============================================================================
# SECTION 5 : SAUVEGARDES
# ==============================================================================

def load_settings():
    c = DEFAULT_BACKUP_CONFIG.copy()
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f: c.update(json.loads(f.read()))
        except Exception: pass
    return c

def save_settings(new_s):
    c = load_settings(); c.update(new_s)
    with open(SETTINGS_FILE, 'w') as f: json.dump(c, f, indent=4)
    update_backup_schedule()

def get_rclone_remotes():
    """Retrieve list of available remotes directly from rclone config file or command."""
    remotes = []
    if os.path.exists(RCLONE_CONF_PATH):
        try:
            res = subprocess.run(['rclone', '--config', RCLONE_CONF_PATH, 'listremotes'], capture_output=True, text=True, timeout=5)
            if res.returncode == 0:
                remotes = [r.strip().replace(':', '') for r in res.stdout.splitlines() if r.strip()]
        except Exception: pass
    return remotes

def perform_backup_task():
    if not DOCKER_AVAILABLE: return
    fpath = os.path.join(BACKUP_DIR, f"echo_backup_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.tar.xz")
    try:
        client = docker.from_env(); target = client.containers.get(TARGET_CONTAINER)
        try: target_qdrant = client.containers.get('echo-qdrant')
        except Exception: target_qdrant = None
        target.stop()
        if target_qdrant: target_qdrant.stop()
        subprocess.run(['tar', '-cJf', fpath, '/app/backend/data', '/qdrant/storage'], check=True)
        target.start()
        if target_qdrant: target_qdrant.start()
        sets = load_settings()
        if sets.get("auto_cleanup"):
            files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'echo_backup_*.tar.*')), key=os.path.getmtime, reverse=True)
            mode, val = sets.get("cleanup_mode", "count"), int(sets.get("cleanup_value", 5))
            if mode == 'count' and len(files) > val:
                for f in files[val:]: os.remove(f)
            elif mode == 'days':
                for f in files:
                    if os.path.getmtime(f) < (time.time() - (val * 86400)): os.remove(f)
        
        # External Backup
        ext_mode = sets.get("ext_mode", "local")
        if ext_mode == "sftp":
            try:
                import stat
                t = paramiko.Transport((sets.get("sftp_host"), int(sets.get("sftp_port", 22))))
                t.connect(username=sets.get("sftp_user"), password=sets.get("sftp_pass"))
                sftp = paramiko.SFTPClient.from_transport(t)
                r_dir = sets.get("sftp_path").rstrip("/")
                try: sftp.stat(r_dir)
                except FileNotFoundError: sftp.mkdir(r_dir)
                sftp.put(fpath, f"{r_dir}/{os.path.basename(fpath)}")
                
                # Cleanup distant SFTP
                cmode, cval = sets.get("ext_cleanup_mode", "count"), int(sets.get("ext_cleanup_value", 5))
                f_stat = [f for f in sftp.listdir_attr(r_dir) if stat.S_ISREG(f.st_mode) and f.filename.startswith("echo_backup_")]
                f_stat.sort(key=lambda x: x.st_mtime, reverse=True)
                if cmode == 'count' and len(f_stat) > cval:
                    for f in f_stat[cval:]: sftp.remove(f"{r_dir}/{f.filename}")
                elif cmode == 'days':
                    for f in f_stat:
                        if f.st_mtime < (time.time() - (cval * 86400)): sftp.remove(f"{r_dir}/{f.filename}")
                sftp.close()
            except Exception as e: print(f"SFTP Backup Error: {e}")
            
        elif ext_mode == "rclone":
            try:
                remote = sets.get("rclone_remote", "")
                conf_path = RCLONE_CONF_PATH
                if conf_path and os.path.exists(conf_path) and remote:
                    subprocess.run(['rclone', '--config', conf_path, 'copy', fpath, remote], check=True)
                    cmode, cval = sets.get("ext_cleanup_mode", "count"), int(sets.get("ext_cleanup_value", 5))
                    if cmode == 'days':
                        subprocess.run(['rclone', '--config', conf_path, 'delete', remote, '--min-age', f"{cval}d"], check=False)
                    elif cmode == 'count':
                        res = subprocess.run(['rclone', '--config', conf_path, 'lsjson', remote], capture_output=True, text=True)
                        if res.returncode == 0:
                            try:
                                f_list = [f for f in json.loads(res.stdout) if not f.get('IsDir') and f.get('Name', '').startswith("echo_backup_")]
                                f_list.sort(key=lambda x: x.get('ModTime'), reverse=True)
                                if len(f_list) > cval:
                                    for f in f_list[cval:]:
                                        subprocess.run(['rclone', '--config', conf_path, 'deletefile', f"{remote}/{f['Path']}"], check=False)
                            except Exception: pass
            except Exception as e: print(f"Rclone Backup Error: {e}")
    except Exception:
        try: docker.from_env().containers.get(TARGET_CONTAINER).start()
        except Exception: pass
        try: docker.from_env().containers.get('echo-qdrant').start()
        except Exception: pass

def update_backup_schedule():
    if not HAS_SCHEDULER: return
    if 'backup_scheduler' not in globals(): return
    try:
        backup_scheduler.remove_all_jobs()
        sets = load_settings()
        if sets.get("auto_backup"):
            h, m = map(int, sets.get("backup_time", "03:00").split(':'))
            now = datetime.datetime.now()
            start = now.replace(hour=h, minute=m, second=0, microsecond=0)
            if start <= now: start += datetime.timedelta(days=1)
            backup_scheduler.add_job(perform_backup_task, 'interval', days=int(sets.get("interval_days", 1)), start_date=start)
    except Exception: pass

def get_backup_list():
    files = sorted(glob.glob(os.path.join(BACKUP_DIR, 'echo_backup_*.tar.*')), key=os.path.getmtime, reverse=True)
    return [{'name': os.path.basename(f), 'size': human_size(os.path.getsize(f)), 'date': datetime.datetime.fromtimestamp(os.path.getmtime(f)).strftime('%d/%m/%Y %H:%M')} for f in files]

# ==============================================================================
# SECTION 6 : ROUTES FLASK
# ==============================================================================

@app.route('/')
def index():
    if not session.get('logged_in'): return render_template_string(HTML_LOGIN)
    
    # Volume Total (Docs + Bases)
    vault_storage = get_dir_stats(ECHO_USERS_ROOT)
    
    # Compteur de Sessions Réelles (Uniquement .db dans /chats/)
    real_session_count = 0
    if os.path.exists(ECHO_USERS_ROOT):
        for root, _, files in os.walk(ECHO_USERS_ROOT):
            if "chats" in root:
                for f in files:
                    if f.endswith('.db'): real_session_count += 1

    # Pourcentage d'utilisation du volume Docker (echo-webui-data monté sur OWUI_DATA_ROOT)
    vault_disk_percent = 0
    if HAS_PSUTIL:
        try:
            vd = psutil.disk_usage(OWUI_DATA_ROOT)
            vault_disk_percent = vd.percent
        except Exception: pass

    # Taille totale du dossier de backups
    backup_stats = get_dir_stats(BACKUP_DIR)

    stats = {
        "uploads": get_dir_stats(UPLOADS_DIR),
        "vault": vault_storage,
        "vault_disk_percent": vault_disk_percent,
        "logs": get_dir_stats(os.path.join(OWUI_DATA_ROOT, "debug_logs")),
        "real_sessions": real_session_count,
        "backup_size": backup_stats.get("size_fmt", "N/A")
    }
    return render_template_string(HTML_DASHBOARD, settings=load_settings(), 
                                storage_stats=stats, maint=load_maint_config(),
                                version=get_echo_version(), user=session.get('username'),
                                backups=get_backup_list(), history=load_maint_history(),
                                rclone_remotes=get_rclone_remotes(),
                                server_time_iso=datetime.datetime.now().isoformat())

@app.route('/api/maint/history')
def maint_history():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(load_maint_history())

@app.route('/', methods=['POST'])
def login():
    try:
        ssh = paramiko.SSHClient(); ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(HOST_GATEWAY, username=request.form.get('username'), password=request.form.get('password'), timeout=5)
        ssh.close(); session['logged_in'] = True; session['username'] = request.form.get('username')
    except Exception: flash('Authentification échouée.', 'danger')
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/api/user_stats')
def user_stats():
    if not session.get('logged_in'): return jsonify([])
    data = []
    try:
        with sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True) as conn:
            users = conn.execute("SELECT id, name, email, role FROM user ORDER BY name ASC").fetchall()
            for u in users:
                v_dir = os.path.join(ECHO_USERS_ROOT, str(u[0]), "chats")
                count = len([f for f in os.listdir(v_dir) if f.endswith('.db')]) if os.path.exists(v_dir) else 0
                data.append({"id": u[0], "name": u[1], "email": u[2], "role": u[3], "chat_count": count})
    except Exception: pass
    return jsonify(data)

@app.route('/api/admin/password')
def admin_password():
    if not session.get('logged_in'): return jsonify({}), 403
    try:
        if os.path.exists(OWUI_ADMIN_SECRET_PATH):
            with open(OWUI_ADMIN_SECRET_PATH, 'r') as f: return jsonify({"password": f.read().strip()})
    except Exception: pass
    return jsonify({"password": "N/A"})

@app.route('/api/backups')
def api_backups():
    if not session.get('logged_in'): return jsonify([]), 403
    return jsonify(get_backup_list())

@app.route('/download/<filename>')
def download_backup(filename):
    if not session.get('logged_in'): return redirect(url_for('index'))
    path = os.path.join(BACKUP_DIR, secure_filename(filename))
    if os.path.exists(path): return send_file(path, as_attachment=True)
    flash('Fichier introuvable.', 'danger')
    return redirect(url_for('index'))

@app.route('/api/stats')
def sys_stats():
    if not HAS_PSUTIL: return jsonify({})
    mem = psutil.virtual_memory(); disk = psutil.disk_usage(BACKUP_DIR)
    return jsonify({
        "cpu_percent": psutil.cpu_percent(), "cpu_count": os.cpu_count(),
        "cpu_model": get_cpu_model_name(), "cpu_load": [round(l, 2) for l in os.getloadavg()],
        "ram_percent": mem.percent, "ram_used": human_size(mem.used), "ram_total": human_size(mem.total),
        "disk_percent": disk.percent, "disk_used": human_size(disk.used), "disk_total": human_size(disk.total)
    })

@app.route('/api/containers')
def containers():
    if not DOCKER_AVAILABLE: return jsonify([])
    try:
        return jsonify([{"id": c.short_id, "name": c.name, "status": c.status.capitalize()} for c in docker.from_env().containers.list(all=True)])
    except Exception: return jsonify([])

@app.route('/action/<action>', methods=['POST'])
def handle_action(action):
    if not session.get('logged_in'): return redirect(url_for('index'))
    if action == 'backup': threading.Thread(target=perform_backup_task).start(); flash('Sauvegarde complète lancée.', 'info')
    elif action == 'pruning': threading.Thread(target=run_semantic_pruning).start(); flash('Élagage sémantique lancé.', 'info')
    elif action == 'consolidate':
        def _run_consolidation():
            cfg = load_maint_config()
            total_promoted = 0
            try:
                conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
                valid_ids = {str(row[0]) for row in conn.execute("SELECT id FROM user").fetchall()}
                conn.close()
            except Exception as e:
                print(f"[ECHO-CONSOLIDATION] \u274c Lecture DB: {e}"); return
            for uid in valid_ids:
                result = consolidate_memories_for_user(uid, cfg)
                total_promoted += result.get("points_promoted", 0)
            print(f"[ECHO-CONSOLIDATION] \u2705 Terminé : {total_promoted} clusters promus en lvl2.")
        threading.Thread(target=_run_consolidation, daemon=True).start()
        flash('Consolidation des souvenirs Triviaux lancée.', 'info')
    elif action == 'docker_prune':
        def _docker_prune():
            freed_msg = ""
            try:
                result = subprocess.run(['docker', 'builder', 'prune', '-a', '-f'],
                    capture_output=True, text=True, timeout=120)
                for line in result.stdout.splitlines():
                    if 'reclaimed' in line.lower():
                        freed_msg = line.strip()
            except Exception as e:
                print(f"[ECHO-PRUNE] Erreur builder prune: {e}")
            try:
                subprocess.run(['apt-get', 'clean'], capture_output=True, timeout=30)
            except Exception:
                pass
            print(f"[ECHO-PRUNE] Terminé. {freed_msg}")
        threading.Thread(target=_docker_prune, daemon=True).start()
        flash('Purge du cache Docker et APT lancée.', 'info')
    elif action == 'restart':
        cid = request.form.get('container')
        if cid and DOCKER_AVAILABLE:
            try: docker.from_env().containers.get(cid).restart(); flash('Redémarré.', 'success')
            except Exception: pass
    elif action == 'delete_backup':
        f = request.form.get('filename')
        if f and os.path.exists(p := os.path.join(BACKUP_DIR, secure_filename(f))): os.remove(p); flash('Supprimé.', 'warning')
    elif action == 'restore':
        f = request.form.get('filename')
        if f and DOCKER_AVAILABLE:
            p = os.path.join(BACKUP_DIR, secure_filename(f))
            if not os.path.exists(p):
                flash('Erreur: Fichier de sauvegarde introuvable.', 'danger')
                return redirect(url_for('index'))
            try:
                client = docker.from_env(); target = client.containers.get(TARGET_CONTAINER)
                try: target_qdrant = client.containers.get('echo-qdrant')
                except Exception: target_qdrant = None
                target.stop()
                if target_qdrant: target_qdrant.stop()
                
                list_cmd = subprocess.run(['tar', '-tf', p], capture_output=True, text=True, check=True)
                files_list = list_cmd.stdout.split('\n')
                if any(f.startswith('app/backend/data') for f in files_list):
                    shutil.rmtree('/app/backend/data', ignore_errors=True)
                    os.makedirs('/app/backend/data', exist_ok=True)
                    shutil.rmtree('/qdrant/storage', ignore_errors=True)
                    os.makedirs('/qdrant/storage', exist_ok=True)
                    subprocess.run(['tar', '-xf', p, '-C', '/'], check=True)
                else:
                    shutil.rmtree(OWUI_DATA_ROOT, ignore_errors=True)
                    os.makedirs(OWUI_DATA_ROOT, exist_ok=True)
                    subprocess.run(['tar', '-xf', p, '-C', OWUI_DATA_ROOT], check=True)
                
                target.start()
                if target_qdrant: target_qdrant.start()
                flash('Restauration terminée.', 'success')
            except Exception as e: flash(f'Erreur: {e}', 'danger')
    return redirect(url_for('index'))

@app.route('/action/auth_reset/<user_id>', methods=['POST'])
def auth_reset_user(user_id):
    if not session.get('logged_in'): return redirect(url_for('index'))
    
    try:
        conn = sqlite3.connect(f"file:{WEBUI_DB_PATH}?mode=ro", uri=True)
        user = conn.execute("SELECT name, email FROM user WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if user and (user[1].endswith('@echo.local') or user[0] in ['admin', 'install-stack']):
            flash('Action strictement interdite sur un compte système.', 'danger')
            return redirect(url_for('index'))
    except Exception as e:
        flash(f'Erreur de vérification: {e}', 'danger')
        return redirect(url_for('index'))

    db_path = os.path.join(ECHO_USERS_ROOT, secure_filename(user_id), 'identity.db')
    if os.path.exists(db_path):
        try:
            with sqlite3.connect(db_path, timeout=5.0) as conn: conn.execute("DELETE FROM auth_data")
            flash('Accès sécurisés (Google OAuth / API) purgés pour cet utilisateur.', 'success')
        except Exception as e: flash(f'Erreur lors de la purge: {e}', 'danger')
    else: flash('Identité introuvable.', 'danger')
    return redirect(url_for('index'))

@app.route('/action/rclone/create', methods=['POST'])
def action_rclone_create():
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        remote_name = secure_filename(request.form.get('remote_name', '').strip())
        if not remote_name: raise ValueError("Nom de profil invalide.")
        provider = request.form.get('provider')
        if provider not in ('drive', 's3', 'webdav'):
            raise ValueError(f"Fournisseur '{provider}' non supporté.")
        cmd = ['rclone', '--config', RCLONE_CONF_PATH, 'config', 'create', remote_name, provider]
        
        if provider == 'drive':
            sa_file = request.files.get('drive_sa_file')
            token = request.form.get('drive_token', '').strip()
            if sa_file and sa_file.filename:
                sf = os.path.join(RCLONE_CONF_DIR, f"{remote_name}_sa.json")
                sa_file.save(sf)
                cmd.extend(['service_account_file', sf])
            elif token:
                cmd.extend(['token', token])
            else: raise ValueError("Fournir un Token ou un Service Account JSON.")
            cmd.extend(['scope', 'drive'])
            
        elif provider == 's3':
            cmd.extend(['provider', request.form.get('s3_provider', 'AWS')])
            cmd.extend(['access_key_id', request.form.get('s3_access_key', '')])
            cmd.extend(['secret_access_key', request.form.get('s3_secret_key', '')])
            ep = request.form.get('s3_endpoint', '').strip()
            if ep: cmd.extend(['endpoint', ep])
            cmd.extend(['env_auth', 'false'])
            
        elif provider == 'webdav':
            cmd.extend(['url', request.form.get('webdav_url', '')])
            cmd.extend(['vendor', request.form.get('webdav_vendor', 'other')])
            cmd.extend(['user', request.form.get('webdav_user', '')])
            cmd.extend(['pass', request.form.get('webdav_pass', '')])
            
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if res.returncode == 0:
            flash(f"Profil Rclone '{remote_name}' créé avec succès.", 'success')
        else:
            flash(f"Erreur Rclone : {res.stderr}", 'danger')

    except subprocess.TimeoutExpired:
        flash("Erreur : La création du profil Rclone a expiré (30s). Vérifiez la connectivité réseau du conteneur.", 'danger')
    except Exception as e: flash(f"Erreur : {e}", 'danger')
    return redirect(url_for('index'))

@app.route('/action/rclone/delete', methods=['POST'])
def action_rclone_delete():
    """Supprime un profil Rclone de la configuration."""
    if not session.get('logged_in'): return redirect(url_for('index'))
    try:
        remote_name = request.form.get('remote_name', '').strip()
        if not remote_name: raise ValueError("Nom de profil invalide.")
        # Sécurité : interdire la suppression du profil actuellement utilisé
        sets = load_settings()
        active_remote = sets.get('rclone_remote', '')
        if active_remote and active_remote.startswith(f"{remote_name}:"):
            flash(f"Impossible : le profil '{remote_name}' est actuellement utilisé pour l'externalisation. Désactivez-le d'abord.", 'warning')
            return redirect(url_for('index'))
        # Suppression via rclone
        res = subprocess.run(
            ['rclone', '--config', RCLONE_CONF_PATH, 'config', 'delete', remote_name],
            capture_output=True, text=True, timeout=10
        )
        if res.returncode == 0:
            # Nettoyage du fichier Service Account JSON associé s'il existe
            sa_path = os.path.join(RCLONE_CONF_DIR, f"{remote_name}_sa.json")
            if os.path.exists(sa_path): os.remove(sa_path)
            flash(f"Profil Rclone '{remote_name}' supprimé.", 'success')
        else:
            flash(f"Erreur Rclone : {res.stderr}", 'danger')
    except Exception as e: flash(f"Erreur : {e}", 'danger')
    return redirect(url_for('index'))

@app.route('/settings', methods=['POST'])
def save_settings_route():
    save_settings({
        "auto_backup": 'auto_backup' in request.form,
        "auto_cleanup": 'auto_cleanup' in request.form,
        "interval_days": int(request.form.get('interval_days', 1)),
        "backup_time": request.form.get('backup_time', "03:00"),
        "cleanup_mode": request.form.get('cleanup_mode', "count"),
        "cleanup_value": int(request.form.get('cleanup_value', 5))
    })
    flash('Paramètres sauvegardes mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/settings/external_backup', methods=['POST'])
def save_ext_backup_route():
    rm_name = request.form.get("rclone_remote_name", "").strip()
    rm_sub = request.form.get("rclone_subpath", "").strip()
    full_remote = f"{rm_name}:{rm_sub}" if rm_name else ""
    
    save_settings({
        "ext_mode": request.form.get("ext_mode", "local"),
        "sftp_host": request.form.get("sftp_host", ""),
        "sftp_port": request.form.get("sftp_port", "22"),
        "sftp_user": request.form.get("sftp_user", ""),
        "sftp_pass": request.form.get("sftp_pass", ""),
        "sftp_path": request.form.get("sftp_path", "/backups/echo"),
        "rclone_remote": full_remote,
        "ext_cleanup_mode": request.form.get("ext_cleanup_mode", "count"),
        "ext_cleanup_value": int(request.form.get("ext_cleanup_value", 5))
    })
    flash('Paramètres d\'externalisation mis à jour.', 'success')
    return redirect(url_for('index'))

@app.route('/settings/maintenance', methods=['POST'])
def update_maint():
    c = load_maint_config()
    c["cleanup_hour"] = request.form.get("cleanup_hour", "03:00")
    c["retention"]["uploads_days"] = int(request.form.get("ret_uploads", 1095))
    c["retention"]["vault_days"] = int(request.form.get("ret_vault", 1095))
    
    if "memory_ttl" not in c: c["memory_ttl"] = {}
    c["memory_ttl"]["lvl1"] = int(request.form.get("ttl_lvl1", 30))
    c["memory_ttl"]["lvl2"] = int(request.form.get("ttl_lvl2", 60))
    c["memory_ttl"]["lvl3"] = int(request.form.get("ttl_lvl3", 180))
    c["memory_ttl"]["lvl4"] = int(request.form.get("ttl_lvl4", 365))
    c["memory_ttl"]["lvl5"] = int(request.form.get("ttl_lvl5", 540))

    # Paramètres de consolidation (exposés dans l'UI)
    c.setdefault("consolidation", DEFAULT_MAINT_CONFIG["consolidation"].copy())
    c["consolidation"]["trigger_threshold"] = int(request.form.get("consol_threshold", 10))
    c["consolidation"]["min_cluster_size"]   = int(request.form.get("consol_min_cluster", 3))
    c["consolidation"]["similarity_threshold"] = float(request.form.get("consol_similarity", 0.75))
    
    c["purge_orphaned_chats"] = 'purge_orphaned_chats' in request.form
    c["purge_orphaned_users"] = 'purge_orphaned_users' in request.form

    ok = save_maint_config(c)
    setup_lifecycle_scheduler()
    if ok:
        flash('Cycle de vie et Mémoire mis à jour.', 'success')
    else:
        flash('Erreur : impossible d\'écrire la configuration.', 'danger')
    return redirect(url_for('index'))

@app.route('/action/security/passwd', methods=['POST'])
def passwd_change():
    u = session.get('username')
    new_p, conf_p = request.form.get('new_password'), request.form.get('confirm_password')
    if new_p != conf_p: flash('Mots de passe différents.', 'danger')
    else:
        ok, msg = change_system_password(u, request.form.get('current_password'), new_p)
        flash(f'✅ {msg}' if ok else f'❌ {msg}', 'success' if ok else 'danger')
    return redirect(url_for('index'))

# ==============================================================================
# SECTION 7 : TEMPLATES (UX VÉRITÉ SÉMANTIQUE)
# ==============================================================================

HTML_LOGIN = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Login - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"></head>
<body class="bg-black d-flex align-items-center justify-content-center vh-100"><div class="card p-5 border-secondary shadow-lg" style="width:400px;"><h2 class="text-center text-primary mb-4">ECHO Admin</h2><form method="POST"><input type="text" name="username" class="form-control mb-3" placeholder="Admin" required autofocus><input type="password" name="password" class="form-control mb-4" placeholder="Password" required><button class="btn btn-primary w-100">Entrer</button></form></div></body></html>"""

HTML_DASHBOARD = """
<!doctype html><html lang="fr" data-bs-theme="dark"><head><meta charset="utf-8"><title>Console - ECHO Admin</title><link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/css/bootstrap.min.css" rel="stylesheet"><link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.5/font/bootstrap-icons.css"><link href="https://cdn.jsdelivr.net/npm/gridstack@10.1.2/dist/gridstack.min.css" rel="stylesheet"/><style>.card{background-color:#161b22;border-color:#30363d;overflow:hidden;display:flex;flex-direction:column}.card>.card-body{overflow-y:auto;flex:1 1 auto}.navbar{background-color:#161b22;border-bottom:1px solid #30363d}.table{--bs-table-bg:transparent;--bs-table-border-color:#30363d;color:#c9d1d9}.x-small{font-size:0.7rem;color:#888}.grid-stack-item-content{background-color:transparent !important;overflow-y:auto;overflow-x:hidden}.grid-stack-item-content::-webkit-scrollbar{width:6px}.grid-stack-item-content::-webkit-scrollbar-thumb{background-color:#30363d;border-radius:4px}.grid-stack .card{margin-bottom:0 !important;}</style></head>
<body style="background-color:#0d1117;">
    <div id="loader" style="position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.8);z-index:9999;display:none;flex-direction:column;justify-content:center;align-items:center;"><div class="spinner-border text-primary mb-3"></div><h4 id="loader-msg">Action en cours...</h4></div>
    <nav class="navbar px-4 py-2 mb-4"><span class="navbar-brand text-primary fw-bold" data-bs-toggle="tooltip" title="ECHO Infrastructure Manager"><i class="bi bi-tree-fill"></i> ECHO CONSOLE {{ version }}</span><div class="d-flex align-items-center gap-3"><button class="btn btn-sm btn-outline-secondary" onclick="resetLayout()" data-bs-toggle="tooltip" title="Restaurer l'affichage par défaut"><i class="bi bi-grid-3x3-gap"></i></button><span class="badge bg-dark border border-secondary" id="clock">--:--:--</span><span class="text-muted small">{{ user }}</span><a href="/logout" class="btn btn-sm btn-outline-danger">Quitter</a></div></nav>
    <div class="container-fluid px-4">
        {% with msgs = get_flashed_messages(with_categories=true) %}{% for c,m in msgs %}<div class="alert alert-{{c}} alert-dismissible fade show border-0 mb-4">{{m}}<button type="button" class="btn-close" data-bs-dismiss="alert"></button></div>{% endfor %}{% endwith %}
        
        <div class="grid-stack">
            <!-- Ligne 1 : Stats -->
            <div class="grid-stack-item" gs-w="2" gs-h="1" gs-id="card-cpu">
                <div class="grid-stack-item-content"><div class="card h-100 p-3" data-bs-toggle="tooltip" title="Charge CPU du serveur hôte"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">CPU</span><span class="fs-4 fw-bold text-primary"><span id="cpu">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="cpu-details">...</small></div><div class="progress mt-2" style="height:4px;"><div id="cpu-bar" class="progress-bar bg-primary" style="width:0%;"></div></div></div></div>
            </div>
            <div class="grid-stack-item" gs-w="2" gs-h="1" gs-id="card-ram">
                <div class="grid-stack-item-content"><div class="card h-100 p-3" data-bs-toggle="tooltip" title="Utilisation de la mémoire vive"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">RAM</span><span class="fs-4 fw-bold text-info"><span id="ram">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="ram-text">--/--</small></div><div class="progress mt-2" style="height:4px;"><div id="ram-bar" class="progress-bar bg-info" style="width:0%;"></div></div></div></div>
            </div>
            <div class="grid-stack-item" gs-w="2" gs-h="1" gs-id="card-disk">
                <div class="grid-stack-item-content"><div class="card h-100 p-3" data-bs-toggle="tooltip" title="Utilisation du disque racine"><div class="d-flex justify-content-between align-items-center"><span class="fw-bold small text-muted">Disque</span><span class="fs-4 fw-bold" id="disk-pct-wrap"><span id="disk-pct">--</span>%</span></div><div class="d-flex justify-content-between align-items-center mt-1"><small class="x-small" id="disk-text">--/--</small></div><div class="progress mt-2" style="height:4px;"><div id="disk-bar" class="progress-bar" style="width:0%;"></div></div></div></div>
            </div>
            <div class="grid-stack-item" gs-w="3" gs-h="1" gs-id="card-sessions">
                <div class="grid-stack-item-content"><div class="card h-100 p-3 text-center" data-bs-toggle="tooltip" title="Sessions de chat ECHO réelles (fichiers .db dans /chats/)">Sessions Actives<br><b class="text-primary fs-4">{{ storage_stats.real_sessions }}</b></div></div>
            </div>
            <div class="grid-stack-item" gs-w="3" gs-h="1" gs-id="card-volume">
                <div class="grid-stack-item-content"><div class="card h-100 p-3 text-center" data-bs-toggle="tooltip" title="Volume total occupé par l'Espace Personnel (Docs + Bases + Identités)">Volume Espace Personnel<br><b class="text-success fs-5">{{ storage_stats.vault.size_fmt }}</b></div></div>
            </div>

            <!-- Ligne 2 : Users & Maintenance -->
            <div class="grid-stack-item" gs-w="7" gs-h="3" gs-id="card-users">
                <div class="grid-stack-item-content"><div class="card h-100"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-people"></i> Sessions par Utilisateur</span><button class="btn btn-sm btn-outline-secondary" onclick="refreshUsers()"><i class="bi bi-arrow-repeat"></i></button></div><div class="p-0"><table class="table table-hover mb-0"><thead><tr><th class="ps-3">Nom</th><th>Email</th><th class="text-center">Sessions</th><th class="text-end pe-3">Actions</th></tr></thead><tbody id="user-list"></tbody></table></div></div></div>
            </div>
            
            <div class="grid-stack-item" gs-w="5" gs-h="6" gs-id="card-maint">
                <div class="grid-stack-item-content"><div class="card h-100 border-info"><div class="card-header text-info"><i class="bi bi-scissors"></i> Élagage & Cycle de Vie (Jours)</div><div class="card-body small">
                    <form action="/settings/maintenance" method="post" class="mb-3">
                        <div class="row g-2 mb-2">
                            <div class="col-6"><label class="x-small">Uploads</label><input type="number" name="ret_uploads" class="form-control form-control-sm" value="{{maint.retention.uploads_days}}" data-bs-toggle="tooltip" title="Durée de conservation (en jours) des fichiers temporaires uploadés."></div>
                            <div class="col-6"><label class="x-small">Vault</label><input type="number" name="ret_vault" class="form-control form-control-sm" value="{{maint.retention.vault_days}}" data-bs-toggle="tooltip" title="Durée de conservation (en jours) des fichiers persistants de l'Espace Personnel."></div>
                        </div>
                        <div class="row g-2 mb-2">
                            <div class="col-12"><label class="x-small text-muted">Durée de conservation de la mémoire (TTL par niveau) :</label></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Trivial</label><input type="number" name="ttl_lvl1" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl1}}" title="Lv1 (Trivial)"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Mineur</label><input type="number" name="ttl_lvl2" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl2}}" title="Lv2"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Utile</label><input type="number" name="ttl_lvl3" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl3}}" title="Lv3"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Majeur</label><input type="number" name="ttl_lvl4" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl4}}" title="Lv4"></div>
                            <div class="col text-center"><label class="x-small text-secondary mb-1">Axiome</label><input type="number" name="ttl_lvl5" class="form-control form-control-sm text-center" value="{{maint.memory_ttl.lvl5}}" title="Lv5 (Axiome/Critique)"></div>
                        </div>
                        <label class="x-small">Heure d'élagage automatique</label><input type="time" name="cleanup_hour" class="form-control form-control-sm mb-2" value="{{maint.cleanup_hour}}">
                        <div class="row g-2 mb-2">
                            <div class="col-12"><label class="x-small text-muted">Consolidation mémoire lvl1 → lvl2 :</label></div>
                            <div class="col-6">
                                <label class="x-small text-secondary mb-1">Seuil (nb lvl1)</label>
                                <input type="number" name="consol_threshold" class="form-control form-control-sm text-center" value="{{maint.consolidation.trigger_threshold}}" title="Nb de souvenirs Triviaux par user avant consolidation" min="3" max="50">
                            </div>
                            <div class="col-6">
                                <label class="x-small text-secondary mb-1">Cluster min</label>
                                <input type="number" name="consol_min_cluster" class="form-control form-control-sm text-center" value="{{maint.consolidation.min_cluster_size}}" title="Nb minimum de souvenirs similaires pour fusionner" min="2" max="10">
                            </div>
                            <div class="col-12 mt-1">
                                <label class="x-small text-secondary mb-1">Seuil cosinus (0.0-1.0)</label>
                                <input type="number" name="consol_similarity" class="form-control form-control-sm text-center" value="{{maint.consolidation.similarity_threshold}}" title="Score cosinus minimal pour regrouper deux souvenirs dans un même cluster (0.75 = très similaires, 0.5 = assez proches)" min="0.4" max="0.99" step="0.05">
                            </div>
                        </div>
                        <div class="form-check form-switch mb-1"><input class="form-check-input" type="checkbox" name="purge_orphaned_chats" id="sw_purge_chats" {{ 'checked' if maint.purge_orphaned_chats }}><label class="form-check-label x-small" for="sw_purge_chats" data-bs-toggle="tooltip" title="Si activé, supprime les fichiers d'un chat dans l'Espace Personnel ECHO si le chat n'existe plus dans Open WebUI.">Purger les chats orphelins</label></div>
                        <div class="form-check form-switch mb-2"><input class="form-check-input" type="checkbox" name="purge_orphaned_users" id="sw_purge_users" {{ 'checked' if maint.purge_orphaned_users }}><label class="form-check-label x-small" for="sw_purge_users" data-bs-toggle="tooltip" title="Si activé, détruit l'Espace Personnel complet (fichiers, bases, mémoires vectorielles) d'un utilisateur supprimé d'Open WebUI.">Purger les utilisateurs orphelins</label></div>
                        <button class="btn btn-sm btn-info w-100">Programmer le Cycle</button>
                    </form>
                    <hr><p class="m-0 mb-1">Transit (Uploads) : <b>{{ storage_stats.uploads.size_fmt }}</b></p>
                    <div class="d-flex gap-2">
                        <form action="/action/pruning" method="post" onsubmit="showLoader('Élagage profond...')" class="flex-grow-1"><button class="btn btn-outline-info btn-sm w-100">Lancer l'Élagage</button></form>
                        <button class="btn btn-sm btn-outline-secondary" data-bs-toggle="collapse" data-bs-target="#historyLog"><i class="bi bi-journal-text"></i> Logs</button>
                    </div>
                    <form action="/action/consolidate" method="post" onsubmit="showLoader('Consolidation lvl1 → lvl2...')" class="mt-2">
                        <button class="btn btn-outline-warning btn-sm w-100" title="Fusionne les souvenirs Triviaux similaires en souvenirs Mineurs (centroïde vectoriel).">
                            🧬 Consolider Mémoires Lvl1
                        </button>
                    </form>
                    <form action="/action/docker_prune" method="post" onsubmit="return confirm('Purger le cache de build Docker et le cache APT système ?') && (showLoader('Purge en cours...'), true)">
                        <button class="btn btn-outline-danger btn-sm w-100 mt-1" title="Libère l'espace du build cache Docker (~4 Go) et du cache APT système.">🧹 Purge Cache Docker</button>
                    </form>
                    <div class="collapse mt-3" id="historyLog">
                        <div class="bg-dark p-2 rounded border border-secondary" style="max-height: 200px; overflow-y: auto;">
                            <h6 class="x-small text-uppercase text-muted border-bottom border-secondary pb-1">Historique 1 an</h6>
                            {% for entry in history %}
                            <div class="mb-2 pb-1 border-bottom border-secondary last-child-border-0">
                                <span class="x-small text-info">{{ entry.timestamp }}</span><br>
                                <span style="font-size: 0.75rem;">{{ entry.report }}</span>
                            </div>
                            {% endfor %}
                            {% if not history %}<span class="x-small text-muted">Aucun log disponible.</span>{% endif %}
                        </div>
                    </div>
                </div></div></div>
            </div>

            <div class="grid-stack-item" gs-w="7" gs-h="2" gs-id="card-backups">
                <div class="grid-stack-item-content"><div class="card h-100"><div class="card-header d-flex justify-content-between align-items-center"><span><i class="bi bi-hdd-network"></i> Sauvegardes <span class="badge bg-secondary ms-1" data-bs-toggle="tooltip" title="Espace total occupé par les backups locaux">{{ storage_stats.backup_size }}</span></span><div class="btn-group"><button class="btn btn-sm btn-outline-secondary" onclick="refreshBackups()"><i class="bi bi-arrow-repeat"></i></button><form action="/action/backup" method="post" onsubmit="showLoader()"><button class="btn btn-sm btn-success">+</button></form></div></div><div class="p-0"><table class="table table-sm mb-0"><thead><tr><th class="ps-3">Fichier</th><th>Date</th><th>Taille</th><th class="text-end pe-3">Action</th></tr></thead><tbody id="backup-rows"></tbody></table></div></div></div>
            </div>

            <div class="grid-stack-item" gs-w="5" gs-h="3" gs-id="card-auto-backups">
                <div class="grid-stack-item-content"><div class="card h-100 border-success"><div class="card-header text-success"><i class="bi bi-calendar-check"></i> Planification Sauvegardes</div><div class="card-body small">
                    <form action="/settings" method="post" class="mb-2">
                        <div class="form-check form-switch"><input class="form-check-input" type="checkbox" name="auto_backup" {% if settings.auto_backup %}checked{% endif %}> Backup Auto</div>
                        <div class="row g-2 mt-1"><div class="col-6"><label class="x-small">Intervalle (j)</label><input type="number" name="interval_days" class="form-control form-control-sm" value="{{settings.interval_days}}"></div><div class="col-6"><label class="x-small">Heure</label><input type="time" name="backup_time" class="form-control form-control-sm" value="{{settings.backup_time}}"></div></div>
                        <div class="input-group mt-2"><select name="cleanup_mode" class="form-select form-select-sm"><option value="count" {% if settings.cleanup_mode == 'count' %}selected{% endif %}>Garder X</option><option value="days" {% if settings.cleanup_mode == 'days' %}selected{% endif %}>Max X jours</option></select><input type="number" name="cleanup_value" class="form-control form-control-sm" value="{{settings.cleanup_value}}"></div>
                        <button class="btn btn-sm btn-primary w-100 mt-2">Sauver Paramètres Local</button>
                    </form>
                    <button class="btn btn-sm btn-outline-info w-100" data-bs-toggle="modal" data-bs-target="#extBackupModal"><i class="bi bi-cloud-upload"></i> Externalisation</button>
                </div></div></div>
            </div>

            <div class="grid-stack-item" gs-w="5" gs-h="2" gs-id="card-access-security">
                <div class="grid-stack-item-content"><div class="card h-100 border-warning"><div class="card-header text-warning"><i class="bi bi-shield-lock"></i> Accès Système</div><div class="card-body small">
                    <button class="btn btn-sm btn-outline-warning w-100 mb-2" onclick="copyPwd()">Copier Pass Admin OWUI</button>
                    <button class="btn btn-sm btn-outline-secondary w-100 mb-2" data-bs-toggle="collapse" data-bs-target="#ssh">Changer Pass Système</button>
                    <div class="collapse mt-2" id="ssh"><form action="/action/security/passwd" method="post" class="bg-dark p-2 rounded"><input type="password" name="current_password" class="form-control form-control-sm mb-1" placeholder="Actuel"><input type="password" name="new_password" class="form-control form-control-sm mb-1" placeholder="Nouveau"><input type="password" name="confirm_password" class="form-control form-control-sm mb-1" placeholder="Confirmer le nouveau"><button class="btn btn-sm btn-warning w-100">Valider</button></form></div>
                </div></div></div>
            </div>

            <div class="grid-stack-item" gs-w="7" gs-h="2" gs-id="card-containers">
                <div class="grid-stack-item-content"><div class="card h-100 border-secondary"><div class="card-header d-flex justify-content-between align-items-center"><span>Containers</span><button class="btn btn-sm btn-link text-secondary p-0" onclick="refreshContainers()"><i class="bi bi-arrow-repeat"></i></button></div><ul class="list-group list-group-flush" id="container-list"></ul></div></div>
            </div>
        </div>
        
        <!-- Modal Externalisation -->
        <div class="modal fade" id="extBackupModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content bg-dark text-light"><div class="modal-header border-secondary"><h5 class="modal-title"><i class="bi bi-cloud-arrow-up"></i> Externalisation des Sauvegardes</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/settings/external_backup" method="post"><div class="modal-body"><div class="mb-3"><label class="form-label text-info">Mode Actif</label><select name="ext_mode" class="form-select bg-dark text-light border-secondary"><option value="local" {% if settings.ext_mode == 'local' %}selected{% endif %}>Désactivé (Local Uniquement)</option><option value="sftp" {% if settings.ext_mode == 'sftp' %}selected{% endif %}>Serveur NAS (SFTP)</option><option value="rclone" {% if settings.ext_mode == 'rclone' %}selected{% endif %}>Cloud Grand Public (Rclone)</option></select></div><hr class="border-secondary"><div class="row"><div class="col-md-6"><h6 class="text-warning">Configuration SFTP (NAS)</h6><div class="mb-2"><input type="text" name="sftp_host" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Hôte (IP/Domaine)" value="{{settings.sftp_host}}"></div><div class="mb-2"><input type="text" name="sftp_port" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Port (ex: 22)" value="{{settings.sftp_port}}"></div><div class="mb-2"><input type="text" name="sftp_user" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Utilisateur" value="{{settings.sftp_user}}"></div><div class="mb-2"><input type="password" name="sftp_pass" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Mot de passe" value="{{settings.sftp_pass}}"></div><div class="mb-2"><input type="text" name="sftp_path" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Chemin (ex: /backups/echo)" value="{{settings.sftp_path}}"></div></div><div class="col-md-6"><h6 class="text-primary">Configuration Rclone (Cloud)</h6><div class="mb-2 d-flex gap-2"><select id="rcloneRemoteSelect" name="rclone_remote_name" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="">-- Choisir un profil Rclone --</option>{% for r in rclone_remotes %}<option value="{{ r }}" {% if settings.rclone_remote and settings.rclone_remote.startswith(r) %}selected{% endif %}>{{ r }}</option>{% endfor %}</select><button type="button" class="btn btn-sm btn-outline-primary text-nowrap" data-bs-toggle="modal" data-bs-target="#rcloneWizardModal">➕ Ajouter</button><button type="button" class="btn btn-sm btn-outline-danger text-nowrap" onclick="deleteRcloneProfile()" title="Supprimer le profil sélectionné">✕</button></div><div class="mb-2"><input type="text" name="rclone_subpath" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Dossier cible (ex: ECHO_Backups)" value="{{settings.rclone_remote.split(':')[1] if ':' in settings.rclone_remote else ''}}"></div><small class="text-muted d-block" style="font-size:0.75rem;">L'authentification OAuth et la génération du fichier de configuration sont désormais entièrement gérées par l'assistant.</small></div></div><hr class="border-secondary"><h6 class="text-danger">Élagage Distant de l'Externalisation</h6><div class="input-group mt-2 w-75"><select name="ext_cleanup_mode" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="count" {% if settings.ext_cleanup_mode == 'count' %}selected{% endif %}>Garder X</option><option value="days" {% if settings.ext_cleanup_mode == 'days' %}selected{% endif %}>Max X jours</option></select><input type="number" name="ext_cleanup_value" class="form-control form-control-sm bg-dark text-light border-secondary" value="{{settings.ext_cleanup_value}}"></div><small class="text-muted d-block mt-1">S'applique uniquement au dossier distant configuré ci-dessus.</small></div><div class="modal-footer border-secondary"><button type="submit" class="btn btn-success">Enregistrer les paramètres</button></div></form></div></div></div>
        
        <!-- Modal Wizard Rclone -->
        <div class="modal fade" id="rcloneWizardModal" tabindex="-1" style="background: rgba(0,0,0,0.8);"><div class="modal-dialog modal-lg"><div class="modal-content bg-dark text-light border-primary"><div class="modal-header border-secondary"><h5 class="modal-title text-primary"><i class="bi bi-magic"></i> Assistant de Profil Cloud (Rclone)</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><form action="/action/rclone/create" method="post" enctype="multipart/form-data" onsubmit="showLoader('Création du profil Cloud en cours...')"><div class="modal-body"><div class="row mb-3"><div class="col-md-6"><label class="form-label x-small" data-bs-toggle="tooltip" title="Identifiant unique du profil Rclone. Utilisé pour référencer cette connexion dans la configuration.">Nom du Profil <i class="bi bi-info-circle"></i></label><input type="text" name="remote_name" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="ex: MonGoogleDrive" required pattern="[a-zA-Z0-9_-]+"></div><div class="col-md-6"><label class="form-label x-small" data-bs-toggle="tooltip" title="Type de stockage distant. Chaque fournisseur requiert des informations de connexion différentes.">Fournisseur <i class="bi bi-info-circle"></i></label><select id="wizardProvider" name="provider" class="form-select form-select-sm bg-dark text-light border-secondary" onchange="toggleWizardFields()"><option value="drive">Google Drive</option><option value="s3">S3 / R2 / B2 (Standard)</option><option value="webdav">WebDAV (Nextcloud, kDrive...)</option></select></div></div><hr class="border-secondary">
        
        <!-- Section S3 -->
        <div id="wiz_s3" style="display:none;"><div class="row g-2 mb-2"><div class="col-md-12"><label class="x-small" data-bs-toggle="tooltip" title="Sélectionnez le fournisseur de stockage objet S3 compatible.">Provider <i class="bi bi-info-circle"></i></label><select name="s3_provider" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="AWS">Amazon S3</option><option value="Cloudflare">Cloudflare R2</option><option value="Backblaze">Backblaze B2</option><option value="Other">Autre S3 Compatible</option></select></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Clé d'accès publique fournie par votre fournisseur S3.">Access Key ID <i class="bi bi-info-circle"></i></label><input type="text" name="s3_access_key" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="AKIAIOSFODNN7EXAMPLE"></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Clé secrète associée. Ne sera jamais réaffichée après enregistrement.">Secret Access Key <i class="bi bi-info-circle"></i></label><input type="password" name="s3_secret_key" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="wJalrXUtnFEMI/K7MDENG/bPxRfi"></div><div class="col-md-12 mt-2"><label class="x-small" data-bs-toggle="tooltip" title="URL du endpoint S3. Obligatoire pour R2, B2 et les services S3 non-AWS.">Endpoint URL <i class="bi bi-info-circle"></i></label><input type="text" name="s3_endpoint" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="https://s3.eu-west-1.amazonaws.com"></div></div></div>
        
        <!-- Section WebDAV -->
        <div id="wiz_webdav" style="display:none;"><div class="row g-2 mb-2"><div class="col-md-8"><label class="x-small" data-bs-toggle="tooltip" title="URL complète du point d'accès WebDAV de votre service Cloud.">URL du Serveur WebDAV <i class="bi bi-info-circle"></i></label><input type="url" name="webdav_url" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="https://nextcloud.exemple.com/remote.php/webdav/"></div><div class="col-md-4"><label class="x-small" data-bs-toggle="tooltip" title="Type de serveur WebDAV pour optimiser la compatibilité.">Type de serveur <i class="bi bi-info-circle"></i></label><select name="webdav_vendor" class="form-select form-select-sm bg-dark text-light border-secondary"><option value="nextcloud">Nextcloud</option><option value="owncloud">Owncloud</option><option value="other" selected>Autre (kDrive, etc.)</option></select></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Identifiant de connexion à votre service Cloud (email ou login).">Utilisateur <i class="bi bi-info-circle"></i></label><input type="text" name="webdav_user" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="utilisateur@email.com"></div><div class="col-md-6"><label class="x-small" data-bs-toggle="tooltip" title="Mot de passe d'application recommandé. Générez-le depuis les paramètres de sécurité de votre fournisseur.">Mot de passe / Token d'App <i class="bi bi-info-circle"></i></label><input type="password" name="webdav_pass" class="form-control form-control-sm bg-dark text-light border-secondary" placeholder="Token ou mot de passe d'application"></div></div></div>
        
        
        <!-- Section Google Drive -->
        <div id="wiz_drive" style="display:block;">
            <ul class="nav nav-pills nav-fill mb-3" id="gdriveTabs" role="tablist">
                <li class="nav-item" role="presentation"><button class="nav-link active py-1 x-small" data-bs-toggle="pill" data-bs-target="#gdriveToken" type="button" role="tab">Méthode 1 : Token Local (Facile)</button></li>
                <li class="nav-item" role="presentation"><button class="nav-link py-1 x-small" data-bs-toggle="pill" data-bs-target="#gdriveJson" type="button" role="tab">Méthode 2 : Service Account (Pro)</button></li>
            </ul>
            <div class="tab-content">
                <div class="tab-pane fade show active" id="gdriveToken" role="tabpanel">
                    <div class="alert alert-dark border-secondary p-2 small mb-2 text-muted">
                        <i class="bi bi-info-circle text-info"></i> Google a interdit la copie manuelle de code depuis 2022. Pour autoriser Google Drive :<br>
                        1. Téléchargez Rclone sur votre PC Windows : <a href="https://rclone.org/downloads/" target="_blank" class="text-primary">Lien Officiel</a><br>
                        2. Ouvrez un terminal (PowerShell) sur votre PC et tapez : <code class="text-warning">rclone authorize "drive"</code><br>
                        3. Rclone ouvrira votre navigateur. Connectez-vous à Google et validez.<br>
                        4. Rclone affichera un bloc de texte (JSON) dans votre terminal. Copiez-le et collez-le ci-dessous.
                    </div>
                    <label class="x-small text-primary">Token OAuth (Coller le bloc JSON retourné par votre terminal local) :</label>
                    <textarea name="drive_token" class="form-control form-control-sm bg-dark text-light border-primary" rows="4" placeholder='{"access_token":"ya29.a0A...","token_type":"Bearer","refresh_token":"1//0f..."}'></textarea>
                </div>
                <div class="tab-pane fade" id="gdriveJson" role="tabpanel">
                    <div class="alert alert-dark border-secondary p-2 small mb-2 text-muted">
                        <i class="bi bi-shield-check text-success"></i> Recommandé pour les serveurs sans écran. Pas d'expiration de token.
                    </div>
                    <label class="x-small text-primary">Fichier JSON du Compte de Service (Google Cloud) :</label>
                    <input type="file" name="drive_sa_file" class="form-control form-control-sm bg-dark text-light border-primary" accept=".json">
                </div>
            </div>
        </div>
        
        </div><div class="modal-footer border-secondary"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Annuler</button><button type="submit" class="btn btn-primary">Générer le Profil</button></div></form></div></div></div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0/dist/js/bootstrap.bundle.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/gridstack@10.1.2/dist/gridstack-all.js"></script>
    <script>
        var grid = GridStack.init({
            cellHeight: '120px',
            margin: 15,
            minRow: 1,
            float: true
        });

        // Version du layout : incrémenter quand la structure des tuiles change (ajout/suppression)
        const LAYOUT_VERSION = 3;
        let savedLayout = localStorage.getItem('echo_admin_layout');
        let savedVersion = parseInt(localStorage.getItem('echo_admin_layout_v') || '0');
        if (savedLayout && savedVersion === LAYOUT_VERSION) {
            let parsed = JSON.parse(savedLayout);
            if (!(parsed.length && parsed[0].content !== undefined)) {
                grid.load(parsed);
            }
        } else {
            // Purge : version obsolète ou premier chargement
            localStorage.removeItem('echo_admin_layout');
        }

        grid.on('change', function() {
            // save(false) = positions uniquement (x, y, w, h), pas le HTML
            let layout = grid.save(false);
            localStorage.setItem('echo_admin_layout', JSON.stringify(layout));
            localStorage.setItem('echo_admin_layout_v', LAYOUT_VERSION.toString());
        });

        function resetLayout() {
            if(confirm('Restaurer la disposition par défaut ?')) {
                localStorage.removeItem('echo_admin_layout');
                location.reload();
            }
        }

        const initialTime = new Date('{{ server_time_iso }}');
        function initClock() {
            let now = initialTime;
            setInterval(() => {
                now.setSeconds(now.getSeconds() + 1);
                document.getElementById('clock').innerText = now.toLocaleTimeString();
            }, 1000);
        }
        function toggleWizardFields() {
            const provider = document.getElementById('wizardProvider').value;
            ['drive', 's3', 'webdav'].forEach(p => {
                const el = document.getElementById('wiz_' + p);
                if (el) el.style.display = provider === p ? 'block' : 'none';
            });
        }
        function deleteRcloneProfile() {
            const sel = document.getElementById('rcloneRemoteSelect');
            const name = sel.value;
            if (!name) { alert('Sélectionnez un profil à supprimer.'); return; }
            if (!confirm('Supprimer définitivement le profil Rclone « ' + name + ' » ?')) return;
            const f = document.createElement('form');
            f.method = 'POST'; f.action = '/action/rclone/delete';
            const i = document.createElement('input');
            i.type = 'hidden'; i.name = 'remote_name'; i.value = name;
            f.appendChild(i); document.body.appendChild(f); f.submit();
        }
        function showLoader(m='Action en cours...'){document.getElementById('loader-msg').innerText=m;document.getElementById('loader').style.display='flex'}
        async function refreshUsers(){const r=await fetch('/api/user_stats');const d=await r.json();document.getElementById('user-list').innerHTML=d.map(u=>{const btn = u.email.endsWith('@echo.local') ? '<i class="bi bi-shield-lock text-muted" data-bs-toggle="tooltip" title="Système Protégé"></i>' : `<form action="/action/auth_reset/${u.id}" method="post" class="d-inline" onsubmit="return confirm('Purger les accès distants (Google/API) de ${u.name} ?')"><button type="submit" class="btn btn-sm btn-link text-danger p-0" data-bs-toggle="tooltip" title="Purger Tokens/Clés"><i class="bi bi-shield-lock"></i></button></form>`; return `<tr><td class="ps-3">${u.name}</td><td>${u.email}</td><td class="text-center"><span class="badge bg-primary">${u.chat_count}</span></td><td class="text-end pe-3">${btn}</td></tr>`}).join(''); var t=[].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));t.map(function(e){return new bootstrap.Tooltip(e)})}
        async function refreshBackups(){const r=await fetch('/api/backups');const d=await r.json();document.getElementById('backup-rows').innerHTML=d.map(b=>`<tr><td class="ps-3 text-truncate" style="max-width:200px;">${b.name}</td><td>${b.date}</td><td><span class="badge bg-secondary">${b.size}</span></td><td class="text-end pe-3"><div class="btn-group"><a href="/download/${b.name}" class="btn btn-sm text-primary"><i class="bi bi-download"></i></a><form action="/action/restore" method="post" onsubmit="return confirm('RESTAURER ?')" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm text-warning">↺</button></form><form action="/action/delete_backup" method="post" class="d-inline"><input type="hidden" name="filename" value="${b.name}"><button class="btn btn-sm text-danger">×</button></form></div></td></tr>`).join('')}
        async function refreshContainers(){const r=await fetch('/api/containers');const d=await r.json();document.getElementById('container-list').innerHTML=d.map(c=>`<li class="list-group-item bg-transparent small d-flex justify-content-between align-items-center"><span>${c.name}</span><div class="d-flex align-items-center gap-2"><span class="badge ${c.status.startsWith('Up')?'bg-success':'bg-danger'}">${c.status}</span><form action="/action/restart" method="post"><input type="hidden" name="container" value="${c.id}"><button class="btn btn-sm btn-link text-secondary p-0"><i class="bi bi-power"></i></button></form></div></li>`).join('')}
        async function copyPwd(){
            try {
                const r = await fetch('/api/admin/password');
                const d = await r.json();
                if (d.password === "N/A") { alert('Erreur : Mot de passe introuvable sur le serveur.'); return; }

                if (navigator.clipboard && window.isSecureContext) {
                    await navigator.clipboard.writeText(d.password);
                    alert('Copié dans le presse-papier !');
                } else {
                    let textArea = document.createElement("textarea");
                    textArea.value = d.password;
                    textArea.style.position = "fixed"; textArea.style.left = "-999999px"; textArea.style.top = "-999999px";
                    document.body.appendChild(textArea); textArea.focus(); textArea.select();
                    try { document.execCommand('copy'); alert('Copié ! (Méthode fallback)'); }
                    catch (err) { alert('Erreur lors de la copie manuelle'); }
                    document.body.removeChild(textArea);
                }
            } catch (e) { alert('Erreur réseau lors de la récupération du mot de passe.'); }
        }
        setInterval(async()=>{const r=await fetch('/api/stats');
const d=await r.json();document.getElementById('cpu').innerText=d.cpu_percent;document.getElementById('cpu-bar').style.width=d.cpu_percent+'%';document.getElementById('cpu-details').innerText=`${d.cpu_count} cœurs | Load: ${d.cpu_load.join(', ')}`;document.getElementById('ram').innerText=d.ram_percent;document.getElementById('ram-bar').style.width=d.ram_percent+'%';document.getElementById('ram-text').innerText=d.ram_used+' / '+d.ram_total;document.getElementById('disk-pct').innerText=d.disk_percent;document.getElementById('disk-bar').style.width=d.disk_percent+'%';document.getElementById('disk-text').innerText=d.disk_used+' / '+d.disk_total;const dc=d.disk_percent>85?'danger':d.disk_percent>70?'warning':'success';document.getElementById('disk-pct-wrap').className='fs-4 fw-bold text-'+dc;document.getElementById('disk-bar').className='progress-bar bg-'+dc},3000);
        initClock();refreshUsers();refreshContainers();refreshBackups();
        var tList=[].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]')).map(function(el){return new bootstrap.Tooltip(el)})
    </script>
</body></html>"""

# ==============================================================================
# SECTION 8 : POINT D'ENTRÉE
# ==============================================================================
if __name__ == '__main__':
    if HAS_PSUTIL: psutil.cpu_percent(interval=None)
    if HAS_SCHEDULER:
        backup_scheduler = BackgroundScheduler(); backup_scheduler.start(); update_backup_schedule()
    if HAS_MAINT_SCHEDULER:
        setup_lifecycle_scheduler()
        def maint_loop():
            while True:
                try: schedule.run_pending()
                except Exception: pass
                time.sleep(60)
        threading.Thread(target=maint_loop, daemon=True).start()
    app.run(host='0.0.0.0', port=3001, debug=False, threaded=True)
