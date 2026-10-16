# Architectural-boundary tests

This directory holds the tests that protect the project's layering
discipline. They run as part of the normal `pytest` suite — there's no
opt-in flag — so a CI failure here means an architectural rule was
violated by the change you're trying to ship.

## Why this exists

PaLLi has a deliberate layered architecture:

```
bots/  →  core/  ←  ai/   (with voice.types as a shared type vocabulary)
                       ↘
voice/ →  core/      ai/voice (forbidden)
```

Surface adapters (`bots/`, `voice/`) depend on the platform-agnostic
core. Core depends on AI as a service. AI depends only on stable type
modules from voice. None of those arrows reverses.

The arrows reverse when no one's looking. That's what these tests
prevent.

## The rules

| # | File | Rule |
|---|---|---|
| 1 | `test_import_boundaries.py::test_core_does_not_import_bots` | core MUST NOT import bots (with grandfather list) |
| 2 | `test_import_boundaries.py::test_ai_does_not_import_bots` | ai MUST NOT import bots |
| 3 | `test_import_boundaries.py::test_ai_does_not_import_voice_runtime` | ai MUST NOT import voice EXCEPT voice.types |
| 4 | `test_import_boundaries.py::test_voice_does_not_import_bots` | voice MUST NOT import bots |
| 5 | `test_no_circular_imports.py::test_no_circular_imports` | no two-or-more module SCC in the import graph |
| 6 | `test_coupling_budget.py::test_no_module_exceeds_coupling_budget` | no module imports from > 25 in-repo modules |

The companion `test_delegation_boundaries.py` file pre-dates Phase 8a
and enforces a more specific rule: only `ai/delegation/voicer.py` may
import identity. That stays as-is.

## What to do when one fails

### Rule 1, 2, or 4 (cross-tree import)

A new file just imported across a forbidden boundary. Two paths:

- **Move the imported symbol.** If `core/foo.py` is suddenly importing
  `bots/discord/x.py`, the `x` is probably platform-agnostic and
  belongs in `core/` already. Move it; the import becomes legal.
- **Invert the dependency.** Sometimes the import direction itself is
  wrong. Make the surface depend on a callback or an interface defined
  in core, not the other way around.

### Rule 3 (ai → voice runtime)

You imported something from `voice/` other than `voice.types` from an
AI module. The fix is either to:

- Move the symbol you wanted into `voice.types` if it's actually a
  type definition.
- Pass the value through a callback or argument from the voice surface
  rather than reaching for it from AI.

### Rule 5 (circular import)

The failure message names every module in the cycle. Find the symbol
that's actually shared between the two sides and move it to a leaf
module both can depend on. A cycle of size 2 is usually a 5-line fix;
a cycle of size 3+ is a sign the responsibilities of the cycle's
modules need to be reconsidered.

### Rule 6 (coupling budget)

A file is now importing from 26+ in-repo modules. The fix is **never**
to raise `MAX_FAN_OUT` or add the file to the exemption list. The fix
is to extract a focused helper module that can shoulder some of the
imports, then have the over-budget module import only that helper.

If you genuinely have a new bootstrap-tier file that legitimately
wires many subsystems (a new entry point, a new central orchestrator),
adding it to `EXEMPT_BOOTSTRAP_MODULES` is appropriate — but state the
case in your commit message.

## Running locally

```bash
venv/Scripts/python.exe -m pytest tests/architecture/ -v
```

Whole suite runs in under 5 seconds.

## Grandfather lists

Two lists exist for legitimate-but-undesirable historical violations:

- `GRANDFATHERED_CORE_TO_BOTS` in `test_import_boundaries.py` — files
  that already import from `bots/` and would need a refactor to clean
  up. Each entry has a comment explaining why and pointing at the
  Phase that should fix it.
- `EXEMPT_BOOTSTRAP_MODULES` in `test_coupling_budget.py` — files that
  legitimately wire many subsystems at startup.

Both lists have meta-tests (`test_grandfather_list_does_not_grow_silently`,
`test_exemption_list_does_not_grow_silently`) that fail if an entry no
longer applies. So if you fix one of the grandfathered violations and
forget to delete the entry, the meta-test fires and tells you to clean
up the list.

This is the only way the lists shrink over time without manual
review effort.
