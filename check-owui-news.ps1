<#
.SYNOPSIS
    ECHO V6 - Veille différentielle des nouveautés amont Open WebUI
    VERSION : 6.0.0.1
#>
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.0.0.1: Création de l'outil d'audit différentiel sur .tmp_audit/open-webui.

$ErrorActionPreference = "Stop"
$lockFile = Join-Path $PSScriptRoot "16-docker-echo-webui\UPSTREAM_LOCK.json"
$owuiMirror = Join-Path $PSScriptRoot ".tmp_audit\open-webui"

if (-not (Test-Path $lockFile)) {
    Write-Error "Fichier UPSTREAM_LOCK.json introuvable : $lockFile"
}
$lock = Get-Content $lockFile -Raw | ConvertFrom-Json
$baseCommit = $lock.base_commit

Write-Host "Mise à jour du miroir Open WebUI ($owuiMirror)..." -ForegroundColor Cyan
git -C $owuiMirror fetch origin main

$headCommit = (git -C $owuiMirror rev-parse origin/main).Trim()
$headTag = (git -C $owuiMirror describe --tags --always origin/main).Trim()

Write-Host "Base actuelle ECHO V6 : $($lock.base_tag) ($($baseCommit.Substring(0,8)))" -ForegroundColor Gray
Write-Host "Dernier amont OWUI    : $headTag ($($headCommit.Substring(0,8)))" -ForegroundColor Gray
Write-Host "----------------------------------------" -ForegroundColor DarkGray

Write-Host "`n--- Nouveaux Commits pertinents (Frontend Chat & Socket/Backend) ---" -ForegroundColor Cyan
git -C $owuiMirror log "$baseCommit..origin/main" --oneline -n 40 -- src/lib/components/chat backend/open_webui/socket backend/open_webui/routers/chats.py
