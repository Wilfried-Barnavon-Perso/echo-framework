# ==============================================================================
# SCRIPT DE DÉPLOIEMENT : ARCHITECTURE "ECHO V5.2 INFRASTRUCTURE"
# ==============================================================================
# MODIFICATIONS V5.2 : 
# - Inclusion Browser Agent, Filtres, Outils v2
# - Injection des scripts de mise à jour (update/upgrade) liés à GitHub
# - Injection des scripts de maintenance (Admin Manager Server)
# ==============================================================================

# --- CONFIGURATION VM ---
$VMName = "ECHO-v5.2-Prod-005"
$SwitchName = "Bridge LAN" # /!\ Vérifiez le nom de votre switch Hyper-V
$ISOPath = "D:\ISO\ubuntu-24.04.3-live-server-amd64.iso" 
$VMPath = "D:\Virtual Machines"
$VHDPath = "$VMPath\Virtual Hard Drives\$VMName.vhdx"
$SeedPath = "$VMPath\Virtual Hard Drives\$VMName-seed.vhdx"
$VHDSize = 50GB 
$RAMStartup = 4096MB

$AutoUser = "echo"
$AppPassword = "password" 
$AutoHostname = $VMName.ToLower()
# Hash généré pour 'password' (SHA-512)
$HashPassword = '$6$salt$Izj.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0.j/0' 


$ScriptDir = $PSScriptRoot

# --- VERIFICATION DES FICHIERS (MAPPING V5.2) ---
# Format : "Chemin_Destination_VM" = "Chemin_Source_Windows_Local"
$FilesMap = @{
  # 1. SCRIPTS D'INSTALLATION & MAINTENANCE (Tout dans /opt/owui-scripts)
  "/opt/owui-scripts/install-stack.sh"            = "$ScriptDir\00-Install\install-stack.sh"
  "/opt/owui-scripts/update-echo.sh"              = "$ScriptDir\00-Install\update-echo.sh"   # Déplacé dans 00-Install
  "/opt/owui-scripts/upgrade-echo.sh"             = "$ScriptDir\00-Install\upgrade-echo.sh"  # Déplacé dans 00-Install
  "/opt/owui-scripts/config-owui.sh"              = "$ScriptDir\00-Install\config-owui.sh"
  
  # 2. SERVICES BACKEND (Code source Python)
  "/opt/admin-manager/server.py"                  = "$ScriptDir\01-docker-admin-manager\server.py"
  "/opt/python-worker/worker_api.py"              = "$ScriptDir\02-docker-python-worker\worker_api.py"
  "/opt/browser-agent/browser_api.py"             = "$ScriptDir\06-docker-browser-agent\browser_api.py"

  # 3. CŒUR COGNITIF (Pipes)
  "/opt/owui-functions/pipe_engine.py"            = "$ScriptDir\03-OWUI-functions\pipe_engine.py"
  
  # 4. OUTILS (Tools)
  "/opt/owui-tools/python_code_executor.py"       = "$ScriptDir\04-OWUI-tools\python_code_executor.py"
  "/opt/owui-tools/gemini_internal_web_search.py" = "$ScriptDir\04-OWUI-tools\gemini_internal_web_search.py"
  "/opt/owui-tools/web_browser_advanced.py"       = "$ScriptDir\04-OWUI-tools\web_browser_advanced.py"
  "/opt/owui-tools/api_client.py"                 = "$ScriptDir\04-OWUI-tools\api_client.py"

  # 5. FILTRES (Filters)
  "/opt/owui-filters/token_monitor.py"            = "$ScriptDir\05-OWUI-filters\token_monitor.py"
}

# Vérification présence locale
foreach ($Key in $FilesMap.Keys) {
  if (-not (Test-Path $FilesMap[$Key])) { 
    Write-Error "MANQUANT : $($FilesMap[$Key]) (Destination: $Key)"
    Exit 
  }
}

# --- AUTO-ELEVATION ADMIN ---
if (!([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
  Start-Process powershell.exe -ArgumentList ("-NoProfile -ExecutionPolicy Bypass -File `"{0}`"" -f $PSCommandPath) -Verb RunAs
  Exit
}



# --- GENERATION BLOC WRITE_FILES (CLOUD-INIT) ---
$WriteFilesBlock = ""

foreach ($DestPath in $FilesMap.Keys) {
  $LocalPath = $FilesMap[$DestPath]
  $RawContent = Get-Content -Path $LocalPath -Raw
    
  # Remplacement dynamique user si besoin
  if ($DestPath -eq "/opt/owui-scripts/install-stack.sh") {
    $RawContent = $RawContent.Replace('${AutoUser}', $AutoUser)
  }
    
  # Encodage Base64 pour éviter problèmes caractères spéciaux dans YAML
  $B64Content = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($RawContent.Replace("`r`n", "`n")))
    
  # Indentation YAML stricte (6 espaces)
  $WriteFilesBlock += "      - path: $DestPath`n"
  $WriteFilesBlock += "        permissions: '0755'`n"
  $WriteFilesBlock += "        encoding: b64`n"
  $WriteFilesBlock += "        content: $B64Content`n"
}

# --- USER-DATA CLOUD-INIT ---
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
      # On rend les scripts exécutables dans leur nouvel emplacement
      - [chmod, +x, /opt/owui-scripts/install-stack.sh]
      - [chmod, +x, /opt/owui-scripts/update-echo.sh]
      - [chmod, +x, /opt/owui-scripts/upgrade-echo.sh]
      - [chmod, +x, /opt/owui-scripts/config-owui.sh]
      # Création de liens symboliques pour usage facile (facultatif mais pratique)
      - [ln, -s, /opt/owui-scripts/update-echo.sh, /usr/local/bin/update-echo]
      - [ln, -s, /opt/owui-scripts/upgrade-echo.sh, /usr/local/bin/upgrade-echo]
      # Lancement Installation Initiale
      - [/opt/owui-scripts/install-stack.sh]
"@

# --- CREATION DISQUES & VM ---
$MetaDataContent = "instance-id: $VMName"

if (Test-Path $SeedPath) { Remove-Item $SeedPath -Force }
if (Test-Path $VHDPath) { Remove-Item $VHDPath -Force }

# Disque Seed (Cloud-Init datas)
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

# Construction VM Hyper-V
if (-not (Get-VM -Name $VMName -ErrorAction SilentlyContinue)) {
  Write-Host "Création de la VM $VMName..."
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