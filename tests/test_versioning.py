from __future__ import annotations

import os

from app import versioning


def test_inject_static_version_handles_multiple_prefixes() -> None:
    original_build_id = versioning._BUILD_ID
    prev_env = os.environ.get("BUILD_ID")
    os.environ["BUILD_ID"] = "test-build"
    versioning._BUILD_ID = None
    try:
        html = (
            '<link rel="stylesheet" href="/static/css/styles.css" />'
            '<script src="/admin/ui/config_panel.js"></script>'
        )
        rewritten = versioning.inject_static_version(html)
        assert "/static/css/styles.css?v=test-build" in rewritten
        assert "/admin/ui/config_panel.js?v=test-build" in rewritten
    finally:
        versioning._BUILD_ID = original_build_id
        if prev_env is None:
            os.environ.pop("BUILD_ID", None)
        else:
            os.environ["BUILD_ID"] = prev_env
