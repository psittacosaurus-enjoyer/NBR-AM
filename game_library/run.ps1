param(
    [ValidateSet('browser','resume','update','build','export','test','validate')]
    [string]$Action = 'browser',
    [int]$Port = 8765,
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
if (-not $PythonPath) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $PythonPath = $bundledPython }
    else {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if ($pythonCommand) { $PythonPath = $pythonCommand.Source }
        else { throw 'Set -PythonPath to Python 3.10+ with Pillow installed.' }
    }
}
Push-Location -LiteralPath $projectPath
try {
    switch ($Action) {
        'browser' { & $PythonPath server.py --port $Port }
        'resume' {
            & $PythonPath crawler/fetch.py --resume
            if ($LASTEXITCODE -ne 0) { throw 'Collector failed; see reports/crawl.json.' }
            & $PythonPath build.py
        }
        'update' {
            & $PythonPath crawler/fetch.py --update
            if ($LASTEXITCODE -ne 0) { throw 'Collector failed; see reports/crawl.json.' }
            & $PythonPath build.py
        }
        'build' { & $PythonPath build.py }
        'export' { & $PythonPath build.py --export-only }
        'test' { & $PythonPath -m unittest discover -s tests -v }
        'validate' { & $PythonPath validate.py }
    }
    if ($LASTEXITCODE -ne 0) { throw ('Command failed with exit code ' + $LASTEXITCODE) }
} finally { Pop-Location }
