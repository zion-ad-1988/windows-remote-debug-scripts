<#
.SYNOPSIS
维持蓝牙连接稳定，防止蓝牙驱动/设备掉线
.DESCRIPTION
1. 禁用蓝牙适配器电源管理自动关闭
2. 监控蓝牙系统服务状态
3. 定期检测并重连已配对蓝牙设备
4. 注册表禁用蓝牙选择性挂起
#>

param(
    [switch]$Elevated   # 由自动提权内部传入，避免重复提权
)

# ========== 自动提权（非管理员时经 UAC 重新以管理员身份启动） ==========
$isAdmin = (New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $isAdmin -and -not $Elevated) {
    $scriptPath = $PSCommandPath
    if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Path }
    if (-not $scriptPath) { $scriptPath = $MyInvocation.MyCommand.Definition }
    if (-not $scriptPath -or -not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        Write-Host "无法确定脚本自身路径，请右键以管理员身份运行 bluetooth.bat" -ForegroundColor Red
        exit 1
    }

    $shellExe = (Get-Process -Id $PID).Path
    if (-not $shellExe) { $shellExe = 'powershell.exe' }

    try {
        Write-Host "当前会话非管理员，正在请求管理员身份..." -ForegroundColor Yellow
        Start-Process -FilePath $shellExe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`" -Elevated"
    }
    catch {
        Write-Host "自动提权失败: $_" -ForegroundColor Red
        Write-Host "继续以当前权限尝试运行（电源管理等特权功能可能受限）..." -ForegroundColor Yellow
    }

    if ($isAdmin) {
        exit
    }
}
# ======================================================================

# ========== 配置项 ==========
$CheckInterval = 5       # 检测间隔（秒）
$AutoReconnect = $true    # 是否自动重连已配对设备
$TargetDeviceName = ""    # 指定设备名（空则监控所有已配对设备）
# ============================

# 设置控制台输出编码为 UTF-8
try {
    [System.Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  蓝牙连接保活脚本已启动" -ForegroundColor Cyan
Write-Host "  检测间隔: $CheckInterval 秒" -ForegroundColor Gray
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 控制台局部刷新状态
$script:lastStatusLen = 0
$script:statusActive = $false

function Clear-StatusLine {
    try {
        if ($script:statusActive) {
            $clearLen = [Math]::Max($script:lastStatusLen + 10, 80)
            $pad = ' ' * $clearLen
            Write-Host -NoNewline ("`r" + $pad + "`r")
            $script:statusActive = $false
            $script:lastStatusLen = 0
        }
    } catch {}
}

function Update-Status([string]$msg) {
    try {
        $pad = ""
        if ($script:lastStatusLen -gt $msg.Length) {
            $pad = ' ' * ($script:lastStatusLen - $msg.Length)
        }
        Write-Host -NoNewline ("`r" + $msg + $pad)
        $script:lastStatusLen = $msg.Length
        $script:statusActive = $true
    } catch {
        Write-Host -NoNewline "`r$msg"
    }
}

function Log-Message([string]$msg, [string]$color = 'White') {
    try {
        Clear-StatusLine
        Write-Host $msg -ForegroundColor $color
    } catch {
        Write-Host $msg -ForegroundColor $color
    }
}

