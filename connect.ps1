# 1. Load Configuration
if (Test-Path ".env") {
    $envData = Get-Content ".env" | Where-Object { $_ -match '=' } | ConvertFrom-StringData
    $rpi_ip   = $envData.RPI_IP
    $rpi_user = $envData.RPI_USER
    $rpi_pass = $envData.RPI_PASS
} else {
    Write-Host "[ERROR] .env file missing!" -ForegroundColor Red
    exit
}

# 2. Tool Check: Ensure plink is available for the handshake
$plinkPath = ".\plink.exe"
if (-not (Test-Path $plinkPath)) {
    Invoke-WebRequest -Uri "https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe" -OutFile $plinkPath
}

# 3. Ensure Windows has an SSH key generated
$sshKeyPath = "$HOME\.ssh\id_rsa"
if (-not (Test-Path "$sshKeyPath.pub")) {
    Write-Host "[INFO] Generating a secure SSH key for your computer..." -ForegroundColor Yellow
    ssh-keygen -t rsa -b 4096 -N "" -f $sshKeyPath | Out-Null
}
$pubKey = Get-Content "$sshKeyPath.pub"

# 4. Automate Handshake & Authorize this Computer
Write-Host "[Odyseus SDK] Connecting to $rpi_ip..." -ForegroundColor Cyan

# Dummy command to grab the fingerprint
$testResult = & $plinkPath -pw $rpi_pass -batch "${rpi_user}@${rpi_ip}" "exit" 2>&1 | Out-String
$fingerprint = ""
if ($testResult -match "SHA256:([\w+/=]+)") { $fingerprint = $matches[1] }
$authArgs = if ($fingerprint) { @("-hostkey", "SHA256:$fingerprint") } else { @() }

# Check for the folder AND inject the SSH key in one silent background move
$remote_path = "/home/$rpi_user/odyseus_sdk"
$setupCommand = "mkdir -p ~/.ssh && echo '$pubKey' >> ~/.ssh/authorized_keys && chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && [ -d $remote_path ] && echo 'OK' || echo 'MISSING'"

$checkResult = & $plinkPath @authArgs -pw $rpi_pass -batch "${rpi_user}@${rpi_ip}" $setupCommand

if ($checkResult -match "MISSING") {
    Write-Host "`n[!] ERROR: Folder '$remote_path' not found on the Raspberry Pi." -ForegroundColor Red
    Write-Host "Please run 'upload.ps1' first to sync your project." -ForegroundColor Yellow
    exit
}

# 5. Launch the Final Terminal (Native SSH = No "Press Return" prompt)
Write-Host "[Odyseus SDK] Opening terminal in: $remote_path" -ForegroundColor Gray

# -t: forces interactive terminal, -o StrictHostKeyChecking=no: bypasses host check
ssh -t -o StrictHostKeyChecking=no "${rpi_user}@${rpi_ip}" "cd $remote_path && exec bash --login"