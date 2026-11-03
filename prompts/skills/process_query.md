---
name: process_query
when: User asks what's running, what's using a port, what's eating CPU/memory, or "kill that process"
needs: [powershell]
---

# Process and port queries (Windows)

List all processes:

```
local_shell:
Get-Process | Sort-Object CPU -Descending | Select-Object -First 20 | Format-Table -AutoSize Name, Id, CPU, WS
```

Find what's using a port (e.g. 8000):

```
local_shell:
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue; "$($_.LocalAddress):$($_.LocalPort) -> PID $($_.OwningProcess) ($($proc.ProcessName))" }
```

Find a process by name:

```
local_shell:
Get-Process *python* | Format-Table Id, ProcessName, StartTime, CPU
```

Kill a specific process by PID (destructive — needs confirmation per autonomy):

```
local_shell:
Stop-Process -Id <PID> -Force
```
