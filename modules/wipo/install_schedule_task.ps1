<#
.SYNOPSIS
    注册 / 卸载 / 查询「WIPO 商标监控」每月自动检查的 Windows 计划任务。

.DESCRIPTION
    把「每月检查一次」交给 Windows 计划任务承载，而不是让 python 进程常驻。
    理由：main.py schedule 靠 while True + sleep 阻塞主线程维持，终端一关就停；
    计划任务由系统守护，且天然具备「错过后补跑」与运行历史。

    本脚本会在脚本目录生成 run_monthly_check.bat（任务实际调用的入口），
    再向系统注册一个按月触发的任务。时间取 config.json 的 schedule 段
    （day_of_month / hour / minute）。

    Windows 环境专用；Linux/macOS 请用 cron 或 systemd timer（见 AGENTS.md）。

.PARAMETER Action
    Install    注册（或覆盖）计划任务      —— 默认
    Uninstall  删除计划任务
    Status     查询任务当前状态与上次运行结果
    DryRun     只打印将要执行的计划，不改动系统

.PARAMETER TaskName
    计划任务名称，默认 WIPO_Trademark_Monthly_Check

.PARAMETER PythonExe
    指定 python 解释器；默认自动探测 venv\Scripts\python.exe，其次 PATH 里的 python

.PARAMETER TrademarkFile
    商标清单文件；默认 data\user_trademarks.json，缺失则回退 trademarks.json

.PARAMETER ConfigFile
    配置文件；默认脚本目录下的 config.json

.PARAMETER ReportDir
    报告输出目录；默认脚本目录下的 reports

.EXAMPLE
    .\install_schedule_task.ps1 -Action DryRun
    先看会注册什么，不动系统。

.EXAMPLE
    .\install_schedule_task.ps1
    正式注册。之后可用 -Action Status 查看、-Action Uninstall 删除。

.NOTES
    默认任务以「仅当用户登录时运行」方式注册 —— 不需要输入密码，适合本机使用。
    若需要「无论用户是否登录都运行」，请自行改用 schtasks 的 /RU + /RP 参数，
    或在「任务计划程序」图形界面里勾选该选项后补录密码（脚本不接收明文密码）。
#>
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Status', 'DryRun')]
    [string]$Action = 'Install',

    [string]$TaskName = 'WIPO_Trademark_Monthly_Check',
    [string]$PythonExe = '',
    [string]$TrademarkFile = '',
    [string]$ConfigFile = '',
    [string]$ReportDir = ''
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$BatPath = Join-Path $ScriptDir 'run_monthly_check.bat'

function Write-Step($msg) { Write-Host "  $msg" }
function Write-Head($msg) { Write-Host ""; Write-Host "== $msg" -ForegroundColor Cyan }

# ---------------------------------------------------------------- 卸载 / 查询

if ($Action -eq 'Uninstall') {
    Write-Head "删除计划任务"
    $existing = schtasks.exe /Query /TN $TaskName 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "任务 '$TaskName' 不存在，无需删除。"
        exit 0
    }
    schtasks.exe /Delete /TN $TaskName /F | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Step "已删除计划任务 '$TaskName'。"
        Write-Step "注意：run_monthly_check.bat 仍保留在脚本目录，如需一并清理请手动删除。"
    } else {
        throw "删除计划任务失败（schtasks 退出码 $LASTEXITCODE）"
    }
    exit 0
}

if ($Action -eq 'Status') {
    Write-Head "计划任务状态"
    $q = schtasks.exe /Query /TN $TaskName /V /FO LIST 2>$null
    if ($LASTEXITCODE -ne 0) {
        Write-Step "任务 '$TaskName' 尚未注册。"
        Write-Step "运行 .\install_schedule_task.ps1 进行注册。"
        exit 0
    }
    # schtasks /FO LIST 的键名随系统语言变化（中文系统 vs 英文系统），
    # 因此两种都匹配；匹配不到关键字段时整体回显，避免"看起来没注册"的误判。
    $keys = '^(TaskName|Next Run Time|Last Run Time|Last Result|Task To Run|Status|Schedule Type|Start Time|任务名|下次运行时间|上次运行时间|上次结果|要运行的任务|状态|计划类型|开始时间)'
    $matched = $q | Where-Object { $_ -match $keys }
    if ($matched) {
        $matched | ForEach-Object { Write-Step $_.Trim() }
    } else {
        Write-Step "未能解析 schtasks 输出，原始内容如下："
        $q | ForEach-Object { Write-Step $_.Trim() }
    }
    Write-Step ""
    Write-Step "说明：Last Result = 267011（0x41303）表示任务尚未运行过，属正常。"
    exit 0
}

# ---------------------------------------------------------------- 解析运行参数

Write-Head "解析运行参数"

if (-not $ConfigFile) { $ConfigFile = Join-Path $ScriptDir 'config.json' }
if (-not $ReportDir) { $ReportDir = Join-Path $ScriptDir 'reports' }

