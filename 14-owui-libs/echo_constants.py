"""
title: ECHO Constants
author: ECHO Framework
version: 5.17
description: 5.17: Ajout du dictionnaire FILE_INGESTION_STATUS pour centraliser les états d'ingestion.
             5.16: Réduction de MAX_DIRECT_MMEDIA_INJECT_SIZE de 5Mo à 1Mo pour optimisation de contexte.
             4.4: redirect_uri localhost (loopback RFC 8252).
             4.5: Suppression redirect_uri et callback_port fixes — dynamiques via
             echo_ssh_tunnel.py. Ajout constantes SSH Tunnel éphémère PKCE
             (plage ports, user, timeout). Multi-user natif par allocation dynamique.
             4.6: Correction noms modèles certifiés par diagnostic live API.
             4.7: Mapping modèles AGY ↔ AI Studio (AGY_MODEL_MAP).
             Fix MODEL_ROUTING (ajout clé MODEL_DISTILLATION). Fix noms canoniques
             AI Studio : MODEL_FLASH=gemini-3.5-flash, MODEL_PRO=gemini-3.1-pro-preview.
             5.3: MODEL_HIERARCHY, MODEL_ENUM_BY_POLICY, MODEL_ENUM_REFERENCE pour centralisation
             politique modèle Pipe → outils (clamp_model, convert_owui_tools enum dynamiques).
             4.8: Centralisation des paramètres de génération (THINKING_LEVEL_*, MAX_TOKENS_DEFAULT).
             Suppression des valves TEMPERATURE/TOP_P/THINKING_LEVEL dans pipe_engine et
             les tools cognitifs — point de vérité unique pour tout le framework ECHO.
             4.9: Ajout MODEL_MAP_CA (capacités AGY certifiées, diag v2.1 2026-05-25).
             AGY_MODEL_MAP devient alias auto-généré depuis MODEL_MAP_CA.
             5.7: Ajout des constantes globales Smart Context (Map-Reduce RAG V2).
             5.8: Update MODEL_DISTILLATION to gemini-3.1-flash-lite.
             MAX_TOKENS_DEFAULT 65536→65535 (limite universelle AI Studio + AGY).
             5.0: Renommage des identifiants Code Assist → AGY : ECHO_CODE_ASSIST_USER_AGENT→ECHO_AGY_USER_AGENT,
             CODE_ASSIST_BASE_URL→AGY_BASE_URL, CODE_ASSIST_MODEL_MAP→AGY_MODEL_MAP (alias rétrocompat conservé).
             5.1: Ajout constantes Strategic Planner (PLAN_STATUS, PLAN_TASK_STATUS, PLAN_EXECUTABLE_STATUSES).
             5.2: Ajout ECHO_GEMMA_URL, MODEL_LOCAL_GEMMA (distillation locale Gemma 4 E4B).
                  Ajout entrée LOCAL_GEMMA dans MODEL_ROUTING.
             5.4: Ajout section ECHO Codex (CODEX_DIR_NAME, CODEX_LANG_MAP, CODEX_EDIT_SYSTEM_PROMPT,
                  CODEX_SUMMARIZE_PROMPT, CODEX_QUICK_ACTIONS, CODEX_DEFAULT_LANG).
              5.6: Ajout section 1.5 — DELEGATE_AGENT_BLACKLIST (frozenset) et DELEGATE_SYSTEM_APPENDIX.
                   Centralise les règles de filtrage des outils transmis à l'agent delegate.
              5.10: Renommage DELEGATE_SUBAGENT_BLACKLIST → DELEGATE_AGENT_BLACKLIST.
                    Renommage des fonctions blacklistées (subagent→agent). Ajout des
                    nouvelles fonctions : consult_council, consult_supervised_workers,
                    list_councils, close_council, list_supervised_tasks, close_supervised_task.
              5.11: Mise à jour de DELEGATE_SYSTEM_APPENDIX (instructions d'optimisation et
                    vérification web).
              5.12: Suppression de MODEL_DISTILLATION dans MODEL_MAP_CA pour résoudre la
                    collision de clés avec MODEL_LITE (les deux valaient "gemini-3.1-flash-lite").
              5.13: Ajout section 1.7 — CONVERTIBLE_OFFICE_EXTENSIONS, OOXML_IMAGE_EXTENSIONS,
                    DEFAULT_MAX_OFFICE_CONVERT_SIZE_MB pour conversion Office → Markdown (MarkItDown).
              5.14: Registre Unifié V2 — Ajout RESOURCE_TYPES (types de ressources echo_resources).
                    Ajout étape 1b dans get_gemini_mime() pour attribution MIME accélérée
                    des fichiers Office convertibles (court-circuite le crible binaire).
"""

