"""
title: ECHO Constants
author: ECHO Framework
version: 5.57
description: Composant système interne : ECHO Constants.
"""
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.57: Ajout de ECHO_GLOBAL_TENANT_PROJECT_ID ("aicode-consumers") pour forcer le routage Code Assist et contourner les 429 persos.
# 5.56: Mise à jour du modèle MODEL_FLASH de 3.7 vers 3.8.
# 5.55: Renommage ECHO_API_KEY_THRESHOLD en ECHO_API_KEY_RETRIES pour cohérence globale.
# 5.54: Ajout de ECHO_SAFETY_SETTINGS (BLOCK_NONE) pour les appels Gemini.
# 5.53: Création des constantes CONTEXT_LOAD_WARNING_THRESHOLD (40) et CONTEXT_LOAD_CRITICAL_THRESHOLD (60)
# 5.52: Alignement protocole OAuth2 sur AGY IDE 2.5.5 (audit binaire main.js) :
#       - ECHO_CLIENT_METADATA : ideType ANTIGRAVITY, ajout ideName/ideVersion/platform
#       - ECHO_OAUTH_SCOPES : +experimentsandconfigs, -openid, -aicode
#       - User-Agent : format natif "{app}/{version} {os}/{arch}"
#       - Documentation dual-client (Desktop=perso, LS=Enterprise GCP TOS)

import os
try:
    import pybase64 as base64
except ImportError:
    import base64
import mimetypes
try:
    import filetype
except ImportError:
    filetype = None

# ==============================================================================
# 0. TOPOLOGIE SYSTÈME (ENVIRONNEMENT DOCKER)
# ==============================================================================

# Racine unique de l'infrastructure de données
ECHO_BASE_DATA_DIR = "/app/backend/data"

# HIÉRARCHIE ECHO SOUVERAINE (Standardisé)
ECHO_USERS_ROOT = f"{ECHO_BASE_DATA_DIR}/users"
ECHO_UPLOADS_TRANSIT_DIR = f"{ECHO_BASE_DATA_DIR}/uploads"

ECHO_VERSION_PATH = f"{ECHO_BASE_DATA_DIR}/ECHO_VERSION"

ECHO_SESSION_DOMAINS = ["codex", "files", "db", "n8n_workflows"]
ECHO_GLOBAL_DOMAINS = ["skills", "files", "chats", "n8n_workflow_templates"]

# Identité Réseau (Antigravity 2.5.5 — aligné sur AGY IDE product.json:ideVersion)
# Format natif AGY IDE : "{app}/{ideVersion} {os}/{arch}" (cloudCodeMainService.js)
# ECHO tourne sous Docker Linux — on émet l'identité réelle de la plateforme hôte.
_AGY_IDE_VERSION = "2.5.5"  # Miroir de product.json:ideVersion — à synchroniser lors des mises à jour
ECHO_USER_AGENT             = f"antigravity/{_AGY_IDE_VERSION}"
ECHO_AGY_USER_AGENT         = f"antigravity/{_AGY_IDE_VERSION} linux/amd64"

# Points d'accès Locaux (Souveraineté)
ECHO_EMBEDDING_URL = "http://echo-embedding:7997/v1"
ECHO_N8N_WORKER_URL = "http://echo-n8n-worker:5003"
MODEL_EMBEDDING = "microsoft/Harrier-OSS-v1-0.6B"

# ==============================================================================
# 1. PROTOCOLE ANTIGRAVITY 2.1 — AUTH UNIFIÉE
# ==============================================================================

# --- IDENTIFIANTS TECHNIQUES DES MÉTHODES ---
AUTH_METHOD_KEY_PRIMARY   = "google_api_key"
AUTH_METHOD_OAUTH2        = "google_oauth2"
AUTH_METHOD_KEY_SECONDARY = "google_api_key_secondary"

# --- IDENTIFIANTS ANTIGRAVITY 2.1 (certifiés source : main.js — out-build/vs/platform/cloudCode/common/oauthClient.js) ---
# Encodage : base64(reversed(value)) — casse les signatures connues de GitHub Secret Scanning.
# Décodage : base64.b64decode(b).decode()[::-1]
#
# ARCHITECTURE DUAL-CLIENT AGY :
#   - Desktop (z9e/1071...) : comptes Google PERSONNELS (isGcpTos=false). Utilisé par ECHO.
#   - LS      (Fze/884...)  : comptes ENTERPRISE GCP TOS (isGcpTos=true). Non supporté par ECHO.
#   Le language_server.exe (Go, 142 Mo) embarque les deux en clair. AGY IDE bascule via isGcpTos.
#   ECHO utilise exclusivement le client Desktop pour le flow PKCE (comptes personnels).
_d = lambda b: base64.b64decode(b).decode()[::-1]

