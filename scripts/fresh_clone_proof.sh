#!/usr/bin/env bash
#
# The proof that a stranger's fresh clone works (stranger-proofing week 1,
# task 5).
#
# Nothing in the test suite can catch this class of failure, because every unit
# test runs inside a checkout that is already configured. This runs the first
# five minutes of somebody else's life with PaLLi: clone, install, copy the
# example config, ask her to check herself.
#
# Five runs, because the first-run contract has five halves and each one has
# been broken at least once:
#
#   run 1  a complete config       -> exit 0, "Preflight passed."
#   run 2  the shipped .env.template, untouched
#                                  -> exit 0, "Preflight passed.", a line saying
#                                     the Discord door is closed for this boot,
#                                     AND the brain line naming the dashboard —
#                                     the shipped brain needs a key and ships
#                                     without one, so this IS setup mode
#   run 3  a genuinely broken setting (a wide bind whose allowed-networks list
#          parses to nothing)      -> exit 2, a NUMBERED punch list
#   run 4  a Telegram-only stranger: a Telegram token, no Discord token
#                                  -> exit 0, "Preflight passed.", the Discord
#                                     door named as shut, and NOTHING said
#                                     about Telegram — which is also the proof
#                                     that the install carried
#                                     python-telegram-bot
#   run 5  the other half: a brain that needs no key at all (the template
#          repointed at a local model server)
#                                  -> exit 0, "Preflight passed.", and NOTHING
#                                     about a missing brain — someone running
#                                     their own server is already done
#   (was: a provider that needs a key, with NO key at all (the template +
#          LLM_PROVIDER=deepseek)  -> exit 0, "Preflight passed.", and the
#                                     setup-mode line naming the address where
#                                     a brain can be added
#
# Run 2 is the one that regressed silently before week 1: a fresh clone failed
# validation and exited 0, so a stranger's first `python main.py` looked like it
# had worked and then did nothing. Week 1 made the missing Discord token a
# blocker and run 2 demanded exit 2. The owner's ruling of 2026-09-10 reverses
# that: the dashboard is a stranger's first door, the shipped preset needs no
# key, so the UNTOUCHED template must boot — saying out loud which door is shut.
# Run 3 keeps the other half honest: something really broken still refuses,
# loudly and in order.
#
# Run 2 is the first door (2026-09-17), and since 2026-09-18 it is the SHIPPED
# case: the template names a brain that needs a key and ships the key blank, so
# an untouched clone lands on the door. It used to name a local model server,
# which needs no key — she booted "ready" and a stranger without that server
# got a timeout on every message and never saw the key step at all. Run 5 now
# holds the other half honest: a brain that genuinely needs no key still boots
# clean, with nothing said about a missing one.
#
# Usage:
#   bash scripts/fresh_clone_proof.sh [--full] [SOURCE_REPO]
#
#   (default)  FAST — symlinks an existing venv instead of building one. Seconds.
#              Proves the boot contract, not the install.
#   --full     Builds a real venv with python3.13 and installs requirements.txt
#              from scratch. That is a ~5 GB download; CI does this, a laptop
#              usually should not.
#
# Environment:
#   PALLI_PROOF_VENV         fast mode only — the venv to borrow. Default: the
#                            venv of the source repo (symlinks resolved).
#   PALLI_PROOF_TORCH_INDEX  full mode only — install torch from THIS index
#                            before requirements.txt, so the CPU wheel is
#                            already in place when sentence-transformers asks
#                            for one. CI sets it to the PyTorch CPU index.
#                            PIP_EXTRA_INDEX_URL would NOT do this job: an extra
#                            index ADDS candidates, it does not restrict them,
#                            so the day PyPI carries a newer torch than the CPU
#                            index, pip picks the 2.5 GB CUDA wheel.
#
# Exit: 0 and "PROOF OK" only if ALL FIVE runs behaved. 1 and a named failure
# otherwise. The temporary clone is removed either way.

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

MODE="fast"
SOURCE_REPO=""
for arg in "$@"; do
    case "${arg}" in
        --full) MODE="full" ;;
        --fast) MODE="fast" ;;
        -h|--help) sed -n '2,68p' "${BASH_SOURCE[0]}"; exit 0 ;;
        -*) echo "unknown option: ${arg}" >&2; exit 1 ;;
        *) SOURCE_REPO="${arg}" ;;
    esac
