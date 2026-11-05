param([string]$RunSuffix = (Get-Date -Format yyyyMMddHHmmss))
$ErrorActionPreference = 'Stop'
$probeRoot = Split-Path -Parent $PSScriptRoot
$probeHelper = Join-Path $PSScriptRoot 'crash_helper.py'
$pythonInterpreter = 'D:/Anaconda/python.exe'
$stages = @('after_prepared','after_first_insert','after_marker_before_commit','after_db_commit','after_file_replace','after_file_journal','reset_partial_move','reset_after_archive','reset_fresh_db_without_epoch','reset_after_install')
$items = @()
foreach ($stage in $stages) {
    & $pythonInterpreter -I -S -B $probeHelper crash $stage $RunSuffix
    $crashCode = $LASTEXITCODE
    if ($crashCode -ne 86) { throw "Expected controlled crash 86 at $stage, got $crashCode" }
    & $pythonInterpreter -I -S -B $probeHelper verify $stage $RunSuffix
    $verifyCode = $LASTEXITCODE
    $resultPath = Join-Path $PSScriptRoot ('hard_' + $RunSuffix + '_' + $stage + '/verification.json')
    $items += Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    if ($verifyCode -ne 0) { Write-Output "VERIFY_FAILED: $stage" }
}
$result = @{ suite='recovery_hard_exit'; suffix=$RunSuffix; passed=@($items | Where-Object status -eq 'passed').Count; failed=@($items | Where-Object status -eq 'failed').Count; cases=$items }
$result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'crash_results.json') -Encoding utf8
$result | Select-Object passed,failed | ConvertTo-Json
