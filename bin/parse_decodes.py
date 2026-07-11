#!/usr/bin/env python3
"""Parse jt9 stdout for one slot -> append decodes.jsonl, rebuild status.json.
Usage: parse_decodes.py YYMMDD HHMMSS < jt9out.txt   (run from data/)"""
import sys, re, json, time, os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import station_config

_C = station_config.load()
DATE = sys.argv[1] if len(sys.argv) > 1 else time.strftime("%y%m%d", time.gmtime())
SLOT = sys.argv[2] if len(sys.argv) > 2 else time.strftime("%H%M%S", time.gmtime())
ADIF = os.path.expanduser(_C.get("ADIF", "~/.local/share/WSJT-X/wsjtx_log.adi"))
MYCALL = _C.get("MYCALL", "N0CALL")

line_re = re.compile(r"^\s*(\d{4,6})\s+(-?\d+)\s+(-?[\d.]+)\s+(\d+)\s+\S\s+(.+?)\s*$")
decodes = []
for line in sys.stdin:
    m = line_re.match(line)
    if not m:
        continue
    t, snr, dt, freq, msg = m.groups()
    decodes.append({"date": DATE, "slot": SLOT, "t": t, "snr": int(snr),
                    "dt": float(dt), "freq": int(freq), "msg": msg})

with open("decodes.jsonl", "a") as f:
    for d in decodes:
        f.write(json.dumps(d) + "\n")

# recent decodes (last 60)
recent = []
try:
    with open("decodes.jsonl") as f:
        recent = [json.loads(l) for l in f.readlines()[-60:]]
except FileNotFoundError:
    pass

# worked calls from the WSJT-X ADIF log
worked, qsos = set(), []
try:
    adi = open(ADIF, errors="ignore").read()
    def fld(rec, name):
        m = re.search(rf"<{name}:(\d+)>", rec, re.I)
        return rec[m.end():m.end() + int(m.group(1))] if m else ""
    for rec in adi.split("<eor>"):
        call = fld(rec, "call")
        if call:
            worked.add(call.upper())
            qsos.append({"call": call.upper(), "band": fld(rec, "band"),
                         "date": fld(rec, "qso_date"), "grid": fld(rec, "gridsquare")})
except FileNotFoundError:
    pass

# candidate next calls: CQs in the last 3 slots, unworked, best SNR first.
# Modifier whitelist (DX/POTA/...) so 1x1 special-event calls like "CQ K2A FN13"
# aren't eaten by a greedy optional group (which made the grid parse as the call).
cq_re = re.compile(
    r"^CQ(?:\s+(?:DX|NA|EU|AS|SA|AF|OC|POTA|SOTA|QRP|TEST))?"
    r"\s+([A-Z0-9/]{3,})\s*([A-R]{2}[0-9]{2})?\b", re.I)
slots_seen = sorted({d["slot"] for d in recent})[-3:]
cands = {}
for d in recent:
    if d["slot"] not in slots_seen:
        continue
    m = cq_re.match(d["msg"])
    if m:
        call = m.group(1).upper()
        if call != MYCALL and call not in worked:
            if call not in cands or d["snr"] > cands[call]["snr"]:
                cands[call] = {"call": call, "grid": m.group(2) or "",
                               "snr": d["snr"], "freq": d["freq"], "slot": d["slot"]}
ranked = sorted(cands.values(), key=lambda c: -c["snr"])

# anyone calling ME?
calling_me = [d for d in recent if d["slot"] in slots_seen
              and d["msg"].upper().startswith(MYCALL + " ")]

status = {
    "updated_utc": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime()),
    "slot": SLOT, "slot_decodes": len(decodes),
    "recent": recent[-40:],
    "next_call": ranked[0] if ranked else None,
    "candidates": ranked[:8],
    "calling_me": calling_me,
    "qso_count": len(qsos),
    "qsos": qsos[-25:],
}
tmp = "status.json.tmp"
with open(tmp, "w") as f:
    json.dump(status, f)
os.replace(tmp, "status.json")
print(f"{SLOT}: {len(decodes)} decodes, next={status['next_call']['call'] if status['next_call'] else '-'}")