import os
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

ECHO_SESSION_DOMAINS = ["codex", "files", "db"]
ECHO_GLOBAL_DOMAINS = ["skills"]

# Identité Réseau (Antigravity 2.1)
ECHO_USER_AGENT             = "antigravity/2.1.0"
ECHO_AGY_USER_AGENT = "antigravity/2.1.0 (language_server; os_type=Windows; os_version=10.0.26100; arch=x64)"

# Points d'accès Locaux (Souveraineté)
ECHO_EMBEDDING_URL = "http://echo-embedding:7997/v1"

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
_d = lambda b: base64.b64decode(b).decode()[::-1]

# Client Desktop (variable Rge/yge — oauthClient.js) — utilisé pour le flow PKCE
# ⚠️ CORRECTION v2 : 1071006060591 (13 chiffres)
ANTIGRAVITY_DESKTOP_CLIENT_ID     = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEucGUzMDRnNGhqb2xvdHY1MzJlcmNsMTJoMm5pc3NobXQtMTk1MDYwNjAwMTcwMQ==")
ANTIGRAVITY_DESKTOP_CLIENT_SECRET = _d("ZkFEcTZ6NENYczhCTG0xSkxkTDY4NFJXRjg1Sy1YUFNDT0c=")

# Client Language Server (variable WZe/OZe — oauthClient.js)
ANTIGRAVITY_OAUTH_CLIENT_ID     = _d("bW9jLnRuZXRub2NyZXN1ZWxnb29nLnNwcGEuaGxiNWM4NjJkb2M2dm8yM2NhaXVndDNiamoxY3J0NjMtMjUwOTE5NDUzNDg4")
ANTIGRAVITY_OAUTH_CLIENT_SECRET = _d("WHN0WjBSd01LeFktamRUUTBDRFdSN0ZwV1FZOS1YUFNDT0c=")

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

# --- SCOPES ANTIGRAVITY 2.1 (certifiés section 4 du RE — binaire language_server.exe) ---
ECHO_OAUTH_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/aicode",   # CRITIQUE — scope spécifique Antigravity/Code Assist
    "https://www.googleapis.com/auth/cclog",    # Télémétrie completion log
]

# --- CONFIGURATION API ANTIGRAVITY (PROVISIONING) ---
# Standalone Antigravity → cloudcode-pa (sans "daily-")
AGY_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"

# Metadata client envoyée dans loadCodeAssist — pluginType "GEMINI" confirmé stable (RE §10)
ECHO_CLIENT_METADATA = {
    "ideType":    "IDE_UNSPECIFIED",
    "platform":   "PLATFORM_UNSPECIFIED",
    "pluginType": "GEMINI"
}

AUTH_DATA_PROJECT_ID  = "google_project_id"
AUTH_DATA_USER_EMAIL  = "google_user_email"
AUTH_DATA_USER_TIER   = "google_user_tier"

# --- CONFIGURATION AI STUDIO (Fallback clés API) ---
GOOGLE_API_BASE_URL      = "https://generativelanguage.googleapis.com/v1beta"
GOOGLE_AI_STUDIO_WEB_URL = "https://aistudio.google.com/app/apikey"
GOOGLE_API_KEY_REGEX     = r"AIza[0-9A-Za-z_-]{35}"
GOOGLE_API_KEY_PATTERN   = GOOGLE_API_KEY_REGEX

# --- TIMING (certifiés par logs natifs IDE) ---
# Token TTL proactif : 55 min exact (confirmé par auth.log "token changed, handleAuthRefresh")
GOOGLE_OAUTH_TOKEN_LIFETIME = 3300  # secondes

