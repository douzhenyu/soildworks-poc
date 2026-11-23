$ErrorActionPreference='Stop'
$bundledPython=Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
if(Test-Path $bundledPython) { $pythonExe=$bundledPython } else { $pythonExe=(Get-Command python -ErrorAction Stop).Source }
& $pythonExe -c 'import pymupdf'
if($LASTEXITCODE -ne 0) { throw '请先安装依赖：python -m pip install -r requirements.txt' }
Write-Host '请在浏览器打开 http://127.0.0.1:8765。关闭此窗口将停止服务。'
& $pythonExe (Join-Path $PSScriptRoot 'server.py')
