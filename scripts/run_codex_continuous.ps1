param(
    [string]$Goal = "Continue PaLLi Local toward the end vision in PLANS.md.",
    [string]$BranchName = "",
    [string]$PhaseHint = "",
    [int]$Iterations = 1,
    [string]$Model = "",
    [switch]$Fast,
    [string]$FastModel = "gpt-5.4-mini",
    [ValidateSet("low", "medium", "high", "xhigh")]
    [string]$ReasoningEffort = "xhigh",
    [bool]$AutoAdvancePhase = $true,
    [string]$Profile = "",
    [ValidateSet("full-auto", "danger")]
    [string]$ExecutionMode = "full-auto",
    [ValidateSet("native", "json")]
    [string]$OutputMode = "native",
    [string]$StopFilePath = "",
    [switch]$CreateBranch,
    [switch]$ResumeLast,
    [switch]$DryRun
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-SessionPhase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SessionStatePath
    )

    if (-not (Test-Path $SessionStatePath)) {
        return ""
    }

    $inCurrentPhaseSection = $false
    foreach ($line in Get-Content -LiteralPath $SessionStatePath) {
        if ($line -match '^\s*##\s+Current Phase') {
            $inCurrentPhaseSection = $true
            continue
        }

        if ($inCurrentPhaseSection) {
            if ($line -match '^\s*##\s+') {
                break
            }

            if ($line -match '^\s*-\s*`([^`]+)`\s*$') {
                return $matches[1].Trim()
            }

            if ($line -match '^\s*-\s*(.+)\s*$') {
                return $matches[1].Trim("`r","`n"," ")
            }
        }
    }

    return ""
}

function Get-MarkdownSectionText {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Header
    )

    if (-not (Test-Path $Path)) {
        return ""
    }

    $capture = $false
    $collected = New-Object System.Collections.Generic.List[string]
    $headerPattern = '^\s*##\s+' + [regex]::Escape($Header) + '\s*$'

    foreach ($line in Get-Content -LiteralPath $Path) {
        if (-not $capture) {
            if ($line -match $headerPattern) {
                $capture = $true
            }
            continue
        }

        if ($line -match '^\s*##\s+') {
            break
        }

        $collected.Add($line)
    }

    return (($collected -join [Environment]::NewLine).Trim())
}

function Get-PlanPhases {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PlansPath
    )

    if (-not (Test-Path $PlansPath)) {
        return @()
    }

    $phases = New-Object System.Collections.Generic.List[pscustomobject]
    foreach ($line in Get-Content -LiteralPath $PlansPath) {
        if ($line -match '^\s*###\s*Phase\s+(\d+)\s*:\s*(.+)$') {
            $phases.Add([pscustomobject]@{
                Number = [int]$matches[1]
                Title = $matches[2].Trim()
            })
        }
    }
    return $phases.ToArray()
}

function Get-StopRequest {
    param(
        [string]$StopFilePath
    )

    if ([string]::IsNullOrWhiteSpace($StopFilePath)) {
        return $null
    }

    if (-not (Test-Path -LiteralPath $StopFilePath)) {
        return $null
    }

    $reason = ""
    try {
        $reason = (Get-Content -LiteralPath $StopFilePath -Raw -ErrorAction Stop).Trim()
    } catch {
        $reason = ""
    }

    return [pscustomobject]@{
        Path = $StopFilePath
        Reason = $reason
    }
}

function Get-PhaseNumber {
    param(
        [string]$PhaseName
    )

    if ([string]::IsNullOrWhiteSpace($PhaseName)) {
        return -1
    }

    if ($PhaseName -match 'Phase\s+(\d+)') {
        return [int]$matches[1]
    }
    return -1
}

function Get-PhaseNameByNumber {
    param(
        [array]$PlanPhases,
        [int]$PhaseNumber
    )

    $phase = $PlanPhases | Where-Object { $_.Number -eq $PhaseNumber } | Select-Object -First 1
    if ($null -eq $phase) {
        return "Phase $PhaseNumber"
    }

    return "Phase ${PhaseNumber}: $($phase.Title)"
}

function Resolve-NextPhase {
    param(
        [string]$CurrentPhase,
        [array]$PlanPhases
    )

    $currentPhaseNumber = Get-PhaseNumber -PhaseName $CurrentPhase
    if ($currentPhaseNumber -lt 0) {
        return $CurrentPhase
    }

    $nextPhaseNumber = $currentPhaseNumber + 1
    $known = $PlanPhases | Where-Object { $_.Number -eq $nextPhaseNumber } | Select-Object -First 1
    if ($null -eq $known) {
        return $CurrentPhase
    }

    return Get-PhaseNameByNumber -PlanPhases $PlanPhases -PhaseNumber $nextPhaseNumber
}

