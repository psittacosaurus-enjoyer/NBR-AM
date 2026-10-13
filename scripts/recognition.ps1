param(
    [ValidateSet('analyze','validate','test')][string]$Action='analyze',
    [string]$InputPath='',
    [string]$OutputPath='',
    [string]$PythonPath='',
    [switch]$Recursive,
    [switch]$Force
)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent $PSScriptRoot
$sourceDirectory=Join-Path $projectRoot 'src/item_recognition'
if (-not $PythonPath) { $PythonPath=Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $PythonPath)) { throw 'Supply -PythonPath pointing to Python with numpy, OpenCV and Pillow.' }
$previousBytecodeSetting=$env:PYTHONDONTWRITEBYTECODE
$env:PYTHONDONTWRITEBYTECODE='1'
Push-Location -LiteralPath $sourceDirectory
try {
    switch ($Action) {
        'test' { & $PythonPath -B -m unittest discover -s (Join-Path $projectRoot 'tests/item_recognition') -v }
        'validate' { & $PythonPath -B validate.py }
        'analyze' {
            if (-not $InputPath) { throw 'Supply -InputPath with a saved screenshot or directory.' }
            $analysisArguments=@('-B','cli.py',$InputPath)
            if ($OutputPath) { $analysisArguments+=@('--output',$OutputPath) }
            if ($Recursive) { $analysisArguments+='--recursive' }
            if ($Force) { $analysisArguments+='--force' }
            & $PythonPath @analysisArguments
        }
    }
    if ($LASTEXITCODE -ne 0) { throw ('Offline command failed: '+$LASTEXITCODE) }
} finally {
    Pop-Location
    $env:PYTHONDONTWRITEBYTECODE=$previousBytecodeSetting
}
