# ==============================================================================
# SCRIPT DE DÉPLOIEMENT : ARCHITECTURE "ECHO V5 INFRASTRUCTURE"
# ==============================================================================
# SCRIPT VERSION : 5.5.2 (Based on v5.4.0 Stable)
# DATE           : 2026-01-13
# AUTHOR         : Wilfried BARNAVON
# ==============================================================================
#
# --- QUOI (WHAT) ---
# Ce script PowerShell automatise la création d'une VM Linux sur Hyper-V et y déploie
# toute la stack ECHO (Docker, Scripts, Configs) en une seule opération "One-Click".
#
# --- POURQUOI (WHY) ---
# Le déploiement manuel d'une infrastructure IA est complexe, lent et sujet aux erreurs humaines.
# L'automatisation garantit :
# 1. Reproductibilité : Chaque VM est identique au bit près.
# 2. Vitesse : 2 minutes pour avoir un environnement de prod complet.
# 3. Traçabilité : On sait exactement quelle version du code tourne où.
#
# --- COMMENT (HOW - ALGO) ---
# 1. PRE-FLIGHT : Vérifie que le fichier VERSION et les scripts sources sont présents localement.
# 2. CONFIGURATION : Définit les ressources VM (CPU, RAM, Disque).
# 3. ENCODAGE : Lit tous les fichiers locaux (scripts .sh, python .py), les encode en Base64.
# 4. CLOUD-INIT : Génère un fichier 'user-data' énorme qui contient :
#    - La config utilisateur (login/pass).
#    - Les paquets à installer (docker, git, curl).
#    - Les fichiers encodés à écrire sur le disque (/opt/...). 
#    - Les commandes à lancer au premier boot (runcmd) : permissions, git clone, install-stack.sh.
# 5. HYPER-V : Crée le disque virtuel, attache l'ISO seed (cloud-init), et lance la VM.
# ==============================================================================

# --- FONCTION UTILITAIRE : PAUSE SUR ERREUR ---
# But : Empêcher la fenêtre de se fermer brutalement en cas d'erreur critique,
# pour laisser le temps à l'utilisateur de lire le message d'erreur.
function Pause-OnError {
  param([string]$Message)
  Write-Error "❌ ERREUR CRITIQUE : $Message"
  Write-Host "Appuyez sur Entrée pour quitter..." -ForegroundColor Red
  Read-Host
  Exit 1
}

# --- 1. INITIALISATION & VERSIONING ---
$SCRIPT_VERSION = "5.5.2"
$ScriptDir = $PSScriptRoot
$VersionFile = "$ScriptDir\VERSION"

# --- CONFIGURATION BRANCHE (NOUVEAU) ---
# Permet de définir quelle branche git sera suivie par la VM.
# Modifiez cette valeur si vous souhaitez déployer une branche de dev (ex: "dev", "feature-x").
#$BRANCHE = "main"
$BRANCHE = "dev"

Write-Host "🚀 ECHO INFRASTRUCTURE DEPLOYER [Script v$SCRIPT_VERSION]" -ForegroundColor Cyan
Write-Host "==========================================================" 

# Vérification Stricte du Fichier VERSION (Source de vérité de la Stack)
if (-not (Test-Path $VersionFile)) {
  Pause-OnError "Fichier 'VERSION' introuvable à la racine ($VersionFile). Requis pour la stack."
}

# Lecture et Nettoyage de la version (Retrait du 'v' si présent pour normalisation X.Y.Z)
$RAW_VERSION = (Get-Content -Path $VersionFile -Raw).Trim()
$ECHO_VERSION = $RAW_VERSION -replace "^v", ""

if ($ECHO_VERSION -notmatch "^\d+\.\d+\.\d+") {
  Write-Warning "⚠️  Format de version atypique détecté : $ECHO_VERSION (Attendu: X.Y.Z)"
}

Write-Host "📦 Stack Target    : v$ECHO_VERSION" -ForegroundColor Green
Write-Host "🌿 Target Branch   : $BRANCHE" -ForegroundColor Green

# Vérification de cohérence (Optionnel mais informatif)
if ($ECHO_VERSION -ne $SCRIPT_VERSION) {
  Write-Warning "⚠️  Attention : La version du script ($SCRIPT_VERSION) diffère de la version cible ($ECHO_VERSION)."
}

