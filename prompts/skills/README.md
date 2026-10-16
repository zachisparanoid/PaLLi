# Skills

Each `.md` file in this directory describes a capability available on
the host machine. The decide LLM reads a compact summary of all skills
when picking how to act on a user request.

A skill is just a documented way to use the host shell. The LLM picks
`local_shell` as the tier-1 tool, then composes a command informed by
the relevant skill. Skills are not separate dispatchers — the user's
shell is the only execution surface.

Frontmatter format (YAML-shape, parsed loosely):

```
---
name: short_kebab_id
when: one-line description of when to use this skill
needs: [optional list of binaries that must be on PATH]
---

# Title

Short prose. Two or three example commands. PaLLi's monologue can pick
this up and use it without further reading.
```

Add a new skill by dropping a new `.md` file here. The loader is
permissive — missing fields fall back to defaults. Keep each skill
under ~30 lines.
