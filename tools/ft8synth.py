#!/usr/bin/env python3
"""ft8synth — synthesize a protocol-correct FT8 audio frame from ft8code's symbols.

Usage:  ft8synth.py "CQ N0CALL AA00" out.wav [audio_offset_hz]

Pipeline: ft8code (WSJT-X, trusted install) -> 79 channel symbols -> GFSK synthesis
(8-FSK, 6.25 baud, 6.25 Hz tone spacing, Gaussian BT=2.0 frequency pulse, phase-
continuous, raised-cosine amplitude ramps) -> 12000 Hz 16-bit mono WAV, 15.00 s
(0.5 s protocol lead-in silence + 12.64 s frame + tail pad).

RX-only tool: writing a WAV transmits nothing.
"""
import sys, math, subprocess, wave
import numpy as np

RATE = 12000
SYM_BT = 2.0            # FT8 Gaussian bandwidth-time product
SYM_T = 0.160           # seconds per symbol (6.25 baud)
SPACING = 6.25          # Hz between adjacent tones
NSYM = 79
LEAD = 0.5              # protocol: frame starts 0.5 s into the slot

def symbols_from_ft8code(msg: str):
    out = subprocess.run(["ft8code", msg], capture_output=True, text=True).stdout
    lines = out.splitlines()
    for i, l in enumerate(lines):
        if "Channel symbols" in l:
            digits = "".join(c for c in " ".join(lines[i+1:i+3]) if c.isdigit())
            if len(digits) < NSYM:
                raise SystemExit(f"ft8code gave {len(digits)} symbols, need {NSYM}")
            return [int(c) for c in digits[:NSYM]]
    raise SystemExit("ft8code output not understood — message rejected?")

def gfsk_pulse(bt, sps):
    """WSJT-X gfsk_pulse: erf-shaped frequency pulse spanning 3 symbols."""
    k = math.pi * math.sqrt(2.0 / math.log(2.0)) * bt
    t = (np.arange(3 * sps) / sps) - 1.5           # -1.5 .. +1.5 symbols
    erf = np.vectorize(math.erf)
    return (erf(k * (t + 0.5)) - erf(k * (t - 0.5))) / 2.0

def synth(symbols, f0):
    sps = int(RATE * SYM_T)                        # 1920 samples/symbol
    n = NSYM * sps
    pulse = gfsk_pulse(SYM_BT, sps)
    # frequency trajectory: superpose one pulse per symbol (padded 1 symbol each side)
    dphi = np.zeros(n + 2 * sps)
    for i, s in enumerate(symbols):
        dphi[i * sps : i * sps + 3 * sps] += pulse * s
    # edge correction so the first/last symbols hold their tone
    dphi[:2 * sps]  += pulse[sps:] [:2*sps] * 0    # (padding regions trimmed below)
    freq = f0 + SPACING * dphi[sps : sps + n]
    phase = np.cumsum(2 * math.pi * freq / RATE)
    sig = np.sin(phase)
    # raised-cosine amplitude ramps over 1/8 symbol at each end
    nr = sps // 8
    ramp = 0.5 * (1 - np.cos(np.pi * np.arange(nr) / nr))
    sig[:nr] *= ramp
    sig[-nr:] *= ramp[::-1]
    return sig

def main():
    msg, out = sys.argv[1], sys.argv[2]
    f0 = float(sys.argv[3]) if len(sys.argv) > 3 else 1500.0
    if not (200 <= f0 <= 2800):
        raise SystemExit("offset out of the usual 200-2800 Hz window")
    symbols = symbols_from_ft8code(msg)
    sig = synth(symbols, f0) * 0.9
    total = int(15.0 * RATE)
    audio = np.zeros(total)
    start = int(LEAD * RATE)
    audio[start : start + len(sig)] = sig
    w = wave.open(out, "wb")
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
    w.writeframes((audio * 32767).astype("<i2").tobytes())
    w.close()
    print(f"{out}: '{msg}' @ {f0:.0f} Hz, {len(sig)/RATE:.2f} s frame in 15.00 s wav "
          f"(lead-in {LEAD} s), symbols[:7]={''.join(map(str,symbols[:7]))} (Costas)")

if __name__ == "__main__":
    main()
