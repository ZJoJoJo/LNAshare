$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment' }
}
& .\.venv\Scripts\python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies' }
& .\.venv\Scripts\python.exe collect_licenses.py
if ($LASTEXITCODE -ne 0) { throw 'Failed to collect dependency licenses' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onedir --windowed --name LANShare --collect-all pyftpdlib --collect-all paramiko --collect-all psutil --hidden-import asyncore --hidden-import asynchat main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --onefile --windowed --name LANSharePortable --workpath build-single --add-data 'third-party-licenses:third-party-licenses' --collect-all pyftpdlib --collect-all paramiko --collect-all psutil --hidden-import asyncore --hidden-import asynchat main.py
if ($LASTEXITCODE -ne 0) { throw 'Single-file build failed' }
Copy-Item -LiteralPath '使用说明.txt' -Destination 'dist\LANShare\使用说明.txt' -Force
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.txt' -Destination 'dist\LANShare\THIRD_PARTY_NOTICES.txt' -Force
Copy-Item -LiteralPath 'third-party-licenses' -Destination 'dist\LANShare' -Recurse -Force
Copy-Item -LiteralPath '以管理员身份运行.cmd' -Destination 'dist\LANShare\以管理员身份运行.cmd' -Force
Compress-Archive -Path 'dist\LANShare' -DestinationPath 'dist\LANShare-Windows-x64.zip' -Force
Copy-Item -LiteralPath '使用说明.txt' -Destination 'dist\使用说明.txt' -Force
Copy-Item -LiteralPath 'THIRD_PARTY_NOTICES.txt' -Destination 'dist\THIRD_PARTY_NOTICES.txt' -Force
Compress-Archive -LiteralPath 'dist\LANSharePortable.exe', 'dist\使用说明.txt', 'dist\THIRD_PARTY_NOTICES.txt' -DestinationPath 'dist\LANShare-Portable-Windows-x64.zip' -Force
Write-Output 'Portable package: dist\LANShare-Windows-x64.zip'
