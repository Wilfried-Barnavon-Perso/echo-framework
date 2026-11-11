<#
.SYNOPSIS
    ECHO V6 - Synchronisation intelligente des évolutions depuis la branche dev (V5)
    VERSION : 6.3.0.1
#>
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.3.0.1: Sanctuarisation des filtres allégés et supprimés (tcp_keepalive, edge_embed, window_manager, context_gauge).
# 6.0.0.6: Sanctuarisation de 00-echo-scripts et open_webui_bug_correction_filter.py et gestion git rm des fichiers supprimés en V6.
# 6.0.0.5: Remplacement des émojis bruts par des points de code Unicode dynamiques (immunité d'encodage PowerShell 5.1).
# 6.0.0.4: Fix encodage UTF-8 BOM pour PowerShell 5.1 et résilience de restauration sanctuarisée.
# 6.0.0.3: Sanctuarisation des manifestes Compose YAML (01-config/*.yml) contre les écrasements V5.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Définition dynamique des symboles Unicode (immunité totale contre les divergences d'encodage Windows-1252 / UTF-8)
$icoSync = [char]::ConvertFromUtf32(0x1F504) # Symbole synchronisation
$icoLock = [char]::ConvertFromUtf32(0x1F512) # Symbole cadenas
$icoOk   = [char]::ConvertFromUtf32(0x2705)  # Symbole validation
$icoErr  = [char]::ConvertFromUtf32(0x274C)  # Symbole croix rouge
$icoWarn = [char]::ConvertFromUtf32(0x26A0)  # Symbole avertissement
$icoInfo = [char]::ConvertFromUtf32(0x2139)  # Symbole information
$icoPlan = [char]::ConvertFromUtf32(0x1F4CB) # Symbole plan
$icoShld = [char]::ConvertFromUtf32(0x1F6E1) # Symbole bouclier

# 1. Vérification de l'état de l'arbre de travail
$dirty = git status --porcelain
if ($dirty) {
    Write-Error "$icoErr L'espace de travail contient des modifications non validées. Committez ou remisez avant de synchroniser."
    exit 1
}

# 2. Vérification de la branche courante
$currentBranch = (git branch --show-current).Trim()
if ($currentBranch -ne "dev_v6") {
    Write-Warning "$icoWarn Vous n'êtes pas sur la branche 'dev_v6' (branche active : $currentBranch)."
    $confirm = Read-Host "Continuer malgré tout ? (o/N)"
    if ($confirm -ne "o" -and $confirm -ne "O") {
        exit 0
    }
}

Write-Host "$icoSync [1/4] Récupération de v5-local/dev..." -ForegroundColor Cyan
git fetch v5-local dev
if ($LASTEXITCODE -ne 0) {
    Write-Error "$icoErr Échec lors du git fetch v5-local."
    exit 1
}

# 3. Contrôle des nouveaux commits
$commitCount = (git rev-list HEAD..v5-local/dev --count).Trim()
if ([int]$commitCount -eq 0) {
    Write-Host "$icoOk La branche dev_v6 est déjà parfaitement synchronisée avec v5-local/dev (0 commit d'écart)." -ForegroundColor Green
    exit 0
}
Write-Host "$icoInfo $commitCount commit(s) à intégrer depuis v5-local/dev." -ForegroundColor Gray

# 4. Déclenchement de la fusion sous contrôle
Write-Host "$icoSync [2/4] Fusion sous sanctuarisation (--no-commit --no-ff)..." -ForegroundColor Cyan
& git merge v5-local/dev --no-commit --no-ff *>$null
$mergeExitCode = $LASTEXITCODE

# 5. Sanctuarisation des fichiers spécifiques à V6
$SanctuaryPatterns = @(
    "^VERSION$",
    "^VERSIONING\.md$",
    "^install-hyperv\.ps1$",
    "^README\.md$",
    "^AGENTS\.md$",
    "^01-config/.*\.ya?ml$",
    "^docs/.*",
    "^00-echo-scripts/.*",
    "^11-owui-filters/open_webui_bug_correction_filter\.py$",
    "^11-owui-filters/tcp_keepalive_filter\.py$",
    "^11-owui-filters/edge_embed_bridge_filter\.py$",
    "^11-owui-filters/window_manager_filter\.py$",
    "^11-owui-filters/context_gauge_filter\.py$",
    "^11-owui-filters/AGENTS\.md$",
    "^16-docker-echo-webui/.*",
    "^check-owui-news\.ps1$",
    "^sync-from-v5\.ps1$"
)

