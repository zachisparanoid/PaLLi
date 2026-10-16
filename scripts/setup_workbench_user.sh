#!/usr/bin/env bash
# Her Own Work — one-time activation infrastructure (THE OWNER RUNS THIS, with sudo).
# Creates the palli-work user (the OS wall), her workspace, her NAS folder
# (only when this machine has a NAS), and the sudoers line the app uses to
# run her shell. Idempotent.
# Spec: docs/superpowers/specs/2026-07-28-her-own-work-design.md §4/§4c.
#
# WHY HER WORKSPACE LIVES IN HER OWN HOME (build deviation, 2026-07-28):
# the spec's session default put her workspace inside the OWNER's home. A home
# directory is normally 750 and its owner's, so palli-work has no traverse bit
# and cannot reach a workspace under it. The only chmod that would let her in
# (751) opens everything world-readable one level down — on a developer's box
# that is the projects tree, which typically holds archives of the app itself:
# HER OWN substrate. Spec §4 says the palli-work user gets no read on the app
# repo, data/, or secrets, so the workspace moved into her own home instead,
# where the wall costs nothing. LEGACY_LINK below is a convenience symlink at
# the old path, so notes that still name it keep working for the owner.
set -euo pipefail

WORK_USER="${WORK_USER:-palli-work}"
WORKSPACE="${WORKSPACE:-/home/palli-work/workspace}"
# Her folder on the house NAS, IF this machine has one. PALLI_NAS_ROOT is
# the app's one NAS knob (core/config.py) — but this script runs in the
# owner's sudo shell, which does not read .env / .env.local, so pass it in:
#   sudo PALLI_NAS_ROOT=/mnt/your-nas bash scripts/setup_workbench_user.sh
# (or set NAS_DIR outright). NEITHER set = there is no NAS folder, and every
# NAS step below is skipped. It must never default to a plausible-looking
# path: the old `${PALLI_NAS_ROOT:-/mnt/nas}` created and chowned a stray
# /mnt/nas/palli the app would never use, and then the probe reported it
# "writable" — a green light for the wrong folder.
# The default is a DEDICATED subfolder, <root>/palli/workbench, never
# <root>/palli itself: that folder can hold the owner's own files, and what
# the step below hands to $WORK_USER becomes hers to change or delete (the
# owner's ruling, 2026-09-28: "New empty subfolder"). The app still promises
# <root>/palli until WORKBENCH_NAS_DIR names the folder this prepares.
NAS_DIR="${NAS_DIR:-${PALLI_NAS_ROOT:+${PALLI_NAS_ROOT}/palli/workbench}}"
# The user the APP runs as — the one that may become her, and the one that reads
# her workspace. SUDO_USER first so `sudo bash scripts/setup_workbench_user.sh`
# does not decide the app runs as root; `id -un` when the script is run plainly
# and each step's own sudo does the escalating.
APP_USER="${APP_USER:-${SUDO_USER:-$(id -un)}}"
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"
LEGACY_LINK="${LEGACY_LINK:-${APP_HOME:+$APP_HOME/palli-workspace}}"
# Her substrate: this app's own tree, DERIVED from where this script lives — the
# same rule the app itself uses (core/agent/builtin_tools/workbench_shell.py
# _substrate_root). The probes at the bottom prove she cannot read or write it,
# and a probe that tests a path from someone else's machine proves nothing: it
# fails because there is nothing there and prints a green line anyway.
SUBSTRATE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

id -u "$WORK_USER" >/dev/null 2>&1 || sudo useradd -m -s /bin/bash "$WORK_USER"
WORK_HOME="$(getent passwd "$WORK_USER" | cut -d: -f6)"

# The wall, positively: she owns her spaces...
# Her home is traversable+readable by the app's group (his open-visibility
# law: the workspace and its git history are fully readable by him) and by
# nobody else. She owns it; he reads it; the world sees nothing.
sudo chgrp "$APP_USER" "$WORK_HOME"
sudo chmod 750 "$WORK_HOME"
sudo mkdir -p "$WORKSPACE"
sudo chown -R "$WORK_USER:$APP_USER" "$WORKSPACE"
sudo chmod 2750 "$WORKSPACE"          # she writes; the app/owner group reads

# ...and nothing else. NOTE, EMPHATICALLY: never add palli-work to the app
# user's group to "fix" a permission problem. A developer's projects tree is
# commonly group-writable, so that membership is WRITE access to everything in
# it — the exact surgery-on-her-own-brain the wall exists to prevent. No group
# memberships beyond her own, and no sudo of her own.
# Guarded: gpasswd takes an exclusive lock on /etc/group + /etc/gshadow and
# retries on a sleep loop when it's contended, so calling it unconditionally
# makes a freshly-created user (never in sudo) look like a hang. Only call it
# when there is actually something to remove.
if id -nG "$WORK_USER" 2>/dev/null | tr ' ' '\n' | grep -qx sudo; then
  sudo gpasswd -d "$WORK_USER" sudo || true
