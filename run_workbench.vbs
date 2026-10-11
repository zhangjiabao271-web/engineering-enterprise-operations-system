' Primary writable entry; each startup backs up the selected ledger and attachments.
Option Explicit
Dim shell, files, root, python, command
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")
root = files.GetParentFolderName(WScript.ScriptFullName)
python = files.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not files.FileExists(python) Then
  MsgBox "Python environment is missing. See docs/desktop-workbench.md.", 16, "Engineering Workbench"
  WScript.Quit 1
End If
shell.CurrentDirectory = root
command = Chr(34) & python & Chr(34) & " -B -X utf8 " & Chr(34) & files.BuildPath(root, "web_workbench\desktop.py") & Chr(34) & " --allow-write"
shell.Run command, 0, False
