---
name: git_inspect
when: User asks about git state, recent commits, what changed, what branch they're on
needs: [git]
---

# Git read operations

Working tree status:

```
local_shell: git status
```

Recent commits (concise):

```
local_shell: git log --oneline -10
```

What's changed in the working tree:

```
local_shell: git diff --stat
local_shell: git diff
```

What branches exist locally:

```
local_shell: git branch -a
```

What's about to be committed:

```
local_shell: git diff --cached
```

Git WRITE operations (commit, push, reset, branch switch) are
destructive — only run them when the user explicitly authorizes the
write, and report what you did honestly.
