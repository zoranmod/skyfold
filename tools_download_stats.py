"""How many downloads of Skyfold came from other people, release by release.

    python tools_download_stats.py                 # the table
    python tools_download_stats.py --verified v1.0.22   # note a check download of the maintainers

GitHub only counts downloads per file. This subtracts the ones that were ours:
  * the maintainer's own updates in the app (read from Skyfold's log, which is rotated, so every
    update found is kept in a small ledger on this PC), each one zip + one .sha256, and
  * downloads made to check a release (recorded with --verified), also one zip + one .sha256.
What is left are downloads by other people: first installs and their updates. GitHub doesn't say
how many different people that is; downloads of the zip are the closest measure.

The ledger stays on this PC (%LOCALAPPDATA%/Skyfold/download-ledger.json); nothing is sent anywhere
except the one authenticated `gh api` request for the release list.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = "zoranmod/skyfold"
ZIP = "Skyfold-windows.zip"
DATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Skyfold"
LEDGER = DATA / "download-ledger.json"
AVAILABLE = re.compile(r"^(\S+ \S+) INFO skyfold: Update: Skyfold (\d+(?:\.\d+)+) is available")
INSTALLED = re.compile(r"^(\S+ \S+) INFO skyfold\.update: Update installed")


def load() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"updates": {}, "verified": []}


def save(ledger: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps(ledger, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, LEDGER)


def read_updates(ledger: dict) -> int:
    """In-app updates of this PC: an "is available" line followed by "Update installed"."""
    found = 0
    for log in sorted(DATA.glob("skyfold.log*")):
        try:
            lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        offered = None
        for line in lines:
            m = AVAILABLE.match(line)
            if m:
                offered = m.group(2)
                continue
            m = INSTALLED.match(line)
            if m and offered:
                key = m.group(1)  # the time it was installed: the same update is never counted twice
                if key not in ledger["updates"]:
                    ledger["updates"][key] = "v" + offered
                    found += 1
                offered = None
    return found


def releases() -> list[dict]:
    query = ".[] | {tag_name, published_at, assets: [.assets[] | {name, download_count}]} | @json"
    out = subprocess.run(["gh", "api", f"repos/{REPO}/releases", "--paginate", "--jq", query],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    data = [json.loads(line) for line in out.splitlines() if line.strip()]  # one release per line
    rows = []
    for rel in data:
        counts = {a["name"]: a["download_count"] for a in rel.get("assets", [])}
        rows.append({"tag": rel["tag_name"], "date": (rel.get("published_at") or "")[:10],
                     "zip": counts.get(ZIP, 0), "sha": counts.get(ZIP + ".sha256", 0)})
    return rows


def main(argv: list[str]) -> None:
    ledger = load()
    if len(argv) == 2 and argv[0] == "--verified":
        ledger["verified"].append(argv[1] if argv[1].startswith("v") else "v" + argv[1])
        save(ledger)
        print(f"Noted a check download of {argv[1]}.")
        return
    new = read_updates(ledger)
    save(ledger)
    mine: dict[str, int] = {}
    for tag in list(ledger["updates"].values()) + ledger["verified"]:
        mine[tag] = mine.get(tag, 0) + 1
    rows = releases()
    print(f"{'release':<10} {'date':<11} {'zip':>5} {'ours':>5} {'others':>7}   (.sha256: all / others)")
    tz = to = ts = tso = 0
    for r in rows:
        ours = mine.get(r["tag"], 0)
        others = max(0, r["zip"] - ours)
        sha_others = max(0, r["sha"] - ours)
        tz, to, ts, tso = tz + r["zip"], to + others, ts + r["sha"], tso + sha_others
        print(f"{r['tag']:<10} {r['date']:<11} {r['zip']:>5} {ours:>5} {others:>7}   ({r['sha']} / {sha_others})")
    print("-" * 52)
    print(f"{'total':<22} {tz:>5} {tz - to:>5} {to:>7}   ({ts} / {tso})")
    print()
    print(f"Ours: {len(ledger['updates'])} in-app updates on this PC (log), {len(ledger['verified'])} check downloads"
          + (f"; {new} new updates found in the log now." if new else "."))
    print("Others: first installs plus their own updates; not the number of different people.")


if __name__ == "__main__":
    main(sys.argv[1:])