done
SOURCE_REPO="${SOURCE_REPO:-${REPO_ROOT}}"

WORKDIR="$(mktemp -d)"
cleanup() { rm -rf "${WORKDIR}"; }
trap cleanup EXIT

CLONE="${WORKDIR}/palli"

say() { printf '\n=== %s\n' "$*"; }
fail() { printf '\nPROOF FAILED: %s\n' "$*" >&2; exit 1; }

say "cloning ${SOURCE_REPO}"
git clone --quiet "${SOURCE_REPO}" "${CLONE}"
cd "${CLONE}"
printf 'cloned %s at %s\n' "$(git rev-parse --short HEAD)" "${CLONE}"

# --- the install ------------------------------------------------------------

if [ "${MODE}" = "full" ]; then
    say "building a venv and installing requirements.txt (this is the slow one)"
    python3.13 -m venv venv
    venv/bin/python -m pip install --quiet --upgrade pip
    if [ -n "${PALLI_PROOF_TORCH_INDEX:-}" ]; then
        # --index-url, not --extra-index-url: this has to be the ONLY place pip
        # looks for torch, or a newer CUDA wheel on PyPI wins the version
        # comparison and lands on a runner with no GPU. requirements.txt then
        # finds torch already satisfied.
        say "pre-installing torch from ${PALLI_PROOF_TORCH_INDEX}"
        venv/bin/python -m pip install --quiet torch --index-url "${PALLI_PROOF_TORCH_INDEX}"
    fi
    venv/bin/python -m pip install --quiet -r requirements.txt
else
    BORROWED="${PALLI_PROOF_VENV:-${SOURCE_REPO}/venv}"
    # The source venv is often itself a symlink into another checkout.
    BORROWED="$(readlink -f "${BORROWED}")"
    [ -x "${BORROWED}/bin/python" ] || fail "no venv to borrow at ${BORROWED} (set PALLI_PROOF_VENV, or use --full)"
    say "fast mode: borrowing the venv at ${BORROWED}"
    ln -s "${BORROWED}" "${CLONE}/venv"
fi

# --- the config a stranger starts from --------------------------------------

say "copying the shipped examples into place"
cp data/palli.example.yaml data/palli.yaml
cp .env.template .env.local
printf 'data/palli.yaml  <- data/palli.example.yaml\n.env.local       <- .env.template\n'

# .env.local, NOT the shell environment. core/config.py loads the dotenv file
# with override=True outside tests, so an exported variable loses to whatever
# the file says — which for a fresh clone is "unset". A proof that set these in
# the environment would prove nothing about a stranger's clone.
write_env_keys() {
    {
        printf '\n# --- written by fresh_clone_proof.sh ---\n'
        printf '%s\n' "$@"
    } >> "${CLONE}/.env.local"
}

# --- run 1: a complete config boots -----------------------------------------

say "run 1 — a complete config must exit 0"
write_env_keys \
    "DISCORD_TOKEN=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
    "LLM_PROVIDER=deepseek" \
    "DEEPSEEK_API_KEY=sk-test" \
    "PALLI_PREFLIGHT_OFFLINE=1"

set +e
RUN1_OUT="$(venv/bin/python main.py --preflight-only 2>&1)"
RUN1_CODE=$?
set -e
printf '%s\n--- exit %s\n' "${RUN1_OUT}" "${RUN1_CODE}"

# --- run 2: the template as shipped BOOTS, and says which door is shut ------

say "run 2 — the untouched .env.template must exit 0, say Discord is off, and name the door"
cp .env.template .env.local
# The offline pin is the ONE thing added back to the shipped template. The
# template's brain needs a key and ships without one, so nothing would be
# called anyway; the pin keeps that true no matter what the template later
# names. Nothing else is added — that is the point.
write_env_keys "PALLI_PREFLIGHT_OFFLINE=1"

set +e
RUN2_OUT="$(venv/bin/python main.py --preflight-only 2>&1)"
RUN2_CODE=$?
set -e
printf '%s\n--- exit %s\n' "${RUN2_OUT}" "${RUN2_CODE}"

