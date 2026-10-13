# run.ps1 — автоматическое AI-ревью PR (день 32).
#
#   .\run.ps1 -Mode test                                              # проверки на имитации (ключ и интернет не нужны)
#   .\run.ps1 -Mode review -Repo "C:\папка\репозиторий" -Base main -Head feature
#                                                                     # ревью ветки на ПК, результат — в output\review.md
#
# Ключ AITunnel спрашивается скрытым вводом и в файлы не сохраняется.
# Если Windows блокирует запуск:  powershell -ExecutionPolicy Bypass -File .\run.ps1

param(
    [ValidateSet("review", "test")]
    [string]$Mode = "review",
    [string]$Repo = "",
    [string]$Base = "main",
    [string]$Head = "HEAD",
    [ValidateSet("aitunnel", "ollama")]
    [string]$Llm = "aitunnel"
)

Set-Location $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

function Find-Python {
    $installed = @(Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe", "$env:ProgramFiles\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | ForEach-Object { $_.FullName })
    foreach ($candidate in @("py", "python") + $installed) {
        try {
            $output = & $candidate --version 2>&1
            if ($output -match "Python \d") { return $candidate }
        }
        catch { }
    }
    return $null
}

$py = Find-Python
if (-not $py) { Write-Host "Python не найден. Установите: winget install Python.Python.3.12 --scope user" -ForegroundColor Red; exit 1 }
Write-Host "Python: $py" -ForegroundColor Green

& $py -c "import numpy" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Ставлю numpy (один раз)..." -ForegroundColor Cyan
    & $py -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Write-Host "Не удалось установить numpy. Проверьте интернет и повторите." -ForegroundColor Red; exit 1 }
}

if ($Mode -eq "test") {
    & $py .\test_day32.py
    Write-Host "`nОтчёт: $PSScriptRoot\output\test_report.md" -ForegroundColor Green
    exit $LASTEXITCODE
}

if (-not $Repo) { Write-Host "Укажите папку репозитория: -Repo `"C:\папка\репозиторий`"" -ForegroundColor Red; exit 1 }
if (-not (Test-Path $Repo)) { Write-Host "Папка не найдена: $Repo" -ForegroundColor Red; exit 1 }
$Repo = (Resolve-Path $Repo).Path
New-Item -ItemType Directory -Force -Path "$PSScriptRoot\output" | Out-Null
$out = "$PSScriptRoot\output\review.md"

Write-Host "Ревью: ветка $Head против $Base в $Repo" -ForegroundColor Cyan
& $py .\review\review.py --repo $Repo --base $Base --head $Head --llm $Llm --out $out
$code = $LASTEXITCODE
if ($code -eq 0 -and (Test-Path $out)) {
    Write-Host "`nГотово: $out" -ForegroundColor Green
    Start-Process notepad.exe $out
}
exit $code
