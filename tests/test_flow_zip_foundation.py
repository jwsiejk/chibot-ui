from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from app.admin.flow_zip import build_flow_zip


class BuildFlowZipFoundationTest(unittest.TestCase):
    def _prepare_session(self, root: Path, sid: str) -> Path:
        session_dir = root / sid
        session_dir.mkdir(parents=True, exist_ok=True)

        manifest = {
            "sid": sid,
            "schema_version": "1",
            "started_ms": 1_732_200_000_000,
            "open": False,
            "events_written": 2,
            "by_type": {"test": 2},
        }
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, separators=(",", ":")), encoding="utf-8"
        )
        (session_dir / "events.ndjson").write_text("{}\n{}\n", encoding="utf-8")

        return session_dir

    def test_build_flow_zip_foundation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "exports"
            sid = "sid-test"
            session_dir = self._prepare_session(root, sid)

            archive_path = build_flow_zip(sid, root=root)
            self.assertEqual(archive_path, session_dir / "flow.zip")
            self.assertTrue(archive_path.exists())

            with ZipFile(archive_path) as zf:
                names = zf.namelist()
                self.assertEqual(
                    names,
                    [
                        "README.txt",
                        "events.redacted.ndjson",
                        "flow_timeline.ndjson",
                        "manifest.json",
                        "nlg.ndjson",
                        "nlu.ndjson",
                    ],
                )
                readme = zf.read("README.txt").decode("utf-8")

            self.assertIn(sid, readme)
            self.assertIn("privacy-safe flow artifacts", readme)
            self.assertNotIn("1732200000000", readme)  # ensure human friendly timestamp

            digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            archive_path_second = build_flow_zip(sid, root=root)
            self.assertEqual(archive_path_second.read_bytes(), archive_path.read_bytes())
            self.assertEqual(
                hashlib.sha256(archive_path_second.read_bytes()).hexdigest(), digest
            )

    def test_build_flow_zip_requires_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            root = tmp_path / "exports"
            sid = "missing"
            session_dir = root / sid
            session_dir.mkdir(parents=True, exist_ok=True)

            # Only manifest present
            (session_dir / "manifest.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                build_flow_zip(sid, root=root)

            # Remove manifest, only events present
            (session_dir / "manifest.json").unlink()
            (session_dir / "events.ndjson").write_text("{}\n", encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                build_flow_zip(sid, root=root)


if __name__ == "__main__":
    unittest.main()
