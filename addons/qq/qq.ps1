param(
    [ValidateSet('init','scan','pair','config','run','stop','unlock')]
    [string]$Action = 'run',
    [string]$Config = '',
    [switch]$Background
)
$ErrorActionPreference = 'Stop'
$bridgeRoot = $PSScriptRoot
$entryPath = Join-Path $bridgeRoot 'src\main.mjs'
if (-not $Config) { $Config = Join-Path $bridgeRoot '..\..\user\qq\config.json' }
$configPath = [System.IO.Path]::GetFullPath($Config)
$nodePath = (Get-Command node.exe -ErrorAction Stop).Source
if ($Background) {
    if ($Action -ne 'run') { throw 'Only run supports -Background. Scan and pair require a foreground terminal.' }
    if ($entryPath.Contains('"') -or $configPath.Contains('"')) { throw 'Invalid path.' }
    $launchArguments = '"' + $entryPath + '" run --config "' + $configPath + '"'
    $process = Start-Process -FilePath $nodePath -ArgumentList $launchArguments -WorkingDirectory $bridgeRoot -WindowStyle Hidden -PassThru
    Write-Output ('Bridge launch requested, PID ' + $process.Id + '. Check the private bridge log; launch is not a connection confirmation.')
} else {
    & $nodePath $entryPath $Action --config $configPath
    if ($LASTEXITCODE -ne 0) { throw 'Bridge command did not complete. See the safe message above and README.' }
}
