Option Explicit

Dim shell, fso, baseDir, pythonExe, pythonConsoleExe, command, cleanupCommand, userEnv, processEnv, envName

Function WithoutPathEntry(pathValue, excludedSuffix)
    Dim part, normalized, cleaned
    cleaned = ""
    For Each part In Split(pathValue, ";")
        normalized = LCase(Replace(Trim(part), "/", "\"))
        Do While Len(normalized) > 3 And Right(normalized, 1) = "\"
            normalized = Left(normalized, Len(normalized) - 1)
        Loop
        If Right(normalized, Len(excludedSuffix)) <> LCase(excludedSuffix) Then
            If Len(Trim(part)) > 0 Then
                If Len(cleaned) > 0 Then cleaned = cleaned & ";"
                cleaned = cleaned & part
            End If
        End If
    Next
    WithoutPathEntry = cleaned
End Function

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
pythonExe = baseDir & "\.venv\Scripts\pythonw.exe"
pythonConsoleExe = baseDir & "\.venv\Scripts\python.exe"

If Not fso.FileExists(pythonExe) Then
    MsgBox "Virtual environment not found:" & vbCrLf & pythonExe, vbCritical, "Launch failed"
    WScript.Quit 1
End If

' TortoiseSVN ships an obsolete MSVCP140.dll in its bin directory. When that
' directory is inherited by pythonw, Windows may load it instead of the system
' runtime and terminate the launcher with 0xc0000005.
Set processEnv = shell.Environment("PROCESS")
processEnv("PATH") = WithoutPathEntry(processEnv("PATH"), "\tortoisesvn\bin")

' Only the previously registered launcher process tree is terminated. This
' prevents an older in-memory launcher and its browser drivers from running.
cleanupCommand = """" & pythonConsoleExe & """ -m ei_ui_smoke.execution_guard cleanup"
shell.CurrentDirectory = baseDir
shell.Run cleanupCommand, 0, True

' Read persisted user settings explicitly. Explorer may still have an old
' environment block after setx, which would otherwise disable auto-submit.
Set userEnv = shell.Environment("USER")
For Each envName In Array( _
    "ZENTAO_AUTO_SUBMIT", "ZENTAO_URL", "ZENTAO_USERNAME", _
    "ZENTAO_PASSWORD", "ZENTAO_PRODUCT", "ZENTAO_ASSIGNEE", _
    "ZENTAO_MODULE_FALLBACK", "ZENTAO_HEADLESS")
    If Len(Trim(userEnv(envName))) > 0 Then
        processEnv(envName) = userEnv(envName)
    End If
Next

command = """" & pythonExe & """ -m ei_ui_smoke.launcher"
shell.Run command, 1, False
