# Admin settings bootstrap

The application expects a small set of runtime flags to exist in the `public.admin_settings` table. The canonical schema uses the columns `key` (primary key) and `value_jsonb` (JSONB payload). No legacy `value` or `settings_value` columns are required or supported.

Seed the following keys to ensure deterministic behaviour on first boot:

```sql
INSERT INTO public.admin_settings (key, value_jsonb, updated_by)
VALUES
  ('diag_client_hud', 'false', 'bootstrap'),
  ('audio_guardrails', '{"enabled": true}', 'bootstrap'),
  ('diag_audio_guard', 'true', 'bootstrap'),
  ('diag_chunk_sample_n', '10', 'bootstrap')
ON CONFLICT (key) DO UPDATE SET
  value_jsonb = EXCLUDED.value_jsonb,
  updated_at = NOW(),
  updated_by = EXCLUDED.updated_by,
  version = admin_settings.version + 1;
```

Values must be valid JSON literals (`true`, `false`, numbers, strings with quotes, or structured objects such as `{"enabled": true}`). Update the keys directly in Neon to toggle features at runtime without code changes.
