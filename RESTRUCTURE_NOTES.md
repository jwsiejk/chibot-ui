# Restructure Summary

This repository was reorganized to prevent app.py bloat while preserving **all behavior**.

## What changed (no regressions)
- Original **app.py** is now wrapped as a factory in **app/legacy_app.py** (`create_app()`).
- Top-level **app.py** is now a thin shim:
  ```python
  from app import create_app
  app = create_app()
  ```
- Guardrails added:
  - `pyproject.toml` (Black + Ruff)
  - `.pylintrc` with `max-module-lines=400`
  - `.pre-commit-config.yaml` (Black, Ruff, Pylint)
- Cleanup: removed caches (`__pycache__`, `.ipynb_checkpoints`), stray binaries.
- Future-ready folders created:
  - `app/routes/`, `app/services/`, `app/data/`, `app/utils/` (you can gradually move code here).

## Why this is safe
We didn't rewrite route logic or business logic. The entire previous application code runs inside
`create_app()`, and we simply return the same Flask `app` object. Deployment entry points like
`gunicorn -w 4 app:app` continue to work as before.

## Next safe steps (optional)
- Migrate individual route groups from `app/legacy_app_raw.py` into `app/routes/*.py` Blueprints.
- Move data access helpers into `app/data/` and update imports gradually.
- Keep modules under 400 lines; the linter will flag overgrowth.

