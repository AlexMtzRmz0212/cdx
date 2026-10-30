# cdx installer. Run via install.cmd (or: powershell -File install.ps1 [-Uninstall] [-Dev])
# -Dev: don't copy; the cd macro points at the files in this folder, so edits here apply immediately.
param([switch]$Uninstall, [switch]$Dev)

$ErrorActionPreference = 'Stop'
$dest    = Join-Path $env:LOCALAPPDATA 'cdx'
$autorun = Join-Path $dest 'autorun.cmd'
$regPath = 'HKCU:\Software\Microsoft\Command Processor'
$entry   = '"' + $autorun + '"'

function Get-AutoRun {
    if (-not (Test-Path $regPath)) { return $null }
    return (Get-ItemProperty -Path $regPath -ErrorAction SilentlyContinue).AutoRun
}

function Remove-AutoRunEntry {
    $cur = Get-AutoRun
    if (-not $cur) { return }
    $new = $cur.Replace(" & $entry", '').Replace("$entry & ", '').Replace($entry, '').Trim()
    if ($new) { Set-ItemProperty -Path $regPath -Name AutoRun -Value $new }
    else      { Remove-ItemProperty -Path $regPath -Name AutoRun }
}

function Test-Python {
    foreach ($cmd in 'py', 'python') {
        if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { continue }
        try {
            if ($cmd -eq 'py') { $out = & py -3 --version 2>&1 } else { $out = & python --version 2>&1 }
            if ($LASTEXITCODE -eq 0 -and ("$out" -match 'Python 3\.(\d+)') -and [int]$Matches[1] -ge 8) { return $true }
        } catch { }
    }
    return $false
}

if ($Uninstall) {
    Remove-AutoRunEntry
    if (Test-Path $dest) { Remove-Item -Recurse -Force $dest }
    Write-Host 'cdx removed. Open a new cmd window (this one keeps the alias until closed).'
    exit 0
}

if (-not (Test-Python)) {
    Write-Host 'Python 3.8+ was not found. Install it from https://www.python.org/downloads/ (tick "Add python.exe to PATH"), then run this again.'
    exit 1
}

foreach ($f in 'cdx.py', 'cdx.cmd') {
    if (-not (Test-Path (Join-Path $PSScriptRoot $f))) { Write-Host "Missing $f next to the installer."; exit 1 }
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
if ($Dev) {
    $cmdDir = $PSScriptRoot
} else {
    $cmdDir = $dest
    Copy-Item (Join-Path $PSScriptRoot 'cdx.py')  $dest -Force
    Copy-Item (Join-Path $PSScriptRoot 'cdx.cmd') $dest -Force
}

# autorun.cmd defines the doskey macro: typing "cd ..." runs cdx.cmd, which passes real paths straight to cd
$macro = '@doskey cd="' + (Join-Path $cmdDir 'cdx.cmd') + '" $*'
Set-Content -Path $autorun -Value $macro -Encoding ASCII

if (-not (Test-Path $regPath)) { New-Item -Path $regPath -Force | Out-Null }
$cur = Get-AutoRun
if (-not $cur) {
    Set-ItemProperty -Path $regPath -Name AutoRun -Value $entry
} elseif (-not $cur.Contains($entry)) {
    Set-ItemProperty -Path $regPath -Name AutoRun -Value ($cur + ' & ' + $entry)
}

Write-Host ''
Write-Host "Installed to $cmdDir"
Write-Host 'Open a NEW cmd window, then type:  cd'
Write-Host 'To use it in this window right now, run:'
Write-Host ('  ' + $macro.TrimStart('@'))