function Set-SessionPhase {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SessionStatePath,
        [Parameter(Mandatory = $true)]
        [string]$PhaseName
    )

    if (-not (Test-Path $SessionStatePath)) {
        return
    }

    $lines = Get-Content -LiteralPath $SessionStatePath
    $updatedLines = New-Object System.Collections.Generic.List[string]
    $inCurrentPhaseSection = $false
    $phaseLineWritten = $false

    foreach ($line in $lines) {
        if ($line -match '^\s*##\s+Current Phase') {
            $inCurrentPhaseSection = $true
            $updatedLines.Add($line)
            continue
        }

        if ($inCurrentPhaseSection) {
            if ($line -match '^\s*##\s+') {
                if (-not $phaseLineWritten) {
                    $updatedLines.Add("- `"$PhaseName`"")
                    $phaseLineWritten = $true
                }
                $inCurrentPhaseSection = $false
                $updatedLines.Add($line)
                continue
            }

            if (-not $phaseLineWritten -and $line -match '^\s*-\s*') {
                $updatedLines.Add("- `"$PhaseName`"")
                $phaseLineWritten = $true
                continue
            }

            if (-not $phaseLineWritten -and [string]::IsNullOrWhiteSpace($line)) {
                $updatedLines.Add($line)
                continue
            }

            if (-not $phaseLineWritten) {
                $updatedLines.Add("- `"$PhaseName`"")
                $phaseLineWritten = $true
            }

            continue
        }

        $updatedLines.Add($line)
    }

    if ($inCurrentPhaseSection -and -not $phaseLineWritten) {
        $updatedLines.Add("- `"$PhaseName`"")
    }

    if (-not $phaseLineWritten) {
        $updatedLines.Add("")
        $updatedLines.Add("## Current Phase")
        $updatedLines.Add("- `"$PhaseName`"")
    }

    Set-Content -LiteralPath $SessionStatePath -Value ($updatedLines -join [Environment]::NewLine)
}

function Get-CommitsInRange {
    param(
        [Parameter(Mandatory = $true)]
        [string]$From,
        [Parameter(Mandatory = $true)]
        [string]$To
    )

    if ([string]::IsNullOrWhiteSpace($From) -or [string]::IsNullOrWhiteSpace($To)) {
        return @()
    }

    if ($From -eq $To) {
        return @()
    }

    $rangeOutput = & git log --no-merges --pretty=format:%s "$From..$To" 2>&1
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    if ([string]::IsNullOrWhiteSpace(($rangeOutput | Out-String))) {
        return @()
    }

    if ($rangeOutput -is [string]) {
        return $rangeOutput -split "`r?`n"
    }
    return @($rangeOutput)
}

function Resolve-PhaseAdvanceFromSignals {
    param(
        [string]$CurrentPhase,
        [string[]]$CommitMessages,
        [string]$LastMessageText,
        [array]$PlanPhases
    )

    $currentPhaseNumber = Get-PhaseNumber -PhaseName $CurrentPhase
    if ($currentPhaseNumber -lt 0) {
        return $null
    }

    $signals = @()
    if ($CommitMessages) {
        $signals += $CommitMessages
    }
    if (-not [string]::IsNullOrWhiteSpace($LastMessageText)) {
        $signals += ($LastMessageText -split "`r?`n")
    }

    $nextPhase = Resolve-NextPhase -CurrentPhase $CurrentPhase -PlanPhases $PlanPhases
    $nextPhaseNumber = Get-PhaseNumber -PhaseName $nextPhase

    foreach ($signal in $signals) {
        if ([string]::IsNullOrWhiteSpace($signal)) {
            continue
        }

        if ($signal -match '(?i)\b(?:phase\s*transition|phase\s*advance)\s*:\s*phase\s+(\d+)\s*(?:->|to)\s*phase\s+(\d+)') {
            $fromPhase = [int]$matches[1]
            $toPhase = [int]$matches[2]
            if ($fromPhase -eq $currentPhaseNumber -and $toPhase -gt $currentPhaseNumber) {
                return [pscustomobject]@{
                    TargetPhase = Get-PhaseNameByNumber -PlanPhases $PlanPhases -PhaseNumber $toPhase
                    Reason = "explicit transition signal ($signal)"
                }
            }
        }

        if ($signal -match '(?i)\[phase\s*completion\s*:\s*phase\s+(\d+)\]') {
            $completePhase = [int]$matches[1]
            if ($completePhase -eq $currentPhaseNumber -and $nextPhaseNumber -gt $currentPhaseNumber) {
                return [pscustomobject]@{
                    TargetPhase = $nextPhase
                    Reason = "explicit phase-completion signal ($signal)"
                }
            }
        }
    }

    return $null
}

function Get-RecentCommitSubjects {
    param(
        [int]$Count = 8
    )

    if ($Count -lt 1) {
        return @()
    }

    $output = & git log --no-merges --pretty=format:%s -n $Count 2>&1
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    $text = ($output | Out-String).Trim()
    if ([string]::IsNullOrWhiteSpace($text)) {
        return @()
    }

    return @($text -split "`r?`n" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Get-RecentCodeHotspots {
    param(
        [int]$CommitCount = 8,
        [int]$Top = 5
    )

    if ($CommitCount -lt 1 -or $Top -lt 1) {
        return @()
    }

    $output = & git log --no-merges --name-only --pretty=format: -n $CommitCount 2>&1
    if ($LASTEXITCODE -ne 0) {
        return @()
    }

    $paths = @()
    foreach ($line in ($output | Out-String) -split "`r?`n") {
        $path = [string]$line
        if ([string]::IsNullOrWhiteSpace($path)) {
            continue
        }
        if ($path -match '(^|[\\/])logs([\\/]|$)') {
            continue
        }
        if ($path -match '(^|[\\/])\.tmp([\\/]|$)') {
            continue
        }
        if ($path -match '\.md$') {
            continue
        }
        if ($path -eq "PLANS.md" -or $path -eq "SESSION_STATE.md") {
            continue
        }
        $paths += $path.Trim()
    }

    if (-not $paths) {
        return @()
    }

    $grouped = $paths | Group-Object | Sort-Object -Property Count -Descending
    return @(
        $grouped |
            Select-Object -First $Top |
            ForEach-Object {
                [pscustomobject]@{
                    Path = $_.Name
                    Count = [int]$_.Count
                }
            }
    )
}

function Get-FocusLoopRisk {
    param(
        [string[]]$RecentCommitSubjects,
        [object[]]$RecentCodeHotspots
    )

    $scopeCounts = @{}
    foreach ($subject in $RecentCommitSubjects) {
        if ([string]::IsNullOrWhiteSpace($subject)) {
            continue
        }

        if ($subject -match '^[^(]+\(([^)]+)\):') {
            $scope = $matches[1].Trim().ToLowerInvariant()
            if (-not [string]::IsNullOrWhiteSpace($scope)) {
                if (-not $scopeCounts.ContainsKey($scope)) {
                    $scopeCounts[$scope] = 0
                }
                $scopeCounts[$scope] += 1
            }
        }
    }

    $dominantScope = $null
    foreach ($entry in $scopeCounts.GetEnumerator() | Sort-Object -Property Value -Descending) {
        $dominantScope = $entry
        break
    }

    if ($null -ne $dominantScope -and [int]$dominantScope.Value -ge 4) {
        return [pscustomobject]@{
            Detected = $true
            Reason = ("recent commit scope concentration on '{0}' ({1} of last {2} commits)" -f $dominantScope.Key, $dominantScope.Value, $RecentCommitSubjects.Count)
        }
    }

    $dominantHotspot = $null
    foreach ($hotspot in $RecentCodeHotspots | Sort-Object -Property Count -Descending) {
        $dominantHotspot = $hotspot
        break
    }

    if ($null -ne $dominantHotspot -and [int]$dominantHotspot.Count -ge 3) {
        return [pscustomobject]@{
            Detected = $true
            Reason = ("recent code churn concentration on '{0}' ({1} touches in recent commits)" -f $dominantHotspot.Path, $dominantHotspot.Count)
        }
    }

    return [pscustomobject]@{
        Detected = $false
        Reason = ""
    }
}

function Get-RepoRoot {
    return Split-Path -Parent $PSScriptRoot
}

function Get-GitOutput {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        $message = ($output | Out-String).Trim()
        throw "git $($Arguments -join ' ') failed: $message"
    }
    return ($output | Out-String).Trim()
}

function Test-LocalBranchExists {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    & git rev-parse --verify --quiet "refs/heads/$Name" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Ensure-Branch {
    param(
        [string]$TargetBranch,
        [switch]$AllowCreate
    )

    $currentBranch = Get-GitOutput -Arguments @("branch", "--show-current")
    if ([string]::IsNullOrWhiteSpace($currentBranch)) {
        throw "Detached HEAD is not supported for continuous Codex runs. Switch to a branch first."
    }

    if ([string]::IsNullOrWhiteSpace($TargetBranch)) {
        return $currentBranch
    }

    if ($currentBranch -eq $TargetBranch) {
        return $currentBranch
    }

    if (Test-LocalBranchExists -Name $TargetBranch) {
        & git switch $TargetBranch
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to switch to branch '$TargetBranch'."
        }
        return $TargetBranch
    }

    if (-not $AllowCreate) {
        throw "Branch '$TargetBranch' does not exist locally. Re-run with -CreateBranch to create it."
    }

    & git switch -c $TargetBranch
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create branch '$TargetBranch'."
    }
    return $TargetBranch
}

function New-IndentedBlock {
    param(
        [string]$Text,
        [string]$Prefix = "  "
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        $clean = "(clean)"
    } else {
        $clean = $Text.TrimEnd()
    }
    $lines = $clean -split "`r?`n"
    return (($lines | ForEach-Object { "$Prefix$_" }) -join [Environment]::NewLine)
}

function Build-CodexPrompt {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CurrentBranch,
        [Parameter(Mandatory = $true)]
        [string]$HeadCommit,
        [Parameter(Mandatory = $true)]
        [string]$GitStatus,
        [Parameter(Mandatory = $true)]
        [string]$RunGoal,
        [string]$CurrentPhase,
        [string]$SessionCurrentFocus = "",
        [string]$SessionLastCheckpoint = "",
        [string]$SessionValidation = "",
        [string]$SessionNextTask = "",
        [string]$SessionKnownBlockers = "",
        [string[]]$RecentCommitSubjects = @(),
        [object[]]$RecentCodeHotspots = @(),
        [string]$LoopRiskReason = ""
    )

    $phaseLine = ""
    if (-not [string]::IsNullOrWhiteSpace($CurrentPhase)) {
        $phaseLine = "- Phase hint: $CurrentPhase`n"
    }

    $statusBlock = New-IndentedBlock -Text $GitStatus -Prefix "  "
    $focusBlock = New-IndentedBlock -Text $SessionCurrentFocus -Prefix "  "
    $checkpointBlock = New-IndentedBlock -Text $SessionLastCheckpoint -Prefix "  "
    $validationBlock = New-IndentedBlock -Text $SessionValidation -Prefix "  "
    $nextTaskBlock = New-IndentedBlock -Text $SessionNextTask -Prefix "  "
    $blockersBlock = New-IndentedBlock -Text $SessionKnownBlockers -Prefix "  "

    $recentCommitText = if ($RecentCommitSubjects -and $RecentCommitSubjects.Count -gt 0) {
        (($RecentCommitSubjects | ForEach-Object { "- $_" }) -join [Environment]::NewLine)
    } else {
        "(none)"
    }
    $recentCommitBlock = New-IndentedBlock -Text $recentCommitText -Prefix "  "

    $recentHotspotText = if ($RecentCodeHotspots -and $RecentCodeHotspots.Count -gt 0) {
        (($RecentCodeHotspots | ForEach-Object { "- $($_.Path) ($($_.Count) recent touches)" }) -join [Environment]::NewLine)
    } else {
        "(none)"
    }
    $recentHotspotBlock = New-IndentedBlock -Text $recentHotspotText -Prefix "  "

    $loopRiskLine = ""
    if (-not [string]::IsNullOrWhiteSpace($LoopRiskReason)) {
        $loopRiskLine = "- Loop-risk advisory: $LoopRiskReason. Do not spend this run on the same seam unless it is still the highest-leverage unblocked task and you can produce materially new tested progress there.`n"
    }

    return @"
Read these files first and treat them as the source of truth for this repo:

1. AGENTS.md
2. PLANS.md
3. docs/strategy/product-north-star.md
4. docs/architecture/identity-architecture-foundation.md
5. docs/strategy/openclaw-parity-positioning.md
6. docs/strategy/openclaw-inner-workflow-lessons-for-palli.md
7. docs/strategy/palli-origin-vision-pivot-and-end-goal.md
8. docs/strategy/palli-reliability-parity-playbook.md
9. docs/reference/openai-realtime-voice-engine-notes.md
10. docs/reference/voice-model-routing-current-state.md
11. docs/roadmaps/palli-parity-execution-roadmap.md
12. SESSION_STATE.md

Execution instructions:

- Treat PLANS.md as the active execution roadmap.
- Treat SESSION_STATE.md as the live handoff for the current branch and update it before you finish.
- Start from the highest-priority unfinished work implied by PLANS.md and SESSION_STATE.md.
- Prefer direct implementation over extended discussion.
- Make code changes directly, run focused tests, and verify behavior where practical.
- Commit in small, logical checkpoints as you complete meaningful chunks.
- Update PLANS.md when a phase materially advances, sequencing changes, or important new constraints are discovered.
- Use the configured reasoning effort for this run and optimize for substantive progress, not shallow speed.
- If you complete a phase, commit that checkpoint and include one explicit harness signal:
  - `[phase completion: phase N]` for standard next-phase progression
  - `phase transition: phase N -> phase M` for explicit jumps
- Preserve PaLLi Local's product center: local-first, single-owner, companion/operator, chat-and-voice primary, dashboard as mission control.
- Do not drift toward generic platform work or hosted SaaS assumptions unless clearly required by the current phase.
- Do not commit logs, .env secrets, databases, or owner-specific runtime state.
- Favor additive integration and decomposition over destructive rewrites.
- Prioritize reliability, trust, inspectability, continuity, and cross-surface coherence over feature sprawl.
- Use SESSION_STATE.md to decide the next highest-leverage unblocked task instead of continuing the previous seam by inertia.
- If the current target is blocked or only offers cleanup churn, pivot within the current phase to the next unblocked task rather than spending another pass in the same module.
- Each run should end in one of two states:
  - a tested code checkpoint with a commit
  - a clearly recorded blocker plus an updated next task in SESSION_STATE.md
- Before you finish, update SESSION_STATE.md with branch, current phase, last checkpoint, tests run, next task, and blockers.
$(if ($AutoAdvancePhase) { "- AUTO-Phase mode: SESSION_STATE.md will only auto-advance when an explicit phase signal is present and this run creates at least one new commit." } else { "" })
$loopRiskLine

Run context:
- Branch: $CurrentBranch
- HEAD: $HeadCommit
$phaseLine- Goal: $RunGoal
- Reasoning effort: $ReasoningEffort
- Service tier: $(if ($Fast) { "fast" } else { "default/profile" })
- Git status:
$statusBlock

SESSION_STATE handoff summary:
- Current focus:
$focusBlock
- Last completed checkpoint:
$checkpointBlock
- Last validation:
$validationBlock
- Next highest-leverage task:
$nextTaskBlock
- Known blockers:
$blockersBlock

Recent progress context:
- Recent commit subjects:
$recentCommitBlock
- Recent code hotspots:
$recentHotspotBlock

Task for this run:
- Choose the next highest-leverage unblocked task from the handoff summary and execute it.
- Continue from the current checkpoint and keep moving until blocked or until you complete a substantial, well-tested chunk.
- If the worktree is already dirty when you start, understand and preserve the existing changes instead of reverting them.
- If you complete a meaningful checkpoint, create a commit before you finish.
- Prefer a real feature/fix/extraction/test checkpoint over another naming-only or typing-only cleanup pass in the same hotspot.
- If you must stay in the same hotspot, be explicit about the unresolved seam you are closing and add or update focused tests that prove the new progress.
"@
}

function Resolve-RequestedModel {
    param(
        [string]$ExplicitModel,
        [switch]$UseFastMode,
        [string]$ConfiguredFastModel
    )

    if (-not [string]::IsNullOrWhiteSpace($ExplicitModel)) {
        return $ExplicitModel.Trim()
    }

    if ($UseFastMode -and -not [string]::IsNullOrWhiteSpace($ConfiguredFastModel)) {
        return $ConfiguredFastModel.Trim()
    }

    return ""
}

function Write-CodexWrappedText {
    param(
        [string]$Text,
        [string]$Prefix = ""
    )

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return
    }

    $normalized = $Text.Trim() -split "`r?`n"
    foreach ($line in $normalized) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            Write-Host ""
        } else {
            Write-Host ($Prefix + $line)
        }
    }
}

function Write-CodexTodoList {
    param(
        [object[]]$Items
    )

    if (-not $Items) {
        return
    }

    foreach ($entry in $Items) {
        $done = [bool]$entry.completed
        $marker = if ($done) { "[x]" } else { "[ ]" }
        $text = [string]$entry.text
        if (-not [string]::IsNullOrWhiteSpace($text)) {
            Write-Host ("    {0} {1}" -f $marker, $text.Trim())
        }
    }
}

function Write-CodexEventSummary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Line
    )

    $trimmed = $Line.Trim()
    if ([string]::IsNullOrWhiteSpace($trimmed)) {
        return
    }

    try {
        $event = $trimmed | ConvertFrom-Json -Depth 100
    } catch {
        Write-Host $trimmed
        return
    }

    $eventType = [string]$event.type
    switch ($eventType) {
        "thread.started" {
            if (-not [string]::IsNullOrWhiteSpace([string]$event.thread_id)) {
                Write-Host ("[thread] {0}" -f [string]$event.thread_id)
            }
            return
        }
        "turn.started" {
            Write-Host "[turn] started"
            return
        }
        "turn.completed" {
            Write-Host "[turn] completed"
            return
        }
        "turn.failed" {
            Write-Host "[turn] failed"
            return
        }
    }

    $item = $event.item
    if ($null -eq $item) {
        return
    }

    $itemType = [string]$item.type
    $status = [string]$item.status

    switch ($itemType) {
        "agent_message" {
            Write-CodexWrappedText -Text ([string]$item.text)
            return
        }
        "todo_list" {
            Write-Host "[todo]"
            Write-CodexTodoList -Items $item.items
            return
        }
        "command_execution" {
            $commandText = [string]$item.command
            if ($eventType -eq "item.started") {
                if (-not [string]::IsNullOrWhiteSpace($commandText)) {
                    Write-Host ("[cmd] {0}" -f $commandText)
                }
                return
            }

            $exitCode = $item.exit_code
            if (-not [string]::IsNullOrWhiteSpace($commandText)) {
                if ($null -ne $exitCode) {
                    Write-Host ("[cmd:done] exit={0} {1}" -f $exitCode, $commandText)
                } elseif (-not [string]::IsNullOrWhiteSpace($status)) {
                    Write-Host ("[cmd:{0}] {1}" -f $status, $commandText)
                } else {
                    Write-Host ("[cmd:done] {0}" -f $commandText)
                }
            }
            return
        }
    }
}

