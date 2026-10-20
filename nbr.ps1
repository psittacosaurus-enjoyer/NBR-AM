param(
    [ValidateSet('start','stop','status','observe')][string]$Command = 'status',
    [ValidateSet('universal','ninja_road')][string]$Stage
)
# Preserve positional and named parameters from the supported entry point.
& (Join-Path $PSScriptRoot 'scripts\nbr.ps1') @PSBoundParameters
exit $LASTEXITCODE
