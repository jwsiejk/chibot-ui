
-- Neon-compatible schema (subset) for Ask Chip
CREATE TABLE IF NOT EXISTS users (
  email TEXT PRIMARY KEY,
  name TEXT, title TEXT, region TEXT,
  created_at TIMESTAMPTZ, last_seen TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  email TEXT REFERENCES users(email),
  persona_id TEXT,
  started_at TIMESTAMPTZ, ended_at TIMESTAMPTZ,
  summary_jsonb JSONB
);
CREATE TABLE IF NOT EXISTS messages (
  id BIGSERIAL PRIMARY KEY,
  session_id TEXT REFERENCES sessions(id),
  role TEXT NOT NULL,
  text TEXT NOT NULL,
  meta_jsonb JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS logs (
  id BIGSERIAL PRIMARY KEY,
  email TEXT, role TEXT, message TEXT, created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS memory_facts (
  email TEXT, key TEXT, value_jsonb JSONB, updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY(email, key)
);
CREATE TABLE IF NOT EXISTS app_config (
  key TEXT PRIMARY KEY,
  value_json JSONB
);
CREATE TABLE IF NOT EXISTS layouts (
  breakpoint TEXT PRIMARY KEY,
  state_json JSONB
);