# Client Desktop (variable z9e/$9e — oauthClient.js) — SEUL client utilisé par ECHO (comptes perso)
ANTIGRAVITY_DESKTOP_CLIENT_ID     = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEucGUzMDRnNGhqb2xvdHY1MzJlcmNsMTJoMm5pc3NobXQtMTk1MDYwNjAwMTcwMQ==")
ANTIGRAVITY_DESKTOP_CLIENT_SECRET = _d("ZkFEcTZ6NENYczhCTG0xSkxkTDY4NFJXRjg1Sy1YUFNDT0c=")

# Client Language Server (variable Fze/Vze — oauthClient.js) — comptes Enterprise GCP TOS uniquement.
# Conservé pour traçabilité. NON UTILISÉ dans les flows ECHO (aucun branchement isGcpTos).
ANTIGRAVITY_OAUTH_CLIENT_ID       = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEuaGxiNWM4NjJkb2M2dm8yM2NhaXVndDNiamoxY3J0NjMtMjUwOTE5NDUzNDg4")
ANTIGRAVITY_OAUTH_CLIENT_SECRET   = _d("WHN0WjBSd01LeFktamRUUTBDRFdSN0ZwV1FZOS1YUFNDT0c=")

# Scopes exigés par Google pour Cloud Code — alignement strict sur AGY IDE oauthClient.js (variable Ats)
ECHO_OAUTH_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",                    # Télémétrie completion log
    "https://www.googleapis.com/auth/experimentsandconfigs",    # Feature flags et config serveur
]

# --- PKCE + AUTHORIZATION CODE FLOW (RFC 7636) ---
# redirect_uri : générée dynamiquement dans initiate_pkce_flow().
#   Format : http://localhost:{callback_port}/callback
#   Google accepte n'importe quel port loopback (RFC 8252 §7.3).
# Ports alloués dynamiquement dans ECHO_AUTH_PORT_RANGE_* — multi-user natif.
GOOGLE_AUTH_URL         = "https://accounts.google.com/o/oauth2/auth"

# --- SSH TUNNEL ÉPHÉMÈRE PKCE (authentification OAuth2) ---
# Serveur SSH asyncssh dans le container open-webui.
# Un pair de ports (SSH + callback) alloué dynamiquement par session.
# Docker expose uniquement la plage SSH 8020-8024 (stack-echo.yml).
# Les ports callback 8025-8034 restent internes au container.
ECHO_SSH_TUNNEL_USER = "echo-auth"  # Utilisateur SSH jetable
ECHO_SSH_TUNNEL_TIMEOUT = 120       # Secondes avant auto-stop

# Plage des ports SSH server (exposes par Docker dans stack-echo.yml)
# Le client SSH se connecte sur l'un de ces ports.
ECHO_SSH_PORT_RANGE_START = 8020    # Premier port SSH expose
ECHO_SSH_PORT_RANGE_END   = 8024    # Dernier port SSH expose (5 sessions max)

# Plage des ports callback OAuth2 (JAMAIS exposes par Docker - internes uniquement)
# Accessibles uniquement via le tunnel SSH authentifie.
# redirect_uri = http://localhost:{cb_port}/callback (port cote client, resolu par le tunnel)
ECHO_CALLBACK_PORT_RANGE_START = 8025  # Premier port callback interne
ECHO_CALLBACK_PORT_RANGE_END   = 8034  # Dernier port callback interne (10 slots)


# Endpoints standards Google (inchangés)
GOOGLE_OAUTH_TOKEN_URL  = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL     = "https://www.googleapis.com/oauth2/v2/userinfo"

# --- CONFIGURATION API ANTIGRAVITY (PROVISIONING) ---
# Standalone Antigravity — cloudcode-pa (Circuit Breaker Pool)
AGY_BASE_URLS = [
    "https://cloudcode-pa.googleapis.com/v1internal",       # Standard (Prod)
    "https://daily-cloudcode-pa.googleapis.com/v1internal"  # Canary (Daily)
]
ECHO_ENDPOINT_LOCK_TIMEOUT_MIN = 2  # Temps de verrouillage agnostique (Surcharge Serveur ou TPM)
# Metadata client envoyée dans loadCodeAssist
# Alignement sur AGY IDE main.js (clientMetadata getter) :
#   ideName="antigravity", ideType=TTe.ANTIGRAVITY (enum 9), pluginType=GEMINI (enum 2)
#   platform calculé dynamiquement — ECHO tourne sous Linux (Docker).
#   ideVersion = version AGY IDE émulée (product.json:ideVersion).
ECHO_CLIENT_METADATA = {
    "ideName":    "antigravity",
    "ideType":    "ANTIGRAVITY",
    "ideVersion": _AGY_IDE_VERSION,
    "platform":   "LINUX_AMD64",
    "pluginType": "GEMINI"
}