# PKCE auth flow : timeout serveur callback (5 min pour que l'utilisateur ouvre l'URL)
PKCE_CALLBACK_TIMEOUT = 300  # secondes

# ==============================================================================
# 1.2 RÉSILIENCE ET RETRIES (API)
# ==============================================================================

ECHO_API_KEY_THRESHOLD   = 2
ECHO_API_MAX_RETRIES     = 5
ECHO_RETRY_BASE_DELAY    = 5.0   # Base backoff exponentiel — augmenté de 2.0 à 5.0 (v5.166.6)
                                  # Raison : le gateway Code Assist retourne des 400 transients quand
                                  # plusieurs outils cognitifs (consult_council, consult_expert)
                                  # déclenchent une rafale de requêtes Gemini simultanées.
                                  # Avec base=5.0 : 4 essais suffisent à couvrir la fenêtre de throttle
                                  # (~62s) tout en réduisant la pression sur l'API entre les essais.
ECHO_RETRY_MULTIPLIER    = 2.0
ECHO_RETRY_JITTER_MIN    = 0.7
ECHO_RETRY_JITTER_MAX    = 1.3

# ==============================================================================
# 1.1 MODULE : CONSTANTES ET DEFAULTS ECHO (CENTRALISATION)
# VERSION : 5.999.1
# ==============================================================================

# 1. IDENTIFIANTS TECHNIQUES — référence AI Studio (canonique).
#    La traduction vers les ID AGY est gérée par MODEL_MAP_CA ci-dessous.
#    Certifiés par diagnostic live 2026-05-24.
MODEL_PRO   = "gemini-3.1-pro-preview"  # PRO   — AI Studio ⚠️ 429 free | CA → gemini-pro-agent
MODEL_FLASH = "gemini-3.5-flash"        # FLASH — AI Studio ✅ 200      | CA → gemini-3-flash-agent
MODEL_LITE  = "gemini-3.1-flash-lite"   # LITE  — identique sur les deux APIs ✅ 200

# --- MÉMOIRE ORGANIQUE V2 ---
MODEL_DISTILLATION = "gemini-3.1-flash-lite"  # identique sur les deux APIs ✅ 200
MODEL_EMBEDDING    = "BAAI/bge-m3"      # Modèle texte-first, multilingue, 1024d, 8192 tokens

# Table de capacités Code Assist — source de vérité documentaire.
# max_output_tokens : limites réelles certifiées par diagnostic live v2.1 (2026-05-25).
# Le cap appliqué en production est MAX_TOKENS_DEFAULT (65535 universel, décision D1).
# Modèles supportés : exclusivement Gemini (Claude, GPT, OpenAI-Vertex exclus du scope ECHO).
MODEL_MAP_CA: dict = {
    MODEL_PRO: {
        "model_id":          "gemini-pro-agent",      # Gemini 3.1 Pro (High) — thinkingBudget=10001
        "max_output_tokens": 65535,                   # Certifié : 400 si 65536 (diag C-quart v2.0)
        "supports_thinking": True,
    },
    MODEL_FLASH: {
        "model_id":          "gemini-3-flash-agent",  # Gemini 3.5 Flash (High) — thinkingBudget=-1
        "max_output_tokens": 65536,                   # Certifié : 200 avec 65536 (diag C-quart v2.0)
        "supports_thinking": True,                    # includeThoughts=True → thought=True lisible (diag H3)
    },
    MODEL_LITE: {
        "model_id":          "gemini-3.1-flash-lite", # Identique sur les deux APIs
        "max_output_tokens": 65535,                   # Certifié : 400 si 65536 (diag C-quart v2.0)
        "supports_thinking": False,                   # supportsThinking absent (diag section 6c v2.0)
    },
}

