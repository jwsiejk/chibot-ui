# Flow hand-off validation

The flow hand-off ZIP bundles the transcript payload (`flow.ndjson` or
`events/flow.ndjson.gz`), the prompt, supplemental metadata, and a
`manifest.json` file that records the SHA-1 fingerprints for every artifact.
When `privacy.pii_scrub` is enabled the export pipeline hashes obvious PII
tokens (email addresses, API keys, external IPv4s) before the files are added
to the archive. The response exposes the `X-Flow-PII-Scrubbed` header so you can
confirm the option that was applied.

If the optional `limits.max_bytes` guard is supplied the server enforces the cap
and returns `413` with `{"error": "export_too_large"}` when the assembled ZIP
would exceed the limit. This keeps bulk exports from starving the service and
gives operators quick feedback when they need to tighten the include set.

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
prints `OK`. Because the manifest lists the `include`, `privacy`, and `limits`
blocks verbatim you can verify that UI toggles (logs, WS frames, PII scrub,
max-bytes) were captured accurately.

The `tools/example_flow.zip` fixture mirrors the structure produced by the
`/api/v1/flow/handoff` endpoint and can be used as a smoke test for future
changes to the schema or validator. Refer to `docs/flow_logging.md` for an
overview of the logging taxonomy and the **`flow_dropped`** breadcrumb that now
appears whenever FlowStore trims old events.
