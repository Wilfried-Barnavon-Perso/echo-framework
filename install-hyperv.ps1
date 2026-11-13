# ==============================================================================
# SCRIPT DE DÉPLOIEMENT : ARCHITECTURE "ECHO V5 INFRASTRUCTURE"
# ==============================================================================
# VERSION : 6.5.1.2
# DATE    : 2026-11-13
# AUTHOR         : Wilfried BARNAVON
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script PowerShell automatise la création d'une VM Linux sur Hyper-V et y déploie
# toute la stack ECHO (Docker, Scripts, Configs) via injection d'archive ZIP.
#
# --- POURQUOI (WHY) ---
# L'utilisation d'un disque Seed (CIDATA) pour injecter l'archive ZIP source
# permet de s'affranchir des limitations de taille du YAML Cloud-Init (Base64)
# et garantit un transfert rapide des fichiers vers la VM.
#
# --- COMMENT (HOW - ALGO) ---
# 1. PRE-FLIGHT : Vérifie que le fichier VERSION est présent.
# 2. CONFIGURATION : Définit les ressources VM.
# 3. PACKAGING : 
#    - Zippe le dossier courant (Exclusions: .git, .venv, etc.)
#    - Monte un disque VHD temporaire (Seed) pour y copier le ZIP.
# 4. CLOUD-INIT : Génère un fichier 'user-data' qui : 
#    - Crée l'utilisateur initial ($AutoUser).
#    - Monte le disque Seed au démarrage.
#    - Dézippe les sources dans /opt/ECHO/source.
#    - Installe les outils (Docker, yq, jq).
#    - Lance echo-sync.sh --local-only.
#    - Lance install-stack.sh.
# 5. HYPER-V : Crée et lance la VM.
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 6.5.1.2: 3 niveaux RAM a 8192 Mo (priorite 50), snapshots auto desactives et hostname majeur + branche normalisee.
# 6.5.1.1: Pointage ISO vers ubuntu-26.04.1-live-server-amd64-autoinstall.iso.
# 6.0.0.6: Extension du disque Seed temporaire a 256Mo (marge securite archive ZIP).
# 6.0.0.5: Exclusion dynamique automatique de l'ensemble des regles du .gitignore pour le ZIP.
# 6.0.0.4: QoS bridee aux puissances de 2 (2^n) et bridage dynamique.

# --- FONCTION UTILITAIRE : PAUSE SUR ERREUR ---
$SwitchName = "Bridge LAN" # /!\ Vérifiez le nom de votre switch Hyper-V
$ISOPath = "D:\ISO\ubuntu-26.04.1-live-server-amd64-autoinstall.iso"
$VMPath = "D:\Virtual Machines"
$VHDSize = 60GB

# RAM de démarrage fixée à 8 Go
$RAMStartup = 8192MB

# --- CONFIGURATION RESEAU (IP STATIQUE) ---
$STATIC_IP_CIDR = "192.168.147.101/24"
$GATEWAY_IP = "192.168.147.254"
$DNS_SERVERS = "[86.54.11.100, 1.1.1.1, 8.8.8.8]"

# --- CONFIGURATION BRANCHE ---
$BRANCHE = "dev_v6"
#$BRANCHE = "main"

function Pause-OnError {
  param([string]$Message)
  Write-Error "❌ ERREUR CRITIQUE : $Message"
  Write-Host "Appuyez sur Entrée pour quitter..." -ForegroundColor Red
  Read-Host
  Exit 1
}

# --- FONCTION UTILITAIRE : BENCHMARK DISQUE HORS-CACHE (8 Ko UNBUFFERED) ---
function Measure-DiskIOPS {
  param(
    [string]$TargetDir = "D:\Virtual Machines",
    [double]$DurationSec = 1.5,
    [int]$BlockSize = 8192
  )
  Write-Host "⏱️  Mesure de la performance disque hors-cache (8 Ko WriteThrough)..." -ForegroundColor Cyan
  if (-not (Test-Path $TargetDir)) { New-Item -Path $TargetDir -ItemType Directory -Force | Out-Null }
  $TestFile = Join-Path $TargetDir "__echo_qos_bench_$([Guid]::NewGuid()).tmp"
  $Buffer = New-Object byte[] $BlockSize
  (New-Object Random).NextBytes($Buffer)
  
  $Count = 0
  $Sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $Fs = New-Object System.IO.FileStream($TestFile, [System.IO.FileMode]::Create, [System.IO.FileAccess]::Write, [System.IO.FileShare]::None, $BlockSize, [System.IO.FileOptions]::WriteThrough)
    while ($Sw.ElapsedMilliseconds -lt ($DurationSec * 1000)) {
      $Fs.Write($Buffer, 0, $BlockSize)
      $Count++
    }
    $Fs.Flush([bool]$true)
    $Fs.Close()
    $Fs.Dispose()
  }
  finally {
    $Sw.Stop()
    if (Test-Path $TestFile) { Remove-Item $TestFile -Force -ErrorAction SilentlyContinue }
  }
  
  $ElapsedSec = $Sw.ElapsedMilliseconds / 1000
  $MeasuredIOPS = if ($ElapsedSec -gt 0) { [math]::Round($Count / $ElapsedSec) } else { 5000 }
  $MeasuredThroughput = [math]::Round(($Count * $BlockSize) / (1MB * $ElapsedSec), 1)
  Write-Host "   📊 Performance brute mesurée : $MeasuredIOPS IOPS (~$MeasuredThroughput Mo/s)" -ForegroundColor Green
  return [Math]::Max(1000, $MeasuredIOPS)
}

