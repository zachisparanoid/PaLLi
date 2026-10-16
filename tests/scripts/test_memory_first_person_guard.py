"""Batch 5, Task 10 (memory that picks, 2026-09-28): the guard on a rewritten
memory line.

Her memory of them is being rewritten into her own voice — by an LLM, one
batch per reflection and once over the whole file. The wording is the
model's; the FACTS are not. ``guard(old, new)`` compares the multiset of
guarded tokens between the two lines — ISO dates, ``[8-hex]`` ids, paths
(POSIX, Windows drive and UNC) and file names, URLs, emails, numbers (money
and percent tokens too), double-quoted spans and backtick code spans — and
requires the leading ``- `` and the leading ``[YYYY-MM-DD]`` stamps
unchanged and a new body with at least one word. Names (fix round 1): every
capitalized word of the OLD body, sentence openers included, must survive as
a whole word in the new body (case-insensitive) — an opener of four letters
or more by its four-letter stem, ruling R58, so "Wants…" → "They want…"
passes; a capitalized word the NEW body adds mid-sentence is refused. A rewrite that fails is kept as written,
and the reason names only token KINDS and COUNTS, never a value.

The first test is the brief's, verbatim. Every value here is made up.
"""
from __future__ import annotations

import pytest

from core.memory_first_person import guard_failure
from scripts.memory_first_person import guard


def test_guard_keeps_facts_byte_exact():
    old = "- [2026-09-12] User's phone number is in ~/.palli/owner_contact.json; explicitly invited PaLLi to call anytime (2026-08-01)."
    ok = "- [2026-09-12] Their number is in ~/.palli/owner_contact.json; they told me to call anytime (2026-08-01)."
    bad = "- [2026-09-12] Their number is in ~/.palli/contact.json; they told me to call anytime (2026-08-02)."
    assert guard(old, ok) is True and guard(old, bad) is False


# --- the token classes ------------------------------------------------------

def test_dates_stay_byte_exact():
    old = "- User moved house on 2026-03-04."
    assert guard(old, "- They moved house on 2026-03-04.") is True
    assert guard(old, "- They moved house on 2026-03-05.") is False
    assert guard(old, "- They moved house in March.") is False


def test_bracketed_ids_stay_byte_exact():
    old = "- User asked PaLLi to keep quest [abc12345] open."
    assert guard(old, "- They asked me to keep quest [abc12345] open.") is True
    assert guard(old, "- They asked me to keep quest [abc12346] open.") is False
    assert guard(old, "- They asked me to keep quest abc12345 open.") is False


@pytest.mark.parametrize("path", ["~/notes/today.md", "/etc/hosts", "./run.sh"])
def test_paths_stay_byte_exact(path):
    old = f"- User keeps the list in {path} for PaLLi."
    assert guard(old, f"- They keep the list in {path} for me.") is True
    assert guard(old, f"- They keep the list in {path}x for me.") is False
    assert guard(old, "- They keep the list for me.") is False


def test_windows_drive_paths_stay_byte_exact():
    old = r"- User keeps notes in C:\Users\pat\Documents\notes.txt for PaLLi."
    assert guard(old, r"- They keep notes in C:\Users\pat\Documents\notes.txt for me.") is True
    assert guard(old, r"- They keep notes in C:\Users\sam\Documents\notes.txt for me.") is False
    assert guard(old, "- They keep notes in a text file for me.") is False


def test_unc_paths_stay_byte_exact():
    old = r"- User's builds land on \\nas\builder; PaLLi may read them."
    assert guard(old, r"- Their builds land on \\nas\builder; I may read them.") is True
    assert guard(old, r"- Their builds land on \\nas\build; I may read them.") is False
    assert guard(old, r"- Their builds land on \\nas2\builder; I may read them.") is False


def test_a_drive_path_ending_in_a_backslash_is_one_token():
    old = r"- User keeps the LLC papers under Z:\palli\llc\ for PaLLi to file."
    assert guard(old, r"- They keep the LLC papers under Z:\palli\llc\ for me to file.") is True
    assert guard(old, r"- They keep the LLC papers under Z:\palli\ for me to file.") is False


def test_file_names_stay_byte_exact():
    old = "- User edits palli.yaml by hand."
    assert guard(old, "- They edit palli.yaml by hand.") is True
    assert guard(old, "- They edit palli.yml by hand.") is False


def test_money_and_percent_tokens_stay_byte_exact():
    old = "- User earns $70k and saves 12% of it."
    assert guard(old, "- They earn $70k and save 12% of it.") is True
    assert guard(old, "- They earn $80k and save 12% of it.") is False
    assert guard(old, "- They earn $70k and save 12 percent of it.") is False


