# 营业执照巡检 —— Windows 计划任务注册脚本
#
# 用法（管理员 PowerShell 不需要；「仅当用户登录时运行」模式普通权限即可）：
#   .\install_schedule_task.ps1 -Action DryRun    # 只看会注册什么，不改系统
#   .\install_schedule_task.ps1                   # 注册（每日 09:00 给业务 + 每月 1 日 09:05 给主管）
#   .\install_schedule_task.ps1 -Action Status    # 查下次运行时间与两个触发器
#   .\install_schedule_task.ps1 -Action Uninstall # 卸载
#   .\install_schedule_task.ps1 -Action RunNow    # 立刻跑一次（验证配置）
#
# 参数：
#   -Companies    企业名单文件（默认 companies.txt）
#   -Webhook      群机器人地址；不传则只产出 CSV 不推送
#   -Time         每日触发时间 HH:mm（默认 09:00）
#   -MonthlyDay   每月第几天 1..28（默认 1）★ 上限 28 见下方说明
#   -MonthlyTime  月度触发时间 HH:mm（默认比 -Time 晚 5 分钟，避免两次同时抢浏览器）
#   -NoMonthly    只注册每日，不注册月度
#   -Weekly       退回旧的「每周一次」行为（-DayOfWeek 配合）
#   -DayOfWeek    每周几（仅在 -Weekly 下生效）
#
# 设计要点（沿用 WIPO 系统的既有经验）：
#   1. 计划任务调用 .bat，而不是把整条命令行塞进 schtasks /TR
#      —— 避开 261 字符上限与多层引号转义；
#   2. 退出码语义：credit_check 0=成功；notify 0=已推送 / 10=无需推送 / 1=失败。
#      .bat 只把 1 视为失败，10 不算失败（否则「无异常」会被误报成任务失败）；
#   3. 本文件必须保存为 UTF-8 with BOM —— Windows PowerShell 5.1 对无 BOM 的
#      UTF-8 会按 GBK 解码，中文与 here-string 会解析失败报 Unexpected token。
#      ★ 用 edit/write 工具改本文件后会丢掉 BOM，改完必须补：
#        $t=$s=[IO.File]::ReadAllText($p,[Text.UTF8Encoding]::new($false))
#        [IO.File]::WriteAllText($p,$s,[Text.UTF8Encoding]::new($true))
#   4. .bat 正文用单引号 here-string（字面，无反引号转义）+ 占位符替换生成，
#      New-RunnerBat 与 DryRun 预览共用同一函数 Build-BatContent，
#      保证「预览所见 = 实际生成」。
#   5. 月度触发器不能用 New-ScheduledTaskTrigger —— 它只有 Once/Daily/Weekly/
#      Startup/Logon 五组参数集，没有 Monthly。本机实测：CIM 类
#      MSFT_TaskMonthlyTrigger 直接交给 -Trigger 会报「参数错误。」；只能走
#      「原生 cmdlet 注册 → Export-ScheduledTask 取合法 XML 骨架 → 插入第二个
#      CalendarTrigger → Register-ScheduledTask -Xml」这条路。
#   6. -MonthlyDay 上限取 28 不是偷懒：29/30/31 在 2 月不存在，Task Scheduler
#      不会顺延到 3 月 1 日，而是**整个 2 月静默不触发**。写 1 号最稳，
#      想「月底跑」应改用 RunOnLastDayOfMonth，那要另写 XML，不在此脚本范围。
#
#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Install', 'Uninstall', 'Status', 'DryRun', 'RunNow')]
    [string]$Action = 'Install',

    [string]$Companies = 'companies.txt',
    [string]$Webhook = '',
    [ValidatePattern('^\d{2}:\d{2}$')]
    [string]$Time = '09:00',

    [ValidateRange(1, 28)]
    [int]$MonthlyDay = 1,
    [ValidatePattern('^$|^\d{2}:\d{2}$')]
    [string]$MonthlyTime = '',
    [switch]$NoMonthly,
    [switch]$Weekly,
    [ValidateSet('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')]
    [string]$DayOfWeek = 'Monday',

    [string]$TaskName = 'CreditChina-LicenseCheck'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BatPath = Join-Path $ScriptDir 'run_credit_check.bat'
