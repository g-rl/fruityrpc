Option Explicit

Dim shell, fso, here, target, args, i, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

here = fso.GetParentFolderName(WScript.ScriptFullName)
target = fso.BuildPath(here, "FruityRPC.bat")

If Not fso.FileExists(target) Then
    MsgBox "FruityRPC.bat was not found next to this script." & vbCrLf & _
           "Keep every FruityRPC file in the same folder.", 16, "FruityRPC"
    WScript.Quit 1
End If

shell.Environment("PROCESS")("FRUITYRPC_SILENT") = "1"

args = ""
For i = 0 To WScript.Arguments.Count - 1
    args = args & " """ & WScript.Arguments(i) & """"
Next

command = """" & target & """" & args

shell.Run command, 0, False
