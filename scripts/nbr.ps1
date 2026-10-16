param(
    [ValidateSet('start','stop','status','observe')][string]$Command = 'status',
    [ValidateSet('universal')][string]$Stage
)
$projectRoot = Split-Path -Parent $PSScriptRoot
$farmArgs = @($Command)
if ($Stage) { $farmArgs += @('--stage', $Stage) }
& "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\src\farm.py" @farmArgs
exit $LASTEXITCODE
