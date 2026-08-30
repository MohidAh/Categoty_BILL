; ─── BillBook NSIS installer hooks ───────────────────────────────────────────
; v8.18.2: kill any running billbook_sidecar.exe process tree BEFORE the
; installer copies files.
;
; WHY: the sidecar is a separate backend process the app launches at
; startup. Windows locks a running exe file, so if a sidecar process is
; still alive when the installer tries to overwrite it, NSIS aborts with
; "Error opening file for writing: ...\billbook_sidecar.exe" — the user
; then has to kill it in Task Manager and click Retry (the recurring
; v8.17.13 / v8.18.1 update complaint).
;
; The app's own updater already stops its sidecar (v8.17.14) and sweeps
; zombies by name (v8.18.2), but this hook covers the remaining paths:
;   - updates installed by OLD app versions whose updater never stopped
;     the sidecar (e.g. a v8.17.13 install updating itself)
;   - sidecar zombies leaked on machines that ran pre-v8.17.14 builds
;   - manual double-click installs of the setup.exe
; because the sweep runs inside the installer itself, right before the
; File commands that need the lock released.
;
; nsExec::Exec runs the command with NO console-window flash (unlike
; ExecWait) and waits for it to finish. taskkill exits with code 128 when
; no such process exists — harmless; the result is deliberately ignored.
!macro NSIS_HOOK_PREINSTALL
  nsExec::Exec 'taskkill /F /T /IM billbook_sidecar.exe'
!macroend
