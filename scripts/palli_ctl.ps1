Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ScriptPath = Join-Path $RootDir "scripts\palli_ctl.py"

if (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 $ScriptPath @args
    exit $LASTEXITCODE
}

if (Get-Command python -ErrorAction SilentlyContinue) {
    & python $ScriptPath @args
    exit $LASTEXITCODE
}

Write-Error "Python is required to run scripts/palli_ctl.py"
exit 1
