# Copyright (c) 2025-2026 Wilfried BARNAVON
# SPDX-License-Identifier: Apache-2.0
<#
.SYNOPSIS
    ECHO V6 - Audit et maintenance hygiène/sécurité locale des dépendances et Dockerfiles
    VERSION : 6.18.8.1
#>
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.18.8.1: Ajout de la sonde d'intégrité référentielle du graphe package-lock.json pour prévenir les échecs npm ci sous Linux.
# 6.18.7.3: Création de l'outil local d'audit CVE/deprecated/Dockerfile/requirements et mode FixSafe à 5 portes avec rollback automatique.

param(
  [ValidateSet('Audit', 'FixSafe')]
  [string]$Mode = 'Audit',
  [switch]$IncludeUpstream,
  [string]$ReportPath = '',
  [switch]$RegisterScheduledTask,
  [switch]$UnregisterScheduledTask
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$WebuiDir = Join-Path $RepoRoot '16-docker-echo-webui'
$PolicyPath = Join-Path $PSScriptRoot 'HYGIENE_POLICY.json'
if ([string]::IsNullOrWhiteSpace($ReportPath)) {
  $ReportPath = Join-Path $PSScriptRoot 'reports\hygiene-report.md'
}

$TaskName = 'ECHO-Hygiene-Audit'

if ($RegisterScheduledTask) {
  $pwshExe = (Get-Process -Id $PID).Path
  $scriptQuoted = "`"$PSCommandPath`""
  $action = New-ScheduledTaskAction -Execute $pwshExe -Argument "-NoProfile -ExecutionPolicy Bypass -File $scriptQuoted -Mode Audit"
  $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At 09:00
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Force | Out-Null
  Write-Host "Tâche planifiée '$TaskName' enregistrée (lundis 09:00, session utilisateur)." -ForegroundColor Green
  exit 0
}

if ($UnregisterScheduledTask) {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Tâche planifiée '$TaskName' supprimée." -ForegroundColor Yellow
  exit 0
}

if (-not (Test-Path $PolicyPath)) {
  Write-Error "Politique introuvable : $PolicyPath"
}
$policy = Get-Content $PolicyPath -Raw -Encoding UTF8 | ConvertFrom-Json

function Write-Utf8NoBomLf {
  param([string]$Path, [string]$Content)
  $parent = Split-Path -Parent $Path
  if ($parent -and -not (Test-Path $parent)) {
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
  }
  $normalized = $Content -replace "`r`n", "`n"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $normalized, $utf8NoBom)
}

function Normalize-FileUtf8NoBomLf {
  param([string]$Path)
  $raw = [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
  $raw = $raw.TrimStart([char]0xFEFF) -replace "`r`n", "`n"
  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllText($Path, $raw, $utf8NoBom)
}

if ($Mode -eq 'FixSafe') {
  $pkgJsonPath = Join-Path $WebuiDir 'package.json'
  $lockJsonPath = Join-Path $WebuiDir 'package-lock.json'
  $pkgBackup = [System.IO.File]::ReadAllBytes($pkgJsonPath)
  $lockBackup = [System.IO.File]::ReadAllBytes($lockJsonPath)

  Push-Location $WebuiDir
  try {
    $env:ONNXRUNTIME_NODE_INSTALL_CUDA = 'skip'
    Write-Host '[FixSafe] Exécution de npm audit fix --no-fund...' -ForegroundColor Cyan
    cmd /c 'npm audit fix --no-fund' | Out-Null

    Normalize-FileUtf8NoBomLf -Path $pkgJsonPath
    Normalize-FileUtf8NoBomLf -Path $lockJsonPath

    $gates = @(
      @{ Label = 'svelte-kit sync'; Cmd = 'npx svelte-kit sync' },
      @{ Label = 'vitest run'; Cmd = 'npx vitest run --passWithNoTests' },
      @{ Label = 'npm run i18n:check'; Cmd = 'npm run i18n:check' },
      @{ Label = 'npm run crypto:verify'; Cmd = 'npm run crypto:verify' },
      @{ Label = 'vite build'; Cmd = 'node --max-old-space-size=6144 node_modules/vite/bin/vite.js build' }
    )

    foreach ($gate in $gates) {
      Write-Host "[FixSafe] Porte : $($gate.Label)..." -ForegroundColor Cyan
      cmd /c $gate.Cmd
      if ($LASTEXITCODE -ne 0) {
        Write-Warning "[FixSafe] Échec porte '$($gate.Label)' (code $LASTEXITCODE). Rollback en cours..."
        [System.IO.File]::WriteAllBytes($pkgJsonPath, $pkgBackup)
        [System.IO.File]::WriteAllBytes($lockJsonPath, $lockBackup)
        cmd /c 'npm ci --no-audit --no-fund'
        exit 1
      }
    }
    Write-Host '[FixSafe] Toutes les portes de validation ont réussi.' -ForegroundColor Green
  }
  finally {
    Pop-Location
  }
}

$Failures = [System.Collections.Generic.List[string]]::new()
$AcceptedNotes = [System.Collections.Generic.List[string]]::new()
$Today = (Get-Date).ToString('yyyy-MM-dd')
$SeverityRank = @{ low = 1; moderate = 2; high = 3; critical = 4 }

# 1. Sonde CVE npm audit --json
Push-Location $WebuiDir
try {
  $auditRaw = cmd /c 'npm audit --json 2>nul' | Out-String
}
finally {
  Pop-Location
}
if ([string]::IsNullOrWhiteSpace($auditRaw)) {
  $Failures.Add('npm audit --json a retourné une sortie vide.')
  $auditData = $null
}
else {
  $auditData = $auditRaw | ConvertFrom-Json
}

if ($auditData -and $auditData.vulnerabilities) {
  $failSevs = @($policy.fail_on_unaccepted_severities)
  foreach ($prop in $auditData.vulnerabilities.PSObject.Properties) {
    $v = $prop.Value
    $sev = "$($v.severity)".ToLowerInvariant()
    if ($failSevs -notcontains $sev) { continue }

    $vRank = if ($SeverityRank.ContainsKey($sev)) { $SeverityRank[$sev] } else { 99 }
    $matchedException = $null
    foreach ($ex in $policy.accepted_exceptions) {
      if (@($ex.packages) -contains $v.name) {
        $maxSev = "$($ex.max_severity)".ToLowerInvariant()
        $maxRank = if ($SeverityRank.ContainsKey($maxSev)) { $SeverityRank[$maxSev] } else { 0 }
        if ($vRank -le $maxRank -and ($Today -lt $ex.review_after)) {
          $matchedException = $ex
          break
        }
      }
    }

    if ($matchedException) {
      $AcceptedNotes.Add("$($v.name) ($sev, scope=$($matchedException.scope), revue<$($matchedException.review_after))")
    }
    else {
      $Failures.Add("CVE non acceptée : $($v.name) [severity=$sev]")
    }
  }
}

# 2. Sonde deprecated & intégrité référentielle dans package-lock.json (via node)
$lockPath = Join-Path $WebuiDir 'package-lock.json'
$lockScanRaw = node -e "const fs=require('fs');const p=JSON.parse(fs.readFileSync(process.argv[1],'utf8')).packages||{};const dep=[],miss=[];for(const[k,v]of Object.entries(p)){if(!v)continue;if(v.deprecated)dep.push({key:k,shortName:k.split('node_modules/').pop(),version:v.version||''});if(!k)continue;for(const d of Object.keys({...(v.dependencies||{}),...(v.optionalDependencies||{})})){let c=k,ok=false;while(true){const t=c?c+'/node_modules/'+d:'node_modules/'+d;if(p[t]){ok=true;break;}const i=c.lastIndexOf('/node_modules/');if(i===-1){if(c.startsWith('node_modules/')){c='';continue;}break;}c=c.slice(0,i);}if(!ok)miss.push(k+' -> '+d);}}console.log(JSON.stringify({deprecated:dep,missing:miss}));" $lockPath | Out-String
$lockScan = $lockScanRaw | ConvertFrom-Json
$deprecatedList = @($lockScan.deprecated | Where-Object { $_ -ne $null })
foreach ($m in @($lockScan.missing)) {
  if (-not [string]::IsNullOrWhiteSpace("$m")) {
    $Failures.Add("Lien manquant dans package-lock.json (échec npm ci Linux) : $m")
  }
}
$allowedDeprecated = @($policy.allowed_deprecated_transitives)
foreach ($d in $deprecatedList) {
  if ($allowedDeprecated -notcontains $d.shortName) {
    $Failures.Add("Paquet deprecated non autorisé : $($d.shortName)@$($d.version) ($($d.key))")
  }
}

# 3. Sonde npm outdated --json (informative)
Push-Location $WebuiDir
try {
  $outdatedRaw = cmd /c 'npm outdated --json 2>nul' | Out-String
}
finally {
  Pop-Location
}
$safeUpdates = [System.Collections.Generic.List[string]]::new()
$pinnedUpdates = [System.Collections.Generic.List[string]]::new()
if (-not [string]::IsNullOrWhiteSpace($outdatedRaw)) {
  $outdatedObj = $outdatedRaw | ConvertFrom-Json
  if ($outdatedObj) {
    $pinnedNames = @($policy.pinned_majors.PSObject.Properties.Name)
    foreach ($prop in $outdatedObj.PSObject.Properties) {
      $pkgName = $prop.Name
      $info = $prop.Value
      if ($info.current -ne $info.wanted) {
        $safeUpdates.Add("$pkgName : $($info.current) -> $($info.wanted)")
      }
      if ($pinnedNames -contains $pkgName -and $info.current -ne $info.latest) {
        $pinMajor = $policy.pinned_majors.$pkgName
        $pinnedUpdates.Add("$pkgName : $($info.current) (verrouillé majeur $pinMajor, latest=$($info.latest))")
      }
    }
  }
}

# 4. Sonde Dockerfile & requirements.txt
$dockerfiles = [System.Collections.Generic.List[string]]::new()
$webuiDf = Join-Path $WebuiDir 'Dockerfile'
if (Test-Path $webuiDf) { $dockerfiles.Add($webuiDf) }
$baseDf = Join-Path $RepoRoot 'docker-python-base\Dockerfile'
if (Test-Path $baseDf) { $dockerfiles.Add($baseDf) }
Get-ChildItem -Path $RepoRoot -Directory -Filter '*-docker-*' | ForEach-Object {
  if ($_.Name -eq '16-docker-echo-webui') { return }
  $df = Join-Path $_.FullName 'Dockerfile'
  if (Test-Path $df) { $dockerfiles.Add($df) }
}

foreach ($df in $dockerfiles) {
  $relDf = $df.Substring($RepoRoot.Length + 1)
  $bytes = [System.IO.File]::ReadAllBytes($df)
  if ($bytes -contains 13) {
    $Failures.Add("CRLF détecté dans $relDf")
  }
  $text = [System.Text.Encoding]::UTF8.GetString($bytes)
  if ($text -match 'alpine3\.20') {
    $Failures.Add("Tag alpine3.20 interdit détecté dans $relDf")
  }
  $lines = $text -split "`n"
  $tailStart = [Math]::Max(0, $lines.Count - 25)
  $tailText = ($lines[$tailStart..($lines.Count - 1)]) -join "`n"
  if ($tailText -notmatch 'LABEL\s+version=') {
    $Failures.Add("LABEL version= absent des 25 dernières lignes de $relDf")
  }
}

$reqPath = Join-Path $WebuiDir 'backend\requirements.txt'
$reqBytes = [System.IO.File]::ReadAllBytes($reqPath)
if ($reqBytes -contains 13) {
  $Failures.Add('CRLF détecté dans 16-docker-echo-webui/backend/requirements.txt')
}
$reqLines = ([System.Text.Encoding]::UTF8.GetString($reqBytes)) -split "`n"
$inEchoNative = $false
foreach ($line in $reqLines) {
  $trimmed = $line.Trim()
  if ($trimmed -eq '## ECHO V6 Native Runtime Dependencies') {
    $inEchoNative = $true
    continue
  }
  if ($inEchoNative) {
    if ([string]::IsNullOrWhiteSpace($trimmed) -or $trimmed.StartsWith('#')) { continue }
    $clean = ($trimmed -split '#', 2)[0].Trim()
    if ($clean -notmatch '<') {
      $Failures.Add("Borne supérieure '<' manquante dans requirements.txt : $clean")
    }
  }
}

# 5. Génération du rapport Markdown
$metaVulns = if ($auditData -and $auditData.metadata) { $auditData.metadata.vulnerabilities } else { $null }
$reportLines = [System.Collections.Generic.List[string]]::new()
$reportLines.Add("# Rapport Hygiène ECHO-UI ($Today)")
$reportLines.Add('')
$reportLines.Add("- **Statut global** : $(if ($Failures.Count -eq 0) { 'PASS' } else { 'FAIL' })")
if ($metaVulns) {
  $reportLines.Add("- **npm audit** : total=$($metaVulns.total) (critical=$($metaVulns.critical), high=$($metaVulns.high), moderate=$($metaVulns.moderate), low=$($metaVulns.low))")
}
$reportLines.Add("- **Paquets deprecated (lockfile)** : $($deprecatedList.Count)")
$reportLines.Add('')
$reportLines.Add('## Échecs bloquants')
if ($Failures.Count -eq 0) {
  $reportLines.Add('- Aucun')
}
else {
  foreach ($f in $Failures) { $reportLines.Add("- $f") }
}
$reportLines.Add('')
$reportLines.Add('## Exceptions CVE acceptées')
if ($AcceptedNotes.Count -eq 0) {
  $reportLines.Add('- Aucune')
}
else {
  foreach ($a in $AcceptedNotes) { $reportLines.Add("- $a") }
}
$reportLines.Add('')
$reportLines.Add('## Mises à jour SemVer sûres (current != wanted)')
if ($safeUpdates.Count -eq 0) {
  $reportLines.Add('- Aucune')
}
else {
  foreach ($s in $safeUpdates) { $reportLines.Add("- $s") }
}
$reportLines.Add('')
$reportLines.Add('## Majeures verrouillées (pinned_majors)')
if ($pinnedUpdates.Count -eq 0) {
  $reportLines.Add('- Aucune')
}
else {
  foreach ($p in $pinnedUpdates) { $reportLines.Add("- $p") }
}

Write-Utf8NoBomLf -Path $ReportPath -Content (($reportLines -join "`n") + "`n")

Write-Host "=== Résumé Hygiène ECHO ($Today) ===" -ForegroundColor Cyan
Write-Host "Rapport : $ReportPath" -ForegroundColor Gray
Write-Host "Exceptions acceptées : $($AcceptedNotes.Count) | Échecs bloquants : $($Failures.Count)" -ForegroundColor $(if ($Failures.Count -eq 0) { 'Green' } else { 'Red' })
if ($Failures.Count -gt 0) {
  foreach ($f in $Failures) { Write-Host "  [FAIL] $f" -ForegroundColor Red }
}

if ($IncludeUpstream) {
  & (Join-Path $PSScriptRoot 'check-owui-news.ps1')
}

if ($Failures.Count -eq 0) { exit 0 } else { exit 1 }