def test_a_changed_number_fails():
    old = "- User runs 5 km on Mondays."
    assert guard(old, "- They run 5 km on Mondays.") is True
    assert guard(old, "- They run 6 km on Mondays.") is False


def test_urls_stay_byte_exact_and_a_trailing_period_is_not_part_of_one():
    old = "- User keeps notes at https://example.com/notes?tab=1."
    assert guard(old, "- They keep notes at https://example.com/notes?tab=1.") is True
    assert guard(old, "- They keep notes at https://example.com/notes?tab=2.") is False


def test_emails_stay_byte_exact():
    old = "- User's work address is sam@example.com."
    assert guard(old, "- Their work address is sam@example.com.") is True
    assert guard(old, "- Their work address is sam@example.org.") is False


def test_straight_quoted_spans_stay_byte_exact():
    old = '- User said "call me Sam" on the first day.'
    assert guard(old, '- They said "call me Sam" on the first day.') is True
    assert guard(old, '- They said "call me sam" on the first day.') is False
    assert guard(old, "- They said to call them Sam on the first day.") is False


def test_curly_quoted_spans_stay_byte_exact():
    old = "- User calls the cat “Mochi the Menace”."
    assert guard(old, "- They call the cat “Mochi the Menace”.") is True
    assert guard(old, "- They call the cat “Mochi the menace”.") is False


def test_backtick_code_spans_stay_byte_exact():
    old = "- User tracks spend with `ccusage`; PaLLi may run it."
    assert guard(old, "- They track spend with `ccusage`; I may run it.") is True
    assert guard(old, "- They track spend with `ocusage`; I may run it.") is False
    assert guard(old, "- They track spend with ccusage; I may run it.") is False


# --- names (fix round 1, IMPORTANT 2) -------------------------------------------

def test_a_name_kept_passes_and_a_name_dropped_fails():
    old = "- User works with Priya on Atlas."
    assert guard(old, "- They work with Priya on Atlas.") is True
    assert guard(old, "- They work on Atlas.") is False
    assert guard(old, "- They work with Priya on a project.") is False


def test_a_name_after_a_period_is_still_guarded():
    old = "- User sees Dr. Patel on Fridays."
    assert guard(old, "- They see Dr. Patel on Fridays.") is True
    assert guard(old, "- They see Dr. Jones on Fridays.") is False
    old2 = "- User finished the audit. Northwind sent the letter to PaLLi."
    assert guard(old2, "- They finished the audit. Northwind sent the letter to me.") is True
    assert guard(old2, "- They finished the audit. Contoso sent the letter to me.") is False


def test_an_opening_word_that_survives_in_lower_case_passes():
    old = "- Decided to open-source PaLLi and run a public release week for it."
    assert guard(old, "- They decided to open-source me and run a public release week for it.") is True
    old2 = "- User dropped the meditation habit. Cited an inconsistent schedule."
    assert guard(old2, "- They dropped the meditation habit; they cited an inconsistent schedule.") is True
    assert guard(old2, "- They dropped the meditation habit; their schedule was inconsistent.") is False


def test_r58_an_opener_of_four_letters_or_more_survives_by_its_stem():
    assert guard("- Wants a quieter Sunday morning.", "- They want a quieter Sunday morning.") is True
    assert guard("- Prefers tea in the evening.", "- They prefer tea in the evening.") is True
    assert guard("- Decided to open-source PaLLi and run a public release week.",
                 "- They decided to open-source me and run a public release week.") is True
    assert guard("- User finished the audit. Northwind sent the letter to PaLLi.",
                 "- They finished the audit. Contoso sent the letter to me.") is False
    assert guard("- User sees Dr. Patel on Fridays.", "- They see Dr. Jones on Fridays.") is False


def test_r58_a_short_opener_and_a_mid_sentence_word_keep_the_whole_word_rule():
    assert guard("- Has a cat named Mochi.", "- They has a cat named Mochi.") is True     # 3 letters: whole word
    assert guard("- Has a cat named Mochi.", "- They have a cat named Mochi.") is False
    assert guard("- Ran 5 km on Sunday.", "- They run 5 km on Sunday.") is False
    assert guard("- User prefers Rust for tools.", "- They prefer Rust for tools.") is True
    assert guard("- User prefers Rust for tools.", "- They prefer Rusty for tools.") is False  # mid-sentence: whole word
    assert guard("- User calls the cat Mochi. Wants another.", "- They call the cat Mochi; they want another.") is True
    assert guard("- User calls the cat Mochi. Wants another.", "- They call the cat Mochis; they want another.") is False


def test_a_capitalized_word_the_rewrite_adds_mid_sentence_fails_but_an_added_opener_passes():
    old = "- User likes tea."
    assert guard(old, "- They like Earl Grey tea.") is False
    assert guard(old, "- They like tea. Especially in the evening.") is True


