# ==============================================================================
# SCRIPT DE DÉPLOIEMENT : ARCHITECTURE "ECHO V5 INFRASTRUCTURE"
# ==============================================================================
# VERSION : 5.24.1
# DATE    : 2026-02-18

# AUTHOR         : Wilfried BARNAVON
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script PowerShell automatise la création d'une VM Linux sur Hyper-V et y déploie
# toute la stack ECHO (Docker, Scripts, Configs) via injection d'archive ZIP.
#
# --- POURQUOI (WHY) ---
# L'injection d'un ZIP Base64 unique permet de s'affranchir de la complexité
# de déclaration de multiples fichiers dans Cloud-Init et supporte mieux les
# gros volumes de fichiers ou les structures profondes.
#
# --- COMMENT (HOW - ALGO) ---
# 1. PRE-FLIGHT : Vérifie que le fichier VERSION est présent.
# 2. CONFIGURATION : Définit les ressources VM.
# 3. PACKAGING : 
#    - Zippe le dossier courant (Exclusions: .git, .venv, etc.)
#    - Encode le ZIP en Base64.
# 4. CLOUD-INIT : Génère un fichier 'user-data' qui : 
#    - Injecte le ZIP.
#    - Dézippe dans /opt/echo-framework-source.
#    - Installe les outils (Docker, yq, jq).
#    - Lance sync-echo.sh --local-only (évite le git clone).
#    - Lance install-stack.sh.
# 5. HYPER-V : Crée et lance la VM.
# ==============================================================================

# --- FONCTION UTILITAIRE : PAUSE SUR ERREUR ---
$SwitchName = "Bridge LAN" # /!\ Vérifiez le nom de votre switch Hyper-V
$ISOPath = "D:\ISO\ubuntu-24.04.3-live-server-amd64-autoinstall.iso"
$VMPath = "D:\Virtual Machines"
$VHDSize = 50GB
$RAMStartup = 2048

# --- CONFIGURATION RESEAU (IP STATIQUE) ---
$STATIC_IP_CIDR = "192.168.147.100/24"
$GATEWAY_IP = "192.168.147.254"
$DNS_SERVERS = "[86.54.11.100, 1.1.1.1, 8.8.8.8]"
$SWITCH_NAME = "Bridge LAN"

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

# --- 1. INITIALISATION & VERSIONING ---
$SCRIPT_VERSION = "5.24.0"
$ScriptDir = $PSScriptRoot
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
  Write-Warning "⚠️  Format de version atypique détecté : $ECHO_VERSION (Attendu: X.Y.Z)"
}

Write-Host "📦 Stack Target    : v$ECHO_VERSION" -ForegroundColor Green
Write-Host "🌿 Target Branch   : $BRANCHE" -ForegroundColor Green

# --- 2. CONFIGURATION VM DYNAMIQUE ---
$VMName = "ECHO-v$ECHO_VERSION-$BRANCHE"
$VHDPath = "$VMPath\Virtual Hard Drives\$VMName.vhdx"
$SeedPath = "$VMPath\Virtual Hard Drives\$VMName-seed.vhdx"

Write-Host "🖥️  VM Name         : $VMName" -ForegroundColor Yellow

$AutoUser = "echo"
$AppPassword = "password"
$AutoHostname = $VMName.ToLower() -replace '\s', ''
# Hash généré pour 'password' (SHA-512)
$HashPassword = '$6$salt$Izj.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0'

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

  Write-Host "🔐 Encodage Base64..."
  $RawBytes = [System.IO.File]::ReadAllBytes($TempZipPath)
  $ZipB64Content = [Convert]::ToBase64String($RawBytes)

}

catch {
  Pause-OnError "Echec lors de la création de l'archive ZIP : $_ "
}

finally {
  # NETTOYAGE IMMEDIAT
  if (Test-Path $TempZipPath) { 
    Remove-Item $TempZipPath -Force 
    Write-Host "   🧹 Fichier temporaire supprimé."
  }
}

# --- 5. GENERATION BLOC WRITE_FILES (CLOUD-INIT) ---
$WriteFilesBlock = ""

# Injection de l'archive ZIP
$WriteFilesBlock += "      - path: /opt/echo-framework-source/source.zip`n"
$WriteFilesBlock += "        permissions: '0644'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $ZipB64Content`n"

# Traçabilité du Script de Déploiement
$ScriptVerContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($SCRIPT_VERSION))
$WriteFilesBlock += "      - path: /opt/echo_deploy_script_version`n"
$WriteFilesBlock += "        permissions: '0444'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $ScriptVerContent`n"

# Injection de la BRANCHE (Requis par sync-echo.sh)
$BrancheContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($BRANCHE))
$WriteFilesBlock += "      - path: /opt/ECHO_BRANCH`n"
$WriteFilesBlock += "        permissions: '0644'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $BrancheContent`n"