$repoRoot = Get-RepoRoot
Set-Location $repoRoot

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw "The 'codex' CLI is not installed or is not on PATH."
}

$sessionStatePath = Join-Path $repoRoot "SESSION_STATE.md"
if (-not (Test-Path $sessionStatePath)) {
    throw "SESSION_STATE.md is missing. Create it before running the harness."
}

$executionModeRequested = if ($PSBoundParameters.ContainsKey("ExecutionMode")) { $ExecutionMode } else { "full-auto" }
$effectiveExecutionMode = "danger"

if ($Iterations -lt 1) {
    throw "-Iterations must be at least 1."
}

$plansPath = Join-Path $repoRoot "PLANS.md"
$plansPhases = Get-PlanPhases -PlansPath $plansPath
$activeBranch = Ensure-Branch -TargetBranch $BranchName -AllowCreate:$CreateBranch
$runsRoot = Join-Path $repoRoot "logs\codex-runs"
New-Item -ItemType Directory -Force -Path $runsRoot | Out-Null
$resolvedStopFilePath = if ([string]::IsNullOrWhiteSpace($StopFilePath)) {
    Join-Path $runsRoot "stop-after-current.flag"
} else {
    $executionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StopFilePath)
}
$requestedModel = Resolve-RequestedModel -ExplicitModel $Model -UseFastMode:$Fast -ConfiguredFastModel $FastModel