fi

# Convenience only: the spec's original path, pointing at the real one, so
# `cd ~/palli-workspace` still works FOR THE OWNER. She never traverses it
# (no execute bit for her on his home) and does not need to.
if [ -n "$LEGACY_LINK" ] && [ ! -e "$LEGACY_LINK" ]; then
  sudo -u "$APP_USER" ln -s "$WORKSPACE" "$LEGACY_LINK" 2>/dev/null || true
fi

# The app (ubuntu) may become her, passwordless, for the shell tool only.
# Validated before install: a malformed file in /etc/sudoers.d can break
# sudo for the whole machine, and this runs on his live host.
SUDOERS_TMP="$(mktemp)"
printf '%s ALL=(%s) NOPASSWD: ALL\n' "$APP_USER" "$WORK_USER" > "$SUDOERS_TMP"
if sudo visudo -cf "$SUDOERS_TMP" >/dev/null 2>&1; then
  sudo install -m 440 -o root -g root "$SUDOERS_TMP" /etc/sudoers.d/palli-workbench
  rm -f "$SUDOERS_TMP"
else
  rm -f "$SUDOERS_TMP"
  echo "REFUSING: generated sudoers line failed visudo validation. Nothing installed." >&2
  exit 1
fi

# True when the folder exists and holds anything, or cannot be listed: a
# folder we cannot see into is never assumed empty. -H looks THROUGH a start
# point that is a link: plain find examines the link itself, sees nothing
# under it, and a linked folder full of files would read as empty.
folder_holds_files() {
  [ -d "$1" ] || return 1
  local first
  first="$(sudo find -H "$1" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" || return 0
  [ -n "$first" ]
}

# Her NAS folder, handed to her ONLY when it is new or empty: a folder that
# already holds files is never re-owned, because those files are not hers
# (the owner's ruling, 2026-09-28). On a CIFS mount with forceuid/forcegid
# (e.g. uid=1000,gid=1000,file_mode=0755,dir_mode=0755) the chown and chmod
# below are no-ops by design of the mount — every file displays as the app
# user's, 0755, no matter what the server says. The probe further down tells
# you whether she can actually write there; see its remedy if she cannot.
NAS_DIR_HOLDS_FILES=""
NAS_DIR_NOT_OURS=""
if [ -n "$NAS_DIR" ]; then
  if [ -L "$NAS_DIR" ] || { [ -e "$NAS_DIR" ] && [ ! -d "$NAS_DIR" ]; }; then
    # A link, or anything that is not a folder, is not ours to touch: chown
    # and chmod follow a link into whatever folder it names, and a file was
    # never a folder she could work in.
    NAS_DIR_NOT_OURS=1
    echo "NAS_DIR $NAS_DIR is a link or not a folder — NOT touching it:"
    echo "  chown/chmod would follow a link into whatever it names. Give her a real, new,"
    echo "  empty folder instead (NAS_DIR=...)."
  elif folder_holds_files "$NAS_DIR"; then
    NAS_DIR_HOLDS_FILES=1
    echo "NAS folder $NAS_DIR already holds files — NOT handing it to $WORK_USER:"
    echo "  what is in it is not hers. Give her a new, empty folder instead (NAS_DIR=...)."
  else
    sudo mkdir -p "$NAS_DIR" 2>/dev/null || true
    sudo chown "$WORK_USER:$APP_USER" "$NAS_DIR" 2>/dev/null || true
    sudo chmod 2770 "$NAS_DIR" 2>/dev/null || true
  fi
else
  echo "no NAS configured (PALLI_NAS_ROOT / NAS_DIR unset) — skipping the NAS steps"
fi

# Seed her workspace (only if empty — never clobber her work).
if [ ! -e "$WORKSPACE/WORKBENCH.md" ]; then
  sudo -u "$WORK_USER" bash -c "cat > '$WORKSPACE/WORKBENCH.md'" <<'EOF'
# Workbench

(nothing here yet — nothing is owed)
EOF
fi
if [ ! -e "$WORKSPACE/SUGGESTIONS.md" ]; then
  sudo -u "$WORK_USER" bash -c "cat > '$WORKSPACE/SUGGESTIONS.md'" <<'EOF'
# Suggestions

Ideas from the owner land here. They are inspiration, never assignment: read
them when choosing what to work on, take what genuinely pulls you, and
feel free to decline the rest — saying why, in your own words, is welcome.
EOF
fi
sudo -u "$WORK_USER" mkdir -p "$WORKSPACE/projects"
if [ ! -d "$WORKSPACE/.git" ]; then
  sudo -u "$WORK_USER" git -C "$WORKSPACE" init -q
  sudo -u "$WORK_USER" git -C "$WORKSPACE" config user.name "PaLLi"
  sudo -u "$WORK_USER" git -C "$WORKSPACE" config user.email "palli@local"
  sudo -u "$WORK_USER" git -C "$WORKSPACE" add -A
  sudo -u "$WORK_USER" git -C "$WORKSPACE" commit -qm "the workbench, before any work"