$MonthlyBatPath = Join-Path $ScriptDir 'run_credit_check_monthly.bat'
$PyPath = Join-Path $ScriptDir '.venv\Scripts\python.exe'

function Write-Step($msg) { Write-Host "  $msg" }
function Write-Head($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# 月度时间默认比每日晚 5 分钟：两次都要独占一个浏览器实例（worker 串行），
# 撞在同一刻会有一段无谓的排队与日志噪音。
function Get-EffectiveMonthlyTime {
    if ($MonthlyTime) { return $MonthlyTime }
    $d = [datetime]::ParseExact($Time, 'HH:mm', $null)
    return $d.AddMinutes(5).ToString('HH:mm')
}

# ---------------------------------------------------------------- 环境自检

function Test-Prerequisites {
    $ok = $true

    if (-not (Test-Path $PyPath)) {
        Write-Host "  [X] 找不到 venv Python: $PyPath" -ForegroundColor Red
        Write-Host "      先建环境：python -m venv .venv" -ForegroundColor Yellow
        Write-Host "                .venv\Scripts\python.exe -m pip install -r requirements.txt" -ForegroundColor Yellow
        $ok = $false
    } else {
        Write-Step "[OK] Python: $PyPath"
    }

    $companiesPath = if ([IO.Path]::IsPathRooted($Companies)) { $Companies } else { Join-Path $ScriptDir $Companies }
    if (-not (Test-Path $companiesPath)) {
        Write-Host "  [!] 名单文件不存在: $companiesPath" -ForegroundColor Yellow
        Write-Host "      创建后即可运行：一行一家企业全称，# 开头为注释" -ForegroundColor Yellow
    } else {
        $n = @(Get-Content $companiesPath -Encoding UTF8 |
               Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') }).Count
        Write-Step "[OK] 名单: $companiesPath（$n 家）"
    }

    foreach ($f in 'credit_check.py', 'notify.py', 'healthcheck.py') {
        if (-not (Test-Path (Join-Path $ScriptDir $f))) {
            if ($f -eq 'healthcheck.py') {
                Write-Host "  [!] 缺少 healthcheck.py：将跳过服务自检，只报企业状态" -ForegroundColor Yellow
            } else {
                Write-Host "  [X] 缺少脚本: $f" -ForegroundColor Red
                $ok = $false
            }
        }
    }

    if ($Webhook) {
        if ($Webhook -match '^https?://') {
            Write-Step "[OK] Webhook: $($Webhook.Substring(0, [Math]::Min(48, $Webhook.Length)))..."
        } else {
            Write-Host "  [X] Webhook 必须以 http:// 或 https:// 开头" -ForegroundColor Red
            $ok = $false
        }
    } else {
        Write-Step "[!] 未指定 -Webhook：任务只发邮件，不发群消息"
    }

    return $ok
}

# ---------------------------------------------------------------- .bat 生成

function Build-BatContent {
    <#
      生成 .bat 正文。唯一的真源：New-RunnerBat 与 DryRun 预览都调它，
      避免「预览和实际生成不一致」。

      用单引号 here-string（完全字面，PowerShell 不做反引号转义、不做变量展开），
      再用 .Replace() 填占位符 —— 这样 cmd 需要的每一对双引号都能原样保留。

      .bat 只做四件事：切目录、跑自检、跑巡检、按需推送。
      关键：把 credit_check 的非 0 退出码与 notify 的 10 都当作正常，
      只有真正的失败（notify 返回 1）才让任务标红。
      healthcheck 的退出码**不参与**任务成败：自检红说明巡检可能不完整，
      但它恰恰是我们要报出去的内容，若在此处 exit 1 就会跳过 notify，
      变成「有问题所以没发信」的反向结果。
    #>
    param([ValidateSet('daily', 'monthly')][string]$Mode = 'daily')

    $companiesArg = if ([IO.Path]::IsPathRooted($Companies)) { $Companies } else { "%~dp0$Companies" }

    $notifyLine = '"%PY%" notify.py "%CSV%" --mode ' + $Mode + ' --health "%HEALTH%"'
    if ($Webhook) {
        $notifyLine = $notifyLine + ' --webhook "' + $Webhook + '"'
    }

    $tpl = @'
@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
rem 本文件由 install_schedule_task.ps1 自动生成，请勿手工编辑
rem 节奏：__MODE__（daily=每日给业务人员 / monthly=每月给主管）

set "PY=%~dp0.venv\Scripts\python.exe"
set "DIR=%~dp0"
set "MODE=__MODE__"
cd /d "%DIR%"
if not exist "%DIR%logs" mkdir "%DIR%logs"
if not exist "%DIR%data" mkdir "%DIR%data"

rem 时间戳文件名，避免覆盖历史结果。
rem 不用 wmic：Win11 24H2 起已弃用，本机实测 wmic 返回空值。
set "STAMP="
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"`) do if not defined STAMP set "STAMP=%%i"
if not defined STAMP set "STAMP=unknown"
set "CSV=%DIR%result_%STAMP%.csv"
set "HEALTH=%DIR%data\health_%STAMP%.json"

echo [%DATE% %TIME%] 巡检开始 mode=%MODE% >> "%DIR%logs\task.log"

rem 服务自检：确认「巡检服务自己还能不能跑」。缺脚本时降级为跳过，
rem 不让旧部署因为少一个文件就整体失败。
rem ★ 不要写成 if exist ... ( ... set RC=%ERRORLEVEL% ) —— 括号块内的
rem   %VAR% 在解析整个块时就已展开，拿到的是进块之前的值（永远是旧值）。
rem   改用 goto 跳转，避免为此开 delayedexpansion（会改变所有 %VAR% 的语义）。
set RC_HEALTH=0
if not exist "%DIR%healthcheck.py" goto :afterhealth
"%PY%" healthcheck.py --json "%HEALTH%" --companies "__COMPANIES__" --mark-run
set RC_HEALTH=%ERRORLEVEL%
:afterhealth
echo [%DATE% %TIME%] healthcheck 退出码=%RC_HEALTH% >> "%DIR%logs\task.log"

"%PY%" credit_check.py "__COMPANIES__" -o "%CSV%"
set RC_CHECK=%ERRORLEVEL%
echo [%DATE% %TIME%] credit_check 退出码=%RC_CHECK% >> "%DIR%logs\task.log"

__NOTIFY__
set RC_NOTIFY=%ERRORLEVEL%
echo [%DATE% %TIME%] notify 退出码=%RC_NOTIFY% >> "%DIR%logs\task.log"

rem 退出码语义：notify 0=已推送 / 10=无需推送 / 1=失败。只有 1 算失败。
if "%RC_NOTIFY%"=="1" exit /b 1
if not "%RC_CHECK%"=="0" exit /b 1
exit /b 0
'@

    return $tpl.Replace('__COMPANIES__', $companiesArg).
                Replace('__NOTIFY__', $notifyLine).
                Replace('__MODE__', $Mode)
}

function Write-BatFile {
    param([string]$Path, [string]$Mode)
    $text = Build-BatContent -Mode $Mode
    # 用 CRLF + UTF-8(无 BOM) 写 .bat：cmd 在 chcp 65001 下读 UTF-8 正常，
    # 加 BOM 反而会让第一行 @echo off 前面多出字符。
    $text = ($text -replace "`r?`n", "`r`n").TrimEnd() + "`r`n"
    [IO.File]::WriteAllText($Path, $text, (New-Object Text.UTF8Encoding $false))
    Write-Step "[OK] 已生成 $Path"
}

function New-RunnerBat {
    Write-BatFile -Path $BatPath -Mode 'daily'
    if (-not $NoMonthly) {
        Write-BatFile -Path $MonthlyBatPath -Mode 'monthly'
    }
}

# ---------------------------------------------------------------- 触发器构建

function New-TaskComponents {
    param([string]$Execute, [string]$WorkDir)

    $action = New-ScheduledTaskAction -Execute $Execute -WorkingDirectory $WorkDir
    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
        -MultipleInstances IgnoreNew
    # 仅当用户登录时运行：不需要密码，普通权限即可注册
    $principal = New-ScheduledTaskPrincipal `
        -UserId "$env:USERDOMAIN\$env:USERNAME" `
        -LogonType Interactive `
        -RunLevel Limited

    return @{ Action = $action; Settings = $settings; Principal = $principal }
}

function Get-PrimaryTrigger {
    param([string]$At, [string]$Day)
    if ($Weekly) {
        return New-ScheduledTaskTrigger -Weekly -DaysOfWeek $Day -At $At
    }
    return New-ScheduledTaskTrigger -Daily -At $At
}

function Merge-MonthlyTriggerXml {
    <#
      在已注册任务的 XML 里插入第二个 CalendarTrigger 实现「每月一次」。

      做法：克隆现有的（每日/每周）CalendarTrigger，把 <ScheduleByDay> 或
      <ScheduleByWeek> 段换成 <ScheduleByMonth>，改掉 StartBoundary，再拼到
      </Triggers> 前面。用 String.Replace 而不是 -replace / regex.Replace，
      因为后两者会把替换串里的 $ 当反向引用（XML 里出现 $ 就炸）。
    #>
    param([string]$Xml, [string]$MonthlyTime)

    $cal = [regex]::Match($Xml, '(?s)<CalendarTrigger>.*?</CalendarTrigger>')
    if (-not $cal.Success) {
        throw '导出的 XML 里找不到 <CalendarTrigger>，无法派生月度触发器'
    }

    $calText = $cal.Value
    $dayBlock = [regex]::Match($calText, '(?s)<ScheduleByDay>.*?</ScheduleByDay>')
    $weekBlock = [regex]::Match($calText, '(?s)<ScheduleByWeek>.*?</ScheduleByWeek>')
    $srcBlock = if ($dayBlock.Success) { $dayBlock.Value } elseif ($weekBlock.Success) { $weekBlock.Value } else { $null }
    if (-not $srcBlock) {
        throw '现有的 CalendarTrigger 既非 ScheduleByDay 也非 ScheduleByWeek，无法派生月度触发器'
    }

    $monthNames = 'January', 'February', 'March', 'April', 'May', 'June',
                  'July', 'August', 'September', 'October', 'November', 'December'
    $monthsXml = ($monthNames | ForEach-Object { "<$_ />" }) -join ''
    $monthBlock = "<ScheduleByMonth><DaysOfMonth><Day>$MonthlyDay</Day></DaysOfMonth>" +
                  "<Months>$monthsXml</Months></ScheduleByMonth>"

    $monthly = $calText.Replace($srcBlock, $monthBlock)

    # 克隆出来的触发器带着与主触发器相同的 <Id>，两个同 Id 的触发器会被
    # 判定为 XML 不合法。干脆去掉 —— Task Scheduler 会自行分配。
    $monthly = [regex]::Replace($monthly, '(?s)\s*<Id>[^<]*</Id>', '')

    # 起始时间改成月度时间。日期本身对「每月」触发器无意义（只看时分），
    # 但**时区偏移必须保留**：系统导出的主触发器是
    #   2026-10-18T09:00:00+08:00
    # 若这里只写 yyyy-MM-ddTHH:mm:ss，月度触发器会变成「本地时间无偏移」，
    # 跨时区/夏令时机器上会和主触发器算出不同的实际时刻。
    $offset = [regex]::Match($calText, '<StartBoundary>[^<]*(?<off>[+-]\d{2}:\d{2})</StartBoundary>')
    $suffix = if ($offset.Success) { $offset.Groups['off'].Value } else { '' }
    $newStart = (Get-Date).ToString('yyyy-MM-dd') + 'T' + $MonthlyTime + ':00' + $suffix
    $oldStart = [regex]::Match($monthly, '<StartBoundary>[^<]*</StartBoundary>').Value
    if ($oldStart) {
        $monthly = $monthly.Replace($oldStart, "<StartBoundary>$newStart</StartBoundary>")
    }

    return $Xml.Replace('</Triggers>', $monthly + '</Triggers>')
}

function Register-DualTriggerTask {
    <#
      注册「每日 + 每月」双触发器。失败时回退到单触发器，
      保证不会把用户留在「一个任务都没注册」的状态。
    #>
    param([string]$Description)

    $parts = New-TaskComponents -Execute $BatPath -WorkDir $ScriptDir
    $primary = Get-PrimaryTrigger -At $Time -Day $DayOfWeek

    # 第 1 步：用原生 cmdlet 注册，借它生成结构合法的 XML 骨架
    # （Settings 子元素顺序、UserId 解析成 SID 等都由系统完成，
    #   手写 XML 会被拒为「任务 XML 包含意外节点」——本机实测。）
    Register-ScheduledTask -TaskName $TaskName -Trigger $primary `
        -Action $parts.Action -Settings $parts.Settings -Principal $parts.Principal `
        -Description $Description | Out-Null

    if ($NoMonthly) {
        return $primary
    }

    $mTime = Get-EffectiveMonthlyTime
    try {
        $xml = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $newXml = Merge-MonthlyTriggerXml -Xml $xml -MonthlyTime $mTime
        Register-ScheduledTask -TaskName $TaskName -Xml $newXml -Force | Out-Null

        # 回读校验：CIM 对象只报基类 MSFT_TaskTrigger，读不出 DaysOfMonth，
        # 必须看导出的 XML 文本才能确认月度真的落地了。
        $verify = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        if ($verify -notmatch '<ScheduleByMonth>') {
            throw '回读 XML 中没有 <ScheduleByMonth>，月度触发器未生效'
        }
        if ($verify -notmatch '<ScheduleByDay>|<ScheduleByWeek>') {
            throw '回读 XML 中主触发器丢失'
        }
        Write-Step "[OK] 双触发器已确认：主触发器 + 每月 $MonthlyDay 日 $mTime"
        return $primary
    } catch {
        Write-Host "  [!] 月度触发器注册失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "      回退为单触发器（只有主节奏）。可稍后重跑本脚本重试。" -ForegroundColor Yellow
        # 回退：把任务重建成单触发器，避免留在「主触发器没了」的中间态
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Register-ScheduledTask -TaskName $TaskName -Trigger $primary `
            -Action $parts.Action -Settings $parts.Settings -Principal $parts.Principal `
            -Description $Description | Out-Null
        return $primary
    }
}

# ---------------------------------------------------------------- 注册/卸载

function Get-Task {
    try { return Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
    catch { return $null }
}

function Invoke-Install {
    Write-Head '环境自检'
    if (-not (Test-Prerequisites)) { throw '环境自检未通过，已中止' }

    Write-Head '生成运行入口'
    New-RunnerBat

    Write-Head '注册计划任务'
    $existing = Get-Task
    if ($existing) {
        Write-Step "[!] 已存在同名任务，将覆盖"
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $desc = if ($Weekly) {
        '信用中国营业执照状态巡检（每周一次）'
    } else {
        '信用中国营业执照状态巡检（每日→业务人员，每月→主管）'
    }
    $null = Register-DualTriggerTask -Description $desc

    Write-Step "[OK] 已注册：$TaskName"
    if ($Weekly) {
        Write-Step "     主触发：每周 $DayOfWeek $Time → $BatPath"
    } else {
        Write-Step "     主触发：每日 $Time → $BatPath"
    }
    if (-not $NoMonthly) {
        Write-Step "     月触发：每月 $MonthlyDay 日 $(Get-EffectiveMonthlyTime) → $MonthlyBatPath"
    }
    Write-Host ''
    Write-Host '  提示：若需「无论是否登录都运行」，请在「任务计划程序」图形界面' -ForegroundColor Yellow
    Write-Host '        勾选该选项并补录密码 —— 本脚本不接收明文密码。' -ForegroundColor Yellow
}

function Invoke-Uninstall {
    Write-Head '卸载计划任务'
    if (-not (Get-Task)) {
        Write-Step "[!] 任务不存在：$TaskName"
    } else {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Step "[OK] 已卸载：$TaskName"
    }
    foreach ($p in @($BatPath, $MonthlyBatPath)) {
        if (Test-Path $p) {
            Remove-Item $p -Force
            Write-Step "[OK] 已删除 $p"
        }
    }
}

function Invoke-Status {
    Write-Head '任务状态'
    $t = Get-Task
    if (-not $t) {
        Write-Host "  [X] 任务未注册：$TaskName" -ForegroundColor Red
        Write-Host "      运行 .\install_schedule_task.ps1 注册" -ForegroundColor Yellow
        return
    }

    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    Write-Step "任务名   : $($t.TaskName)"
    Write-Step "状态     : $($t.State)"
    Write-Step "下次运行 : $($info.NextRunTime)"
    Write-Step "上次运行 : $($info.LastRunTime)"
    Write-Step "上次结果 : $($info.LastTaskResult)"

    switch ($info.LastTaskResult) {
        0         { Write-Step "           (0 = 成功)" }
        267011    { Write-Step "           (267011 = 尚未运行过)" }
        267009    { Write-Step "           (267009 = 正在运行)" }
        default   { Write-Step "           (非 0，检查 logs\task.log)" }
    }

    # 触发器明细：CIM 对象读不出 DaysOfMonth，只能看导出 XML
    Write-Head '触发器'
    try {
        $xml = Export-ScheduledTask -TaskName $TaskName -ErrorAction Stop
        $cats = [regex]::Matches($xml, '(?s)<CalendarTrigger>.*?</CalendarTrigger>')
        if ($cats.Count -eq 0) { Write-Step '(无日历触发器)' }
        foreach ($c in $cats) {
            $body = $c.Value
            if ($body -match '<ScheduleByMonth>') {
                $day = ([regex]::Match($body, '<DaysOfMonth>\s*<Day>(\d+)</Day>')).Groups[1].Value
                $at = ([regex]::Match($body, '<StartBoundary>[^T]*T([\d:]+)')).Groups[1].Value
                Write-Step "每月 $day 日 $at"
            } elseif ($body -match '<ScheduleByDay>') {
                $iv = ([regex]::Match($body, '<DaysInterval>(\d+)</DaysInterval>')).Groups[1].Value
                $at = ([regex]::Match($body, '<StartBoundary>[^T]*T([\d:]+)')).Groups[1].Value
                Write-Step "每 $iv 天 $at"
            } elseif ($body -match '<ScheduleByWeek>') {
                $at = ([regex]::Match($body, '<StartBoundary>[^T]*T([\d:]+)')).Groups[1].Value
                $days = ([regex]::Matches($body, '<(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*/>') |
                         ForEach-Object { $_.Groups[1].Value }) -join ','
                Write-Step "每周 $days $at"
            } else {
                Write-Step '(未识别的触发器类型)'
            }
        }
        if ($xml -notmatch '<ScheduleByMonth>' -and -not $NoMonthly) {
            Write-Host '  [!] 未找到月度触发器（可能注册时回退为单触发器）' -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  [!] 读取触发器失败：$($_.Exception.Message)" -ForegroundColor Yellow
    }

    Write-Head '最近任务日志'
    $log = Join-Path $ScriptDir 'logs\task.log'
    if (Test-Path $log) {
        Get-Content $log -Encoding UTF8 -Tail 10 | ForEach-Object { Write-Step $_ }
    } else {
        Write-Step '(尚无 logs\task.log，任务可能还没跑过)'
    }

    Write-Head '最近结果 CSV'
    $csv = Get-ChildItem $ScriptDir -Filter 'result_*.csv' -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($csv) {
        $kb = [Math]::Round($csv.Length / 1KB, 1)
        Write-Step "$($csv.Name)  ($kb KB, $($csv.LastWriteTime))"
    } else {
        Write-Step '(尚无结果文件)'
    }
}

function Invoke-DryRun {
    Write-Head 'DryRun：只预览，不修改系统'
    if (-not (Test-Prerequisites)) {
        Write-Host "`n  环境自检未通过（DryRun 不中止，仅提示）" -ForegroundColor Yellow
    }

    Write-Head '将注册的计划任务'
    Write-Step "任务名   : $TaskName"
    if ($Weekly) {
        Write-Step "主触发   : 每周 $DayOfWeek $Time → $BatPath"
    } else {
        Write-Step "主触发   : 每日 $Time → $BatPath"
    }
    if (-not $NoMonthly) {
        Write-Step "月触发   : 每月 $MonthlyDay 日 $(Get-EffectiveMonthlyTime) → $MonthlyBatPath"
    } else {
        Write-Step "月触发   : （-NoMonthly，不注册）"
    }
    Write-Step "执行     : $BatPath"
    Write-Step "工作目录 : $ScriptDir"
    Write-Step "运行身份 : $env:USERDOMAIN\$env:USERNAME（仅登录时，无需密码）"
    Write-Step "超时限制 : 2 小时"

    Write-Head '将生成的 run_credit_check.bat 内容（每日）'
    (Build-BatContent -Mode 'daily') -split "`n" | ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }

    if (-not $NoMonthly) {
        Write-Head '将生成的 run_credit_check_monthly.bat 差异行（每月）'
        (Build-BatContent -Mode 'monthly') -split "`n" |
            Where-Object { $_ -match 'monthly|MODE' } |
            ForEach-Object { Write-Host "    $_" -ForegroundColor DarkGray }
    }

    Write-Head '已存在的任务'
    $t = Get-Task
    if ($t) {
        Write-Step "[!] 已存在：$($t.TaskName)（安装时会被覆盖）"
    } else {
        Write-Step '(无同名任务，安装将新建)'
    }
    Write-Host "`n  DryRun 结束，未做任何修改。" -ForegroundColor Green
    Write-Host "  确认无误后运行：.\install_schedule_task.ps1`n" -ForegroundColor Green
}

