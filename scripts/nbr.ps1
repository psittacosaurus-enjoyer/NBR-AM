param(
    [ValidateSet('select','start','stop','status','observe')][string]$Command = 'status',
    [ValidateSet('universal','scrolls-a','rich-b','practice-part-1','night-of-tragedy-a')][string]$Stage
)
$projectRoot = Split-Path -Parent $PSScriptRoot
$farmArgs = @($Command)
if ($Stage) { $farmArgs += @('--stage', $Stage) }
& "$projectRoot\.venv\Scripts\python.exe" "$projectRoot\src\farm.py" @farmArgs
exit $LASTEXITCODE
