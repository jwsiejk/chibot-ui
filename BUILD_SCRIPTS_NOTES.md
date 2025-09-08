# Build/Test scripts notes

- **render_build_runtime.sh** — Production build without running tests. Use this as your Render **Build Command** to avoid timeouts; it installs deps and verifies `app.asgi_gateway:asgi` imports.
- **build_checks.sh** — Curated checks with safe async/thread cleanup to prevent exit code 143 on Render. Run this in CI or a separate non-prod Render service when you want to gate deploys on tests.
- **pytest.ini** — Adds a 60s per-test timeout and stops on first failure.
- **tests/conftest.py** — Context-managed Starlette `TestClient` to ensure the event loop/executor shuts down after tests.
