"""Tiny shell-style KEY=VALUE parser for station.conf (stdlib only).

Every value comes back as a string; callers cast as needed and supply their
own defaults so a missing file or key can never break the station.
"""
import os

CONF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "station.conf")


def load(path=None):
    """Return {KEY: value} from a shell-style conf file. Missing file -> {}."""
    cfg = {}
    try:
        with open(path or CONF_PATH) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                val = val.split("#", 1)[0].strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                    val = val[1:-1]
                cfg[key.strip()] = val
    except OSError:
        pass
    return cfg
