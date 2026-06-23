@echo off
REM Tongits bot launcher - uses .venv-omniparser (not Anaconda base)
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul
set "ROOT=%~dp0.."
set "VENV_PY=%ROOT%\.venv-omniparser\Scripts\python.exe"
set "SCRIPT=%~dp0main_bot_loop.py"
set "PROTO_BRIDGE_SCRIPT=%~dp0proto_status_bridge.py"
set "LOG_DIR=%~dp0omnioutput\runlogs"
set "PROTO_STATUS_FILE=%~dp0omnioutput\proto_status.json"

REM 实战默认参数（可在命令后继续追加参数覆盖）
set "DEFAULT_ARGS=--qwen-full --auto-play --auto-play-live"

REM 结算金币快模式（可被外部同名环境变量覆盖）
if not defined TONGITS_SETTLEMENT_COIN_POLL_SEC set "TONGITS_SETTLEMENT_COIN_POLL_SEC=0.8"
if not defined TONGITS_SETTLEMENT_COIN_RETRIES set "TONGITS_SETTLEMENT_COIN_RETRIES=2"
if not defined TONGITS_SETTLEMENT_COIN_TIMEOUT_SEC set "TONGITS_SETTLEMENT_COIN_TIMEOUT_SEC=4.6"
if not defined TONGITS_SETTLEMENT_PANEL_RETRIES set "TONGITS_SETTLEMENT_PANEL_RETRIES=2"
if not defined TONGITS_SETTLEMENT_PANEL_TIMEOUT_SEC set "TONGITS_SETTLEMENT_PANEL_TIMEOUT_SEC=4.8"
if not defined TONGITS_OVERLAY_VLM_TIMEOUT_SEC set "TONGITS_OVERLAY_VLM_TIMEOUT_SEC=4.8"
if not defined TONGITS_SETTLEMENT_LOCK_HOLD_SEC set "TONGITS_SETTLEMENT_LOCK_HOLD_SEC=3.0"
if not defined TONGITS_ABORT_IF_OVER_BUDGET set "TONGITS_ABORT_IF_OVER_BUDGET=1"
if not defined TONGITS_OVER_BUDGET_ABORT_SEC set "TONGITS_OVER_BUDGET_ABORT_SEC=0.5"
if not defined TONGITS_FIGHT_SKIP_AFTER_SETTLEMENT_SEC set "TONGITS_FIGHT_SKIP_AFTER_SETTLEMENT_SEC=3.0"
if not defined TONGITS_DUMP_MIN_CENTER_Y set "TONGITS_DUMP_MIN_CENTER_Y=760"
if not defined TONGITS_SETTLEMENT_BLOCK_FIGHT_CONFIRM_FRAMES set "TONGITS_SETTLEMENT_BLOCK_FIGHT_CONFIRM_FRAMES=2"
if not defined TONGITS_SETTLEMENT_BLOCK_FIGHT_REQUIRE_BORDER set "TONGITS_SETTLEMENT_BLOCK_FIGHT_REQUIRE_BORDER=1"
if not defined TONGITS_SETTLEMENT_UI_STRONG_FRAMES set "TONGITS_SETTLEMENT_UI_STRONG_FRAMES=2"
if not defined TONGITS_SETTLEMENT_POST_DUEL_CONTINUE_RATIO_MIN set "TONGITS_SETTLEMENT_POST_DUEL_CONTINUE_RATIO_MIN=0.09"
if not defined TONGITS_SETTLEMENT_POST_DUEL_DETAILS_RATIO_MIN set "TONGITS_SETTLEMENT_POST_DUEL_DETAILS_RATIO_MIN=0.06"
if not defined TONGITS_SETTLEMENT_POST_DUEL_TIMER_RATIO_MIN set "TONGITS_SETTLEMENT_POST_DUEL_TIMER_RATIO_MIN=0.01"
if not defined TONGITS_SETTLEMENT_STABILIZE_SEC set "TONGITS_SETTLEMENT_STABILIZE_SEC=0.0"
if not defined TONGITS_SETTLEMENT_SKIP_VLM_ON_STRONG_UI set "TONGITS_SETTLEMENT_SKIP_VLM_ON_STRONG_UI=1"
if not defined TONGITS_SETTLEMENT_VLM_FAILOPEN set "TONGITS_SETTLEMENT_VLM_FAILOPEN=0"
if not defined TONGITS_SETTLEMENT_RELEASE_ON_VLM_MISS_STREAK set "TONGITS_SETTLEMENT_RELEASE_ON_VLM_MISS_STREAK=4"
if not defined TONGITS_SETTLEMENT_CONFLICT_GRACE_SEC set "TONGITS_SETTLEMENT_CONFLICT_GRACE_SEC=3.0"
if not defined TONGITS_FIGHT_ULTRA_CONF_CHALLENGE_RATIO_MIN set "TONGITS_FIGHT_ULTRA_CONF_CHALLENGE_RATIO_MIN=0.30"
if not defined TONGITS_FIGHT_ULTRA_CONF_FOLD_RATIO_MIN set "TONGITS_FIGHT_ULTRA_CONF_FOLD_RATIO_MIN=0.30"
if not defined TONGITS_FIGHT_DEFAULT_ACTION_NO_CACHE set "TONGITS_FIGHT_DEFAULT_ACTION_NO_CACHE=fold"
if not defined TONGITS_COIN_USE_PROTO set "TONGITS_COIN_USE_PROTO=0"
if not defined TONGITS_COIN_USE_CDP set "TONGITS_COIN_USE_CDP=1"
if not defined TONGITS_CDP_PORT set "TONGITS_CDP_PORT=9222"
if not defined TONGITS_CDP_LAUNCH_CHROME set "TONGITS_CDP_LAUNCH_CHROME=1"
if not defined TONGITS_CDP_GAME_URL set "TONGITS_CDP_GAME_URL=https://www.herontest.xin/"
if not defined TONGITS_MY_NAME set "TONGITS_MY_NAME=victor"
if not defined TONGITS_CDP_DISCOVER set "TONGITS_CDP_DISCOVER=0"
if not defined TONGITS_CDP_SETTLE_FALLBACK_SEC set "TONGITS_CDP_SETTLE_FALLBACK_SEC=6.0"
if not defined TONGITS_SAVE_COIN_CROPS set "TONGITS_SAVE_COIN_CROPS=0"
if not defined TONGITS_BRIDGE_AUTO_INJECT set "TONGITS_BRIDGE_AUTO_INJECT=1"
if not defined TONGITS_PROTO_LOG_ENABLED set "TONGITS_PROTO_LOG_ENABLED=1"
if not defined TONGITS_PROTO_STATUS_POLL_SEC set "TONGITS_PROTO_STATUS_POLL_SEC=1.0"
if not defined TONGITS_PROTO_HEARTBEAT_SEC set "TONGITS_PROTO_HEARTBEAT_SEC=30.0"
if not defined TONGITS_PROTO_BRIDGE_ENABLED set "TONGITS_PROTO_BRIDGE_ENABLED=1"
if not defined TONGITS_PROTO_BRIDGE_PORT set "TONGITS_PROTO_BRIDGE_PORT=17888"
if not defined TONGITS_PROTO_STATUS_FILE set "TONGITS_PROTO_STATUS_FILE=%PROTO_STATUS_FILE%"
if not defined TONGITS_PROTO_MODE set "TONGITS_PROTO_MODE=bridge"

