$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = Join-Path $RepoRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $PythonPath)) {
    Write-Error "Xerox is not installed yet. Run .\install.ps1 first."
    exit 1
}

Set-Location $RepoRoot
& $PythonPath -m xerox @args
exit $LASTEXITCODE