# --- 1. INITIALISATION & VERSIONING ---
$SCRIPT_VERSION = "6.5.1.2"
$ScriptDir = $PSScriptRoot
Set-Location -Path $ScriptDir
$VersionFile = "$ScriptDir\VERSION"

Write-Host "🚀 ECHO INFRASTRUCTURE DEPLOYER [Script v$SCRIPT_VERSION]" -ForegroundColor Cyan
Write-Host "==========================================================" 

# Vérification Stricte du Fichier VERSION
if (-not (Test-Path $VersionFile)) {
  Pause-OnError "Fichier 'VERSION' introuvable à la racine ($VersionFile). Requis pour la stack."
}

# Lecture et Nettoyage de la version
$RAW_VERSION = (Get-Content -Path $VersionFile -Raw).Trim()
$ECHO_VERSION = $RAW_VERSION -replace "^v", ""

if ($ECHO_VERSION -notmatch "^\d+\.\d+\.\d+") {
  Write-Warning "⚠️   Format de version atypique détecté : $ECHO_VERSION (Attendu: X.Y.Z)"
}

Write-Host "📦 Stack Target    : v$ECHO_VERSION" -ForegroundColor Green
Write-Host "🌿 Target Branch   : $BRANCHE" -ForegroundColor Green

# --- 2. CONFIGURATION VM DYNAMIQUE ---
$VMName = "ECHO-v$ECHO_VERSION-$BRANCHE"
$VHDPath = "$VMPath\Virtual Hard Drives\$VMName.vhdx"
$SeedPath = "$VMPath\Virtual Hard Drives\$VMName-seed.vhdx"

Write-Host "🖥️   VM Name         : $VMName" -ForegroundColor Yellow

$AutoUser = "echo"
$AppPassword = "password"
$MajorVersion = ($ECHO_VERSION -split '\.')[0]
$NormalizedBranch = ($BRANCHE.ToLower() -replace '[_\.\s]+', '-').Trim('-')
$AutoHostname = "echo-v$MajorVersion-$NormalizedBranch"

# Hash sha512crypt réel et valide pour le mot de passe "password" (sel: saltsalt)
$HashPassword = '$6$saltsalt$qFmFH.bQmmtXzyBY0s9v7Oicd2z4XSIecDzlB5KiA2/jctKu9YterLp8wwnSq.qc.eoxqOmSuNp2xS0ktL3nh/.'

# --- 3. AUTO-ELEVATION ADMIN ---
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
  Exit
}

# --- 4. PREPARATION ET ENCODAGE DU PACK SOURCE (ZIP) ---
Write-Host "📦 Création de l'archive source..." -ForegroundColor Cyan

$TempZipPath = "$env:TEMP\echo-framework-source-$([Guid]::NewGuid()).zip"

# Exclusion dynamique : aspiration automatique de toutes les regles du fichier .gitignore
$GitIgnorePath = Join-Path $ScriptDir ".gitignore"
$GitExclusions = @()
if (Test-Path $GitIgnorePath) {
  $GitExclusions = Get-Content $GitIgnorePath | 
  ForEach-Object { $_.Trim() } | 
  Where-Object { $_ -and -not $_.StartsWith("#") } | 
  ForEach-Object { $_.TrimEnd("/").TrimStart("/") }
}
$Exclusions = (@(".git", "tmp", "*.zip", "deploy-*.ps1", ".idea") + $GitExclusions) | Select-Object -Unique