set "PYTHONUNBUFFERED=1"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if exist "%VENV_PY%" (
    echo [launcher] venv: %VENV_PY%
    if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
    for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%I"
    if not defined TS set "TS=fallback_%RANDOM%"
    set "LOG_FILE=%LOG_DIR%\main_bot_loop_!TS!.log"
    echo [launcher] log: !LOG_FILE!
    echo [launcher] fast-mode: poll=%TONGITS_SETTLEMENT_COIN_POLL_SEC% coin_retry=%TONGITS_SETTLEMENT_COIN_RETRIES% coin_timeout=%TONGITS_SETTLEMENT_COIN_TIMEOUT_SEC% panel_retry=%TONGITS_SETTLEMENT_PANEL_RETRIES% panel_timeout=%TONGITS_SETTLEMENT_PANEL_TIMEOUT_SEC% overlay_timeout=%TONGITS_OVERLAY_VLM_TIMEOUT_SEC% lock=%TONGITS_SETTLEMENT_LOCK_HOLD_SEC%
    echo [launcher] turn-guard: abort_if_over_budget=%TONGITS_ABORT_IF_OVER_BUDGET% threshold=%TONGITS_OVER_BUDGET_ABORT_SEC%s
    echo [launcher] anti-misclick: fight_skip_after_settlement=%TONGITS_FIGHT_SKIP_AFTER_SETTLEMENT_SEC%s dump_min_y=%TONGITS_DUMP_MIN_CENTER_Y%
    echo [launcher] settlement-gate: confirm_frames=%TONGITS_SETTLEMENT_BLOCK_FIGHT_CONFIRM_FRAMES% require_border=%TONGITS_SETTLEMENT_BLOCK_FIGHT_REQUIRE_BORDER% ui_strong_frames=%TONGITS_SETTLEMENT_UI_STRONG_FRAMES%
    echo [launcher] settlement-post-duel: c=%TONGITS_SETTLEMENT_POST_DUEL_CONTINUE_RATIO_MIN% d=%TONGITS_SETTLEMENT_POST_DUEL_DETAILS_RATIO_MIN% t=%TONGITS_SETTLEMENT_POST_DUEL_TIMER_RATIO_MIN%
    echo [launcher] settlement-stabilize: hold=%TONGITS_SETTLEMENT_STABILIZE_SEC%s
    echo [launcher] settlement-vlm-policy: skip_on_strong_ui=%TONGITS_SETTLEMENT_SKIP_VLM_ON_STRONG_UI%
    echo [launcher] settlement-vlm: failopen=%TONGITS_SETTLEMENT_VLM_FAILOPEN%
    echo [launcher] settlement-release: vlm_miss_streak=%TONGITS_SETTLEMENT_RELEASE_ON_VLM_MISS_STREAK%
    echo [launcher] settlement-priority: conflict_grace=%TONGITS_SETTLEMENT_CONFLICT_GRACE_SEC%s
    echo [launcher] settlement: visual=%TONGITS_SETTLEMENT_VISUAL% (0=API-only 3016, 1=OCR)
    echo [launcher] coin-strategy: cdp=%TONGITS_COIN_USE_CDP% bridge_port=%TONGITS_PROTO_BRIDGE_PORT% auto_inject=%TONGITS_BRIDGE_AUTO_INJECT%
    echo [launcher] 结算全自动: 在调试 Chrome ^(port %TONGITS_CDP_PORT%^) 内打牌即可，无需 F12 粘贴 snippet)
    echo [launcher] cdp-settlement: port=%TONGITS_CDP_PORT% launch_chrome=%TONGITS_CDP_LAUNCH_CHROME% my=%TONGITS_MY_NAME% discover=%TONGITS_CDP_DISCOVER%
    echo [launcher] fight-relax: ultra_conf=%TONGITS_FIGHT_ULTRA_CONF_CHALLENGE_RATIO_MIN%/%TONGITS_FIGHT_ULTRA_CONF_FOLD_RATIO_MIN% no_cache=%TONGITS_FIGHT_DEFAULT_ACTION_NO_CACHE%
    echo [launcher] proto-log: enabled=%TONGITS_PROTO_LOG_ENABLED% mode=%TONGITS_PROTO_MODE% poll=%TONGITS_PROTO_STATUS_POLL_SEC%s heartbeat=%TONGITS_PROTO_HEARTBEAT_SEC%s file=%TONGITS_PROTO_STATUS_FILE%
    if "%TONGITS_PROTO_BRIDGE_ENABLED%"=="1" (
        echo [launcher] proto-bridge: restarting on :%TONGITS_PROTO_BRIDGE_PORT% ...
        powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort %TONGITS_PROTO_BRIDGE_PORT% -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
        timeout /t 1 /nobreak >nul
        start "" /b "%VENV_PY%" "%PROTO_BRIDGE_SCRIPT%" --port %TONGITS_PROTO_BRIDGE_PORT% --output "%TONGITS_PROTO_STATUS_FILE%"
        echo [launcher] proto-bridge: http://127.0.0.1:%TONGITS_PROTO_BRIDGE_PORT%/proto/update
    )
    echo [launcher] args: %DEFAULT_ARGS% %*
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$OutputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false); [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); & '%VENV_PY%' '%SCRIPT%' %DEFAULT_ARGS% %* 2>&1 | Tee-Object -FilePath '!LOG_FILE!'"
    goto :done
)

echo [launcher] ERROR: .venv-omniparser not found
echo [launcher] Create: python -m venv .venv-omniparser
echo [launcher] Or run: .\scripts\setup_omniparser_venv.ps1
exit /b 1

:done
endlocal
