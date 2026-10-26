# Example from the repository root:  .\deploy\deploy.ps1 root@example-host
# Full usage and rollback behaviour: deploy/README.md
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidatePattern('^[A-Za-z0-9_][A-Za-z0-9_.@:-]*$')]
    [string]$Target,
    [switch]$WorkingTree,
    [switch]$DesignOnly,
    [string]$Frontend,
    [switch]$KeepFrontend,
    [string]$Output
)

# Identical entry point in PyImmichFrame, immich-album-butler and immich-dup-butler.
$ErrorActionPreference = 'Stop'
$python = Get-Command python3, python, py -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $python) { throw 'Deployment requires Python 3.11 or newer on PATH.' }

$deployArgs = @((Join-Path $PSScriptRoot 'deploy.py'))
if ($python.Name -eq 'py.exe') { $deployArgs = @('-3') + $deployArgs }
if ($Target) { $deployArgs += $Target }
if ($WorkingTree) { $deployArgs += '--working-tree' }
if ($DesignOnly) { $deployArgs += '--design-only' }
if ($Frontend) { $deployArgs += @('--frontend', $Frontend) }
if ($KeepFrontend) { $deployArgs += '--keep-frontend' }
if ($Output) { $deployArgs += @('--output', $Output) }

& $python.Source @deployArgs
if ($LASTEXITCODE -ne 0) { throw "Deployment failed with exit code $LASTEXITCODE." }
