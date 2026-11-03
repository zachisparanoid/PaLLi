---
name: network_check
when: User asks about network connectivity, "is X site up", ping a host, check DNS
needs: [powershell]
---

# Network connectivity checks

Ping a host:

```
local_shell:
Test-Connection -ComputerName example.com -Count 4
```

Quick reachability test (faster than ping, supports ports):

```
local_shell:
Test-NetConnection -ComputerName example.com -Port 443
```

DNS lookup:

```
local_shell:
Resolve-DnsName example.com
```

Public IP:

```
local_shell:
(Invoke-RestMethod -Uri "https://api.ipify.org?format=json").ip
```

Common patterns:
- "is github up" → Test-NetConnection github.com -Port 443
- "what's my IP" → public IP query
- "can you reach the cellar sensor" → Test-Connection
