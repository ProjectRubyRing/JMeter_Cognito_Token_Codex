#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Scenario,
    [string]$JMeterHome = $env:JMETER_HOME,
    [string]$ResultsDirectory = 'results',
    [string]$Server,
    [Nullable[int]]$Port,
    [ValidateSet('http','https')][string]$Protocol,
    [string]$PropertiesFile,
    [Nullable[int]]$Threads,
    [Nullable[int]]$Loops,
    [Nullable[int]]$RampSeconds,
    [Nullable[int]]$ThinkTimeMs,
    [ValidateRange(1,86400)][int]$TimeoutSeconds = 180,
    [string]$PythonExecutable
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
try {
    $projectDir = Split-Path -Parent $PSScriptRoot
    if (-not $PythonExecutable) {
        $PythonExecutable = Join-Path $projectDir '.venv\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $PythonExecutable)) {
            $PythonExecutable = (Get-Command python -ErrorAction Stop).Source
        }
    }
    $commandArgs = @((Join-Path $projectDir 'cognito_jmeter.py'), 'run', '--scenario', $Scenario,
        '--results', $ResultsDirectory, '--timeout', [string]$TimeoutSeconds)
    if ($JMeterHome) { $commandArgs += @('--jmeter-home', $JMeterHome) }
    if ($Server) { $commandArgs += @('--host', $Server) }
    if ($null -ne $Port) { $commandArgs += @('--port', [string]$Port) }
    if ($Protocol) { $commandArgs += @('--protocol', $Protocol) }
    if ($PropertiesFile) { $commandArgs += @('--properties', $PropertiesFile) }
    if ($null -ne $Threads) { $commandArgs += @('--threads', [string]$Threads) }
    if ($null -ne $Loops) { $commandArgs += @('--loops', [string]$Loops) }
    if ($null -ne $RampSeconds) { $commandArgs += @('--ramp-seconds', [string]$RampSeconds) }
    if ($null -ne $ThinkTimeMs) { $commandArgs += @('--think-time-ms', [string]$ThinkTimeMs) }
    & $PythonExecutable @commandArgs
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
