# gate_lock.sh — sourced by bin/green and bin/dev to serialise the test gate.
#
# Two gate runs in ONE worktree race on the git index (bin/green commits a
# checkpoint), on tmp/dev_test_last.txt (written on red, UNLINKED on green, so
# one run deletes the other's failure log), on the libs/dev_test/timeout_seconds.txt
# ceiling ratchet (a tracked file written mid-run), on tmp/live_gate_cache.json
# (non-atomic read-modify-write), and on the fixed-name
# tmp/live_gate_cache_roundtrip_probe.json (whose write-then-read the other run
# can overwrite, producing a phantom red from the harness's own probe).
#
# Per-worktree, not global: every one of those paths resolves from the
# worktree's own root. The cross-worktree MAIN/tmp/serve/stale.json is a
# best-effort whole-file recompute that self-heals on the next green, so it is
# deliberately NOT covered here. See design/testing.md.
#
# Mechanism mirrors bin/work's work-fold.lock: atomic mkdir, refuse rather than
# block, released by an EXIT trap.

GATE_LOCK_CONTENDED_EXIT=7

# Acquire the gate lock for the worktree rooted at $1, or exit
# $GATE_LOCK_CONTENDED_EXIT. On success, exports GATE_LOCK_HELD so a nested
# gate (bin/green invoking bin/dev test) does not deadlock on itself, and
# installs an EXIT trap that releases the lock.
gate_lock_acquire() {
  local root="$1"
  [ -z "${GATE_LOCK_HELD:-}" ] || return 0

  local lock="$root/tmp/gate.lock"
  mkdir -p "$root/tmp"

  if ! _gate_lock_mkdir_or_reclaim "$lock"; then
    local owner
    owner="$(cat "$lock/pid" 2>/dev/null || true)"
    echo "gate: another test gate is already running in this worktree." >&2
    echo "gate: lock $lock is held by pid ${owner:-unknown}." >&2
    echo "gate: wait for it to finish, or if you are certain nothing is running, delete that directory." >&2
    exit "$GATE_LOCK_CONTENDED_EXIT"
  fi

  printf '%s\n' "$$" > "$lock/pid"
  export GATE_LOCK_HELD="$lock"
  trap 'rm -rf "$GATE_LOCK_HELD" 2>/dev/null || true' EXIT
}

# Take the lock, reclaiming it if its recorded owner is gone. A mkdir lock
# carries no owner, so a SIGKILLed or slept-through run would otherwise leave
# the directory behind and brick every later gate run — bin/work can live with
# that because folds are rare, but the gate runs constantly.
_gate_lock_mkdir_or_reclaim() {
  local lock="$1"
  mkdir "$lock" 2>/dev/null && return 0

  local owner
  owner="$(cat "$lock/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && kill -0 "$owner" 2>/dev/null; then
    return 1
  fi
  rm -rf "$lock" 2>/dev/null || true
  mkdir "$lock" 2>/dev/null
}
