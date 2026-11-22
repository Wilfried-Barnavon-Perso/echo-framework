#!/bin/bash
# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
# ==============================================================================
# SCRIPT : echo-version.sh
# VERSION : 6.18.16.1
# AUTEUR : Wilfried BARNAVON
# ==============================================================================
# ROLE : Afficher la version et l'état de versioning du framework ECHO
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.16.1: Décodage des préfixes linux: et wsl2: de echo_deploy_script_version avec rétrocompatibilité Hyper-V.
# 6.7.2.1: Création du script echo-version.sh affichant la version et l'état de versioning ECHO.

set -u

# --- INITIALISATION TOLÉRANTE ---
ECHO_ROOT="${ECHO_ROOT:-/opt/ECHO}"
GLOBALS_FILE="$ECHO_ROOT/echo-scripts/echo-globals.sh"
if [ -f "$GLOBALS_FILE" ] && [ -r "$GLOBALS_FILE" ]; then
    # shellcheck source=/dev/null
    source "$GLOBALS_FILE"
fi

ECHO_SCRIPTS="${ECHO_SCRIPTS:-$ECHO_ROOT/echo-scripts}"
ECHO_SOURCE="${ECHO_SOURCE:-$ECHO_ROOT/source}"
ECHO_VERSION_FILE="${ECHO_VERSION_FILE:-$ECHO_ROOT/ECHO_VERSION}"
ECHO_BRANCH_FILE="${ECHO_BRANCH_FILE:-$ECHO_ROOT/ECHO_BRANCH}"
ECHO_DEPLOY_VER_FILE="${ECHO_DEPLOY_VER_FILE:-$ECHO_ROOT/echo_deploy_script_version}"
ECHO_DOCKER_WEBUI="${ECHO_DOCKER_WEBUI:-$ECHO_ROOT/docker-echo-webui}"

# Résolution du dépôt local (fonctionnement autonome hors /opt/ECHO)
SCRIPT_PATH="$0"
if command -v readlink >/dev/null 2>&1; then
    RESOLVED_PATH=$(readlink -f "$0" 2>/dev/null || true)
    if [ -n "$RESOLVED_PATH" ]; then
        SCRIPT_PATH="$RESOLVED_PATH"
    fi
fi
SCRIPT_DIR=$(cd "$(dirname "$SCRIPT_PATH")" 2>/dev/null && pwd || dirname "$SCRIPT_PATH")
LOCAL_REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# --- UTILITAIRES LECTURE SEULE ---
usage() {
    cat <<'EOF'
Usage: echo-version.sh [OPTION]

Affiche la version et l'état de versioning du framework ECHO.

Options:
  -s, --short   Affiche uniquement le numéro de version brut
  --json        Affiche les informations au format JSON
  -h, --help    Affiche cette aide et quitte
EOF
}

read_clean_file() {
    local file_path="$1"
    if [ -f "$file_path" ] && [ -r "$file_path" ]; then
        head -n 1 "$file_path" 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
    fi
}

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\t'/\\t}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\n'/\\n}"
    s=$(printf '%s' "$s" | LC_ALL=C tr -d '\000-\037\177')
    printf '"%s"' "$s"
}

json_value() {
    local val="$1"
    if [ -n "$val" ]; then
        json_escape "$val"
    else
        printf 'null'
    fi
}

# --- ANALYSE DES ARGUMENTS ---
OUTPUT_MODE="default"
while [ $# -gt 0 ]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        -s|--short)
            OUTPUT_MODE="short"
            shift
            ;;
        --json)
            OUTPUT_MODE="json"
            shift
            ;;
        *)
            echo "Erreur : option inconnue '$1'. Utilisez --help pour l'aide." >&2
            exit 1
            ;;
    esac
done

# --- COLLECTE DES DONNÉES ---
version=""
version_source=""
installed_version=$(read_clean_file "$ECHO_VERSION_FILE")
local_repo_version=$(read_clean_file "$LOCAL_REPO_ROOT/VERSION")

