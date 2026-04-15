; Tauri NSIS：安装完成后把 bundle.resources 中的便携布局复制到安装根目录（与 dist_jachin_desktop 一致）。
; 热更新分发仍为完整 NSIS 安装包 + minisign，行为与官方 Tauri updater 一致。

!macro NSIS_HOOK_POSTINSTALL
  ; 便携布局：resources → 安装根（与 dist_jachin_desktop 一致；bin 内为 l3_node-<triple>.exe，勿放根目录 l3_node.exe）
  IfFileExists "$INSTDIR\resources\bin" 0 skip_bin
    CreateDirectory "$INSTDIR\bin"
    nsExec::ExecToLog 'cmd /c robocopy "$INSTDIR\resources\bin" "$INSTDIR\bin" /E /IS /IT /NFL /NDL /NJH /NJS /nc /ns /np'
    Pop $0
  skip_bin:
  IfFileExists "$INSTDIR\resources\config" 0 skip_config
    CreateDirectory "$INSTDIR\config"
    nsExec::ExecToLog 'cmd /c robocopy "$INSTDIR\resources\config" "$INSTDIR\config" /E /IS /IT /NFL /NDL /NJH /NJS /nc /ns /np'
    Pop $0
  skip_config:
  IfFileExists "$INSTDIR\resources\scripts" 0 skip_scripts
    CreateDirectory "$INSTDIR\scripts"
    nsExec::ExecToLog 'cmd /c robocopy "$INSTDIR\resources\scripts" "$INSTDIR\scripts" /E /IS /IT /NFL /NDL /NJH /NJS /nc /ns /np'
    Pop $0
  skip_scripts:
  IfFileExists "$INSTDIR\resources\logs" 0 skip_logs
    CreateDirectory "$INSTDIR\logs"
    nsExec::ExecToLog 'cmd /c robocopy "$INSTDIR\resources\logs" "$INSTDIR\logs" /E /IS /IT /NFL /NDL /NJH /NJS /nc /ns /np'
    Pop $0
  skip_logs:
  IfFileExists "$INSTDIR\resources\runtime" 0 skip_runtime
    CreateDirectory "$INSTDIR\runtime"
    nsExec::ExecToLog 'cmd /c robocopy "$INSTDIR\resources\runtime" "$INSTDIR\runtime" /E /IS /IT /NFL /NDL /NJH /NJS /nc /ns /np'
    Pop $0
  skip_runtime:
  IfFileExists "$INSTDIR\resources\.env.example" 0 +2
    CopyFiles /SILENT "$INSTDIR\resources\.env.example" "$INSTDIR\.env.example"
  ; .env：与 dist 同步打包 resources\.env（prepare 从 .env.example 生成）；首次安装若尚无用户 .env 则落盘
  IfFileExists "$INSTDIR\.env" skip_dotenv
  IfFileExists "$INSTDIR\resources\.env" copy_dotenv_from_res
  IfFileExists "$INSTDIR\.env.example" copy_dotenv_from_ex
    Goto skip_dotenv
copy_dotenv_from_res:
  CopyFiles /SILENT "$INSTDIR\resources\.env" "$INSTDIR\.env"
  Goto skip_dotenv
copy_dotenv_from_ex:
  CopyFiles /SILENT "$INSTDIR\.env.example" "$INSTDIR\.env"
skip_dotenv:
  IfFileExists "$INSTDIR\resources\.env.sea.example" 0 +2
    CopyFiles /SILENT "$INSTDIR\resources\.env.sea.example" "$INSTDIR\.env.sea.example"
  IfFileExists "$INSTDIR\resources\README_DEPLOY.md" 0 +2
    CopyFiles /SILENT "$INSTDIR\resources\README_DEPLOY.md" "$INSTDIR\README_DEPLOY.md"
  IfFileExists "$INSTDIR\resources\run_l3.bat" 0 +2
    CopyFiles /SILENT "$INSTDIR\resources\run_l3.bat" "$INSTDIR\run_l3.bat"
  IfFileExists "$INSTDIR\resources\run_l3_standalone.bat" 0 +2
    CopyFiles /SILENT "$INSTDIR\resources\run_l3_standalone.bat" "$INSTDIR\run_l3_standalone.bat"
!macroend

!macro NSIS_HOOK_PREINSTALL
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; CheckIfAppIsRunning 只结束主程序 jachin-desktop.exe，不会结束 L3 侧车（externalBin → l3_node-<triple>.exe）。
  ; 侧车若仍占用 $INSTDIR\bin\*.exe，卸载无法删文件，且 run_l3 弹出的控制台可能仍挂在 cmd 子树上。
  ; 忽略 taskkill 退出码（未运行时返回非 0）。
  DetailPrint "Stopping L3 sidecar (l3_node-*.exe) if running..."
  nsExec::ExecToLog 'cmd /c taskkill /F /IM l3_node-x86_64-pc-windows-msvc.exe /T >nul 2>&1 & taskkill /F /IM l3_node-aarch64-pc-windows-msvc.exe /T >nul 2>&1 & exit /b 0'
  Pop $0
  Sleep 400
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  ; POSTINSTALL 中 robocopy 写入的 $INSTDIR\bin 等不在 NSIS 安装清单内，默认卸载不会删侧车 exe。
  ; 侧车仅用于本应用，卸载时应一并移除。
  IfFileExists "$INSTDIR\bin" 0 skip_rm_l3bin
    DetailPrint "Removing L3 bin directory (sidecar not tracked by NSIS)..."
    RMDir /r "$INSTDIR\bin"
  skip_rm_l3bin:
!macroend