$unmerged = git diff --name-only --diff-filter=U
if ($unmerged) {
    Write-Host "$icoShld [3/4] Arbitrage automatique des fichiers sanctuarisés V6..." -ForegroundColor Yellow
    
    $remainingConflicts = @()
    foreach ($rawFile in $unmerged) {
        $file = $rawFile.Trim().Replace("\", "/")
        $isProtected = $false
        foreach ($pattern in $SanctuaryPatterns) {
            if ($file -match $pattern) {
                $isProtected = $true
                break
            }
        }
        
        if ($isProtected) {
            Write-Host "   $icoLock Sanctuarisé (version V6 préservée) : $file" -ForegroundColor DarkGray
            & git cat-file -e "HEAD:$file" *>$null
            if ($LASTEXITCODE -eq 0) {
                git checkout HEAD -- "$file"
                git add "$file"
            }
            else {
                git rm -f --quiet -- "$file" *>$null
            }
        }
        else {
            $remainingConflicts += $file
        }
    }

    if ($remainingConflicts.Count -gt 0) {
        Write-Host "`n$icoErr Des conflits réels sur le code applicatif nécessitent un arbitrage manuel :" -ForegroundColor Red
        foreach ($c in $remainingConflicts) {
            Write-Host "   $icoWarn $c" -ForegroundColor Yellow
        }
        Write-Host "`nRésolvez ces fichiers puis terminez la fusion avec 'git commit'." -ForegroundColor Red
        exit 1
    }
}

# 5.bis Rétablissement des fichiers sanctuarisés fusionnés automatiquement sans conflit
$stagedChanges = git diff --name-only --cached
foreach ($rawFile in $stagedChanges) {
    $file = $rawFile.Trim().Replace("\", "/")
    foreach ($pattern in $SanctuaryPatterns) {
        if ($file -match $pattern) {
            Write-Host "   $icoLock Sanctuarisé (version V6 restaurée) : $file" -ForegroundColor DarkGray
            & git cat-file -e "HEAD:$file" *>$null
            if ($LASTEXITCODE -eq 0) {
                git checkout HEAD -- "$file"
                git add "$file"
            }
            else {
                git rm -f --quiet -- "$file" *>$null
            }
            break
        }
    }
}

# 6. Synthèse des modifications applicatives intégrées
$stagedChanges = git diff --name-only --cached
$functionalChanges = @()
foreach ($rawFile in $stagedChanges) {
    $file = $rawFile.Trim().Replace("\", "/")
    $isProtected = $false
    foreach ($pattern in $SanctuaryPatterns) {
        if ($file -match $pattern) {
            $isProtected = $true
            break
        }
    }
    if (-not $isProtected) {
        $functionalChanges += $file
    }
}

Write-Host "$icoPlan [4/4] Bilan des évolutions applicatives V5 intégrées :" -ForegroundColor Cyan
if ($functionalChanges.Count -gt 0) {
    foreach ($f in $functionalChanges) {
        Write-Host "   + $f" -ForegroundColor Green
    }
}
else {
    Write-Host "   (Aucune modification applicative hors sanctuaire)" -ForegroundColor Gray
}

# 7. Validation finale et scellement du commit de fusion
$latestV5Hash = (git rev-parse --short v5-local/dev).Trim()
$mergeMessage = "sync(v5): merge updates from v5-local/dev ($latestV5Hash) with v6 sanctuary"

git commit -m "$mergeMessage"
if ($LASTEXITCODE -eq 0) {
    Write-Host "`n$icoOk Synchronisation V5 -> V6 scellée avec succès !" -ForegroundColor Green
}
else {
    Write-Error "$icoErr Erreur lors du scellement du commit de fusion."
    exit 1
}