if [ -n "$installed_version" ]; then
    version="$installed_version"
    version_source="installed"
elif [ -n "$local_repo_version" ]; then
    version="$local_repo_version"
    version_source="repo"
fi

source_version=$(read_clean_file "$ECHO_SOURCE/VERSION")
if [ -z "$source_version" ]; then
    source_version="$local_repo_version"
fi

ui_version=$(read_clean_file "$ECHO_DOCKER_WEBUI/UI_VERSION")
if [ -z "$ui_version" ]; then
    ui_version=$(read_clean_file "$LOCAL_REPO_ROOT/16-docker-echo-webui/UI_VERSION")
fi

deploy_script_version=$(read_clean_file "$ECHO_DEPLOY_VER_FILE")

git_repo_dir=""
if [ -e "$ECHO_SOURCE/.git" ]; then
    git_repo_dir="$ECHO_SOURCE"
elif [ -e "$LOCAL_REPO_ROOT/.git" ]; then
    git_repo_dir="$LOCAL_REPO_ROOT"
fi

branch=$(read_clean_file "$ECHO_BRANCH_FILE")
commit=""
commit_subject=""

if command -v git >/dev/null 2>&1 && [ -n "$git_repo_dir" ]; then
    if [ -z "$branch" ]; then
        branch=$(git -C "$git_repo_dir" rev-parse --abbrev-ref HEAD 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)
    fi
    commit=$(git -C "$git_repo_dir" rev-parse --short HEAD 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)
    commit_subject=$(git -C "$git_repo_dir" log -1 --pretty=%s 2>/dev/null | tr -d '\r\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//' || true)
fi

# --- AFFICHAGE ---
case "$OUTPUT_MODE" in
    short)
        if [ -z "$version" ]; then
            echo "Erreur : version ECHO introuvable." >&2
            exit 1
        fi
        printf '%s\n' "$version"
        ;;
    json)
        printf '{'
        printf '"version":%s,' "$(json_value "$version")"
        printf '"version_source":%s,' "$(json_value "$version_source")"
        printf '"source_version":%s,' "$(json_value "$source_version")"
        printf '"ui_version":%s,' "$(json_value "$ui_version")"
        printf '"branch":%s,' "$(json_value "$branch")"
        printf '"commit":%s,' "$(json_value "$commit")"
        printf '"deploy_script_version":%s' "$(json_value "$deploy_script_version")"
        printf '}\n'
        ;;
    default)
        if [ -n "$version" ]; then
            if [ "$version_source" = "repo" ]; then
                printf 'ECHO : %s (dépôt local)\n' "$version"
            else
                printf 'ECHO : %s\n' "$version"
            fi
        else
            printf 'ECHO : inconnue\n'
        fi

        if [ -n "$branch" ]; then
            printf 'Branche : %s\n' "$branch"
        fi
        if [ -n "$ui_version" ]; then
            printf 'UI : %s\n' "$ui_version"
        fi
        if [ -n "$commit" ]; then
            if [ -n "$commit_subject" ]; then
                printf 'Commit : %s (%s)\n' "$commit" "$commit_subject"
            else
                printf 'Commit : %s\n' "$commit"
            fi
        fi
        if [ -n "$deploy_script_version" ]; then
            case "$deploy_script_version" in
                linux:*)
                    printf 'Script Linux : %s\n' "${deploy_script_version#linux:}"
                    ;;
                wsl2:*)
                    printf 'Script WSL2 : %s\n' "${deploy_script_version#wsl2:}"
                    ;;
                *)
                    printf 'Script Hyper-V : %s\n' "$deploy_script_version"
                    ;;
            esac
        fi
        if [ "$version_source" = "installed" ] && [ -n "$source_version" ] && [ "$source_version" != "$version" ]; then
            printf 'Attention : version source (%s) différente de la version installée (%s).\n' "$source_version" "$version"
        fi
        ;;
esac
