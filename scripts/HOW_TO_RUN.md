# 如何运行脚本

## 问题：脚本打开记事本而不是执行

如果运行 `.\scripts\start.ps1` 后打开记事本而不是执行脚本，这是因为 PowerShell 执行策略或文件关联问题。

## 解决方案

### 方法 1: 使用完整路径和 & 操作符（推荐）

```powershell
& .\scripts\start.ps1
```

### 方法 2: 使用 PowerShell 执行

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

### 方法 3: 临时设置执行策略

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process
.\scripts\start.ps1
```

### 方法 4: 检查文件关联

如果总是打开记事本，可能需要修复文件关联：

```powershell
# 检查 .ps1 文件关联
Get-ItemProperty -Path "HKLM:\SOFTWARE\Classes\Microsoft.PowerShellScript.1\Shell\Open\Command"

# 应该指向 PowerShell，而不是记事本
```

## 推荐使用方式

**最简单的方式**：

```powershell
# 使用 & 操作符
& .\scripts\start.ps1
```

或者创建一个批处理文件 `start.bat`：

```batch
@echo off
powershell -ExecutionPolicy Bypass -File scripts\start.ps1
```

然后直接运行：
```powershell
.\start.bat
```