fi

# ── Probes — fail loudly HERE, never at her first session ──────────────
echo "--- probes ---"
echo "workspace: $WORKSPACE"
sudo -n -u "$WORK_USER" bash -c 'echo "shell as: $(whoami)"'

leaked="$(sudo -n -u "$WORK_USER" env | grep -ciE 'key|token|secret' || true)"
if [ "$leaked" -gt 0 ]; then
  echo "WARNING: $leaked env var(s) matching key/token/secret cross into her shell!"
else
  echo "env clean: no app secrets cross"
fi

if sudo -n -u "$WORK_USER" bash -c "touch '$WORKSPACE/.probe' && rm '$WORKSPACE/.probe'"; then
  echo "workspace writable by $WORK_USER"
else
  echo "DANGER: workspace NOT writable by $WORK_USER — she cannot work. CHECK PERMISSIONS"
fi

# The wall, both directions: no write AND no read on her substrate.
echo "substrate: $SUBSTRATE"
if sudo -n -u "$WORK_USER" bash -c \
    "touch '$SUBSTRATE/.probe' 2>/dev/null"; then
  echo "DANGER: substrate writable — CHECK PERMISSIONS"
  sudo -n -u "$WORK_USER" rm -f "$SUBSTRATE/.probe" 2>/dev/null || true
else
  echo "substrate write correctly DENIED"
fi
if sudo -n -u "$WORK_USER" bash -c \
    "ls '$SUBSTRATE' >/dev/null 2>&1"; then
  echo "DANGER: substrate READABLE by $WORK_USER — spec §4 says her record, their"
  echo "        words, and her prompts are behind the wall in BOTH directions."
else
  echo "substrate read correctly DENIED"
fi

if [ -z "$NAS_DIR" ]; then
  echo "no NAS configured — skipping the NAS write probe"
elif [ -n "$NAS_DIR_NOT_OURS" ]; then
  echo "NAS write probe skipped: $NAS_DIR is a link or not a folder (see above)"
elif sudo -n -u "$WORK_USER" bash -c "touch '$NAS_DIR/.probe' 2>/dev/null && rm '$NAS_DIR/.probe' 2>/dev/null"; then
  if [ -n "$NAS_DIR_HOLDS_FILES" ]; then
    echo "WARNING: NAS folder $NAS_DIR is writable by $WORK_USER AND holds files that are"
    echo "         not hers — she can change or delete them. Give her a new, empty folder"
    echo "         and point WORKBENCH_NAS_DIR at it."
  else
    echo "NAS folder $NAS_DIR writable by $WORK_USER"
    echo "  (the app uses it once WORKBENCH_NAS_DIR=$NAS_DIR is in .env.local)"
  fi
else
  cat <<REMEDY
NAS folder NOT writable by $WORK_USER (read-only for her).
  This is expected on this install and it fails SAFE — she can read the NAS
  and write nothing, which is stricter than the spec's law, not looser. She
  simply cannot use a NAS folder of her own until you choose to give her one.
  Cause: the share is mounted with forceuid/forcegid, so every file shows as
  the app user's uid/gid and mode, and chown/chmod on the mount do nothing.
  Remedy (your call, your infrastructure): give her a NEW, EMPTY folder on
  the NAS and mount ONLY that folder a second time, owned by her. Never open
  a folder that already holds your files, and never mount the whole share
  for her: everything under what you mount becomes hers to change or delete.
    1. On the NAS, create an empty folder: <share>/palli/workbench
    2. sudo mkdir -p /mnt/palli-nas
    3. One line in /etc/fstab (<options> = the guest or credentials options
       your other NAS mount uses):
       //<server>/<share>/palli/workbench /mnt/palli-nas cifs <options>,uid=$(id -u "$WORK_USER"),gid=$(id -g "$WORK_USER"),dir_mode=0770,file_mode=0660,nofail,_netdev 0 0
    4. sudo mount /mnt/palli-nas
    5. Set WORKBENCH_NAS_DIR=/mnt/palli-nas in the app environment (.env.local),
       restart her, and check it with: venv/bin/python scripts/palli_ctl.py doctor
       (or re-run this script with NAS_DIR=/mnt/palli-nas to repeat this probe).
  DO NOT instead add $WORK_USER to the app user's group or remount with that
  group's gid: on a typical box that hands her write access to the whole
  projects tree — her own substrate — which the wall exists to prevent.
REMEDY
fi
echo "done. Flip companion.own_work + restart when ready."
