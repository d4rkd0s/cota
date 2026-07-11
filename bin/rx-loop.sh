#!/usr/bin/env bash
# FT8-Claude RX loop: aligned capture -> jt9 decode -> waterfall -> status.json
# RX ONLY. This script never touches PTT or the CAT port.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[ -f "$ROOT/station.conf" ] && . "$ROOT/station.conf"
DATA=${DATA:-$ROOT/data}
BIN=$ROOT/bin
SRC=${PA_SOURCE:-alsa_input.usb-C-Media_Electronics_Inc._USB_Audio_Device-00.mono-fallback}
MYCALL=${MYCALL:-N0CALL}
BAND=${BAND:-40m}
cd "$DATA" || exit 1
echo "$(date -u '+%F %T') rx-loop start" >> rx-loop.log

while :; do
    # sleep to the next :00/:15/:30/:45 boundary
    python3 -c 'import time; t=time.time(); time.sleep(15 - (t % 15))'
    DATE=$(date -u +%y%m%d); SLOT=$(date -u +%H%M%S)

    timeout 13.5 parecord --device="$SRC" --rate=12000 --channels=1 \
        --format=s16le --raw slot.raw 2>/dev/null
    if [ ! -s slot.raw ]; then
        echo "$(date -u '+%T') no audio captured (WSJT-X holding plughw? DE-19 missing?)" >> rx-loop.log
        continue
    fi

    python3 - <<'PY'
import wave
raw = open('slot.raw','rb').read()
w = wave.open('slot.wav','wb')
w.setnchannels(1); w.setsampwidth(2); w.setframerate(12000)
w.writeframes(raw); w.close()
PY

    sox slot.wav -n spectrogram -x 900 -y 257 -z 70 -l \
        -t "${MYCALL} ${BAND} FT8  ${DATE} ${SLOT}Z" -o waterfall_new.png 2>/dev/null \
        && mv -f waterfall_new.png waterfall.png

    jt9 -8 -d 2 slot.wav > jt9out.txt 2>/dev/null
    python3 "$BIN/parse_decodes.py" "$DATE" "$SLOT" < jt9out.txt >> rx-loop.log 2>&1
done
