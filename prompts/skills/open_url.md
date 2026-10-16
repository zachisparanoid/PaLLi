---
name: open_url
when: User asks PaLLi to open a website in their browser, or "pull up X" / "go to Y"
needs: [powershell or cmd]
---

# Open a URL in the user's default browser

```
local_shell:
Start-Process "https://example.com"
```

Or via cmd:

```
local_shell:
cmd: start "" "https://example.com"
```

This opens the URL in whatever the user's default browser is. It does
NOT bring back the page contents — for that, use `url_browse` (the
tier-1 tool that fetches + summarizes the page server-side).

Use `local_shell` open_url when:
- "pull up the github repo for X"
- "open my discord"
- "go to twitter"

Use `url_browse` when:
- "what does this page say"
- "summarize that link"
- "fetch the README at that URL"
