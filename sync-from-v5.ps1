<#
.SYNOPSIS
    ECHO V6 - Synchronisation des évolutions depuis la branche dev (V5)
    VERSION : 6.0.0.1
#>
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.0.0.1: Création initiale du pont de synchronisation V5 (dev) -> V6 (dev_v6).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Synchronisation des nouveautés V5 (dev) vers V6 (dev_v6)..." -ForegroundColor Cyan
git fetch v5-local dev
git merge v5-local/dev --no-edit
if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Nouveautés V5 intégrées avec succès dans dev_v6." -ForegroundColor Green
} else {
    Write-Host "⚠️ Conflits détectés lors de la fusion. Résolvez-les avant de valider." -ForegroundColor Yellow
}
