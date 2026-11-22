# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    ECHO V6 - Veille différentielle des nouveautés amont Open WebUI
    VERSION : 6.18.7.1
#>
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.7.1: Déplacement dans .hygiene-owui/, tolérance miroir absent, filtrage backported_commits et surveillance middleware.py.
# 6.0.0.1: Création de l'outil d'audit différentiel sur .tmp_audit/open-webui.

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$lockFile = Join-Path $RepoRoot '16-docker-echo-webui\UPSTREAM_LOCK.json'
$owuiMirror = Join-Path $RepoRoot '.tmp_audit\open-webui'

if (-not (Test-Path $lockFile)) {
  Write-Error "Fichier UPSTREAM_LOCK.json introuvable : $lockFile"
}

if (-not (Test-Path (Join-Path $owuiMirror '.git'))) {
  Write-Warning "Miroir amont absent : $owuiMirror. Initialisez-le avec : git clone https://github.com/open-webui/open-webui.git `"$owuiMirror`""
  exit 0
}

$lock = Get-Content $lockFile -Raw -Encoding UTF8 | ConvertFrom-Json
$baseCommit = $lock.base_commit
$backported = @($lock.backported_commits)

Write-Host "Mise à jour du miroir Open WebUI ($owuiMirror)..." -ForegroundColor Cyan
git -C $owuiMirror fetch origin main

$headCommit = (git -C $owuiMirror rev-parse origin/main).Trim()
$headTag = (git -C $owuiMirror describe --tags --always origin/main).Trim()

Write-Host "Base actuelle ECHO V6 : $($lock.base_tag) ($($baseCommit.Substring(0,8)))" -ForegroundColor Gray
Write-Host "Dernier amont OWUI    : $headTag ($($headCommit.Substring(0,8)))" -ForegroundColor Gray
Write-Host '----------------------------------------' -ForegroundColor DarkGray

$watchedPaths = @(
  'src/lib/components/chat',
  'backend/open_webui/socket',
  'backend/open_webui/routers/chats.py',
  'backend/open_webui/utils/middleware.py'
)

Write-Host "`n--- Nouveaux Commits pertinents (hors backported_commits) ---" -ForegroundColor Cyan
$rawLog = @(git -C $owuiMirror log "$baseCommit..origin/main" --oneline -n 60 -- @watchedPaths)
$filteredLog = foreach ($line in $rawLog) {
  if ([string]::IsNullOrWhiteSpace($line)) { continue }
  $shortHash = ($line -split '\s+', 2)[0]
  $isBackported = $false
  foreach ($bp in $backported) {
    if ($shortHash.StartsWith($bp, [System.StringComparison]::OrdinalIgnoreCase)) {
      $isBackported = $true
      break
    }
  }
  if (-not $isBackported) { $line }
}

if ($filteredLog) {
  $filteredLog | ForEach-Object { Write-Host $_ }
} else {
  Write-Host '(Aucun nouveau commit non backporté sur les chemins surveillés)' -ForegroundColor Green
}