@pytest.mark.parametrize("line", [
    "- [2026-09-12] User's phone number is in ~/.palli/owner_contact.json; explicitly invited PaLLi to call anytime (2026-08-01).",
    "- User switched to macOS and keeps notes on an iPhone.",
    "- iPhone backups run nightly; PaLLi checks them.",
    "- User's handle is patIsCareful on Discord.",
    "- Wants a quieter Sunday. Cited Northwind and Dr. Patel.",
])
def test_the_guard_accepts_an_unchanged_line(line):
    assert guard_failure(line, line) is None


def test_a_camel_case_word_is_one_name():
    old = "- User switched to macOS and keeps notes on an iPhone."
    assert guard(old, "- They switched to macOS and keep notes on an iPhone.") is True
    assert guard(old, "- They switched to macOS and keep notes on an iPad.") is False
    assert guard(old, "- They switched to Linux and keep notes on an iPhone.") is False
    assert guard("- iPhone backups run nightly for PaLLi.", "- Their iPhone backups run nightly for me.") is True


def test_old_names_must_survive_case_insensitively_as_whole_words():
    old = "- User has a cat. Mochi is her name."
    assert guard(old, "- They have a cat. Mochi is her name.") is True
    assert guard(old, "- They have a cat; mochi is her name.") is True      # case-insensitive
    assert guard(old, "- They have a cat named after a dessert.") is False  # Mochi gone
    assert guard(old, "- They have a cat; Mochis is her name.") is False    # not a whole word


# --- the frame: the dash, the stamps, a body with a word ----------------------

def test_the_leading_dash_and_stamps_must_be_unchanged():
    old = "- [2026-09-12] User likes tea."
    assert guard(old, "- [2026-09-12] They like tea.") is True
    assert guard(old, "[2026-09-12] They like tea.") is False       # no "- "
    assert guard(old, "- [2026-09-13] They like tea.") is False     # the stamp changed
    assert guard(old, "- They like tea.") is False                  # the stamp dropped
    assert guard(old, "- [2026-09-12] [2026-09-12] They like tea.") is False  # a stamp added


def test_double_stamps_are_kept_as_a_pair():
    old = "- [2026-08-30] [2026-08-30] User likes tea."
    assert guard(old, "- [2026-08-30] [2026-08-30] They like tea.") is True
    assert guard(old, "- [2026-08-30] They like tea.") is False


def test_an_apostrophe_inside_a_word_is_not_a_quote():
    old = "- User would rather PaLLi didn't call after 9pm."
    assert guard(old, "- They'd rather I don't call after 9pm.") is True
    old2 = '- User\'s cat is "Mochi".'
    assert guard(old2, '- Their cat is "Mochi".') is True


def test_an_empty_or_wordless_new_line_fails():
    old = "- [2026-09-12] User likes tea."
    assert guard(old, "") is False
    assert guard(old, "- ") is False
    assert guard(old, "- [2026-09-12]") is False
    assert guard(old, "- [2026-09-12]   ") is False
    assert guard(old, "- [2026-09-12] .") is False       # no word at all (fix round 1, M1)
    assert guard(old, "- [2026-09-12] 42") is False


# --- the reason names kinds and counts, never a value (fix round 1, IMPORTANT 4)

def test_the_reason_carries_kinds_and_counts_only():
    old = "- [2026-09-12] User's phone number is in ~/.palli/owner_contact.json; explicitly invited PaLLi to call anytime (2026-08-01)."
    bad = "- [2026-09-12] Their number is in ~/.palli/contact.json; they told me to call anytime (2026-08-02)."
    reason = guard_failure(old, bad)
    assert reason == "the facts changed — missing: 1 × date, 1 × path; added: 1 × date, 1 × path"
    for value in ("contact.json", "2026-08-02", "2026-08-01", ".palli"):
        assert value not in reason
    assert guard_failure(old, "- [2026-09-12] Their number is in ~/.palli/owner_contact.json; they told me to call anytime (2026-08-01).") is None
    # "Jones" follows "Dr." — a sentence opener by position, so it is not counted as
    # added; the refusal stands on the missing "Patel" (never named in the reason).
    assert guard_failure("- User sees Dr. Patel.", "- They see Dr. Jones.") == "the facts changed — missing: 1 × name"
    assert guard_failure("- User likes tea.", "- They like Earl Grey tea.") == "the facts changed — added: 2 × name"
    assert guard_failure("- [2026-09-12] User likes tea.", "- [2026-09-13] They like tea.") == "the leading date stamps changed"
    assert guard_failure("- User likes tea.", "- .") == "the new line has no words"
