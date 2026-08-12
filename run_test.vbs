Option Explicit

Dim shell, fso, baseDir, pythonExe, command, userEnv, processEnv, envName
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = baseDir & "\.venv\Scripts\pythonw.exe"

If Not fso.FileExists(pythonExe) Then
    MsgBox "Virtual environment not found:" & vbCrLf & pythonExe, vbCritical, "Launch failed"
    WScript.Quit 1
End If

' Read persisted user settings explicitly. Explorer may still have an old
' environment block after setx, which would otherwise disable auto-submit.
Set userEnv = shell.Environment("USER")
Set processEnv = shell.Environment("PROCESS")
For Each envName In Array( _
    "ZENTAO_AUTO_SUBMIT", "ZENTAO_URL", "ZENTAO_USERNAME", _
    "ZENTAO_PASSWORD", "ZENTAO_PRODUCT", "ZENTAO_ASSIGNEE", _
    "ZENTAO_MODULE_FALLBACK", "ZENTAO_HEADLESS")
    If Len(Trim(userEnv(envName))) > 0 Then
        processEnv(envName) = userEnv(envName)
    End If
Next

command = """" & pythonExe & """ -m ei_ui_smoke.launcher"
shell.CurrentDirectory = baseDir
shell.Run command, 1, False