if (-not $TrademarkFile) {
    $webData = Join-Path $ScriptDir 'data\user_trademarks.json'
    $cliData = Join-Path $ScriptDir 'trademarks.json'
    if (Test-Path $webData) { $TrademarkFile = $webData }
    elseif (Test-Path $cliData) { $TrademarkFile = $cliData }
    else { throw "找不到商标清单文件：$webData 与 $cliData 都不存在" }
}

if (-not $PythonExe) {
    $venvPython = Join-Path $ScriptDir 'venv\Scripts\python.exe'
    if (Test-Path $venvPython) {
        $PythonExe = $venvPython
    } else {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { throw "找不到 python：venv 不存在，PATH 里也没有 python" }
        $PythonExe = $cmd.Source
    }
}

# 读取调度配置：与 main.py 的 get_schedule_config 保持同一套默认值与范围
$day = 1; $hour = 9; $minute = 0
if (Test-Path $ConfigFile) {
    $cfg = Get-Content -Raw -Encoding UTF8 $ConfigFile | ConvertFrom-Json
    if ($cfg.schedule) {
        foreach ($pair in @(@('day_of_month', 1, 31, 'day'), @('hour', 0, 23, 'hour'), @('minute', 0, 59, 'minute'))) {
            $key = $pair[0]; $lo = $pair[1]; $hi = $pair[2]
            $val = $cfg.schedule.$key
            if ($null -ne $val -and $val -isnot [bool] -and $val -is [int] -and $val -ge $lo -and $val -le $hi) {
                Set-Variable -Name $pair[3] -Value $val
            }
        }
    }
} else {
    Write-Step "警告：配置文件不存在（$ConfigFile），使用默认调度 每月 $day 日 $($hour):$('{0:D2}' -f $minute)"
}

$runAt = '{0:D2}:{1:D2}' -f $hour, $minute

Write-Step "脚本目录   : $ScriptDir"
Write-Step "Python     : $PythonExe"
Write-Step "商标清单   : $TrademarkFile"
Write-Step "配置文件   : $ConfigFile"
Write-Step "报告目录   : $ReportDir"
Write-Step "调度        : 每月 $day 日 $runAt（来源：$(if (Test-Path $ConfigFile) { 'config.json 的 schedule 段' } else { '内置默认值' })）"
Write-Step "任务名     : $TaskName"

foreach ($p in @($PythonExe, $TrademarkFile, $ConfigFile)) {
    if (-not (Test-Path $p)) { throw "路径不存在：$p" }
}

# ---------------------------------------------------------------- 生成批处理入口

# 任务直接调用 .bat，避免 schtasks /TR 的 261 字符命令行上限与多层引号转义问题。
# 批处理内容保持纯 ASCII，防止 GBK 控制台下中文乱码。
$batContent = @"
@echo off
REM Auto-generated by install_schedule_task.ps1 -- do not edit by hand.
REM Runs the monthly WIPO trademark check. Regenerate via the installer.
chcp 65001 >nul
cd /d "$ScriptDir"
"$PythonExe" main.py run "$TrademarkFile" --config "$ConfigFile" --output "$ReportDir"
exit /b %ERRORLEVEL%
"@

# ---------------------------------------------------------------- 注册

Write-Head $(if ($Action -eq 'DryRun') { "DryRun —— 以下内容不会写入系统" } else { "注册计划任务" })

$taskCommand = "`"$BatPath`""
Write-Step "将写入批处理 : $BatPath"
Write-Step "任务执行命令 : $taskCommand"
Write-Step "schtasks 参数: /SC MONTHLY /D $day /ST $runAt"

if ($Action -eq 'DryRun') {
    Write-Host ""
    Write-Host "--- run_monthly_check.bat 内容 ---" -ForegroundColor DarkGray
    Write-Host $batContent -ForegroundColor DarkGray
    Write-Host "--- 结束 ---" -ForegroundColor DarkGray
    Write-Host ""
    Write-Step "DryRun 完成：未改动系统。去掉 -Action DryRun 即正式注册。"
    exit 0
}

Set-Content -Path $BatPath -Value $batContent -Encoding ASCII
Write-Step "已生成 $BatPath"

# /F 表示同名任务已存在时直接覆盖，保证脚本可重复执行
schtasks.exe /Create /TN $TaskName /TR $taskCommand /SC MONTHLY /D $day /ST $runAt /F | Out-Null
if ($LASTEXITCODE -ne 0) { throw "注册计划任务失败（schtasks 退出码 $LASTEXITCODE）" }

Write-Step "已注册计划任务 '$TaskName'。"

Write-Head "完成"
Write-Step "下次运行：每月 $day 日 $runAt"
Write-Step "查看状态：.\install_schedule_task.ps1 -Action Status"
Write-Step "立即试跑：直接双击 $BatPath（或执行它），报告会写入 $ReportDir"
Write-Step "删除任务：.\install_schedule_task.ps1 -Action Uninstall"
