#!/usr/bin/env python3
"""coa logsync — idempotent QRZ Logbook upload. No AI, cron-able, RX-only in
spirit: this never touches the rig, it only reads the ADIF log and talks to
QRZ over HTTPS (via curl).

Idempotency: the byte offset of the ADIF file we've already synced through
is kept in ~/.config/cota/qrz.state. Each run parses only whole <eor>-
terminated records that start after that offset (the pre-<eoh> header is
always skipped) and POSTs each one to https://logbook.qrz.com/api. The
offset only advances past records QRZ confirms (RESULT=OK, or a duplicate-
record response, which QRZ treats as "already got it" and we treat as
already-synced). A real failure leaves the offset where it was so the next
run retries the same record, and this process exits nonzero.

API key: ~/.config/cota/qrz.key (chmod 600, never committed). This requires
a QRZ "XML Logbook Data" subscription. No subscription? Import your ADIF by
hand (free) at https://logbook.qrz.com/logbook -> Import.

Usage:
  logsync.py               sync new records to QRZ
  logsync.py --dry-run     show what would be uploaded; makes NO network call
  logsync.py --adif PATH   override the ADIF path (mostly for testing)
"""
import argparse
import os
import re
import subprocess
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import station_config

_C = station_config.load()
DEFAULT_ADIF = os.path.expanduser(_C.get("ADIF", "~/.local/share/WSJT-X/wsjtx_log.adi"))

CONF_DIR = os.path.expanduser("~/.config/cota")
KEY_PATH = os.path.join(CONF_DIR, "qrz.key")
STATE_PATH = os.path.join(CONF_DIR, "qrz.state")
API_URL = "https://logbook.qrz.com/api"

NO_KEY_MSG = """\
No QRZ API key found at {key_path}

QRZ Logbook upload needs a QRZ "XML Logbook Data" subscription — the API
key is issued on your QRZ Logbook page once you have it.

  1. Subscribe (or confirm you already have XML Logbook Data):
       https://www.qrz.com/i/subscriptions.html
  2. Copy your Logbook API key:
       https://logbook.qrz.com/logbook  ->  Settings
  3. Save it here (never commit this file):
       mkdir -p {conf_dir}
       echo 'YOUR-KEY-HERE' > {key_path}
       chmod 600 {key_path}

No subscription and don't want one? QRZ Logbook import is free — upload
your ADIF by hand instead:
       https://logbook.qrz.com/logbook  ->  Import
Your ADIF lives at: {adif}
"""


def read_key():
    try:
        with open(KEY_PATH) as f:
            return f.read().strip()
    except OSError:
        return None


def read_offset():
    try:
        with open(STATE_PATH) as f:
            return int((f.read().strip() or "0"))
    except (OSError, ValueError):
        return 0


def write_offset(n):
    os.makedirs(CONF_DIR, exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write(str(n))
    os.replace(tmp, STATE_PATH)


def split_records(data):
    """data: raw bytes of the ADIF file.
    Returns [(record_bytes, end_byte_offset), ...] — records are delimited
    by <eor> (case-insensitive); everything up to and including <eoh> (the
    ADIF header) is skipped. Absent an <eoh>, the whole file is candidate
    body (some minimal exports omit it)."""
    eoh = re.search(rb"<eoh>", data, re.I)
    body_start = eoh.end() if eoh else 0
    records = []
    pos = body_start
    for m in re.finditer(rb"<eor>", data[body_start:], re.I):
        end = body_start + m.end()
        rec = data[pos:end]
        if rec.strip():
            records.append((rec, end))
        pos = end
    return records


def new_records(path, offset):
    """Records ending after `offset` bytes into the ADIF file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return []
    return [(rec, end) for rec, end in split_records(data) if end > offset]


def extract_call(rec_str):
    m = re.search(r"<call:(\d+)>", rec_str, re.I)
    if not m:
        return "?"
    n = int(m.group(1))
    return rec_str[m.end():m.end() + n]


def parse_qrz_response(resp):
    """QRZ's API returns a & -joined KEY=VALUE query string, e.g.
    'RESULT=OK&COUNT=1&LOGID=123' or 'RESULT=FAIL&REASON=...'. Some
    duplicate responses use STATUS instead of RESULT. Returns (result,
    reason) where reason falls back to the raw response if no REASON field
    is present (so a "duplicate" substring check still works)."""
    fields = {}
    for kv in resp.split("&"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            fields[k.strip().upper()] = urllib.parse.unquote_plus(v)
    result = (fields.get("RESULT") or fields.get("STATUS") or "").upper()
    reason = fields.get("REASON", resp)
    return result, reason


def qrz_post(key, adif_record_str):
    """POST one ADIF record to the QRZ Logbook API via curl. Never called in
    --dry-run mode. Returns (result, reason) — see parse_qrz_response."""
    body = urllib.parse.urlencode({"KEY": key, "ACTION": "INSERT", "ADIF": adif_record_str})
    try:
        r = subprocess.run(["curl", "-s", "-S", "--max-time", "20", "--data", body, API_URL],
                            capture_output=True, text=True, timeout=25)
    except FileNotFoundError:
        return "FAIL", "curl not found on this system"
    except subprocess.TimeoutExpired:
        return "FAIL", "request timed out"
    if r.returncode != 0:
        return "FAIL", f"curl exit {r.returncode}: {r.stderr.strip()}"
    return parse_qrz_response(r.stdout.strip())


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                     help="show what would be uploaded; makes no network call, "
                          "does not advance the saved offset")
    ap.add_argument("--adif", default=DEFAULT_ADIF,
                     help="override ADIF path (default: from station.conf)")
    args = ap.parse_args()

    key = read_key()
    if not key:
        print(NO_KEY_MSG.format(key_path=KEY_PATH, conf_dir=CONF_DIR, adif=args.adif))
        return 1

    offset = read_offset()
    recs = new_records(args.adif, offset)
    if not recs:
        print(f"log-sync: nothing new (offset {offset}, ADIF {args.adif})")
        return 0

    print(f"log-sync: {len(recs)} new record(s) since byte offset {offset} in {args.adif}"
          + ("  [dry-run]" if args.dry_run else ""))
    uploaded = 0
    for rec, end in recs:
        rec_str = rec.decode("utf-8", errors="replace").strip()
        call = extract_call(rec_str)
        if args.dry_run:
            print(f"  [dry-run] would POST {len(rec)} bytes for {call} -> {API_URL}")
            uploaded += 1
            continue
        result, reason = qrz_post(key, rec_str)
        if result == "OK":
            print(f"  OK   {call}")
            offset = end
            write_offset(offset)
            uploaded += 1
        elif "duplicate" in (reason or "").lower():
            print(f"  DUP  {call} — QRZ already has this QSO ({reason}) — treating as synced")
            offset = end
            write_offset(offset)
            uploaded += 1
        else:
            print(f"FAIL  {call} — {reason}", file=sys.stderr)
            print(f"stopping: offset held at {offset} — this record retries next run", file=sys.stderr)
            return 1

    if args.dry_run:
        print(f"[dry-run] {uploaded} record(s) would be uploaded; offset not touched")
        return 0
    print(f"log-sync: {uploaded} record(s) synced, offset now {offset}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