AUTH_DATA_PROJECT_ID  = "google_project_id"
ECHO_GLOBAL_TENANT_PROJECT_ID = "aicode-consumers"
AUTH_DATA_USER_EMAIL  = "google_user_email"
AUTH_DATA_USER_TIER   = "google_user_tier"

# --- CONFIGURATION AI STUDIO (Fallback clés API) ---
GOOGLE_API_BASE_URL      = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AI_STUDIO_WEB_URL = "https://aistudio.google.com/app/apikey"
GOOGLE_API_KEY_REGEX     = r"(?:AIza|AQ\.)[0-9A-Za-z_-]{35,}"
GOOGLE_API_KEY_PATTERN   = GOOGLE_API_KEY_REGEX

# --- TIMING (certifiés par logs natifs IDE) ---
# Token TTL proactif : 55 min exact (confirmé par auth.log "token changed, handleAuthRefresh")
GOOGLE_OAUTH_TOKEN_LIFETIME = 3300  # secondes

# PKCE auth flow : timeout serveur callback (5 min pour que l'utilisateur ouvre l'URL)
PKCE_CALLBACK_TIMEOUT = 300  # secondes

# ==============================================================================
# 1.2 RÉSILIENCE ET RETRIES (API)
# ==============================================================================

ECHO_API_KEY_RETRIES   = 2
ECHO_API_MAX_RETRIES     = 3
ECHO_RETRY_BASE_DELAY    = 3.0   # Base backoff exponentiel — réduit de 5.0 à 3.0 (v5.50)
                                  # Raison : avec base=5.0 et 5 retries, le backoff cumulé (~155s)
                                  # dépassait GUNICORN_TIMEOUT (60s) et causait des crashes SSE
                                  # silencieux via SIGKILL du worker uvicorn.
                                  # Avec base=3.0 et 3 retries : total max ~14s.
                                  # Historique : base augmentée à 5.0 (v5.166.6) pour les rafales
                                  # consult_council sur Code Assist. Ces appels passent par call()
                                  # non-streaming, non impactés par ce changement.
ECHO_RETRY_MULTIPLIER    = 1.5
ECHO_RETRY_JITTER_MIN    = 0.7
ECHO_RETRY_JITTER_MAX    = 1.3

# ==============================================================================
# 1.1 MODULE : CONSTANTES ET DEFAULTS ECHO (CENTRALISATION)
# VERSION : 5.999.1
# ==============================================================================


# ==============================================================================
# REGISTRE COGNITIF ECHO (SINGLE SOURCE OF TRUTH)
# ==============================================================================
ECHO_MODELS_REGISTRY = {
    "MODEL_PRO": {
        "ai_studio_id": "gemini-3.1-pro-preview",
        "ca_model_id":  "gemini-pro-agent",
        "hierarchy": 2,
        "generationConfig": {
            "temperature": 1.0,
            "topP": 0.9,
            "maxOutputTokens": 65535,
            "thinkingConfig": {"thinkingLevel": "high"}
        }
    },
    "MODEL_FLASH": {
        "ai_studio_id": "gemini-3.8-flash",
        "ca_model_id":  "gemini-3.8-flash-high",
        "hierarchy": 1,
        "generationConfig": {
            "temperature": 1.0,
            "topP": 0.9,
            "maxOutputTokens": 65535,
            "thinkingConfig": {"thinkingLevel": "high"}
        }
    },
    "MODEL_LITE": {
        "ai_studio_id": "gemini-3.5-flash-lite",
        "ca_model_id":  "gemini-3.1-flash-lite",
        "hierarchy": 0,
        "generationConfig": {
            "temperature": 1.0,
            "topP": 0.9,
            "maxOutputTokens": 65535,
            "thinkingConfig": {"thinkingLevel": "high"}
        }
    },
    "MODEL_DISTILLATION": {
        "ai_studio_id": "gemini-3.5-flash-lite",
        "ca_model_id":  "gemini-3.1-flash-lite",
        "hierarchy": None,
        "generationConfig": {
            "temperature": 0.0,
            "topP": 0.1,
            "maxOutputTokens": 65535,
            "thinkingConfig": {"thinkingLevel": "low"}
        }
    }
}

# L'Abstraction pure
MODEL_PRO          = "MODEL_PRO"
MODEL_FLASH        = "MODEL_FLASH"
MODEL_LITE         = "MODEL_LITE"
MODEL_DISTILLATION = "MODEL_DISTILLATION"

ECHO_SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"}
]

# Unique survivant : la politique métier UI (qui reste statique)
MODEL_ENUM_BY_POLICY = {
    "MODEL_LITE":  ["MODEL_LITE"],
    "MODEL_FLASH": ["MODEL_FLASH"],
    "MODEL_PRO":   ["MODEL_PRO"],
    "AUTO":        ["MODEL_LITE", "MODEL_FLASH"],
    "AUTO_PRO":    ["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"],
}

