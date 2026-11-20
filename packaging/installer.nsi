Unicode true
RequestExecutionLevel user
; Draw sharp at any display scaling instead of being stretched by Windows.
ManifestDPIAware true
!include "MUI2.nsh"
!include "LogicLib.nsh"
; APP_NAME, VERSION, APP_DIR, RELEASE_DIR, APP_ICON and WELCOME_BITMAP come from scripts/build_windows.py.
Name "${APP_NAME}"
OutFile "${RELEASE_DIR}\${APP_NAME}-Setup.exe"
InstallDir "$LOCALAPPDATA\Programs\${APP_NAME}"
InstallDirRegKey HKCU "Software\${APP_NAME}" "InstallDir"
SetCompressor lzma
!define MUI_ICON "${APP_ICON}"
!define MUI_UNICON "${APP_ICON}"
!define MUI_ABORTWARNING
!define MUI_WELCOMEFINISHPAGE_BITMAP "${WELCOME_BITMAP}"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_RUN "$INSTDIR\${APP_NAME}.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Start ${APP_NAME}"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

!define UNINSTALL_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APP_NAME}"

!macro CheckAppClosed
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\${APP_NAME}Dictation") p.r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_ICONSTOP "Quit ${APP_NAME} from its tray menu before continuing. Finish any recording or queued takes first."
    Abort
  ${EndIf}
!macroend

Function .onInit
  !insertmacro CheckAppClosed
FunctionEnd

Function un.onInit
  !insertmacro CheckAppClosed
FunctionEnd

Section "${APP_NAME} (required)" SecApp
  SectionIn RO
  SetShellVarContext current
  SetOutPath "$INSTDIR"
  File /r "${APP_DIR}\*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\${APP_NAME}" "InstallDir" "$INSTDIR"
  CreateShortcut "$SMPROGRAMS\${APP_NAME}.lnk" "$INSTDIR\${APP_NAME}.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayName" "${APP_NAME}"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayIcon" "$INSTDIR\${APP_NAME}.exe"
  WriteRegStr HKCU "${UNINSTALL_KEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegStr HKCU "${UNINSTALL_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_KEY}" "NoRepair" 1
  ; An offline model pack beside the installer is optional. Without one, the
  ; app's welcome window downloads the models on first run.
  IfFileExists "$EXEDIR\models\parakeet-tdt-openvino\model-manifest.json" 0 models_done
    DetailPrint "Installing offline speech and cleanup models..."
    ExecWait '$\"$INSTDIR\${APP_NAME}.exe$\" --import-models $\"$EXEDIR\models$\"' $0
    ${If} $0 != 0
      MessageBox MB_ICONSTOP "Model installation failed. Keep this setup folder and run setup again."
      Abort
    ${EndIf}
  models_done:
SectionEnd

Section /o "Start with Windows" SecStartup
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_NAME}" '$\"$INSTDIR\${APP_NAME}.exe$\"'
SectionEnd

Section "Uninstall"
  SetShellVarContext current
  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APP_NAME}"
  DeleteRegKey HKCU "${UNINSTALL_KEY}"
  DeleteRegKey HKCU "Software\${APP_NAME}"
  Delete "$SMPROGRAMS\${APP_NAME}.lnk"
  ; Only installer-owned application files. User config/models/history are preserved.
  RMDir /r "$INSTDIR\_internal"
  Delete "$INSTDIR\${APP_NAME}.exe"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