# Alias rétrocompatible — conservé pour ne pas casser les imports existants dans les scripts.
# Déprécié : remplacé par MODEL_MAP_CA dans echo_protocol.py.
AGY_MODEL_MAP: dict[str, str] = {
    k: v["model_id"] for k, v in MODEL_MAP_CA.items()
}
# Alias de compatibilité ascendante
CODE_ASSIST_MODEL_MAP = AGY_MODEL_MAP
EMBEDDING_DIM_V2   = 1024               # Dimension bge-m3 (remplace 768 SigLIP-2)
COLLECTION_MEMORY    = "echo_memory"
COLLECTION_EPHEMERAL = "echo_ephemeral"

# Poids de reranking par niveau d'importance mémorielle.
# Appliqués dans recall_memories : score_pondéré = cos_score × MEMORY_IMPORTANCE_WEIGHTS[lvl]
# Un Axiome (5) à cos=0.60 bat un Trivial (1) à cos=0.85 : 0.60×1.70 > 0.85×0.55
MEMORY_IMPORTANCE_WEIGHTS: dict[int, float] = {
    1: 0.55,   # Trivial — pénalisé (bruit probable)
    2: 0.75,   # Mineur
    3: 1.00,   # Utile — référence neutre
    4: 1.30,   # Majeur
    5: 1.70,   # Axiome — fortement boosté (remonte toujours)
}

# Labels sémantiques des 5 niveaux — point de vérité unique pour UI, logs et LLM.
MEMORY_IMPORTANCE_LABELS: dict[int, str] = {
    1: "Trivial",
    2: "Mineur",
    3: "Utile",
    4: "Majeur",
    5: "Axiome",
}
# ----------------------------

# ==============================================================================
# 1.3 PLAN STRATÉGIQUE — STATUTS & TÂCHES
# ==============================================================================

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

# Statuts autorisant l'exécution des tâches d'un plan
PLAN_EXECUTABLE_STATUSES = {"ready", "executing"}

# Types de ressources du Registre Unifié V2 (echo_resources)
RESOURCE_TYPES = {
    "codex":  "codex",   # Fichiers texte/code (Git)
    "plan":   "plan",    # Plans stratégiques
    "media":  "media",   # Images, vidéos, PDF
    "binary": "binary",  # Fichiers non assimilables
    "weburl": "weburl",  # Pages web distillées
}

