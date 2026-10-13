param(
    [ValidateSet('browser','resume','update','build','export','test','validate','fandom-crawl','fandom-import')]
    [string]$Action = 'browser',
    [int]$Port = 8765,
    [string]$PythonPath = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPath = Join-Path $projectRoot 'src/game_library'
if (-not $PythonPath) {
    $environmentPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $environmentPython) { $PythonPath = $environmentPython }
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
            if ($LASTEXITCODE -ne 0) { throw 'Collector failed; see runtime/library/reports/crawl.json.' }
            & $PythonPath build.py
        }
        'update' {
            & $PythonPath crawler/fetch.py --update
            if ($LASTEXITCODE -ne 0) { throw 'Collector failed; see runtime/library/reports/crawl.json.' }
            & $PythonPath build.py
        }
        'build' { & $PythonPath build.py }
        'fandom-crawl' { & $PythonPath fandom_source.py --resume }
        'fandom-import' { & $PythonPath fandom_import.py }
        'export' { & $PythonPath build.py --export-only }
        'test' { & $PythonPath -m unittest discover -s (Join-Path $projectRoot 'tests/game_library') -v }
        'validate' { & $PythonPath validate.py }
    }
    if ($LASTEXITCODE -ne 0) { throw ('Command failed with exit code ' + $LASTEXITCODE) }
} finally { Pop-Location }
