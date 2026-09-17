[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ProbeArguments
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$scriptPath = Join-Path $PSScriptRoot "probe_recruiter_chat_frontend.py"

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw "找不到 uv。请先安装 uv，或直接运行: python `"$scriptPath`" $($ProbeArguments -join ' ')"
}

# Python owns Patchright/CDP. This launcher intentionally needs no Node,
# Electron, npm, or Codex++ host APIs.
Push-Location $repoRoot
try {
    & uv run python $scriptPath @ProbeArguments
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