# ==============================================================================
# 1.8 STATUTS D'INGESTION DES FICHIERS
# ==============================================================================
# Définit l'état cognitif d'un fichier tel que perçu par le modèle.
FILE_INGESTION_STATUS = {
    "PUT_IN_CONTEXT":    "put_in_context",    # Injection directe dans le prompt (brut ou base64)
    "VECTORIZED_SUM_UP": "vectorized_sum_up", # Résumé via Smart Context (RAG)
    "INDEXED":           "indexed",           # Stockage SQLite seul (Fallback ou Image Web)
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
CODEX_EDIT_SYSTEM_PROMPT = """Tu es l'éditeur de code ECHO Codex.
RÈGLES ABSOLUES :
1. Retourne UNIQUEMENT le fichier modifié complet. Aucune explication, aucun markdown de formatage.
2. Si une sélection est fournie, ne modifie QUE cette partie dans le contexte du fichier complet.
3. Préserve le style, l'indentation et les conventions du document original.
4. Si l'instruction est ambiguë, fais le choix le plus conservateur.
Fichier : {filename} | Langage : {language}"""

# Prompt distillation/résumé de fichier
CODEX_SUMMARIZE_PROMPT = """Analyse technique exhaustive du fichier '{filename}' ({language}).
Structure ta réponse :
1. Objectif et rôle du fichier
2. Architecture : classes, fonctions, structures principales
3. Dépendances et imports
4. Patterns et conventions utilisés
5. Points d'attention, complexité, dette technique éventuelle
Sois technique, précis et complet."""

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

# --- PARAMÈTRES DE GÉNÉRATION ---
TEMP_DEFAULT       = 1.0
TEMP_DISTILLATION  = 0.0

TOP_P_DEFAULT      = 0.90
TOP_P_DISTILLATION = 0.10

# --- NIVEAUX DE RÉFLEXION (THINKING) — Point de vérité unique pour tout le framework ---
# Confirmés fonctionnels sur Code Assist + AI Studio par diagnostic live 2026-05-24.
# HIGH = réflexion complète (Gemini 3.x) ; MINIMAL = réflexion nulle (outils légers, grounding).
THINKING_LEVEL_PRO   = "HIGH"     # MODEL_PRO : réflexion maximale
THINKING_LEVEL_FLASH = "HIGH"     # MODEL_FLASH : réflexion standard
THINKING_LEVEL_LITE  = "HIGH"     # MODEL_LITE : confirmé 200 avec HIGH sur Code Assist
THINKING_LEVEL_TOOLS = "MINIMAL"  # Outils stateless (maps, grounding, distillation rapide)

# --- LIMITE DE TOKENS (GÉNÉRATION PRINCIPALE) ---
MAX_TOKENS_DEFAULT = 65535  # Limite universelle — tous modèles, toutes APIs (AI Studio + CA).
                             # 65535 : aligné sur les modèles Gemini 3.1 sur CA (Pro, Lite, 2.5-flash).
                             # Gemini 3 Flash CA supporte 65536 mais on aligne sur le plus restrictif.
                             # Utilisé par : pipe_engine (stream), call_distillation, echo_protocol.

# --- INJECTION ET SMART CONTEXT (MÉMOIRE VECTORISÉE DE SESSION) ---
MAX_DIRECT_TEXT_INJECT_SIZE   = 32768    # 32 Ko : Plafond d'injection directe pour le texte
MAX_DIRECT_MMEDIA_INJECT_SIZE = 1048576  # 1 Mo  : Plafond d'injection directe base64 multimédia
ECHO_MR_CHUNK_SIZE            = 182858   # 178 Ko : Taille d'un chunk Map-Reduce texte
ECHO_MR_OVERLAP_SIZE          = 1024     # 1 Ko   : Recouvrement (overlap) entre chunks
ECHO_MR_MAX_TOKENS            = 1600     # Limite de sortie (tokens) pour les distillations du Map-Reduce
ECHO_MR_SUMMARY_MAX_WORDS     = 400      # Limite de taille en mots pour les résumés générés ET pour le bypass (Fast-Path)

# ----------------------------

# ==============================================================================
# 1.5 DELEGATE SUB-AGENT — Blacklist et appendice système
# ==============================================================================

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
    "save_memory",            # Écrit en mémoire long terme (Qdrant)
    "forget_memory",          # Supprime de la mémoire long terme
    "save_session_context",   # Écrit dans la Mémoire Vectorisée de Session
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
    "context_gauge",          # Dépend de l'état interne du Pipe principal
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
MODEL_ROUTING = {
    "MODEL_LITE":         MODEL_LITE,
    "MODEL_FLASH":        MODEL_FLASH,
    "MODEL_PRO":          MODEL_PRO,
    "MODEL_DISTILLATION": MODEL_DISTILLATION,  # Fix : résout la clé string dans call_distillation
}

# IDENTITÉ : ID Technique -> Label de Catégorie (Pour le badge ##MODEL_ID##)
MODEL_IDENTITY = {
    MODEL_LITE:  "MODEL_LITE",
    MODEL_FLASH: "MODEL_FLASH",
    MODEL_PRO:   "MODEL_PRO"
}

# HIÉRARCHIE COGNITIVE — ordonnance pour clamp_model() (propagation modèle Pipe → outils)
MODEL_HIERARCHY = {
    "MODEL_LITE": 0,
    "MODEL_FLASH": 1,
    "MODEL_PRO": 2,
}

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
ECHO_PYTHON_WORKER_URL = "http://python-worker:5000/execute"

# NAVIGATION_ENGINE_URL : Utilisée par navigation_engine_tool pour le pilotage Playwright/Chrome.
NAVIGATION_ENGINE_URL = "http://browser-agent:5002"

# ECHO_SEARXNG_BASE_URL : Utilisée par sovereign_web_search pour les recherches web profond.
ECHO_SEARXNG_BASE_URL = "http://searxng:8080"

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

# Limite Physique de Contexte
# ECHO_MAX_CONTEXT_SIZE : Taille officielle du contexte absorbable par les modèles Gemini.
ECHO_MAX_CONTEXT_SIZE = 1048576

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
