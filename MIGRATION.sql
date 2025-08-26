
-- Optional manual run (init_db() runs these automatically)
CREATE TABLE IF NOT EXISTS public.users (
  email       text PRIMARY KEY,
  name        text,
  title       text,
  region      text,
  created_at  timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.logs (
  id         bigserial PRIMARY KEY,
  email      text NOT NULL,
  role       text NOT NULL CHECK (role IN ('user','assistant','system')),
  message    text NOT NULL,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_logs_email_time ON public.logs(email, created_at DESC);

CREATE TABLE IF NOT EXISTS public.session_summaries (
  email      text PRIMARY KEY,
  summary    text,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_preferences (
  email      text PRIMARY KEY,
  tone       text DEFAULT 'friendly',
  verbosity  text DEFAULT 'concise',
  channel    text DEFAULT 'web',
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.user_notes (
  id         bigserial PRIMARY KEY,
  email      text NOT NULL,
  topic      text NOT NULL,
  note       text NOT NULL,
  weight     real DEFAULT 0.5,
  created_at timestamptz DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_user_notes_email ON public.user_notes(email);
CREATE INDEX IF NOT EXISTS idx_user_notes_fts
  ON public.user_notes USING GIN (to_tsvector('english', note));

CREATE TABLE IF NOT EXISTS public.feedback (
  id         bigserial PRIMARY KEY,
  email      text NOT NULL,
  session_id text,
  message_id text,
  rating     int,
  note       text,
  created_at timestamptz DEFAULT now()
);
