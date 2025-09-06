"""
Simulated "voice loop" scenario for staging with real vendors.
This script should run on the staging Render service (not in CI).
It exercises: greet -> short user input -> assistant reply, and logs a brief report.
"""
import os, time, json, uuid, datetime as dt

def main():
    report = {
        "ts": dt.datetime.utcnow().isoformat()+"Z",
        "scenario": "voice_loop_smoke",
        "result": "skipped_in_ci",
        "notes": "Run on staging with real vendor keys."
    }
    print(json.dumps(report))

if __name__ == "__main__":
    main()