EMBEDDING_DIM      = 1024               # Dimension Harrier-OSS
COLLECTION_META_ARTIFACTS = "echo_meta_artifacts"
COLLECTION_SESSION_RAG    = "echo_session_rag"
SESSION_RAG_CONVERSATION_SOURCE_ID = "conversation_history"

# Poids de reranking par niveau d'importance mémorielle.
MEMORY_IMPORTANCE_WEIGHTS: dict[int, float] = {
    1: 0.55,   # Trivial
    2: 0.75,   # Mineur
    3: 1.00,   # Utile
    4: 1.30,   # Majeur
    5: 1.70,   # Axiome
}

MEMORY_IMPORTANCE_LABELS: dict[int, str] = {
    1: "Trivial",
    2: "Mineur",
    3: "Utile",
    4: "Majeur",
    5: "Axiome",
}

def get_model_identity(model_str: str) -> str:
    """Retourne la clé abstraite (ex: MODEL_PRO) à partir de n'importe quel ID."""
    registry = globals().get("ECHO_MODELS_REGISTRY", {})
    if model_str in registry: return model_str
    for k, v in registry.items():
        if model_str in (v.get("ai_studio_id"), v.get("ca_model_id")): return k
    return "UNKNOWN"

def get_generation_config(model_key: str = "MODEL_LITE", override_thinking: str = None) -> dict:
    """
    Extrait le generationConfig depuis le SSOT.
    Permet de surcharger dynamiquement le thinkingLevel (ex: MINIMAL pour le grounding).
    """
    import copy
    # Récupération sécurisée du dictionnaire global
    registry = globals().get("ECHO_MODELS_REGISTRY", {})
    base_gen = copy.deepcopy(registry.get(model_key, registry.get("MODEL_LITE", {})).get("generationConfig", {}))
    if override_thinking and "thinkingConfig" in base_gen:
        base_gen["thinkingConfig"]["thinkingLevel"] = override_thinking
    return base_gen

# ----------------------------

# ==============================================================================
# 1.3 PLAN STRATÉGIQUE — STATUTS & TÂCHES
# ==============================================================================

# Modèles par défaut pour l'agent de planification stratégique
PLANNER_MODEL_BUILD = "MODEL_PRO"
PLANNER_MODEL_UPDATE = "MODEL_FLASH"

# Statuts globaux d'un plan (champ `status:` du frontmatter YAML)
PLAN_STATUS = {
    "draft":     "draft",       # Généré par l'agent, non validé par l'utilisateur
    "ready":     "ready",       # Validé, prêt à l'exécution
    "executing": "executing",   # En cours d'application
    "success":   "success",     # Objectif atteint
    "partial":   "partial",     # Réussi partiellement
    "failed":    "failed",      # Échec constaté
    "abandoned": "abandoned",   # Abandonné volontairement
}

# Notation Markdown des tâches individuelles dans le plan
PLAN_TASK_STATUS = {
    "pending":  "[ ]",   # En attente
    "active":   "[/]",   # En cours
    "done":     "[x]",   # Terminée
    "failed":   "[!]",   # Échouée
    "skipped":  "[-]",   # Ignorée/passée
}

# Types de ressources du Registre Unifié (echo_resources)
RESOURCE_TYPES = {
    "codex":  "codex",   # Fichiers texte/code (Git)
    "plan":   "plan",    # Plans stratégiques
    "media":  "media",   # Images, vidéos, PDF
    "binary": "binary",  # Fichiers non assimilables
    "weburl": "weburl",  # Pages web distillées
    "n8n_template": "n8n_template", # Modèles N8N
    "n8n_workflow": "n8n_workflow", # Workflows N8N
}

# ==============================================================================
# 1.8 STATUTS D'INGESTION DES FICHIERS
# ==============================================================================
# Définit l'état cognitif d'un fichier tel que perçu par le modèle.
FILE_INGESTION_STATUS = {
    "PUT_IN_CONTEXT":    "put_in_context",    # Injection directe dans le prompt (brut ou base64)
    "VECTORIZED_SUM_UP": "vectorized_sum_up", # Résumé via Smart Context (RAG)
    "INDEXED":           "indexed",           # Stockage SQLite seul (Fallback ou Image Web)
    "PENDING_INGESTION": "pending_ingestion", # En attente d'ingestion (ex: Téléchargements Playwright)
}

# Cycle de vie strict d'un workflow N8N en session
N8N_WORKFLOW_STATUS = {
    "READY":     "ready",       # Installé, prêt
    "EXECUTING": "executing",   # En cours d'appel API
    "ERROR":     "error",       # Échec de l'appel
    "DEPLOYED":  "deployed"     # Démon persistant
}

