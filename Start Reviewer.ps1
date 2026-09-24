param([string]$InputPath, [string]$SelfTestReport)
$ErrorActionPreference = 'Stop'
try {
    $reviewRoot = $PSScriptRoot
    $reviewPython = Join-Path $reviewRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $reviewPython)) {
        Write-Host 'First start: preparing a private Python environment for the reviewer...'
        $reviewBase = $null
        $reviewBaseArgs = @()
        if (Get-Command py -ErrorAction SilentlyContinue) {
            foreach ($reviewVersion in @('-3.12', '-3.13')) {
                & py $reviewVersion -c 'import sys, tkinter; assert sys.version_info >= (3, 12)' 2>$null
                if ($LASTEXITCODE -eq 0) { $reviewBase = 'py'; $reviewBaseArgs = @($reviewVersion); break }
            }
        }
        if (-not $reviewBase -and (Get-Command python -ErrorAction SilentlyContinue)) {
            & python -c 'import sys, tkinter; assert (3, 12) <= sys.version_info[:2] <= (3, 13)' 2>$null
            if ($LASTEXITCODE -eq 0) { $reviewBase = 'python' }
        }
        if (-not $reviewBase) {
            throw 'Install Python 3.12 or 3.13 from python.org with Tk and the Python launcher enabled, then start this file again. See README.md.'
        }
        & $reviewBase @reviewBaseArgs -m venv (Join-Path $reviewRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the private Python environment.' }
    }
    $reviewRequirements = Join-Path $reviewRoot 'requirements.txt'
    $reviewStamp = Join-Path $reviewRoot '.venv\reviewer-requirements.sha256'
    $reviewHash = (Get-FileHash -LiteralPath $reviewRequirements -Algorithm SHA256).Hash
    if (-not (Test-Path -LiteralPath $reviewStamp) -or (Get-Content -LiteralPath $reviewStamp -Raw).Trim() -ne $reviewHash) {
        Write-Host 'Installing the tested imaging libraries. This first step needs internet access...'
        & $reviewPython -m pip install --disable-pip-version-check -r $reviewRequirements
        if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. Check your internet connection and try again.' }
        Set-Content -LiteralPath $reviewStamp -Value $reviewHash
    }
    $reviewArgs = @((Join-Path $reviewRoot 'app.py'))
    if ($InputPath) { $reviewArgs += @('--input', $InputPath) }
    if ($SelfTestReport) { $reviewArgs += @('--self-test', $SelfTestReport) }
    & $reviewPython @reviewArgs
    if ($LASTEXITCODE -ne 0) { throw 'The reviewer stopped with an error. The details are shown above.' }
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
