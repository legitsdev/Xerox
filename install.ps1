$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $RepoRoot

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Args = @()
    )

    try {
        & $Exe @Args -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" *> $null
        if ($LASTEXITCODE -ne 0) {
            return $false
        }

        $probeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("xerox-python-probe-" + [guid]::NewGuid().ToString("N"))
        $probeVenv = Join-Path $probeRoot 'venv'
        New-Item -ItemType Directory -Path $probeRoot -Force *> $null
        try {
            & $Exe @Args -m venv $probeVenv *> $null
            return ($LASTEXITCODE -eq 0)
        }
        finally {
            Remove-Item $probeRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        return $false
    }
}

function Invoke-Bootstrap {
    param(
        [Parameter(Mandatory = $true)][string]$Exe,
        [string[]]$Args = @()
    )

    & $Exe @Args "$RepoRoot\scripts\bootstrap.py" --shell powershell --launcher ".\\xerox.ps1"
    exit $LASTEXITCODE
}

if ($env:XEROX_PYTHON) {
    if (Test-PythonCandidate -Exe $env:XEROX_PYTHON) {
        Invoke-Bootstrap -Exe $env:XEROX_PYTHON
    }
}

$pyVersions = @('-3.12', '-3.11', '-3.10', '-3')
if (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($version in $pyVersions) {
        if (Test-PythonCandidate -Exe 'py' -Args @($version)) {
            Invoke-Bootstrap -Exe 'py' -Args @($version)
        }
    }
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    if (Test-PythonCandidate -Exe 'python') {
        Invoke-Bootstrap -Exe 'python'
    }
}

Write-Error "No working Python 3.10+ interpreter with a usable venv module was found. Install Python and rerun .\install.ps1. You can also set XEROX_PYTHON to a specific interpreter path."
exit 1