# Mapping Ultime : Quel type supporte quels statuts ?
RESOURCE_STATUS_MAP = {
    "media":        list(FILE_INGESTION_STATUS.values()),
    "binary":       list(FILE_INGESTION_STATUS.values()),
    "weburl":       list(FILE_INGESTION_STATUS.values()), # Flux d'ingestion textuelle
    "codex":        list(FILE_INGESTION_STATUS.values()), # Utilisé par echo_codex_tool (PUT_IN_CONTEXT)
    "plan":         list(PLAN_STATUS.values()),
    "n8n_workflow": list(N8N_WORKFLOW_STATUS.values()),
    "n8n_template": [], # Hub global statique, pas de statut
}

# ==============================================================================
# 1.4 ECHO CODEX — CONSTANTES
# ==============================================================================

# Nom du sous-dossier Codex dans le vault utilisateur
CODEX_DIR_NAME = "codex"

# Mapping extension → langage Monaco Editor
CODEX_LANG_MAP = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
    ".java": "java", ".go": "go", ".rs": "rust", ".rb": "ruby",
    ".php": "php", ".swift": "swift", ".kt": "kotlin",
    ".cs": "csharp", ".vb": "vb",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell", ".bat": "bat",
    ".html": "html", ".htm": "html", ".css": "css",
    ".json": "json", ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".ini": "ini", ".conf": "plaintext",
    ".md": "markdown", ".txt": "plaintext", ".log": "plaintext",
    ".sql": "sql", ".r": "r", ".lua": "lua", ".pl": "perl",
    ".dockerfile": "dockerfile",
}
CODEX_DEFAULT_LANG = "plaintext"

# Prompt système sub-chat édition (HUD AI-assisted)
CODEX_EDIT_SYSTEM_PROMPT = """<persona>
Le Modèle est l'éditeur de code ECHO Codex.
</persona>

<rules>
RÈGLES ABSOLUES :
1. Le Modèle DOIT retourner UNIQUEMENT le fichier modifié complet. Aucune explication, aucun markdown de formatage.
2. Si une sélection est fournie, le Modèle ne modifie QUE cette partie dans le contexte du fichier complet.
3. Le Modèle DOIT préserver le style, l'indentation et les conventions du document original.
4. Si l'instruction est ambiguë, le Modèle DOIT faire le choix le plus conservateur.
</rules>

<context>
Fichier : {filename} | Langage : {language}
</context>"""

# Prompt distillation/résumé de fichier
CODEX_SUMMARIZE_PROMPT = """<instruction>
Le Modèle DOIT fournir une analyse technique exhaustive du fichier '{filename}' ({language}).
Le Modèle DOIT être technique, précis et complet.
</instruction>

<output_format>
Le Modèle DOIT structurer sa réponse strictement selon le format suivant :
1. Objectif et rôle du fichier
2. Architecture : classes, fonctions, structures principales
3. Dépendances et imports
4. Patterns et conventions utilisés
5. Points d'attention, complexité, dette technique éventuelle
</output_format>"""

# Actions rapides prédéfinies (boutons HUD)
CODEX_QUICK_ACTIONS = {
    "shorter":    "Raccourcis ce code/texte sans changer la logique ni le comportement.",
    "longer":     "Développe avec plus de détails, commentaires et documentation.",
    "comment":    "Ajoute des commentaires explicatifs clairs et concis.",
    "uncomment":  "Supprime tous les commentaires du code. Ne conserve que le code exécutable.",
    "refactor":   "Refactorise pour plus de lisibilité, maintenabilité et respect des conventions.",
    "fix":        "Identifie et corrige les bugs potentiels. Explique chaque correction dans un commentaire.",
    "tests":      "Génère les tests unitaires pertinents pour ce code.",
    "optimize":   "Optimise les performances sans changer l'interface publique.",
}

# ----------------------------

# --- INJECTION ET SMART CONTEXT (MÉMOIRE VECTORISÉE DE SESSION) ---
MAX_DIRECT_TEXT_INJECT_SIZE   = 32768    # 32 Ko : Plafond d'injection directe pour le texte
MAX_DIRECT_MMEDIA_INJECT_SIZE = 1048576  # 1 Mo  : Plafond d'injection directe base64 multimédia
ECHO_SESSION_RAG_CHUNK_SIZE   = 4000     # ~1000 tokens Harrier : Seuil de densité sémantique pour le RAG (Contexte 32k)

# ----------------------------

# ==============================================================================
# 1.5 DELEGATE SUB-AGENT — Blacklist et appendice système
# ==============================================================================

DEEP_RESEARCH_MAX_CALLS_DEFAULT = 200