# --- 2. CONFIGURATION VM DYNAMIQUE ---
# Nommage conventionnel : ECHO-vX.Y.Z-BRANCHE
$VMName = "ECHO-v$ECHO_VERSION-$BRANCHE"
Write-Host "🖥️  VM Name         : $VMName" -ForegroundColor Yellow

$SwitchName = "Bridge LAN" # /!\ Vérifiez le nom de votre switch Hyper-V
$ISOPath = "D:\ISO\ubuntu-24.04.3-live-server-amd64.iso"
$VMPath = "D:\Virtual Machines"
$VHDPath = "$VMPath\Virtual Hard Drives\$VMName.vhdx"
$SeedPath = "$VMPath\Virtual Hard Drives\$VMName-seed.vhdx"
$VHDSize = 50GB
$RAMStartup = 4096MB

$AutoUser = "echo"
$AppPassword = "password"
$AutoHostname = $VMName.ToLower() -replace '\s', ''
# Hash généré pour 'password' (SHA-512)
$HashPassword = '$6$salt$Izj.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0'

# --- 3. VERIFICATION DES FICHIERS (MAPPING STRICT - MIS A JOUR v5.5) ---
# Dictionnaire : Source Windows => Destination Linux
$FilesMap = @{
  # 1. SCRIPTS D'INSTALLATION
  "/opt/owui-scripts/install-stack.sh"            = "$ScriptDir\00-Install\install-stack.sh"
  "/opt/owui-scripts/update-echo.sh"              = "$ScriptDir\00-Install\update-echo.sh"
  "/opt/owui-scripts/upgrade-echo.sh"             = "$ScriptDir\00-Install\upgrade-echo.sh"
  "/opt/owui-scripts/config-owui.sh"              = "$ScriptDir\00-Install\config-owui.sh"

  # 2. SERVICES BACKEND
  "/opt/admin-manager/server.py"                  = "$ScriptDir\01-docker-admin-manager\server.py"
  "/opt/python-worker/worker_api.py"              = "$ScriptDir\02-docker-python-worker\worker_api.py"
  "/opt/browser-agent/browser_api.py"             = "$ScriptDir\06-docker-browser-agent\browser_api.py"

  # 3. CŒUR COGNITIF (UPDATED PATH v5.5: pipes instead of functions)
  "/opt/owui-pipes/pipe_engine.py"                = "$ScriptDir\03-OWUI-pipes\pipe_engine.py"

  # 4. OUTILS
  "/opt/owui-tools/python_code_executor.py"       = "$ScriptDir\04-OWUI-tools\python_code_executor.py"
  "/opt/owui-tools/gemini_internal_web_search.py" = "$ScriptDir\04-OWUI-tools\gemini_internal_web_search.py"
  "/opt/owui-tools/web_browser_advanced.py"       = "$ScriptDir\04-OWUI-tools\web_browser_advanced.py"
  "/opt/owui-tools/api_client.py"                 = "$ScriptDir\04-OWUI-tools\api_client.py"
  "/opt/owui-tools/context_gauge.py"              = "$ScriptDir\04-OWUI-tools\context_gauge.py"

  # 5. FILTRES & ACTIONS (UPDATED v5.5)
  "/opt/owui-filters/bypass_rag.py"               = "$ScriptDir\05-OWUI-filters\bypass_rag.py"
  "/opt/owui-actions/reset_auth_action.py"        = "$ScriptDir\07-OWUI-actions\reset_auth_action.py"

  # 6. VERSIONING
  # NOTE: On copie le fichier VERSION local vers /opt/ECHO_VERSION sur la VM
  "/opt/ECHO_VERSION"                             = "$ScriptDir\VERSION"
}

Write-Host "🔍 Vérification de l'intégrité des fichiers sources..."
foreach ($Key in $FilesMap.Keys) {
  # Exception pour echo-manifest.json qui est optionnel
  if ($Key -eq "/opt/echo-manifest.json" -and -not (Test-Path $FilesMap[$Key])) { continue }

  if (-not (Test-Path $FilesMap[$Key])) {
    Pause-OnError "Fichier manquant : $($FilesMap[$Key]) (Destination: $Key)"
  }
}
Write-Host "✅ Tous les fichiers critiques sont présents." -ForegroundColor Green