# --- run 3: something really broken still refuses, in order -----------------

say "run 3 — a broken setting must exit 2 with a numbered punch list"
# A wide bind whose list of who may reach the backend parses to nothing: the
# one combination she must not start in, and the reason the punch list is
# numbered at all.
write_env_keys \
    "BACKEND_HOST=0.0.0.0" \
    "BACKEND_ALLOWED_NETWORKS=nonsense"

set +e
RUN3_OUT="$(venv/bin/python main.py --preflight-only 2>&1)"
RUN3_CODE=$?
set -e
printf '%s\n--- exit %s\n' "${RUN3_OUT}" "${RUN3_CODE}"

# --- run 4: a Telegram-only stranger ----------------------------------------

say "run 4 — a Telegram token and no Discord token must exit 0 and say only Discord is shut"
# The template's own shape for Discord (enabled, token blank) with the Telegram
# door opened on top: the stranger who made a bot with BotFather and never
# touched Discord. The token is fictional — the preflight never calls Telegram.
#
# The brain is repointed at a local server here, and that is load-bearing since
# 2026-09-18: the shipped template now has no brain, and a door with no brain
# behind it is CLOSED for that boot and says so. This run is about the Telegram
# door itself, so it has to be a boot where that door can actually be open.
# Run 2 covers the no-brain case, doors and all.
cp .env.template .env.local
write_env_keys \
    "LLM_PROVIDER=local_openai" \
    "ENABLE_DISCORD_BOT=true" \
    "DISCORD_TOKEN=" \
    "ENABLE_TELEGRAM_BOT=true" \
    "TELEGRAM_BOT_TOKEN=123456:ABC-fictional-token" \
    "LOCAL_OWNER_TELEGRAM_USER_ID=000000000" \
    "PALLI_PREFLIGHT_OFFLINE=1"

set +e
RUN4_OUT="$(venv/bin/python main.py --preflight-only 2>&1)"
RUN4_CODE=$?
set -e
printf '%s\n--- exit %s\n' "${RUN4_OUT}" "${RUN4_CODE}"

# --- run 5: no brain key at all boots into setup mode -----------------------

say "run 5 — a brain that needs no key must exit 0 and say nothing about a missing one"
# The template with its brain repointed at a model server on this machine: the
# person who already runs Ollama, LM Studio or vLLM and needs no account at
# all. She must boot clean and say nothing about a brain — they are done.
cp .env.template .env.local
write_env_keys \
    "LLM_PROVIDER=local_openai" \
    "PALLI_PREFLIGHT_OFFLINE=1"

set +e
RUN5_OUT="$(venv/bin/python main.py --preflight-only 2>&1)"
RUN5_CODE=$?
set -e
printf '%s\n--- exit %s\n' "${RUN5_OUT}" "${RUN5_CODE}"

# --- the verdict ------------------------------------------------------------

say "verdict"
[ "${RUN1_CODE}" -eq 0 ] || fail "run 1 exited ${RUN1_CODE}, expected 0 — a complete config does not boot"
printf '%s' "${RUN1_OUT}" | grep -q "Preflight passed" \
    || fail "run 1 exited 0 but never said the preflight passed"

[ "${RUN2_CODE}" -eq 0 ] || fail "run 2 exited ${RUN2_CODE}, expected 0 — the shipped template must boot"
printf '%s' "${RUN2_OUT}" | grep -q "Preflight passed" \
    || fail "run 2 exited 0 but never said the preflight passed"
# Booting is only half of it. A stranger with no Discord application must be
# TOLD that door is shut and where she is instead, or the quiet boot of week 1
# is back wearing a green exit code.
printf '%s' "${RUN2_OUT}" | grep -q "DISCORD_TOKEN" \
    || fail "run 2 booted but never said the Discord door was closed for this boot"
# And the shipped brain needs a key it does not have, so the door must be named
# here — this is the line a stranger follows into their first hour.
printf '%s' "${RUN2_OUT}" | grep -q "no brain configured" \
    || fail "run 2 booted on the shipped template but never said there is no brain yet"
