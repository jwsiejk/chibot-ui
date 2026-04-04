from __future__ import annotations

from pathlib import Path


class ArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def store_session_artifact(self, session_id: str, artifact_id: str, filename: str, content: bytes) -> Path:
        session_dir = self.root / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        target = session_dir / f'{artifact_id}-{Path(filename).name}'
        target.write_bytes(content)
        return target
