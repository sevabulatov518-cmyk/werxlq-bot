# Заливает бота на VPS и устанавливает его одной командой.
#
# Запуск (из папки werxlq-bot):
#   deploy\deploy.bat 203.0.113.10
#   deploy\deploy.bat 203.0.113.10 2222              (другой порт SSH)
#   deploy\deploy.bat 203.0.113.10 22 myuser          (вход не под root, через sudo)
#
# Проверить, что будет сделано, ничего не отправляя:
#   deploy\deploy.bat 203.0.113.10 22 myuser --dry
#
# Нужен компонент Windows "OpenSSH Client" (обычно уже установлен).

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [int]$Port = 22,

    [string]$RemoteUser = "root",

    [string]$RemotePath = "",

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)
    Write-Host ""
    Write-Host "[!] $Message" -ForegroundColor Red
    Write-Host ""
    exit 1
}

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    Fail "Не найден ssh. Включи компонент 'OpenSSH Client': Параметры - Приложения - Дополнительные компоненты."
}
if (-not (Get-Command scp -ErrorAction SilentlyContinue)) {
    Fail "Не найден scp (обычно ставится вместе с OpenSSH Client)."
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$botDir = Split-Path -Parent $scriptDir
if (-not (Test-Path (Join-Path $botDir "bot.py"))) {
    Fail "Не нашёл bot.py рядом со скриптом. Скрипт должен лежать в werxlq-bot\deploy\."
}

$isRoot = ($RemoteUser -eq "root")
$sudo = if ($isRoot) { "" } else { "sudo " }

# Под обычным пользователем заливаем в домашний каталог, а установщик сам
# скопирует бота в /opt и поставит права через sudo.
if ([string]::IsNullOrWhiteSpace($RemotePath)) {
    $RemotePath = if ($isRoot) { "/opt" } else { "~" }
}

$target = "$RemoteUser@$Server"
$sshOptions = @("-o", "StrictHostKeyChecking=accept-new")
$sshArgs = "ssh -p {0} {1} {2}" -f $Port, ($sshOptions -join " "), $target

Write-Host "Сервер:  $target (порт $Port)"
Write-Host "Проект:  $botDir"
Write-Host "Режим:   $(if ($isRoot) { 'root' } else { "пользователь $RemoteUser (sudo)" })"
Write-Host ""

$scpLine = "scp -P {0} {1} -r `"{2}`" `"{3}:{4}/`"" -f $Port, ($sshOptions -join " "), $botDir, $target, $RemotePath
$installLine = "{0} `"cd {1}/werxlq-bot && {2}bash deploy/install.sh`"" -f $sshArgs, $RemotePath, $sudo
$statusLine = "{0} `"systemctl is-active werxlq-bot; echo ---; {1}journalctl -u werxlq-bot -n 15 --no-pager`"" -f $sshArgs, $sudo

if ($DryRun) {
    Write-Host "[dry-run] Ничего не отправляю. Будут выполнены команды:" -ForegroundColor Yellow
    Write-Host "  $scpLine"
    Write-Host "  $installLine"
    Write-Host "  $statusLine"
    exit 0
}

Write-Host "1/3 Загружаю файлы на сервер..." -ForegroundColor Cyan
& scp -P $Port @sshOptions -r $botDir "${target}:${RemotePath}/"
if ($LASTEXITCODE -ne 0) {
    Fail "scp не смог загрузить файлы. Проверь IP, логин, ключ/пароль и доступность сервера."
}

Write-Host ""
Write-Host "2/3 Устанавливаю бота на сервере..." -ForegroundColor Cyan
& ssh -p $Port @sshOptions $target "cd ${RemotePath}/werxlq-bot && ${sudo}bash deploy/install.sh"
if ($LASTEXITCODE -ne 0) {
    Fail "Установка завершилась ошибкой - смотри вывод выше. Логи: ssh $target '${sudo}journalctl -u werxlq-bot -n 50'"
}

Write-Host ""
Write-Host "3/3 Проверяю сервис..." -ForegroundColor Cyan
& ssh -p $Port @sshOptions $target "systemctl is-active werxlq-bot; echo '---'; ${sudo}journalctl -u werxlq-bot -n 15 --no-pager"

Write-Host ""
Write-Host "Готово. Открой @Ve1vetz_bot в Telegram и нажми /start." -ForegroundColor Green
Write-Host "Логи в любой момент: ssh $target '${sudo}journalctl -u werxlq-bot -f'"
Write-Host ""