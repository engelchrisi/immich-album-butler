[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidatePattern('^[A-Za-z0-9_.@:-]+$')]
    [string]$Target,
    [switch]$WorkingTree,
    [switch]$DesignOnly
)

<#
.SYNOPSIS
Deploy immich-album-butler to a Linux systemd host over SSH.

.DESCRIPTION
Packages committed HEAD by default. -WorkingTree intentionally overlays tracked
local changes, useful while preparing a release. The script only reports
success after the selected services are active and the installed package reports
the expected pyproject.toml version.

.EXAMPLE
.\deploy\deploy.ps1 root@example-host
.\deploy\deploy.ps1 root@example-host -WorkingTree
.\deploy\deploy.ps1 root@example-host -DesignOnly
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

foreach ($command in 'git', 'scp', 'ssh', 'tar') {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Deployment requires '$command' on PATH."
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot '.git'))) {
    throw 'Run this script from an immich-album-butler checkout.'
}

$versionLine = Get-Content -LiteralPath (Join-Path $repoRoot 'pyproject.toml') |
    Where-Object { $_ -match '^version = "' } | Select-Object -First 1
if ($versionLine -notmatch '^version = "([^"]+)"') {
    throw 'Could not read the project version from pyproject.toml.'
}
$expectedVersion = $Matches[1]
$services = if ($DesignOnly) {
    'immich-album-butler-design'
} else {
    'immich-album-butler immich-album-butler-design'
}

$token = [guid]::NewGuid().ToString('N')
$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) "iab-deploy-$token"
$stageRoot = Join-Path $tempRoot 'stage'
$archive = Join-Path $tempRoot 'immich-album-butler.tar.gz'
$remoteArchive = "/tmp/immich-album-butler-deploy-$token.tar.gz"
$remoteRoot = "/tmp/immich-album-butler-deploy-$token"

try {
    New-Item -ItemType Directory -Path $stageRoot | Out-Null
    Push-Location $repoRoot
    try {
        # Start from HEAD so ignored files, local state, and unrelated
        # untracked files cannot be packaged by accident.
        $headArchive = Join-Path $tempRoot 'head.tar'
        & git archive --format=tar --output $headArchive HEAD -- `
            immich_album_butler deploy examples pyproject.toml
        if ($LASTEXITCODE -ne 0) { throw 'git archive failed.' }
        & tar -xf $headArchive -C $stageRoot
        if ($LASTEXITCODE -ne 0) { throw 'Could not unpack the HEAD snapshot.' }

        if ($WorkingTree) {
            # Include only tracked modifications in the deliberate local
            # overlay; untracked files must be committed before deployment.
            $changed = & git diff --name-only HEAD -- `
                immich_album_butler deploy examples pyproject.toml
            if ($LASTEXITCODE -ne 0) { throw 'Could not read working-tree changes.' }
            foreach ($relativePath in $changed) {
                $source = Join-Path $repoRoot $relativePath
                if (-not (Test-Path -LiteralPath $source -PathType Leaf)) { continue }
                $destination = Join-Path $stageRoot $relativePath
                New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
                Copy-Item -LiteralPath $source -Destination $destination -Force
            }
        }

        & tar -C $stageRoot -czf $archive .
        if ($LASTEXITCODE -ne 0) { throw 'Could not package the deployment archive.' }
    } finally {
        Pop-Location
    }

    $sourceLabel = if ($WorkingTree) { 'tracked working tree' } else { 'HEAD' }
    Write-Host "Uploading $sourceLabel version $expectedVersion to $Target..."
    & scp -- $archive "${Target}:$remoteArchive"
    if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }

    $remoteCommand = @"
set -eu
trap 'rm -rf "$remoteRoot" "$remoteArchive"' EXIT
mkdir -p "$remoteRoot"
tar -xzf "$remoteArchive" -C "$remoteRoot"
find "$remoteRoot/deploy" -type f -exec sed -i 's/\\r$//' {} +
sh "$remoteRoot/deploy/install.sh"
systemctl restart $services
systemctl is-active $services
version=`$(PYTHONPATH=/opt/immich-album-butler python3 -c 'import immich_album_butler; print(immich_album_butler.__version__)')
test "`$version" = "$expectedVersion"
printf 'Verified immich-album-butler %s\\n' "`$version"
"@
    & ssh $Target $remoteCommand
    if ($LASTEXITCODE -ne 0) { throw 'Remote installation or verification failed.' }
    Write-Host "Deployment complete: $expectedVersion verified."
} finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
