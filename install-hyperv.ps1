# ==============================================================================
# SCRIPT DE DÉPLOIEMENT : ARCHITECTURE "ECHO V5 INFRASTRUCTURE"
# ==============================================================================
# VERSION : 5.239.9.1
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
# 2. CONFIGURATION : Définit les ressources VM (RAM 8-10 Go dynamique, QoS Stockage 66% en 2^N).
# 3. PACKAGING : 
#    - Zippe le dossier courant (Exclusions: .git, .venv, etc.)
#    - Monte un disque VHD temporaire (Seed) pour y copier le ZIP.
# 4. CLOUD-INIT : Génère un fichier 'user-data' qui : 
#    - Crée l'utilisateur initial ($AutoUser).
#    - Monte le disque Seed au démarrage.
#    - Dézippe les sources dans /opt/ECHO/source.
#    - Installe les outils (Docker, yq, jq, linux-cloud-tools-virtual).
#    - Lance echo-sync.sh --local-only.
#    - Lance install-stack.sh.
# 5. HYPER-V : Crée la VM, calibre la QoS de stockage hors cache quantifiée en puissance de 2 et démarre la VM.
# ==============================================================================
# Règle : Conserver uniquement les 5 dernières versions dans l'historique.
# Historique des versions :
# 5.239.9.1: Normalisation du hostname Cloud-Init (version majeure + branche normalisee RFC 1123).
# 5.239.3.2: Quantification de la Storage QoS sur des puissances de 2 (2^N).
# 5.239.3.1: Mémoire dynamique 8-10 Go (priorité 35%), Services d'invité et Storage QoS 66% hors cache.
# 5.231.40.1: Correction encodage UTF-8 BOM pour le parseur PowerShell 5.1 et alignement version.
# 5.231.39.1: Mise à jour des références cloud-init vers echo-sync.sh.

# --- FONCTION UTILITAIRE : PAUSE SUR ERREUR ---
$SwitchName = "Bridge LAN" # /!\ Vérifiez le nom de votre switch Hyper-V
$ISOPath = "D:\ISO\ubuntu-26.04-live-server-amd64-autoinstall.iso"
$VMPath = "D:\Virtual Machines"
$VHDSize = 100GB

# Configuration Mémoire Dynamique (8 Go - 10 Go, Priorité 35%)
$RAMStartup = 8192MB
$RAMMinimum = 8192MB
$RAMMaximum = 10240MB
$MemoryPriority = 35

# QoS Stockage : Marge de limitation à 66% des IOPS mesurées hors cache
$QoSMarginPercent = 66

# --- CONFIGURATION RESEAU (IP STATIQUE) ---
$STATIC_IP_CIDR = "192.168.147.100/24"
$GATEWAY_IP = "192.168.147.254"
$DNS_SERVERS = "[86.54.11.100, 1.1.1.1, 8.8.8.8]"

# --- CONFIGURATION BRANCHE ---
$BRANCHE = "dev"
#$BRANCHE = "main"

function Pause-OnError {
  param([string]$Message)
  Write-Error "❌ ERREUR CRITIQUE : $Message"
  Write-Host "Appuyez sur Entrée pour quitter..." -ForegroundColor Red
  Read-Host
  Exit 1
}

