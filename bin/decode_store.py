"""Hour/day-rotated decode log storage (stdlib only).

Decodes live under <data_dir>/decodes/<YYYY-MM-DD>/<HH>.jsonl (UTC), one file
per UTC hour, instead of a single ever-growing decodes.jsonl — each file stays
small (at most one hour's worth of 15 s slots) and old ones are never touched
again, so nothing needs active log rotation. Readers get back exactly the
same flat, chronologically-ordered sequence of lines a single big file would
have given them; only the on-disk layout changed.

date_yymmdd matches the format already used everywhere else in this project
(rx-loop.sh's `date -u +%y%m%d`) — 6 digits, e.g. "260711".
"""
import glob, json, os, time


def _daydir(data_dir, date_yymmdd):
    yyyy = "20" + date_yymmdd[0:2]
    mm, dd = date_yymmdd[2:4], date_yymmdd[4:6]
    return os.path.join(data_dir, "decodes", f"{yyyy}-{mm}-{dd}")


def hour_file(data_dir, date_yymmdd, slot_hhmmss):
    """Path for the hour bucket a given (date, slot) falls into."""
    return os.path.join(_daydir(data_dir, date_yymmdd), f"{slot_hhmmss[0:2]}.jsonl")


def timestamp(date_yymmdd, slot_hhmmss):
    """(YYMMDD, HHMMSS) -> ISO 8601 UTC string, e.g. 2026-07-11T14:30:15Z."""
    return (f"20{date_yymmdd[0:2]}-{date_yymmdd[2:4]}-{date_yymmdd[4:6]}T"
            f"{slot_hhmmss[0:2]}:{slot_hhmmss[2:4]}:{slot_hhmmss[4:6]}Z")


def append(data_dir, date_yymmdd, slot_hhmmss, records):
    """Append decode records (list of dict) to the correct hour file."""
    path = hour_file(data_dir, date_yymmdd, slot_hhmmss)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def all_files(data_dir, since_date_yymmdd=None):
    """Every hour file that exists, oldest first. since_date_yymmdd limits to
    that UTC day onward (day-directory names sort correctly as strings)."""
    files = sorted(glob.glob(os.path.join(data_dir, "decodes", "*", "*.jsonl")))
    if since_date_yymmdd:
        cutoff = os.path.basename(_daydir(data_dir, since_date_yymmdd))
        files = [f for f in files if os.path.basename(os.path.dirname(f)) >= cutoff]
    return files


def read_all(data_dir, since_date_yymmdd=None):
    """Concatenated raw lines from all matching hour files, oldest first —
    the same flat chronological sequence one big decodes.jsonl would give."""
    out = []
    for path in all_files(data_dir, since_date_yymmdd):
        try:
            with open(path) as f:
                out.extend(f.readlines())
        except FileNotFoundError:
            pass
    return out


def tail(data_dir, n):
    """Last n raw lines, newest hour files first until n is covered (handles
    the UTC-midnight/hour-boundary edge where today's files alone have <n)."""
    today = time.strftime("%y%m%d", time.gmtime())
    files = all_files(data_dir, since_date_yymmdd=today)
    if not files:
        files = all_files(data_dir)[-2:]  # nothing today yet — use whatever's most recent
    lines = []
    for path in reversed(files):
        try:
            with open(path) as f:
                fl = f.readlines()
        except FileNotFoundError:
            fl = []
        lines = fl + lines
        if len(lines) >= n:
            break
    return lines[-n:]
