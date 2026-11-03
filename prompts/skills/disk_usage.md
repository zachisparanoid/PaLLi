---
name: disk_usage
when: User asks about disk space, free space, biggest files, directory sizes
needs: [powershell]
---

# Disk space and usage (Windows)

Free space across drives:

```
local_shell:
Get-PSDrive -PSProvider FileSystem | Select-Object Name, @{n='UsedGB';e={[math]::Round(($_.Used/1GB),1)}}, @{n='FreeGB';e={[math]::Round(($_.Free/1GB),1)}} | Format-Table -AutoSize
```

Size of a directory:

```
local_shell:
(Get-ChildItem -Path C:/some/dir -Recurse -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum / 1GB
```

Top 10 biggest files in a directory tree:

```
local_shell:
Get-ChildItem -Path C:/some/dir -Recurse -File -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 10 | Format-Table FullName, @{n='SizeMB';e={[math]::Round($_.Length/1MB,1)}}
```
