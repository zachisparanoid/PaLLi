param(
    [switch]$ValidateOnly,
    [switch]$KeepAlternatesFiles
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    return Split-Path -Parent $PSScriptRoot
}

function Assert-WithinPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $resolvedRoot = [System.IO.Path]::GetFullPath($Root).TrimEnd('\')
    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    if (-not ($resolvedPath -eq $resolvedRoot -or $resolvedPath.StartsWith("$resolvedRoot\"))) {
        throw "Path '$resolvedPath' escaped root '$resolvedRoot'."
    }
}

function Get-ObjectPrefixDirectories {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ObjectsRoot
    )

    return Get-ChildItem -LiteralPath $ObjectsRoot -Directory |
        Where-Object { $_.Name -match '^[0-9a-f]{2}$' } |
        Sort-Object Name
}

function Test-ObjectPrefixWritable {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DirectoryPath
    )

    $probePath = Join-Path $DirectoryPath "codex_write_probe.tmp"
    try {
        [System.IO.File]::WriteAllText($probePath, "ok")
        return $true
    } catch {
        return $false
    } finally {
        Remove-Item -LiteralPath $probePath -Force -ErrorAction SilentlyContinue
    }
}

function Get-UnwritableObjectPrefixes {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ObjectsRoot
    )

    $failed = New-Object System.Collections.Generic.List[string]
    foreach ($dir in Get-ObjectPrefixDirectories -ObjectsRoot $ObjectsRoot) {
        if (-not (Test-ObjectPrefixWritable -DirectoryPath $dir.FullName)) {
            [void]$failed.Add($dir.Name)
        }
    }
    return [string[]]$failed.ToArray()
}

function Repair-ObjectPrefixDirectories {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$ObjectsRoot,
        [Parameter(Mandatory = $true)]
        [string[]]$Prefixes
    )

    if (-not $Prefixes.Count) {
        return $null
    }

    $backupRoot = Join-Path $RepoRoot (".git\objects_repair_backup_{0}" -f [DateTime]::Now.ToString("yyyyMMdd_HHmmss"))
    Assert-WithinPath -Path $backupRoot -Root $RepoRoot
    New-Item -ItemType Directory -Path $backupRoot | Out-Null

    foreach ($prefix in $Prefixes) {
        $source = Join-Path $ObjectsRoot $prefix
        $backupDir = Join-Path $backupRoot $prefix
        Assert-WithinPath -Path $source -Root $ObjectsRoot
        Assert-WithinPath -Path $backupDir -Root $backupRoot

        if (-not (Test-Path -LiteralPath $source)) {
            throw "Object prefix directory '$source' does not exist."
        }

        Move-Item -LiteralPath $source -Destination $backupDir
        New-Item -ItemType Directory -Path $source | Out-Null

        Get-ChildItem -LiteralPath $backupDir -Force | Move-Item -Destination $source
        Remove-Item -LiteralPath $backupDir -Force
    }

    $remaining = Get-ChildItem -LiteralPath $backupRoot -Force -ErrorAction SilentlyContinue
    if (-not $remaining) {
        Remove-Item -LiteralPath $backupRoot -Force
        return $null
    }

    return $backupRoot
}

function Copy-AlternateObjects {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string]$ObjectsRoot,
        [Parameter(Mandatory = $true)]
        [string]$AlternateRoot
    )

    if (-not (Test-Path -LiteralPath $AlternateRoot)) {
        return 0
    }

    $copied = 0
    foreach ($dir in Get-ObjectPrefixDirectories -ObjectsRoot $AlternateRoot) {
        $targetDir = Join-Path $ObjectsRoot $dir.Name
        Assert-WithinPath -Path $targetDir -Root $ObjectsRoot
        if (-not (Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir | Out-Null
        }

        foreach ($file in Get-ChildItem -LiteralPath $dir.FullName -File) {
            $targetPath = Join-Path $targetDir $file.Name
            if (-not (Test-Path -LiteralPath $targetPath)) {
                Copy-Item -LiteralPath $file.FullName -Destination $targetPath
                $copied += 1
            }
        }
    }

    return $copied
}

function Disable-AlternatesFiles {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot,
        [Parameter(Mandatory = $true)]
        [string[]]$Files
    )

    $disabled = 0
    foreach ($file in $Files) {
        Assert-WithinPath -Path $file -Root $RepoRoot
        if (-not (Test-Path -LiteralPath $file)) {
            continue
        }

        $disabledPath = "$file.disabled"
        if (Test-Path -LiteralPath $disabledPath) {
            continue
        }

        Rename-Item -LiteralPath $file -NewName ([System.IO.Path]::GetFileName($disabledPath))
        $disabled += 1
    }

    return $disabled
}

$repoRoot = Get-RepoRoot
$objectsRoot = Join-Path $repoRoot ".git\objects"
$alternateRoot = Join-Path $repoRoot ".git_alt_objects"
$alternateFiles = @(
    (Join-Path $repoRoot ".git\objects\info\alternates"),
    (Join-Path $repoRoot ".git_alt_objects\info\alternates")
)

Assert-WithinPath -Path $objectsRoot -Root $repoRoot
Assert-WithinPath -Path $alternateRoot -Root $repoRoot

if (-not (Test-Path -LiteralPath $objectsRoot)) {
    throw "Object store '$objectsRoot' was not found."
}

$failedPrefixes = @(Get-UnwritableObjectPrefixes -ObjectsRoot $objectsRoot)

Write-Host ("Repo root: {0}" -f $repoRoot)
Write-Host ("Objects root: {0}" -f $objectsRoot)
Write-Host ("Unwritable object prefixes: {0}" -f $failedPrefixes.Count)
if ($failedPrefixes.Count) {
    Write-Host ("Bad prefixes: {0}" -f ($failedPrefixes -join ", "))
}

if ($ValidateOnly) {
    if ($failedPrefixes.Count) {
        exit 1
    }
    Write-Host "Object store is writable."
    exit 0
}

$backupPath = Repair-ObjectPrefixDirectories -RepoRoot $repoRoot -ObjectsRoot $objectsRoot -Prefixes $failedPrefixes
$remainingBadPrefixes = @(Get-UnwritableObjectPrefixes -ObjectsRoot $objectsRoot)
if ($remainingBadPrefixes.Count) {
    throw "Repair did not restore write access for: $($remainingBadPrefixes -join ', ')"
}

$copiedObjects = Copy-AlternateObjects -RepoRoot $repoRoot -ObjectsRoot $objectsRoot -AlternateRoot $alternateRoot
$disabledAlternates = 0
if (-not $KeepAlternatesFiles) {
    $disabledAlternates = Disable-AlternatesFiles -RepoRoot $repoRoot -Files $alternateFiles
}

Write-Host ("Repair complete. Repaired prefixes: {0}" -f $failedPrefixes.Count)
if ($backupPath) {
    Write-Host ("Backup root: {0}" -f $backupPath)
}
Write-Host ("Copied alternate-only objects: {0}" -f $copiedObjects)
if (-not $KeepAlternatesFiles) {
    Write-Host ("Disabled alternates files: {0}" -f $disabledAlternates)
}