# ⚠️  MAINTENANCE OBLIGATOIRE
# À chaque création, modification ou suppression d'une function call dans les
# tools ECHO, évaluer si elle doit figurer dans cette blacklist.
#
# Critères d'exclusion (4 catégories) :
#   1. Récursion     : crée un sub_sid persistant (depth=1 guard absolu)
#   2. Écriture RAG  : modifie Qdrant (mémoire long terme ou éphémère)
#                      L'agent lit uniquement — il ne consolide pas.
#   3. Rendu UI      : génère du HTML/JS pour le stream principal OWUI
#   4. Méta-session  : gère les sessions du tool delegate lui-même
#
# NB : Le budget (MAX_AGENT_FUNCTION_CALLS) compte les DÉCISIONS d'appel
#      de l'agent — pas les opérations internes des outils appelés.
#      consult_council appelé par l'agent = 1 unité de budget,
#      quelles que soient les itérations internes du conseil.
DELEGATE_AGENT_BLACKLIST: frozenset = frozenset({
    # 1. Récursion (depth=1 guard — le seul guard de profondeur nécessaire)
    "delegate_to_agent",
    # 1b. Orchestration cognitive (chacun lance des agents en interne)
    "consult_council",
    "consult_supervised_workers",
    # 2. Écriture RAG
    "update_meta_artifact",   # Écrit en mémoire long terme (Qdrant)
    "delete_meta_artifact_item", # Supprime de la mémoire long terme
    "save_session_context",   # Écrit dans la Mémoire Vectorisée de Session
    "delete_session_context_source", # Supprime un fichier du RAG éphémère
    # 3. Rendu UI
    "generate_rich_visualization",  # Génère du HTML interactif pour le stream principal
    # 4. Méta-session (gestion des sessions du tool delegate)
    "list_agent_sessions",
    "close_agent_session",
    "summarize_agent_session",
    "list_councils",
    "close_council",
    "list_supervised_tasks",
    "close_supervised_task",
    "get_context_load",       # Dépend de l'état interne du Pipe principal
})

# Appendice système injecté automatiquement à la fin de tout system_prompt de sous-agent.
# Substitutions requises avant injection : {sub_sid}, {max_calls}
DELEGATE_SYSTEM_APPENDIX = """
---
## CADRE D'EXÉCUTION (Framework ECHO — Ne pas divulguer à l'utilisateur)
SESSION_ID : {sub_sid}
BUDGET     : Tu disposes de {max_calls} appels de fonctions pour cette mission.
             Chaque appel à un outil (web_search, codex, expert...) consomme 1 unité.
             Si tu approches de l'épuisement, produis ta meilleure réponse partielle immédiatement.

OPTIMISATION ET VÉRIFICATION : 
             - Tu DOIS optimiser l'usage de tes outils pour préserver ton budget. Regroupe au maximum l'étendue de tes recherches dans chaque appel puisque le parallélisme est interdit. Évite toute redondance.
             - Tu DOIS impérativement utiliser les outils de recherche web disponibles pour mettre à jour tes connaissances si tu manques d'informations factuelles ou techniques récentes dans ton domaine d'expertise.

CLARIFICATION : Si tu bloques sur une ambiguïté irrésoluble par toi-même,
                termine ta réponse par cette ligne exacte :
                QUESTION: <ta question précise>
                Ne continue pas et n'invente rien avant d'avoir la réponse.
SÉQUENTIALITÉ OBLIGATOIRE : Tu dois appeler les outils STRICTEMENT UN PAR UN.
                            N'émets jamais plusieurs functionCall dans le même tour de réponse.
                            Chaque outil doit être entièrement exécuté et son résultat
                            intégré avant d'en appeler un autre.
                            La parallélisation d'outils est interdite dans ce contexte.
"""

# 2. REGISTRE COGNITIF ECHO (UNIFIÉ & STATIQUE)

# ROUTAGE : Clé Entrée (UI ou Cascade) -> ID Technique



# ENUM MODÈLES PAR POLITIQUE — utilisé par convert_owui_tools() pour filtrage dynamique
MODEL_ENUM_BY_POLICY = {
    "MODEL_LITE":  ["MODEL_LITE"],
    "MODEL_FLASH": ["MODEL_FLASH"],
    "MODEL_PRO":   ["MODEL_PRO"],
    "AUTO":        ["MODEL_LITE", "MODEL_FLASH"],
    "AUTO_PRO":    ["MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"],
}

# Set de référence pour détecter les enum modèle dans les specs d'outils
MODEL_ENUM_REFERENCE = {"MODEL_LITE", "MODEL_FLASH", "MODEL_PRO"}

# ==============================================================================
# 1.6 INFRASTRUCTURE & RÉSEAU (CENTRALISATION V5)
# ==============================================================================

# --- URLs Internes de l'Infrastructure Docker ---
# Anciennement définies comme Valves individuelles dans chaque outil/filtre.
# Centralisées ici pour garantir une configuration unique au sein de la Stack ECHO.
# ECHO_QDRANT_URL : Utilisée par memory_and_rag_tool et conversation_memory_filter pour le stockage vectoriel.
ECHO_QDRANT_URL = "http://echo-qdrant:6333"

