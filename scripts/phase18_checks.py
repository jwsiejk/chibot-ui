from pathlib import Path
import sys, os
BASE = Path(__file__).resolve().parents[1]
def run():
    assert (BASE/"scripts"/"ci_nightly_staging.sh").exists()
    assert (BASE/"scripts"/"scenario_voice_loop.py").exists()
    print("PHASE18: PASS")
if __name__=="__main__":
    run()
