Option Explicit

Dim shell, files, root, pythonw, launcher, pipeline, importer, installer, quote, command, sep
Set shell = CreateObject("WScript.Shell")
Set files = CreateObject("Scripting.FileSystemObject")

root = files.GetParentFolderName(WScript.ScriptFullName)
sep = Chr(92)
pythonw = files.BuildPath(root, ".venv" & sep & "Scripts" & sep & "pythonw.exe")
launcher = files.BuildPath(root, "launch_teacher.pyw")
pipeline = files.BuildPath(root, "src" & sep & "recognition_pipeline.py")
importer = files.BuildPath(root, "src" & sep & "recognition_import.py")
installer = files.BuildPath(root, "一键启动教师端.bat")
quote = Chr(34)

If files.FileExists(pythonw) And files.FileExists(launcher) And files.FileExists(pipeline) And files.FileExists(importer) Then
  command = quote & pythonw & quote & " " & quote & launcher & quote
  shell.Run command, 0, False
Else
  shell.Run quote & installer & quote, 1, False
End If
