"""Write a read-only Polymarket US NFL report for the dashboard and ChatGPT.

Usage: python scripts/update_us_snapshot.py
The result is public information only. No trading credentials are read.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from us_feed import fetch_us_nfl_snapshot


def main() -> None:
    report = fetch_us_nfl_snapshot(days=7, max_events=30, max_quotes=80)
    target = ROOT / "data" / "latest_us_nfl.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(f"{datetime.now(timezone.utc).isoformat()} — "
          f"{report['games_count']} NFL matchups, {report['quote_count']} US quotes")
    if report["warnings"]:
        print("Warning:", "; ".join(report["warnings"]))
    if not report["quote_count"]:
        # Keep prior known-good file rather than replacing it with an empty report.
        raise SystemExit("No US quotes returned: not committing an empty snapshot")


if __name__ == "__main__":
    main()
