@echo off
chcp 65001 >nul
REM 将 HR 插件按新方案部署到 jachin-system / 本地工作区
REM 推荐使用项目根目录的 install.py 进行一键安装（含 MCP 注册、Wasm 编译等）
REM 用法: copy_to_jachin.bat [path_to_jachin-system]
REM 或设置环境变量 JACHIN_SYSTEM_PATH

set JACHIN_PATH=%1
if "%JACHIN_PATH%"=="" set JACHIN_PATH=%JACHIN_SYSTEM_PATH%
set PLUGIN_ROOT=%~dp0..

REM 1. HR 规则 → ~/.jachin/workspace/hr_rules/
set WS=%USERPROFILE%\.jachin\workspace\hr_rules
if not exist "%WS%" mkdir "%WS%"
xcopy /Y "%PLUGIN_ROOT%\1-config-template\hr_rules\*" "%WS%\"
echo [1] HR 规则已复制到 %WS%

REM 2. 若有 jachin-system，复制 SKILL.md
if not "%JACHIN_PATH%"=="" (
    set SKILL_DST=%JACHIN_PATH%\skills_repo\hr-recruiter
    if not exist "%SKILL_DST%" mkdir "%SKILL_DST%"
    copy /Y "%PLUGIN_ROOT%\4-track-b-skill\SKILL.md" "%SKILL_DST%\"
    echo [2] SKILL.md 已复制到 %SKILL_DST%
) else (
    echo [2] 跳过 SKILL.md（未指定 JACHIN_PATH，仅部署本地工作区）
)

echo 部署完成。