try {
  # Compression intelligente (exclut les dossiers lourds ou ignores par Git)
  # Get-ChildItem est utilise pour filtrer avant de passer a Compress-Archive qui gere mal les exclusions recursives complexes par defaut
  Get-ChildItem -Path $ScriptDir -Exclude $Exclusions | Compress-Archive -DestinationPath $TempZipPath -CompressionLevel Optimal -Force

  if (-not (Test-Path $TempZipPath)) { throw "Le fichier ZIP n'a pas été créé." }

  $ZipSize = (Get-Item $TempZipPath).Length / 1MB
  Write-Host "   ✅ Archive créée : $([Math]::Round($ZipSize, 2)) MB"

  # On ne fait plus d'encodage Base64 ici car le YAML rejette les lignes trop longues.
  # Le ZIP sera copié directement sur le disque Seed (CIDATA).
}
catch {
  Pause-OnError "Echec lors de la création de l'archive ZIP : $_ "
}

# --- 5. GENERATION BLOC WRITE_FILES (CLOUD-INIT) ---
$WriteFilesBlock = ""

# Traçabilité du Script de Déploiement
$ScriptVerContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($SCRIPT_VERSION))
$WriteFilesBlock += "      - path: /opt/ECHO/echo_deploy_script_version`n"
$WriteFilesBlock += "        permissions: '0444'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $ScriptVerContent`n"

# Injection de la BRANCHE (Requis par echo-sync.sh)
$BrancheContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($BRANCHE))
$WriteFilesBlock += "      - path: /opt/ECHO/ECHO_BRANCH`n"
$WriteFilesBlock += "        permissions: '0644'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $BrancheContent`n"

# --- 6. GENERATION FICHIER USER-DATA ---
$UserData = @"
#cloud-config
autoinstall:
  version: 1
  locale: fr_FR.UTF-8
  keyboard:
    layout: fr
  identity:
    hostname: $AutoHostname
    password: '$HashPassword'
    username: $AutoUser
  ssh:
    install-server: yes
    allow-pw: yes
  network:
    version: 2
    ethernets:
      eth0:
        dhcp4: no
        addresses: [$STATIC_IP_CIDR]
        nameservers:
          addresses: $DNS_SERVERS
        routes:
          - to: default
            via: $GATEWAY_IP
  storage:
    layout:
      name: direct
  late-commands:
    - echo '${AutoUser}:${AppPassword}' | chroot /target chpasswd
  user-data:
    package_update: true
    packages:
      - unzip
      - curl
      - git
      - jq
      - chrony
      - docker.io
      - docker-compose-v2
      - docker-buildx
    write_files:
      - path: /etc/chrony/conf.d/hyperv.conf
        content: |
          refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0

$WriteFilesBlock
    runcmd:
      # --- PREPARATION OUTILS ---
      - "systemctl enable --now docker"
      - "wget https://github.com/mikefarah/yq/releases/download/v4.40.5/yq_linux_amd64 -O /usr/local/bin/yq && chmod +x /usr/local/bin/yq"

      # --- EXTRACTION ET DEPLOIEMENT ---
      # 1. Préparation du dossier source et récupération du ZIP depuis le disque Seed
      - "mkdir -p /opt/ECHO/source"
      - "mkdir -p /mnt/cidata && mount /dev/disk/by-label/CIDATA /mnt/cidata"
      - "cp /mnt/cidata/source.zip /opt/ECHO/source/source.zip"
      - "umount /mnt/cidata"

      # 2. Décompression de l'archive
      - "unzip -o /opt/ECHO/source/source.zip -d /opt/ECHO/source"

      # 3. Suppression de l'archive (GAIN PLACE)
      - "rm /opt/ECHO/source/source.zip"

      # 4. Préparation du script de synchro
      - "chmod +x /opt/ECHO/source/00-echo-scripts/echo-sync.sh"

      # 5. Lancement de la Synchro en mode LOCAL-ONLY (Pas de git clone)
      #    Cela va distribuer les fichiers de /opt/ECHO/source vers /opt/ECHO/echo-scripts, etc.
      - "/opt/ECHO/source/00-echo-scripts/echo-sync.sh --local-only"

      # 7. Lancement Installation de la Stack avec journalisation temps réel
      - "bash /opt/ECHO/echo-scripts/install-stack.sh 2>&1 | tee /opt/ECHO/install.log"
"@

# --- 7. CREATION DISQUES & VM (HYPER-V) ---
if (-not (Test-Path $VMPath)) { New-Item -Path $VMPath -ItemType Directory | Out-Null }
if (-not (Test-Path "$VMPath\Virtual Hard Drives")) { New-Item -Path "$VMPath\Virtual Hard Drives" -ItemType Directory | Out-Null }

