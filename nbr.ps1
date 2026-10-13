param(
    [ValidateSet('select','start','stop','status','observe')][string]$Command = 'status',
    [ValidateSet('scrolls-a','rich-b','practice-part-1','night-of-tragedy-a')][string]$Stage
)
$farmArgs = @($Command)
if ($Stage) { $farmArgs += @('--stage', $Stage) }
& "$PSScriptRoot\.venv\Scripts\python.exe" "$PSScriptRoot\farm.py" @farmArgs
exit $LASTEXITCODE