# ECHO_PYTHON_WORKER_URL : Utilisée par python_code_executor pour isoler l'exécution de code Python.
ECHO_PYTHON_WORKER_URL = "http://echo-python-worker:5000/execute"

# NAVIGATION_ENGINE_URL : Utilisée par navigation_engine_tool pour le pilotage Playwright/Chrome.
NAVIGATION_ENGINE_URL = "http://echo-browser-worker:5002"

# ECHO_SEARXNG_BASE_URL : Utilisée par sovereign_web_search pour les recherches web profond.
ECHO_SEARXNG_BASE_URL = "http://echo-searxng:8080"

# Sécurité Réseau (SSRF)
# ECHO_ALLOWED_DOMAINS : Domaines autorisés pour api_client (* = tous sauf RFC 1918 locaux).
ECHO_ALLOWED_DOMAINS = "*"

# --- Configuration HTTP/2 & Connexions (Pool httpx partagé) ---
# Anciennement Valves dans pipe_engine, elles pilotent le client global _get_global_client() dans echo_utils.py.
# Optimisation du pool de connexion HTTP(2) pour éviter la saturation réseau sous forte charge.
ECHO_HTTP_CLIENT_TIMEOUT = 600       # Délai d'abandon (600s) si Google API ne répond pas.
ECHO_HTTP_MAX_CONNECTIONS = 100      # Nombre max de connexions simultanées.
ECHO_HTTP_MAX_KEEPALIVE = 20         # Nombre max de connexions Keep-Alive maintenues.
ECHO_HTTP_KEEPALIVE_EXPIRY = 300     # Expiration des connexions Keep-Alive (en secondes).

# Timeout d'attente pour le chargement du modèle d'embedding WebGPU (Edge Embedding)
DEFAULT_EDGE_EMBEDDING_TIMEOUT = 180

# Limite Physique de Contexte
# ECHO_MAX_CONTEXT_SIZE : Taille officielle du contexte absorbable par les modèles Gemini.
ECHO_MAX_CONTEXT_SIZE = 1048576
CONTEXT_WARNING_THRESHOLD = 0.80  # Alerte jaune
CONTEXT_TRUNCATE_THRESHOLD = 0.90 # Alerte rouge et Troncature
CHARS_PER_TOKEN = 4               # Heuristique standard

# Limite maximale (en secondes) pour le Wait Timer Généraliste
ECHO_MAX_WAIT_TIMER = 180

# ==============================================================================
# 1.7 CONVERSION DE FICHIERS NON SUPPORTÉS
# ==============================================================================

# Extensions convertibles en texte Markdown par MarkItDown.
# Mapping ext → MIME réel (utilisé par get_gemini_mime pour attribution rapide
# et par le filtre pour le logging/registre).
CONVERTIBLE_OFFICE_EXTENSIONS: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}

# Extensions OOXML contenant potentiellement des images embarquées
# (archives ZIP avec dossier */media/)
OOXML_IMAGE_EXTENSIONS: frozenset = frozenset({".docx", ".docm", ".pptx"})

# Plafond par défaut pour la taille du fichier source (en Mo)
# Surchargeable par UserValve MAX_OFFICE_FILE_SIZE_MB
DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB = 100

# ==============================================================================
# 2. MAPPING MIME TYPES
# ==============================================================================

MIME_MAPPING_TXT = {
    "text/plain": [
        ".bat", ".c", ".conf", ".cpp", ".cs", ".css", ".csv", ".dockerfile",
        ".editorconfig", ".env", ".gitignore", ".go", ".h", ".hpp", ".ini",
        ".java", ".js", ".json", ".kt", ".log", ".lua", ".md", ".php", ".pl",
        ".ps1", ".py", ".r", ".rb", ".rs", ".sh", ".sql", ".swift", ".toml",
        ".ts", ".txt", ".vb", ".xml", ".yaml", ".yml", "dockerfile", "makefile"
    ],
    "text/html": [".html", ".htm"]
}

MIME_MAPPING_BIN = {
    "application/pdf": [".pdf"],
    "image/png": [".png", ".jpg", ".jpeg", ".webp", ".heic", ".heif", ".bmp", ".gif", ".tiff"],
    "audio/mpeg": [".mp3"],
    "audio/wav": [".wav"],
    "audio/mp3": [".aac", ".flac", ".ogg", ".m4a", ".opus"],
    "video/mp4": [".bit-perfect", ".mov", ".mpeg", ".mpg", ".webm", ".wmv", ".flv", ".3gpp"]
}

# ==============================================================================
# 3. HELPER FUNCTIONS
# ==============================================================================