# Création VHD Principal
if (-not (Test-Path $VHDPath)) {
  New-VHD -Path $VHDPath -SizeBytes $VHDSize -Dynamic | Out-Null
}

# Création Disque Seed (ISO Cloud-Init)
if (Test-Path $SeedPath) { Remove-Item $SeedPath -Force }
New-VHD -Path $SeedPath -SizeBytes 256MB -Dynamic | Out-Null
$Disk = Mount-VHD -Path $SeedPath -Passthru | Get-Disk
Initialize-Disk -Number $Disk.Number -PartitionStyle MBR
$Partition = New-Partition -DiskNumber $Disk.Number -UseMaximumSize -AssignDriveLetter
Format-Volume -DriveLetter $Partition.DriveLetter -FileSystem FAT32 -NewFileSystemLabel "CIDATA" -Confirm:$false
$DriveLetter = "$($Partition.DriveLetter):\"

# Écriture des fichiers Cloud-Init sur le disque Seed (UTF-8 NO BOM + LF pour Linux)
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText("${DriveLetter}user-data", ($UserData -replace "`r`n", "`n"), $Utf8NoBom)
New-Item -Path "${DriveLetter}meta-data" -ItemType File | Out-Null
New-Item -Path "${DriveLetter}vendor-data" -ItemType File | Out-Null

# Copie de l'archive ZIP directement sur le disque Seed
Copy-Item -Path $TempZipPath -Destination "${DriveLetter}source.zip" -Force
Write-Host "   📦 Archive source.zip copiée sur le disque Seed."

Start-Sleep -Seconds 2
Dismount-VHD -Path $SeedPath

# Construction VM
if (-not (Get-VM -Name $VMName -ErrorAction SilentlyContinue)) {
  Write-Host "🔨 Création de la VM Hyper-V : $VMName"

  New-VM -Name $VMName -MemoryStartupBytes $RAMStartup -Generation 2 -SwitchName $SwitchName -NoVHD
  
  # Les 3 niveaux mémoire fixés à 8192 Mo et snapshots automatiques désactivés
  Set-VM -Name $VMName -DynamicMemory -MemoryMinimumBytes 8192MB -MemoryMaximumBytes 8192MB -ProcessorCount 4 -AutomaticCheckpointsEnabled $false
  Set-VMMemory -VMName $VMName -Priority 50
  Get-VMIntegrationService -VMName $VMName | Where-Object { $_.Name -match "Guest|invit" } | Enable-VMIntegrationService
  Set-VMFirmware -VMName $VMName -EnableSecureBoot Off
  
  Add-VMDvdDrive -VMName $VMName -Path $ISOPath
  Add-VMHardDiskDrive -VMName $VMName -Path $VHDPath
  Add-VMHardDiskDrive -VMName $VMName -Path $SeedPath
  
  # Benchmark I/O hors-cache et bridage QoS VHD à 66% plafonné en puissance de 2 (2^n)
  $MeasuredIOPS = Measure-DiskIOPS -TargetDir $VMPath -DurationSec 1.5
  $RawTarget = $MeasuredIOPS * 0.66
  
  # Calcul du palier en puissance de 2 (Floor garantit que la VM ne dépasse pas les 66% alloués)
  $Power = [math]::Floor([math]::Log($RawTarget, 2))
  $PowerClamped = [math]::Min(16, [math]::Max(9, $Power)) # Bornes de sécurité : 512 (2^9) à 65536 (2^16)
  $TargetIOPS = [int][math]::Pow(2, $PowerClamped)
  $EffectiveRatio = [math]::Round(($TargetIOPS / $MeasuredIOPS) * 100, 1)
  
  Write-Host "   🛡️  QoS Disque : Plafond fixé à $TargetIOPS IOPS (2^$PowerClamped, ratio effectif : $EffectiveRatio% de la capacité mesurée)" -ForegroundColor Yellow
  (Get-VMHardDiskDrive -VMName $VMName | Where-Object { $_.Path -eq $VHDPath }) | Set-VMHardDiskDrive -MaximumIOPS $TargetIOPS
  
  Set-VMFirmware -VMName $VMName -FirstBootDevice (Get-VMDvdDrive -VMName $VMName)

  Write-Host "✅ VM Prête ! Lancement..."
  Start-VM -Name $VMName
  
  if (Test-Path $TempZipPath) { Remove-Item $TempZipPath -Force }

  Start-Process -FilePath "vmconnect.exe" -ArgumentList "localhost $VMName"
}
else {
  Write-Warning "⚠️   La VM $VMName existe déjà. Aucune action effectuée."
  Pause-OnError "Veuillez supprimer la VM existante ou incrémenter la version du fichier VERSION."
}