function Invoke-RunNow {
    Write-Head '立即运行一次（验证配置）'
    if (-not (Test-Prerequisites)) { throw '环境自检未通过，已中止' }

    # 必读：先按当前参数重建 .bat 再跑。
    # 否则 .bat 里可能是上次安装时的旧 webhook/名单，本次参数根本不生效
    # —— 实测过「预览说未指定 webhook，实际却去推旧地址」。
    if (Test-Path $BatPath) {
        Write-Step '[!] 已存在的 .bat 将按本次参数重建（避免跑旧配置）'
    }
    New-RunnerBat

    Write-Step "执行：$BatPath"
    Write-Host ''
    & cmd.exe /c "`"$BatPath`""
    $rc = $LASTEXITCODE
    Write-Host ''
    Write-Step "退出码：$rc  $(if ($rc -eq 0) { '(成功)' } else { '(失败，看 logs\task.log)' })"
}

# ---------------------------------------------------------------- 主入口

Write-Host "`n营业执照巡检 · 计划任务管理" -ForegroundColor Cyan
Write-Step "动作：$Action"

switch ($Action) {
    'Install'   { Invoke-Install }
    'Uninstall' { Invoke-Uninstall }
    'Status'    { Invoke-Status }
    'DryRun'    { Invoke-DryRun }
    'RunNow'    { Invoke-RunNow }
}

Write-Host ''
