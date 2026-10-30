# Run from the repository root in a normal PowerShell session; no elevation required.
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')
function Invoke-Checked([string]$Program, [string[]]$Arguments) {
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program failed with exit code $LASTEXITCODE." }
}
try {
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python launcher not found. Install Python 3.12 for your user and reopen PowerShell.' }
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Node.js/npm not found. Install a supported Node.js LTS and reopen PowerShell.' }
    Invoke-Checked 'py' @('-3.12', '--version')
    Invoke-Checked 'node' @('--version')
    Invoke-Checked 'npm.cmd' @('--version')
    if (-not (Test-Path 'jarvis\.venv\Scripts\python.exe')) {
        Invoke-Checked 'py' @('-3.12', '-m', 'venv', 'jarvis\.venv')
    }
    $python = '.\jarvis\.venv\Scripts\python.exe'
    Invoke-Checked $python @('-m', 'pip', 'install', '-r', 'jarvis\requirements.txt')
    Invoke-Checked 'npm.cmd' @('ci', '--no-audit', '--no-fund')
    Write-Host 'Setup complete. Run the diagnostic with:'
    Write-Host '  .\jarvis\.venv\Scripts\python.exe .\jarvis\main.py --check'
    Write-Host 'No model was downloaded and no microphone was activated.'
} catch {
    Write-Error "Setup failed: $_"
    exit 1
}