for ($index = 1; $index -le $Iterations; $index++) {
    $runStamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
    $runNonce = ([guid]::NewGuid().ToString("N")).Substring(0, 8)
    $runId = "$runStamp-$index-$runNonce"
    $runDir = Join-Path $runsRoot $runId
    New-Item -ItemType Directory -Force -Path $runDir | Out-Null

    $headBefore = Get-GitOutput -Arguments @("rev-parse", "HEAD")
    $statusBefore = Get-GitOutput -Arguments @("status", "--short", "--branch", "--untracked-files=no")
    $sessionPhase = Get-SessionPhase -SessionStatePath $sessionStatePath
    $sessionCurrentFocus = Get-MarkdownSectionText -Path $sessionStatePath -Header "Current Focus"
    $sessionLastCheckpoint = Get-MarkdownSectionText -Path $sessionStatePath -Header "Last Completed Checkpoint"
    $sessionValidation = Get-MarkdownSectionText -Path $sessionStatePath -Header "Validation"
    $sessionNextTask = Get-MarkdownSectionText -Path $sessionStatePath -Header "Next Highest-Leverage Task"
    $sessionKnownBlockers = Get-MarkdownSectionText -Path $sessionStatePath -Header "Known Blockers"
    $recentCommitSubjects = Get-RecentCommitSubjects -Count 8
    $recentCodeHotspots = Get-RecentCodeHotspots -CommitCount 8 -Top 5
    $loopRisk = Get-FocusLoopRisk -RecentCommitSubjects $recentCommitSubjects -RecentCodeHotspots $recentCodeHotspots
    $resolvedPhase = if ([string]::IsNullOrWhiteSpace($PhaseHint)) { $sessionPhase } else { $PhaseHint }
    $promptText = Build-CodexPrompt `
        -CurrentBranch $activeBranch `
        -HeadCommit $headBefore `
        -GitStatus $statusBefore `
        -RunGoal $Goal `
        -CurrentPhase $resolvedPhase `
        -SessionCurrentFocus $sessionCurrentFocus `
        -SessionLastCheckpoint $sessionLastCheckpoint `
        -SessionValidation $sessionValidation `
        -SessionNextTask $sessionNextTask `
        -SessionKnownBlockers $sessionKnownBlockers `
        -RecentCommitSubjects $recentCommitSubjects `
        -RecentCodeHotspots $recentCodeHotspots `
        -LoopRiskReason $loopRisk.Reason

    $promptPath = Join-Path $runDir "prompt.txt"
    $jsonlPath = Join-Path $runDir "events.jsonl"
    $consoleLogPath = Join-Path $runDir "console-output.txt"
    $lastMessagePath = Join-Path $runDir "last-message.txt"
    $metaPath = Join-Path $runDir "meta.json"

    Set-Content -LiteralPath $promptPath -Value $promptText -Encoding utf8

    $commandArgs = @()
    if ($ResumeLast) {
        $commandArgs += @("exec", "resume", "--last")
    } else {
        $commandArgs += @("exec")
    }
    if ($OutputMode -eq "json") {
        $commandArgs += @("--json", "--color", "never")
    }
    $commandArgs += @("-o", $lastMessagePath)

    if (-not [string]::IsNullOrWhiteSpace($requestedModel)) {
        $commandArgs += @("--model", $requestedModel)
    }
    if (-not [string]::IsNullOrWhiteSpace($Profile)) {
        $commandArgs += @("--profile", $Profile)
    }
    if (-not [string]::IsNullOrWhiteSpace($ReasoningEffort)) {
        $commandArgs += @("-c", "model_reasoning_effort=`"$ReasoningEffort`"")
    }
    if ($Fast) {
        $commandArgs += @("-c", "service_tier=`"fast`"")
    }

    if ($executionModeRequested -ne "full-auto") {
        Write-Host ("[{0}] execution mode requested: {1} (forcing full access for this harness)" -f $runId, $executionModeRequested)
    }
    $commandArgs += @("--dangerously-bypass-approvals-and-sandbox")

    $commandArgs += @("-")

    $startTime = Get-Date
    Write-Host ("[{0}] Starting Codex run {1}/{2} on branch {3}" -f $startTime.ToString("s"), $index, $Iterations, $activeBranch)
    Write-Host ("[{0}] Prompt: {1}" -f $runId, $promptPath)
    if ($OutputMode -eq "json") {
        Write-Host ("[{0}] JSON output: {1}" -f $runId, $jsonlPath)
    } else {
        Write-Host ("[{0}] Console log: {1}" -f $runId, $consoleLogPath)
    }
    if (-not [string]::IsNullOrWhiteSpace($requestedModel)) {
        Write-Host ("[{0}] Model: {1}" -f $runId, $requestedModel)
    } elseif ($Fast) {
        Write-Host ("[{0}] Fast mode requested, but no fast model resolved." -f $runId)
    }
    if ($Fast) {
        Write-Host ("[{0}] Fast mode: enabled" -f $runId)
    }
    if (-not [string]::IsNullOrWhiteSpace($ReasoningEffort)) {
        Write-Host ("[{0}] Reasoning effort: {1}" -f $runId, $ReasoningEffort)
    }
    if ($Fast) {
        Write-Host ("[{0}] Service tier: fast" -f $runId)
    }
    Write-Host ("[{0}] Graceful stop file: {1}" -f $runId, $resolvedStopFilePath)

    if (-not $DryRun) {
        $promptInput = Get-Content -LiteralPath $promptPath -Raw
        if ($OutputMode -eq "json") {
            $jsonWriter = [System.IO.StreamWriter]::new($jsonlPath, $false, [System.Text.UTF8Encoding]::new($false))
            try {
                $promptInput | & codex @commandArgs | ForEach-Object {
                    $line = [string]$_
                    $jsonWriter.WriteLine($line)
                    $jsonWriter.Flush()
                    Write-CodexEventSummary -Line $line
                }
                $runExitCode = $LASTEXITCODE
            } finally {
                $jsonWriter.Dispose()
            }
        } else {
            Start-Transcript -Path $consoleLogPath -Force | Out-Null
            try {
                $promptInput | & codex @commandArgs
                $runExitCode = $LASTEXITCODE
            } finally {
                Stop-Transcript | Out-Null
            }
        }
    } else {
        $runExitCode = 0
        "DRY RUN: codex $($commandArgs -join ' ')" | Set-Content -LiteralPath $jsonlPath -Encoding utf8
    }

    $endTime = Get-Date
    $headAfter = Get-GitOutput -Arguments @("rev-parse", "HEAD")
    $statusAfter = Get-GitOutput -Arguments @("status", "--short", "--branch", "--untracked-files=no")

    $metaPhaseBefore = $resolvedPhase
    $phaseAdvance = $null
    $phaseAdvanceBlockedReason = ""
    $newCommitMessages = @()
    if ($AutoAdvancePhase) {
        $newCommitMessages = @(Get-CommitsInRange -From $headBefore -To $headAfter)
        $lastMessageText = ""
        if (Test-Path $lastMessagePath) {
            $lastMessageText = Get-Content -LiteralPath $lastMessagePath -Raw
        }

        $phaseAdvanceCandidate = Resolve-PhaseAdvanceFromSignals -CurrentPhase $resolvedPhase -CommitMessages $newCommitMessages -LastMessageText $lastMessageText -PlanPhases $plansPhases
        if ($null -ne $phaseAdvanceCandidate) {
            if ($newCommitMessages.Count -lt 1) {
                $phaseAdvanceBlockedReason = "explicit phase signal detected but no new commit was created in this run"
                Write-Host ("[{0}] Phase signal ignored: {1}" -f $runId, $phaseAdvanceBlockedReason)
            } else {
                $phaseAdvance = $phaseAdvanceCandidate
                $targetPhase = $phaseAdvance.TargetPhase
                Set-SessionPhase -SessionStatePath $sessionStatePath -PhaseName $targetPhase
                Write-Host ("[{0}] Auto-advanced phase: {1} -> {2} ({3})" -f $runId, $resolvedPhase, $targetPhase, $phaseAdvance.Reason)
                $resolvedPhase = $targetPhase
            }
        }
    }

    $meta = [ordered]@{
        run_id = $runId
        started_at = $startTime.ToString("o")
        completed_at = $endTime.ToString("o")
        branch = $activeBranch
        head_before = $headBefore
        head_after = $headAfter
        exit_code = $runExitCode
        goal = $Goal
        phase_hint = $resolvedPhase
        session_current_focus = $sessionCurrentFocus
        session_next_task = $sessionNextTask
        session_known_blockers = $sessionKnownBlockers
        fast_mode = [bool]$Fast
        fast_model = $FastModel
        reasoning_effort = $ReasoningEffort
        service_tier = if ($Fast) { "fast" } else { "" }
        requested_model = $requestedModel
        output_mode = $OutputMode
        resume_last = [bool]$ResumeLast
        dry_run = [bool]$DryRun
        auto_phase_advance = [bool]$AutoAdvancePhase
        loop_risk_detected = [bool]$loopRisk.Detected
        loop_risk_reason = $loopRisk.Reason
        recent_commit_subjects = @($recentCommitSubjects)
        recent_code_hotspots = @($recentCodeHotspots)
        graceful_stop_file = $resolvedStopFilePath
        new_commit_count = $newCommitMessages.Count
        phase_before = $metaPhaseBefore
        phase_after = $resolvedPhase
        phase_transition_reason = if ($null -eq $phaseAdvance) { "" } else { $phaseAdvance.Reason }
        phase_transition_blocked_reason = $phaseAdvanceBlockedReason
        requested_execution_mode = $executionModeRequested
        effective_execution_mode = $effectiveExecutionMode
        execution_mode = $effectiveExecutionMode
        prompt_file = $promptPath
        jsonl_file = if ($OutputMode -eq "json") { $jsonlPath } else { "" }
        console_log_file = if ($OutputMode -eq "native") { $consoleLogPath } else { "" }
        last_message_file = $lastMessagePath
        git_status_before = $statusBefore
        git_status_after = $statusAfter
    }
    $meta | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $metaPath -Encoding utf8

    Write-Host ("[{0}] Completed with exit code {1}" -f $runId, $runExitCode)
    Write-Host ("[{0}] Last message: {1}" -f $runId, $lastMessagePath)
    Write-Host ("[{0}] Metadata: {1}" -f $runId, $metaPath)

    if ($runExitCode -ne 0) {
        throw "Codex run '$runId' failed with exit code $runExitCode."
    }

    $stopRequest = Get-StopRequest -StopFilePath $resolvedStopFilePath
    if ($null -ne $stopRequest) {
        $reasonSuffix = if ([string]::IsNullOrWhiteSpace($stopRequest.Reason)) {
            ""
        } else {
            " Reason: $($stopRequest.Reason)"
        }
        Write-Host ("[{0}] Graceful stop requested. Halting before the next iteration.{1}" -f $runId, $reasonSuffix)
        try {
            Remove-Item -LiteralPath $resolvedStopFilePath -Force -ErrorAction Stop
        } catch {
            Write-Host ("[{0}] Warning: failed to remove graceful stop file {1}" -f $runId, $resolvedStopFilePath)
        }
        break
    }
}