#region 1. 禁用蓝牙适配器电源管理（防止系统自动关闭）
function Disable-BluetoothPowerManagement {
    try {
        $btAdapters = Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | Where-Object {
            $_.InstanceId -match '^(USB|PCI)\\'
        }
        
        if (-not $btAdapters) {
            Write-Host "[电源管理] 未找到活动的蓝牙适配器" -ForegroundColor Yellow
            return $false
        }
        
        foreach ($adapter in $btAdapters) {
            $devicePath = "HKLM:\SYSTEM\CurrentControlSet\Enum\$($adapter.InstanceId)\Device Parameters"
            
            if (Test-Path $devicePath) {
                Set-ItemProperty -Path $devicePath -Name "DeviceSelectiveSuspended" -Value 0 -Type DWord -Force | Out-Null
                Set-ItemProperty -Path $devicePath -Name "SelectiveSuspendEnabled" -Value 0 -Type DWord -Force | Out-Null
                Write-Host "[电源管理] 已禁用适配器省电: $($adapter.FriendlyName)" -ForegroundColor Green
            }
        }
        
        $globalPath = "HKLM:\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters"
        if (Test-Path $globalPath) {
            Set-ItemProperty -Path $globalPath -Name "DisableSelectiveSuspend" -Value 1 -Type DWord -Force | Out-Null
            Write-Host "[电源管理] 已全局禁用蓝牙选择性挂起" -ForegroundColor Green
        }
        
        return $true
    }
    catch {
        Write-Host "[电源管理] 禁用电源管理失败: $_" -ForegroundColor Yellow
        return $false
    }
}
#endregion

#region 2. 监控蓝牙服务
function Test-BluetoothService {
    $service = Get-Service -Name bthserv -ErrorAction SilentlyContinue
    if (-not $service) {
        Log-Message "[服务] 未找到蓝牙支持服务 (bthserv)" "Yellow"
        return $false
    }
    
    if ($service.Status -ne 'Running') {
        Log-Message "[服务] 蓝牙服务状态异常: $($service.Status)，正在重启..." "Yellow"
        try {
            Restart-Service -Name bthserv -Force
            Start-Sleep -Seconds 3
            Log-Message "[服务] 蓝牙服务已重启" "Green"
        }
        catch {
            Log-Message "[服务] 重启蓝牙服务失败: $_" "Red"
            return $false
        }
    }
    return $true
}
#endregion

#region 3. 检测并重连蓝牙设备
function Repair-BluetoothConnections {
    if (-not $AutoReconnect) { return }
    
    try {
        $pairedDevices = Get-PnpDevice -Class Bluetooth -Status OK -ErrorAction SilentlyContinue | Where-Object {
            $_.InstanceId -match '^BTHENUM\\' -and $_.FriendlyName
        }
        
        if (-not $pairedDevices) { return }
        
        foreach ($device in $pairedDevices) {
            if ($TargetDeviceName -and $device.FriendlyName -notmatch [regex]::Escape($TargetDeviceName)) {
                continue
            }
            
            $status = $device.Status
            $name = $device.FriendlyName
            
            if ($status -ne 'OK') {
                Log-Message "[设备] $name 连接异常 ($status)，尝试重连..." "Yellow"
                
                Disable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 2
                Enable-PnpDevice -InstanceId $device.InstanceId -Confirm:$false -ErrorAction SilentlyContinue
                Start-Sleep -Seconds 3
                
                $recheck = Get-PnpDevice -InstanceId $device.InstanceId -ErrorAction SilentlyContinue
                if ($recheck -and $recheck.Status -eq 'OK') {
                    Log-Message "[设备] $name 已重新连接" "Green"
                }
                else {
                    Log-Message "[设备] $name 重连失败" "Red"
                }
            }
        }
    }
    catch {
        Log-Message "[设备] 检测蓝牙设备失败: $_" "Yellow"
    }
}
#endregion

# ========== 初始化执行 ==========
$null = Disable-BluetoothPowerManagement
$null = Test-BluetoothService

Write-Host ""
Write-Host "开始循环监控，按 Ctrl+C 退出..." -ForegroundColor DarkYellow
Write-Host ""

# ========== 主循环 ==========
$checkCount = 0
while ($true) {
    $checkCount++
    $timestamp = Get-Date -Format "HH:mm:ss"
    
    $null = Test-BluetoothService
    Repair-BluetoothConnections
    
    # 检测正常时在固定状态行倒计时刷新
    for ($remaining = $CheckInterval; $remaining -ge 1; $remaining--) {
        Update-Status "[$timestamp] 正常监控中 (已检测 $checkCount 次，下次检测: ${remaining}s)"
        Start-Sleep -Seconds 1
    }
}
