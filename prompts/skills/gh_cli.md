---
name: gh_cli
when: User asks about GitHub issues, pull requests, releases, repo state, or wants to do GitHub-shaped work
needs: [gh]
---

# GitHub CLI (gh)

The GitHub CLI works on Windows (winget install GitHub.cli) and is the
fastest path for GitHub-shaped work. Login once: `gh auth login`. After
that, common operations:

List my open issues across orgs:

```
local_shell:
gh issue list --assignee "@me" --state open --json number,title,repository,url
```

Recent PRs in this repo:

```
local_shell:
gh pr list --state all --limit 10 --json number,title,state,url,author
```

View a PR:

```
local_shell:
gh pr view 123 --json title,body,state,reviews,comments
```

Create an issue:

```
local_shell:
gh issue create --title "Quick title" --body "Body here" --label bug
```

This is a cross-platform CLI ClawHub features under "github" — same
contract, same usage pattern. Other ClawHub-listed tools that work on
Windows after `winget install` or `scoop install`:

- ffmpeg (already used by webcam_capture skill)
- gh (this skill)
- xurl (REST API client) — `winget install Apicurl.xurl`
- node — `winget install OpenJS.NodeJS`
- python — already installed (running PaLLi)

For tools without Windows packages (peekaboo, camsnap, apple-*) skip
on this host or use the equivalent Windows-native cmdlet (Get-Process
for processes, etc).
