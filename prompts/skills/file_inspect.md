---
name: file_inspect
when: User asks "what's in this file", "show me X file", "look at the .env", or wants you to read a specific file in the workspace
needs: []
---

# File inspection

For files inside the workspace (anything under
`C:/Users/<you>/Desktop/claude/palli-local-refactor`), prefer the
dedicated `read_file` tier-1 tool — it's scoped to allowed roots and
returns the content directly:

```
read_file: <relative or absolute path>
```

For files outside the workspace, or when piping/grepping, use
`local_shell`:

```
local_shell:
Get-Content C:/path/to/file.txt -Tail 50
```

Search for a string across files:

```
local_shell:
Select-String -Path "C:/Users/<you>/Desktop/claude/palli-local-refactor/core/turn/*.py" -Pattern "decide_turn"
```

Listing a directory:

```
local_shell:
Get-ChildItem C:/Users/<you>/Desktop -Recurse -Filter "*.py" | Select-Object -First 20
```
