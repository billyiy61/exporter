' Dahua Exporter - launch without console window.
'
' Use this for a desktop shortcut: no black window appears.
' For troubleshooting run START.bat - it shows error messages.
'
Option Explicit

Dim fso, shell, here, pyw, cmd, pyDir, subF
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
pyw = ""

' Look for a windowless Python interpreter.
' pythonw.exe is preferred: it never opens a console.
If HasInPath("pythonw.exe") Then
    pyw = "pythonw"
ElseIf HasInPath("pyw.exe") Then
    pyw = "pyw"
End If

If pyw = "" Then
    ' No windowless interpreter found. Fall back to the regular launcher:
    ' a console window will appear, but the program will still start and
    ' any error message will be visible.
    shell.CurrentDirectory = here
    shell.Run """" & here & "\START.bat"" run", 1, False
    WScript.Quit 0
End If

shell.CurrentDirectory = here

cmd = pyw & " -c ""import sys; sys.path.insert(0, r'" & here & "\src'); " & _
      "from dahua_exporter.gui import main; main()"""

' 0 = hide the window, False = do not wait for the program to exit
shell.Run cmd, 0, False


' Returns True if the given executable exists in PATH.
' Uses FileSystemObject instead of spawning a process: faster and no
' console window flashes on screen.
Function HasInPath(exeName)
    Dim pathVar, folders, f, found
    HasInPath = False

    ' Step 1: walk the PATH entries
    pathVar = shell.ExpandEnvironmentStrings("%PATH%")
    folders = Split(pathVar, ";")

    For Each f In folders
        If Len(Trim(f)) > 0 Then
            On Error Resume Next
            If fso.FileExists(fso.BuildPath(Trim(f), exeName)) Then
                HasInPath = True
            End If
            On Error GoTo 0
        End If
        If HasInPath Then Exit Function
    Next

    ' Step 2: check the usual Python install locations, in case PATH is stale
    For Each pyDir In Array( _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Programs\Python"), _
        shell.ExpandEnvironmentStrings("%PROGRAMFILES%\Python"), _
        shell.ExpandEnvironmentStrings("%PROGRAMFILES(X86)%\Python"), _
        shell.ExpandEnvironmentStrings("%LOCALAPPDATA%\Microsoft\WindowsApps"))

        found = False
        If fso.FolderExists(pyDir) Then
            On Error Resume Next
            If InStr(pyDir, "WindowsApps") > 0 Then
                ' WindowsApps holds Store stubs - check the exact path only
                If fso.FileExists(pyDir & "\" & exeName) Then found = True
            Else
                For Each subF In fso.GetFolder(pyDir).SubFolders
                    If fso.FileExists(subF.Path & "\" & exeName) Then found = True
                Next
            End If
            On Error GoTo 0
        End If

        If found Then
            HasInPath = True
            Exit Function
        End If
    Next
End Function