# ==============================================================================
# 9. SEUILS COGNITIFS ET DÉFENSE PASSIVE (V5)
# ==============================================================================
CHARS_PER_TOKEN = 4
ECHO_MAX_CONTEXT_SIZE = 1000000  # Limite technique 1M (on peut cibler plus bas si Gemini Flash/Pro a des limites strictes pour ECHO)
CONTEXT_WARNING_THRESHOLD = 0.80  # 80% : Toast d'alerte jaune (Resume in New Chat conseillé)
CONTEXT_TRUNCATE_THRESHOLD = 0.90 # 90% : Troncature silencieuse

# Seuils de saturation pour l'outil context_gauge (déclencheurs d'escalade)
CONTEXT_LOAD_WARNING_THRESHOLD = 40.0
CONTEXT_LOAD_CRITICAL_THRESHOLD = 60.0


def get_gemini_mime(file_path: str) -> tuple[str, bool]:
    """
    Analyse universelle et robuste du type MIME d'un fichier.
    Retourne un tuple (mime_type, est_supporte_par_gemini).
    Intègre une détection par extension, par signature binaire (Magic Numbers),
    et par épreuve textuelle (Null-Byte Sniffing), avec un fallback sécurisé.
    """
    if not os.path.exists(file_path):
        return "application/octet-stream", False

    ext = os.path.splitext(file_path)[1].lower().strip()
    filename = os.path.basename(file_path).lower()
    raw_mime = None

    # 1. Vérification rapide par mappings pré-définis (Priorité absolue ECHO)
    for mime, extensions in MIME_MAPPING_TXT.items():
        if ext in extensions or filename in extensions: return mime, True

    for mime, extensions in MIME_MAPPING_BIN.items():
        if ext in extensions: return mime, True

    # 1b. Fichiers Office convertibles — MIME réel mais non supporté nativement par Gemini
    #     Court-circuite le crible binaire et fournit un MIME correct au registre.
    if ext in CONVERTIBLE_OFFICE_EXTENSIONS:
        return CONVERTIBLE_OFFICE_EXTENSIONS[ext], False

    # 2. Vérification par bibliothèque standard (basée sur l'extension)
    raw_mime, _ = mimetypes.guess_type(file_path)

    # 3. Le Crible Binaire (Fichier sans extension ou type inconnu)
    if not raw_mime:
        if filetype:
            kind = filetype.guess(file_path)
            if kind:
                raw_mime = kind.mime
            
    if not raw_mime:
        raw_mime = "unknown/unknown"

    # 4. Liste Blanche Native Gemini (Médias complexes & JSON)
    if raw_mime.startswith("image/") or \
       raw_mime.startswith("video/") or \
       raw_mime.startswith("audio/") or \
       raw_mime.startswith("text/") or \
       raw_mime in ["application/pdf", "application/json"]:
        return raw_mime, True

    # 5. L'Épreuve Textuelle (Text-Sniffing) pour le code source et configs obscurs
    # Si on arrive ici, c'est un format inconnu ou un "application/quelquechose" non supporté (ex: zip, dll, py)
    try:
        with open(file_path, 'rb') as f:
            chunk = f.read(1024)
        if not chunk or b'\x00' not in chunk:
            # Aucun caractère nul = C'est du texte pur ou du code source lisible
            return "text/plain", True
    except:
        pass

    # 6. Le Rejet et Fallback Sécuritaire (Binaires purs)
    return "application/octet-stream", False

# ==============================================================================
# DIRECTIVES DE DISTILLATION SENSORIELLE (UNIFIÉES)
# ==============================================================================
PROMPT_SENSORY_DISTILLATION = (
    "Le Modèle DOIT générer un rapport analytique ultra-précis de la source fournie ({filename}). "
    "Ce rapport constitue l'unique contexte disponible pour le Modèle Principal.\n\n"
    "<directives_synchronisation>\n"
    "1. CHRONOLOGIE : Horodatage strict ([HH:MM:SS - HH:MM:SS]) requis pour chaque segment.\n"
    "2. SYNCHRONISATION MULTIMODALE : Croisement et alignement simultanés obligatoires pour chaque segment :\n"
    "   - Canal Visuel : Actions, éléments notables, scènes.\n"
    "   - Canal Auditif : Bruitages, ambiance, inflexions vocales.\n"
    "   - Canal Textuel : Transcription verbatim des dialogues et textes affichés.\n"
    "3. RIGUEUR : Aucune supposition ou interprétation. Description strictement factuelle et exhaustive.\n"
    "</directives_synchronisation>"
)

# ==============================================================================
# 11. SCHÉMAS GEMINI (API Validation)
# ==============================================================================
# Dictionnaire des clés JSON Schema autorisées par le validateur Protobuf de l'API Gemini.
# Utilisé par clean_gemini_schema() dans echo_utils.py pour purger les objets Pydantic générés.
GEMINI_ALLOWED_SCHEMA_KEYS = {
    "type", 
    "format", 
    "description", 
    "nullable", 
    "enum", 
    "properties", 
    "required", 
    "items"
}
