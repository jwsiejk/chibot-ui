-- Ensure extensions (UUID gen)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Personas table (add missing columns if pre-existing)
CREATE TABLE IF NOT EXISTS personas (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name       TEXT NOT NULL,
  is_active  BOOLEAN NOT NULL DEFAULT FALSE,
  intensity  NUMERIC(4,3) NOT NULL DEFAULT 0.13,
  config     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE personas
  ADD COLUMN IF NOT EXISTS slug TEXT UNIQUE;
CREATE INDEX IF NOT EXISTS idx_personas_active ON personas(is_active);

-- Few-shot examples
CREATE TABLE IF NOT EXISTS persona_examples (
  id               BIGSERIAL PRIMARY KEY,
  persona_id       UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
  intent           TEXT,
  user_pattern     TEXT NOT NULL,
  assistant_target TEXT NOT NULL,
  tags             TEXT[] DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_examples_persona ON persona_examples(persona_id);

-- Intent registry (policy)
CREATE TABLE IF NOT EXISTS persona_intents (
  id           BIGSERIAL PRIMARY KEY,
  persona_id   UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
  name         TEXT NOT NULL,
  teacher_move TEXT,
  chips        TEXT[] DEFAULT '{}',
  tool         TEXT,
  tool_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  priority     INT DEFAULT 100,
  UNIQUE (persona_id, name)
);

-- Intent patterns (NLU)
CREATE TABLE IF NOT EXISTS persona_intent_patterns (
  id           BIGSERIAL PRIMARY KEY,
  persona_id   UUID NOT NULL REFERENCES personas(id) ON DELETE CASCADE,
  intent_name  TEXT NOT NULL,
  pattern      TEXT NOT NULL,
  weight       REAL DEFAULT 1.0
);

-- Seed: active persona slug if none exists (optional)
INSERT INTO personas (slug, name, is_active, intensity, config)
SELECT 'chip-vptm-nebraska','Chip — Virtual PTM', TRUE, 0.13, '{}'::jsonb
WHERE NOT EXISTS (SELECT 1 FROM personas WHERE is_active = TRUE);
