"""Pure decision + narrow-fingerprint + green-cache for gated live tests.

See `design/testing.md` §"Gated live tests" for the full contract this
implements. This module holds only the PURE decision logic and the narrow
disk-fingerprinting/cache-file helpers; wiring this into `bin/dev test` and
writing the actual live `run()` calls are later steps.
"""
import hashlib
import json
from pathlib import Path

TTL_SECONDS = 14400  # 4 hours

MISSING_SENTINEL = "\x00MISSING\x00"


def fingerprint_of_pairs(pairs) -> str:
    """Hex sha256 digest over `[path, content]` pairs, order-independent.

    Sorting `[path, content]` pairs (rather than hashing them in caller
    order) makes the digest depend only on the *set* of pairs, not on the
    order they were supplied in. Framing each pair as its own JSON array
    element keeps `path` and `content` from bleeding into each other.
    """
    normalized = sorted([str(path), str(content)] for path, content in pairs)
    canonical = json.dumps(normalized, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _files_under(path: Path):
    if path.is_dir():
        return sorted(f for f in path.rglob("*") if f.is_file())
    return [path]


def covered_fingerprint(paths, repo_root) -> str:
    """Fingerprint the current on-disk contents of a narrow `covered-paths` set.

    Each entry of `paths` is relative to `repo_root`; directories recurse
    into every file beneath them. A missing path contributes a sentinel
    content string so its appearance/disappearance still changes the digest.
    """
    root = Path(repo_root)
    entries = {}
    for rel in paths:
        abs_path = root / rel
        if not abs_path.exists():
            entries[str(rel)] = MISSING_SENTINEL
            continue
        for f in _files_under(abs_path):
            relpath = f.relative_to(root).as_posix()
            try:
                content = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                content = MISSING_SENTINEL
            entries[relpath] = content
    return fingerprint_of_pairs(list(entries.items()))


def gate_decision(cache_entry, now, current_fingerprint, creds_present, ttl_seconds=TTL_SECONDS, force=False, skip_if_keyless=False) -> dict:
    """PURE gate decision. See design/testing.md for the fail-safe ordering."""
    if not creds_present:
        # skip_if_keyless is how a keyless caller (bin/green) opts into
        # skip-as-pass; an explicit force overrides it and stays a hard fail.
        if skip_if_keyless and not force:
            return {"action": "skip", "reason": "no creds (keyless caller)"}
        return {"action": "fail", "reason": "no creds"}

    # Forcing overrides throttling, not feasibility: it runs only after the
    # creds check above, so a no-creds environment still fails even when
    # force is set.
    if force:
        return {"action": "run", "reason": "forced"}

    if (
        not isinstance(cache_entry, dict)
        or "last_green_epoch" not in cache_entry
        or "covered_fingerprint" not in cache_entry
    ):
        return {"action": "run", "reason": "no cache"}

    try:
        last_green = float(cache_entry["last_green_epoch"])
    except (TypeError, ValueError):
        return {"action": "run", "reason": "corrupt cache"}

    if now < last_green:
        return {"action": "run", "reason": "clock skew"}

    if cache_entry["covered_fingerprint"] != current_fingerprint:
        return {"action": "run", "reason": "relevant code changed"}

    if now - last_green >= ttl_seconds:
        return {"action": "run", "reason": "ttl lapsed"}

    return {"action": "skip", "reason": "fresh"}


def default_cache_path(repo_root) -> str:
    return str(Path(repo_root) / "tmp" / "live_gate_cache.json")


def read_cache(cache_path, test_id):
    """Read the entry for `test_id`, or None on any missing key/file/error.

    Fail-safe: any problem reading the cache falls back to None so the
    caller's gate_decision treats it as "no cache" -> run.
    """
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        entry = obj.get(test_id)
        return entry if isinstance(entry, dict) else None
    except Exception:
        return None


def _read_cache_object(cache_path) -> dict:
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def write_green(cache_path, test_id, now, fingerprint) -> None:
    path = Path(cache_path)
    obj = _read_cache_object(path) if path.exists() else {}
    obj[test_id] = {"last_green_epoch": now, "covered_fingerprint": fingerprint}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)


def _repo_root() -> Path:
    """Resolve the repo root from this file's own stable location in libs/."""
    return Path(__file__).resolve().parents[2]


def _canonical_json(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --- test-support probes (plan-friendly JSON-in/JSON-out wrappers) --------


def gate_decision_probe_json(has_entry, last_green, now, stored_fp, current_fp, creds, force=False, skip_if_keyless=False) -> str:
    cache_entry = None if not has_entry else {
        "last_green_epoch": last_green,
        "covered_fingerprint": stored_fp,
    }
    result = gate_decision(cache_entry, now, current_fp, creds, force=force, skip_if_keyless=skip_if_keyless)
    return _canonical_json(result)


def fingerprint_pairs_probe_json(pairs_json) -> str:
    pairs = json.loads(pairs_json)
    return _canonical_json({"digest": fingerprint_of_pairs(pairs)})


def cache_roundtrip_probe_json(now, fingerprint) -> str:
    test_id = "live_gate_selftest_roundtrip"
    cache_path = _repo_root() / "tmp" / "live_gate_cache_roundtrip_probe.json"
    write_green(str(cache_path), test_id, now, fingerprint)
    entry = read_cache(str(cache_path), test_id)
    try:
        cache_path.unlink()
    except OSError:
        pass
    expected = {"last_green_epoch": now, "covered_fingerprint": fingerprint}
    return _canonical_json({"round_trips": entry == expected})
