from __future__ import annotations

from pathlib import Path


def test_current_build_id_refreshes_when_static_files_change(monkeypatch, tmp_path: Path) -> None:
    from app import config_build

    # Direct the fingerprint scanner to a controlled temporary directory.
    monkeypatch.setattr(config_build, "_FINGERPRINT_PATHS", (tmp_path,))
    monkeypatch.setattr(config_build, "_REFRESH_INTERVAL_SECONDS", 0.0, raising=False)

    # Reset internal caches so the test has a clean slate.
    monkeypatch.setattr(config_build, "_BUILD_ID", None)
    monkeypatch.setattr(config_build, "_BUILD_FINGERPRINT_KEY", None)
    monkeypatch.setattr(config_build, "_FINGERPRINT_CACHE", None)
    monkeypatch.setattr(config_build, "_FINGERPRINT_LAST_CHECK", 0.0)
    monkeypatch.setattr(config_build, "_git_sha_short_cached", lambda: "sha-test")

    first_id = config_build.current_build_id()

    # The build identifier should remain stable when no files have changed.
    assert config_build.current_build_id() == first_id

    # Touch a file in the temporary directory to simulate a redeploy.
    target = tmp_path / "example.txt"
    target.write_text("one")

    # Force the fingerprint cache to be recomputed on the next call.
    config_build._FINGERPRINT_CACHE = None  # type: ignore[attr-defined]

    refreshed_id = config_build.current_build_id()
    assert refreshed_id != first_id