# --- 6. USER-DATA CLOUD-INIT ---
$UserDataContent = @"
#cloud-config
autoinstall:
  version: 1
  network:
    version: 2
    ethernets:
      eth0:
        dhcp4: false
        addresses:
          - $STATIC_IP_CIDR
        gateway4: $GATEWAY_IP
        nameservers:
          addresses: $DNS_SERVERS
  identity: {hostname: $AutoHostname, password: "$HashPassword", username: $AutoUser}
  keyboard: {layout: fr}
  locale: fr_FR.UTF-8
  timezone: Europe/Paris
  ssh: {install-server: true, allow-pw: true}
  storage: {layout: {name: direct}}
  late-commands:
    - "sed -i 's/XKBLAYOUT=\"us\"/XKBLAYOUT=\"fr\"/g' /target/etc/default/keyboard"
  user-data:
    chpasswd:
      list: |
        ${AutoUser}:${AppPassword}
      expire: False
    package_update: true
    package_upgrade: true
    packages:
      - curl
      - ca-certificates
      - gnupg
      - jq
      - git
      - unzip
      - linux-cloud-tools-virtual
      - net-tools
      - chrony
      - docker.io
      - docker-compose
    write_files:
      - path: /etc/chrony/conf.d/hyperv.conf
        content: |
          refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0
      
      # Configuration de la rotation des logs Docker (Sécurité Disque)
      - path: /etc/docker/daemon.json
        content: |
          {
            "log-driver": "json-file",
            "log-opts": {
              "max-size": "10m",
              "max-file": "3"
            }
          }
$WriteFilesBlock
    runcmd:
      - [chown, -R, "${AutoUser}:${AutoUser}", "/home/${AutoUser}"]
      - "systemctl restart chrony"
      
      # --- INSTALLATION OUTILS SUPPLEMENTAIRES ---
      # Installation de yq (Processeur YAML) pour l'introspection des scripts
      - "wget https://github.com/mikefarah/yq/releases/download/v4.40.5/yq_linux_amd64 -O /usr/local/bin/yq && chmod +x /usr/local/bin/yq"

      # --- EXTRACTION ET DEPLOIEMENT ---
      # 1. Préparation du dossier source
      - "mkdir -p /opt/echo-framework-source"
      
      # 2. Décompression de l'archive injectée
      - "unzip -o /opt/echo-framework-source/source.zip -d /opt/echo-framework-source"
      
      # 3. Suppression de l'archive (GAIN PLACE)
      - "rm /opt/echo-framework-source/source.zip"
      
      # 4. Préparation du script de synchro
      - "chmod +x /opt/echo-framework-source/00-echo-scripts/sync-echo.sh"
      
      # 5. Lancement de la Synchro en mode LOCAL-ONLY (Pas de git clone)
      #    Cela va distribuer les fichiers de /opt/echo-framework-source vers /opt/echo-scripts, etc.
      - "/opt/echo-framework-source/00-echo-scripts/sync-echo.sh --local-only"
      
      # 6. Configuration de l'utilisateur dans le script d'install (si variable utilisée)
      - "sed -i 's/\\${AutoUser}/${AutoUser}/g' /opt/echo-scripts/install-stack.sh"

      # 7. Lancement Installation de la Stack
      - "/opt/echo-scripts/install-stack.sh"
"@

# --- 7. CREATION DISQUES & VM (HYPER-V) ---
$MetaDataContent = "instance-id: $VMName"

if (Test-Path $SeedPath) { Remove-Item $SeedPath -Force }
if (Test-Path $VHDPath) { Remove-Item $VHDPath -Force }

# Disque Seed
New-VHD -Path $SeedPath -SizeBytes 100MB -Dynamic | Out-Null
$Disk = Mount-VHD -Path $SeedPath -Passthru
$Disk | Initialize-Disk -PartitionStyle MBR -PassThru | New-Partition -UseMaximumSize -AssignDriveLetter | Format-Volume -FileSystem FAT32 -NewFileSystemLabel "CIDATA" | Out-Null
Start-Sleep -Seconds 2
$DriveLetter = ($Disk | Get-Partition | Get-Volume).DriveLetter + ":\" 

# Ecriture config
[System.IO.File]::WriteAllText("${DriveLetter}user-data", ($UserDataContent -replace "`r`n", "`n"), [System.Text.UTF8Encoding]::new($false))
[System.IO.File]::WriteAllText("${DriveLetter}meta-data", $MetaDataContent, [System.Text.UTF8Encoding]::new($false))
New-Item -Path "${DriveLetter}vendor-data" -ItemType File | Out-Null

Start-Sleep -Seconds 2
Dismount-VHD -Path $SeedPath

# Construction VM
if (-not (Get-VM -Name $VMName -ErrorAction SilentlyContinue)) {
  Write-Host "🔨 Création de la VM Hyper-V : $VMName"

  New-VM -Name $VMName -MemoryStartupBytes $RAMStartup -Generation 2 -SwitchName $SwitchName -NoVHD
  Set-VM -Name $VMName -DynamicMemory -MemoryMinimumBytes 2048MB -MemoryMaximumBytes 8192MB -ProcessorCount 4
  Set-VMFirmware -VMName $VMName -EnableSecureBoot Off
  New-VHD -Path $VHDPath -SizeBytes $VHDSize -Dynamic | Out-Null
  Add-VMHardDiskDrive -VMName $VMName -Path $VHDPath
  Add-VMDvdDrive -VMName $VMName -Path $ISOPath
  Add-VMHardDiskDrive -VMName $VMName -Path $SeedPath
  Set-VMFirmware -VMName $VMName -FirstBootDevice (Get-VMDvdDrive -VMName $VMName)

  Write-Host "✅ VM Prête ! Lancement..."
  Start-VM -Name $VMName
  Start-Process -FilePath "vmconnect.exe" -ArgumentList "localhost $VMName"
}
else {
  Write-Warning "⚠️  La VM $VMName existe déjà. Aucune action effectuée."
  Pause-OnError "Veuillez supprimer la VM existante ou incrémenter la version du fichier VERSION."
}