# --- 4. AUTO-ELEVATION ADMIN ---
# Requis car Hyper-V nécessite des privilèges Administrateur
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  # FIX: Utilisation de l'interpolation simple pour supporter les chemins avec espaces (Google Drives)
  # La syntaxe complexe de la v5.4.0 causait l'erreur "Jeton inattendu" sur les chemins longs
  Start-Process powershell.exe -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
  Exit
}

# --- 5. GENERATION BLOC WRITE_FILES (CLOUD-INIT) ---
$WriteFilesBlock = ""

foreach ($DestPath in $FilesMap.Keys) {
  if (Test-Path $FilesMap[$DestPath]) {
    $LocalPath = $FilesMap[$DestPath]
    # FIX ENCODING: Force la lecture en UTF8 pour éviter les erreurs de caractères spéciaux
    $RawContent = [System.IO.File]::ReadAllText($LocalPath, [System.Text.Encoding]::UTF8)

    # Remplacement dynamique user dans install-stack.sh uniquement
    if ($DestPath -eq "/opt/owui-scripts/install-stack.sh") {
      $RawContent = $RawContent.Replace('${AutoUser}', $AutoUser)
    }

    # Encodage Base64 pour éviter problèmes caractères spéciaux dans YAML
    $B64Content = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RawContent.Replace("`r`n", "`n")))

    $WriteFilesBlock += "      - path: $DestPath`n"
    $WriteFilesBlock += "        permissions: '0755'`n"
    $WriteFilesBlock += "        encoding: b64`n"
    $WriteFilesBlock += "        content: $B64Content`n"
  }
}

# Traçabilité du Script de Déploiement
$ScriptVerContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($SCRIPT_VERSION))
$WriteFilesBlock += "      - path: /opt/echo_deploy_script_version`n"
$WriteFilesBlock += "        permissions: '0444'`n" # Lecture seule
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $ScriptVerContent`n"

# --- INJECTION DE LA BRANCHE (NOUVEAU) ---
# On écrit la variable $BRANCHE dans /opt/ECHO_BRANCH
$BrancheContent = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($BRANCHE))
$WriteFilesBlock += "      - path: /opt/ECHO_BRANCH`n"
$WriteFilesBlock += "        permissions: '0644'`n"
$WriteFilesBlock += "        encoding: b64`n"
$WriteFilesBlock += "        content: $BrancheContent`n"


# --- 6. USER-DATA CLOUD-INIT ---
# REVERT TO 5.4.0 STRUCTURE (Strict Copy of working Logic)
$UserDataContent = @"
#cloud-config
autoinstall:
  version: 1
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
      - linux-cloud-tools-virtual
      - net-tools
      - chrony
      - docker.io
      - docker-compose
    write_files:
      - path: /etc/chrony/conf.d/hyperv.conf
        content: |
          refclock PHC /dev/ptp0 poll 3 dpoll -2 offset 0
$WriteFilesBlock
    runcmd:
      - [chown, -R, "${AutoUser}:${AutoUser}", "/home/${AutoUser}"]
      - [systemctl restart chrony]
      # Permissions Exécutables pour les scripts bash
      - [chmod, +x, /opt/owui-scripts/install-stack.sh]
      - [chmod, +x, /opt/owui-scripts/update-echo.sh]
      - [chmod, +x, /opt/owui-scripts/upgrade-echo.sh]
      - [chmod, +x, /opt/owui-scripts/config-owui.sh]

      # Liens Symboliques pour usage facile
      - [ln, -s, /opt/owui-scripts/update-echo.sh, /usr/local/bin/update-echo]
      - [ln, -s, /opt/owui-scripts/upgrade-echo.sh, /usr/local/bin/upgrade-echo]

      # --- GIT INIT ---
      # Clone du repo pour permettre les updates futurs.
      - [git, clone, "https://github.com/Wilfried-Barnavon-Perso/echo-framework.git", "/opt/echo-framework-source"]

      # Lancement Installation
      - [/opt/owui-scripts/install-stack.sh]
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