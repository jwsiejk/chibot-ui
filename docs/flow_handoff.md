# Flow hand-off validation

The flow hand-off ZIP bundles the redacted transcript (`flow.ndjson`), the
prompt, supplemental metadata, and a `manifest.json` file that records the
SHA-1 fingerprints for every payload. Although the production API does not yet
embed this manifest, you can validate an export locally with the helper script
added for Phase 0.

## Prerequisites

The validator understands JSON Schema draft 7. It will use the `jsonschema`
package when available, but also ships with a lightweight fallback so the script
works even in constrained environments.

```bash
pip install jsonschema
```

## Running the validator

Pass the ZIP archive and the JSON Schema to the validation script:

```bash
python tools/validate_flow_zip.py tools/example_flow.zip tools/flow_manifest.schema.json
```

The script checks that the manifest satisfies the schema and that the SHA-1
values listed for each file match the actual archive contents. A successful run
prints `OK`.

The `tools/example_flow.zip` fixture mirrors the structure produced by the
`/api/v1/flow/handoff` endpoint and can be used as a smoke test for future
changes to the schema or validator.
