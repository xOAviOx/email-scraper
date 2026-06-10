# Watches the repo and auto-commits + pushes any changes to GitHub.
# Usage:  .\auto-push.ps1
#         .\auto-push.ps1 -IntervalSeconds 15

param(
    [string]$Branch = "main",
    [int]$IntervalSeconds = 10
)

$RepoPath = $PSScriptRoot
Set-Location $RepoPath

if (-not (Test-Path ".git")) {
    Write-Error "Not a git repository. Run 'git init' first."
    exit 1
}

function Sync-ToGitHub {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"

    git add -A 2>$null
    $changes = git status --porcelain 2>$null

    if (-not $changes) {
        return $false
    }

    Write-Host "[$timestamp] Changes detected — committing..."
    git commit -m "Auto-sync: $timestamp" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[$timestamp] Commit failed (maybe nothing staged)."
        return $false
    }

    Write-Host "[$timestamp] Pushing to origin/$Branch..."
    git push origin $Branch 2>&1 | ForEach-Object { Write-Host $_ }

    if ($LASTEXITCODE -eq 0) {
        Write-Host "[$timestamp] Push successful." -ForegroundColor Green
        return $true
    }

    Write-Warning "[$timestamp] Push failed. Will retry on next cycle."
    return $false
}

Write-Host "Auto-push watcher started for: $RepoPath"
Write-Host "Branch: $Branch | Poll interval: ${IntervalSeconds}s"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

# Push anything already pending before entering the watch loop.
Sync-ToGitHub | Out-Null

while ($true) {
    Sync-ToGitHub | Out-Null
    Start-Sleep -Seconds $IntervalSeconds
}
