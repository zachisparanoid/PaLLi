---
name: process_query_linux
when: User asks what's running, what's using a port, what's eating CPU/memory, or wants a process stopped (on this Linux host)
needs: [ps]
---

# Processes and ports (Linux)

Top CPU / memory consumers:

```
local_shell:
ps -eo pid,pcpu,pmem,etime,cmd --sort=-pcpu | head -12
```

What is listening on which port:

```
local_shell:
ss -ltnp
```

Find a process by name, then stop it gently (TERM before KILL; never `-9` a process you don't recognize):

```
local_shell:
pgrep -af 'name-fragment' && kill -TERM <pid>
```
