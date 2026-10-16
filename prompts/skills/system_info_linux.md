---
name: system_info_linux
when: User asks about this machine's specs, OS version, uptime, memory, or which computer she is on
needs: [uname]
---

# System info (Linux)

OS, kernel, uptime:

```
local_shell:
uname -a && (cat /etc/os-release | head -3) && uptime
```

CPU and memory:

```
local_shell:
lscpu | grep -E 'Model name|^CPU\(s\)' ; free -h
```

Addresses (including the tailnet one):

```
local_shell:
hostname -I
```
