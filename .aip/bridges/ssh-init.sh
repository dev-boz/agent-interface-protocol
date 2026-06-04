#!/usr/bin/env bash
# ssh-init.sh — pulled to the remote host at session start by the ssh-remote
# bridge (spec §"SSH Remote Execution Bridge"). Mounts the AIP workspace on the
# remote host (SSHFS) or syncs it via rsync, then ensures the remote tmux
# session exists so the agent's work survives connection drops.
set -euo pipefail

WORKSPACE="${AIP_WORKSPACE:-./workspace}"
TMUX_SESSION="${AIP_TMUX_SESSION:-aip-remote}"

# Prefer a live SSHFS mount so workspace files stay authoritative on both ends;
# fall back to a one-shot rsync if SSHFS is unavailable.
if command -v sshfs >/dev/null 2>&1 && [ -n "${AIP_SSHFS_SOURCE:-}" ]; then
  mkdir -p "$WORKSPACE"
  sshfs "$AIP_SSHFS_SOURCE" "$WORKSPACE" -o reconnect,ServerAliveInterval=15
elif [ -n "${AIP_RSYNC_SOURCE:-}" ]; then
  rsync -az --delete "$AIP_RSYNC_SOURCE" "$WORKSPACE"/
fi

# Ensure the remote tmux session exists (idempotent).
if ! tmux has-session -t "$TMUX_SESSION" 2>/dev/null; then
  tmux new-session -d -s "$TMUX_SESSION"
fi
