---
name: app_launch
when: User asks me to open / launch / start an application
needs: [powershell]
---

# Launch a Windows application

By name (relies on PATH):

```
local_shell:
Start-Process notepad
```

By full path:

```
local_shell:
Start-Process "C:/Program Files/Some App/app.exe"
```

With args:

```
local_shell:
Start-Process notepad -ArgumentList "C:/path/to/file.txt"
```

To check if an app is already running first:

```
local_shell:
Get-Process notepad -ErrorAction SilentlyContinue
```

Common requests this covers:
- "open Discord"
- "launch Visual Studio"
- "start OBS"
- "fire up Steam"
