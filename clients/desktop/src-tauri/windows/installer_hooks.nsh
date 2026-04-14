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
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
!macroend
