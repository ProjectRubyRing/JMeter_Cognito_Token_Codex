#requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InputFile,
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Nullable[int]]$Seed,
    [string]$DataFile,
    [string]$Server = 'localhost',
    [ValidateRange(1,65535)][int]$Port = 8080,
    [ValidateSet('http','https')][string]$Protocol = 'http',
    [string]$BasePath = '',
    [switch]$EnableAll,
    [string]$Operation,
    [switch]$Sample,
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
    $commandArgs = @((Join-Path $projectDir 'cognito_jmeter.py'), 'generate', '--input', $InputFile,
        '--out', $OutputDirectory, '--host', $Server, '--port', [string]$Port, '--protocol', $Protocol)
    if ($null -ne $Seed) { $commandArgs += @('--seed', [string]$Seed) }
    if ($DataFile) { $commandArgs += @('--data-file', $DataFile) }
    if ($BasePath) { $commandArgs += @('--base-path', $BasePath) }
    if ($EnableAll) { $commandArgs += '--enable-all' }
    if ($Operation) { $commandArgs += @('--operation', $Operation) }
    if ($Sample) { $commandArgs += '--sample' }
    & $PythonExecutable @commandArgs
    exit $LASTEXITCODE
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 2
}
