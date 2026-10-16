---
name: system_info
when: User asks about machine specs, OS version, network state, what computer they're on
needs: [powershell]
---

# System info

Quick OS + hardware overview:

```
local_shell:
Get-ComputerInfo | Select-Object CsName, OsName, OsVersion, OsArchitecture, CsTotalPhysicalMemory, CsProcessors | Format-List
```

Network adapters and their IPs:

```
local_shell:
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.AddressState -eq 'Preferred'} | Select-Object InterfaceAlias, IPAddress | Format-Table -AutoSize
```

Currently logged-in user:

```
local_shell: whoami
```

Hostname:

```
local_shell: hostname
```
