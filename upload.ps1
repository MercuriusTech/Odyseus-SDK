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

# 2. Setup Tools
$pscpPath = "$PSScriptRoot\pscp.exe"
$plinkPath = "$PSScriptRoot\plink.exe"
if (-not (Test-Path $pscpPath)) { Invoke-WebRequest "https://the.earth.li/~sgtatham/putty/latest/w64/pscp.exe" -OutFile $pscpPath }
if (-not (Test-Path $plinkPath)) { Invoke-WebRequest "https://the.earth.li/~sgtatham/putty/latest/w64/plink.exe" -OutFile $plinkPath }

# 3. Create Clean Staging Area
$stagingPath = "$PSScriptRoot\.upload_staging"
if (Test-Path $stagingPath) { Remove-Item -Recurse -Force $stagingPath }
New-Item -ItemType Directory -Path $stagingPath | Out-Null

# List of things to SKIP (Notice .env and .git are NOT here anymore)
$excludeList = @(
    "pscp.exe", "plink.exe", 
    "venv", ".venv", 
    "node_modules", ".pnp", 
    ".upload_staging", 
    "upload.ps1", "connect.ps1"
)

Write-Host "[Odyseus SDK] Syncing project including .git and .env..." -ForegroundColor Cyan

# -Force is essential here to capture the hidden .git and .env folders/files
Get-ChildItem -Path $PSScriptRoot -Exclude $excludeList -Force | Copy-Item -Destination $stagingPath -Recurse -Force

# 4. Handshake & Remote Folder Prep
Write-Host "[Odyseus SDK] Preparing Pi at $rpi_ip..." -ForegroundColor Gray
$testResult = & $plinkPath -pw $rpi_pass -batch "${rpi_user}@${rpi_ip}" "exit" 2>&1 | Out-String
$fingerprint = ""
if ($testResult -match "SHA256:([\w+/=]+)") { $fingerprint = $matches[1] }
$authArgs = if ($fingerprint) { @("-hostkey", "SHA256:$fingerprint") } else { @() }

$remote_path = "/home/$rpi_user/Odyseus-SDK"
& $plinkPath @authArgs -pw $rpi_pass -batch "${rpi_user}@${rpi_ip}" "mkdir -p $remote_path"

# 5. Perform the Upload
Write-Host "[Odyseus SDK] Transferring files to $remote_path..." -ForegroundColor Cyan
& $pscpPath @authArgs -pw $rpi_pass -batch -scp -r "$stagingPath\*" "${rpi_user}@${rpi_ip}:${remote_path}/"

# 6. Cleanup & Results
Remove-Item -Recurse -Force $stagingPath
if ($LASTEXITCODE -eq 0) {
    Write-Host "[SUCCESS] Mission Accomplished! The Pi is now a functional Git repo." -ForegroundColor Green
} else {
    Write-Host "[FAILED] Upload failed. Check your network or credentials." -ForegroundColor Red
}