# --- FONCTION UTILITAIRE : MESURE IOPS DISQUE HORS CACHE (QoS) ---
function Measure-DiskPhysicalIOPS {
  param(
    [string]$TargetFolder = "D:\Virtual Machines"
  )
  $benchDir = if (Test-Path $TargetFolder) { $TargetFolder } else { $env:TEMP }
  $testFile = Join-Path -Path $benchDir -ChildPath "__echo_qos_bench_$([Guid]::NewGuid().ToString('N')).tmp"
  $blockSize = 64KB
  $totalMB = 64
  $buffer = [byte[]]::new($blockSize)
  $rng = [System.Random]::new()
  $rng.NextBytes($buffer)

  Write-Host "⚡ Mesure physique du stockage hors cache (Direct I/O WriteThrough)..." -ForegroundColor Cyan
  try {
    $sw = [System.Diagnostics.Stopwatch]::StartNew()
    $fs = [System.IO.FileStream]::new(
      $testFile,
      [System.IO.FileMode]::Create,
      [System.IO.FileAccess]::Write,
      [System.IO.FileShare]::None,
      $blockSize,
      [System.IO.FileOptions]::WriteThrough
    )

    $loops = ($totalMB * 1MB) / $blockSize
    for ($i = 0; $i -lt $loops; $i++) {
      $fs.Write($buffer, 0, $blockSize)
    }
    $fs.Flush($true)
    $fs.Close()
    $fs.Dispose()
    $sw.Stop()

    $seconds = $sw.Elapsed.TotalSeconds
    $measuredIOPS = [Math]::Round(($totalMB * 1024 / 8) / $seconds)
    $throughputMBps = [Math]::Round($totalMB / $seconds, 2)
    Write-Host "   📊 Débit physique mesuré : $throughputMBps Mo/s ($measuredIOPS IOPS normalisées 8K)" -ForegroundColor Green
    return $measuredIOPS
  }
  catch {
    Write-Warning "⚠️  Impossible de mesurer le disque hors cache ($_). Repli sur valeur par défaut (10000 IOPS)."
    return 10000
  }
  finally {
    if (Test-Path $testFile) { Remove-Item -Path $testFile -Force -ErrorAction SilentlyContinue }
  }
}

# --- 1. INITIALISATION & VERSIONING ---
$SCRIPT_VERSION = "5.239.9.1"
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
$Exclusions = @(".git", ".venv", ".vscode", "__pycache__", "tmp", "*.zip", "deploy-*.ps1", ".idea", "node_modules")

try {
  # Compression intelligente (exclut les dossiers lourds ou inutiles)
  # Get-ChildItem est utilisé pour filtrer avant de passer à Compress-Archive qui gère mal les exclusions récursives complexes par défaut
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
      - linux-cloud-tools-virtual
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
New-VHD -Path $SeedPath -SizeBytes 64MB -Dynamic | Out-Null
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
  
  # Configuration Mémoire Dynamique (8 Go min, 10 Go max, 4 vCPU et priorité 35%)
  Set-VM -Name $VMName -DynamicMemory -MemoryMinimumBytes $RAMMinimum -MemoryMaximumBytes $RAMMaximum -ProcessorCount 4
  Set-VMMemory -VMName $VMName -Priority $MemoryPriority

  # Activation des Services d'invité (Guest Service Interface pour Copy-VMFile)
  Get-VMIntegrationService -VMName $VMName | Where-Object { $_.Name -like "*Guest*" -or $_.Name -like "*invit*" } | Enable-VMIntegrationService

  Set-VMFirmware -VMName $VMName -EnableSecureBoot Off
  
  Add-VMDvdDrive -VMName $VMName -Path $ISOPath
  Add-VMHardDiskDrive -VMName $VMName -Path $VHDPath
  Add-VMHardDiskDrive -VMName $VMName -Path $SeedPath

  # Mesure physique hors cache et application de la Storage QoS (Plafond 66% quantifié en 2^N)
  $diskIOPS = Measure-DiskPhysicalIOPS -TargetFolder $VMPath
  $targetRawIOPS = [double]($diskIOPS * ($QoSMarginPercent / 100))
  # Règle : Alignement de la limite QoS sur la puissance de 2 immédiatement inférieure (2^N)
  $power = [Math]::Floor([Math]::Log($targetRawIOPS, 2))
  $capIOPS = [uint64][Math]::Pow(2, $power)
  Write-Host "🛡️  Application de la QoS Stockage (Plafond $QoSMarginPercent% quantifié à 2^$power = $capIOPS IOPS)..." -ForegroundColor Cyan
  Get-VMHardDiskDrive -VMName $VMName | Where-Object { $_.Path -eq $VHDPath } | Set-VMHardDiskDrive -MaximumIOPS $capIOPS

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