RUN2_ADDRESS="$(grep -E '^FRONTEND_URL=' .env.template | tail -n 1 | cut -d= -f2-)"
[ -n "${RUN2_ADDRESS}" ] || fail "run 2 could not read FRONTEND_URL from the shipped template"
printf '%s' "${RUN2_OUT}" | grep -qF "${RUN2_ADDRESS}" \
    || fail "run 2 said there is no brain but never named ${RUN2_ADDRESS} to add one at"
# ...and both halves on ONE line, as the punch list renders it. The greps above
# would pass on two unrelated lines, and SETUP.md section 3 quotes this sentence
# whole — the key name included, because that is the other way out.
printf '%s' "${RUN2_OUT}" \
    | grep -qF "LLM — no brain configured; open ${RUN2_ADDRESS} to add one (or set DEEPSEEK_API_KEY in .env.local)" \
    || fail "run 2's setup-mode line is not the whole sentence the docs quote"

[ "${RUN3_CODE}" -eq 2 ] || fail "run 3 exited ${RUN3_CODE}, expected 2 — a broken setting must refuse, loudly"
# The number is the point. An unnumbered wall of text is not a punch list, and
# "one problem per boot" is what a stranger used to get.
printf '%s\n' "${RUN3_OUT}" | grep -q '^1\. ' \
    || fail "run 3 refused but printed no numbered punch list"
printf '%s' "${RUN3_OUT}" | grep -q "BACKEND_ALLOWED_NETWORKS" \
    || fail "run 3 refused but never named the setting that is broken"

[ "${RUN4_CODE}" -eq 0 ] || fail "run 4 exited ${RUN4_CODE}, expected 0 — a Telegram-only config must boot"
printf '%s' "${RUN4_OUT}" | grep -q "Preflight passed" \
    || fail "run 4 exited 0 but never said the preflight passed"
# Discord is the shut door here, and it must say so exactly as run 2 does.
printf '%s' "${RUN4_OUT}" | grep -q "DISCORD_TOKEN" \
    || fail "run 4 booted but never said the Discord door was closed for this boot"
# Telegram is the OPEN door, so there is nothing to say about it. A line here
# means either the token did not arrive or the library is not installed — and
# the second one is the packaging proof: `pip install -r requirements.txt` has
# to have carried python-telegram-bot into the clone's venv.
# (`if`, not `grep && fail`: an AND-list whose first half fails is the one shape
# `set -e` reads differently from every other, and a proof must not depend on it.)
if printf '%s' "${RUN4_OUT}" | grep -q "python-telegram-bot is not installed"; then
    fail "run 4 has a token but no library — requirements.txt did not carry python-telegram-bot"
fi
if printf '%s' "${RUN4_OUT}" | grep -q "TELEGRAM_BOT_TOKEN"; then
    fail "run 4 has a Telegram token but the preflight still closed that door"
fi
# Every deciding var is pinned, the owner id included, so "nothing about
# Telegram" means NOTHING — the same thing the in-suite twin asserts
# (tests/core/test_fresh_clone_boot.py::test_a_telegram_only_stranger_boots_clean).
if printf '%s' "${RUN4_OUT}" | grep -qi "telegram"; then
    fail "run 4 is the clean Telegram case but the preflight still had something to say"
fi

[ "${RUN5_CODE}" -eq 0 ] || fail "run 5 exited ${RUN5_CODE}, expected 0 — a local model server needs no key and must boot"
printf '%s' "${RUN5_OUT}" | grep -q "Preflight passed" \
    || fail "run 5 exited 0 but never said the preflight passed"
# Someone running their own server has already chosen. Telling them there is no
# brain would be false, and would send them to a door they do not need.
if printf '%s' "${RUN5_OUT}" | grep -q "no brain configured"; then
    fail "run 5 points at a local server that needs no key but she still said there is no brain"
fi
# Setup mode is never a numbered blocker, and a brain that is not there is never
# a brain that "did not answer".
if printf '%s' "${RUN5_OUT}" | grep -q "PaLLi cannot start yet"; then
    fail "run 5 booted but still printed a blocking punch list"
fi
if printf '%s' "${RUN5_OUT}" | grep -q "did not answer"; then
    fail "run 5 sent a live probe at a brain that is not configured"
fi

printf '\nPROOF OK\n'
