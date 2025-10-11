import subprocess
from pathlib import Path


def test_voice_final_chunk_before_close():
    repo_root = Path(__file__).resolve().parents[1]
    loader = repo_root / 'tests' / 'js' / 'test-loader.mjs'
    spec = repo_root / 'tests' / 'js' / 'voice-order.test.mjs'
    cmd = [
        'node',
        '--loader',
        str(loader),
        '--test',
        str(spec),
    ]
    subprocess.run(cmd, check=True, cwd=repo_root)


def test_advanced_logging_flag_js():
    repo_root = Path(__file__).resolve().parents[1]
    loader = repo_root / 'tests' / 'js' / 'test-loader.mjs'
    spec = repo_root / 'tests' / 'js' / 'logging-flag.test.mjs'
    cmd = [
        'node',
        '--loader',
        str(loader),
        '--test',
        str(spec),
    ]
    subprocess.run(cmd, check=True, cwd=repo_root)
