---
name: clipboard
when: User wants me to copy text to or read text from their clipboard
needs: [powershell]
---

# Clipboard read / write (Windows)

Read what's on the clipboard:

```
local_shell:
Get-Clipboard
```

Write text to the clipboard:

```
local_shell:
Set-Clipboard -Value "the text to put on the clipboard"
```

Common pattern: "put X on my clipboard" — use Set-Clipboard with the
text the user named.

For long content that has special characters, prefer reading from a
file:

```
local_shell:
Get-Content C:/path/to/text.txt | Set-Clipboard
```
