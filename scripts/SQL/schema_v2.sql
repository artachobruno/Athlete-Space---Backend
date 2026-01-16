-- =========================================
-- AthleteSpace V2 Schema (Postgres)
-- =========================================

-- Required for gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- -------------------------
-- Enums (as TEXT + CHECK)
-- -------------------------
-- (Using CHECK constraints keeps migrations simpler than CREATE TYPE in early iteration)

-- =========================================
-- 1) Core identity + settings
-- =========================================

CREATE TABLE users (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  email         TEXT NOT NULL UNIQUE,
  auth_provider TEXT NOT NULL CHECK (auth_provider IN ('google', 'email', 'apple')),
  role          TEXT NOT NULL DEFAULT 'athlete' CHECK (role IN ('athlete', 'coach', 'admin')),
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled', 'deleted')),
  timezone      TEXT NOT NULL DEFAULT 'UTC',
  onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_settings (
  user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================================
-- 2) Profiles (athlete/coach)
-- =========================================

CREATE TABLE athlete_profiles (
  user_id         UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  first_name      TEXT,
  last_name       TEXT,
  sex             TEXT CHECK (sex IN ('male','female','other')),
  birthdate       DATE,
  height_cm       INTEGER,
  weight_kg       DOUBLE PRECISION,
  ftp_watts       INTEGER,
  threshold_pace_sec_per_km INTEGER,
  baseline_weekly_run_km DOUBLE PRECISION,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE coach_profiles (
  user_id     UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  bio         TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Coach ↔ Athlete relationship (both are users; naming is explicit)
CREATE TABLE coach_athletes (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  coach_user_id   UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  athlete_user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','ended')),
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (coach_user_id, athlete_user_id)
);

-- =========================================
-- 3) Auth / Connected accounts
-- =========================================

CREATE TABLE google_accounts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  google_sub    TEXT NOT NULL UNIQUE,  -- stable Google subject
  email         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id)
);

CREATE TABLE strava_auth (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  athlete_id       BIGINT NOT NULL,
  access_token     TEXT NOT NULL,
  refresh_token    TEXT NOT NULL,
  expires_at       TIMESTAMPTZ NOT NULL,
  scope            TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id),
  UNIQUE (athlete_id)
);

CREATE INDEX idx_strava_auth_expires_at ON strava_auth(expires_at);

-- =========================================
-- 4) Ingestion layer (raw)
-- =========================================

CREATE TABLE provider_events (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  provider      TEXT NOT NULL CHECK (provider IN ('strava')),
  provider_user_id TEXT,          -- e.g., strava athlete id as text if needed
  event_type    TEXT NOT NULL,    -- webhook type
  event_time    TIMESTAMPTZ NOT NULL DEFAULT now(),
  payload       JSONB NOT NULL,
  processed     BOOLEAN NOT NULL DEFAULT FALSE,
  processed_at  TIMESTAMPTZ,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_provider_events_processed ON provider_events(processed, event_time);

CREATE TABLE strava_activities_raw (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  strava_activity_id BIGINT NOT NULL,
  raw_payload        JSONB NOT NULL,
  fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, strava_activity_id)
);

-- =========================================
-- 5) Canonical activities (normalized)
-- =========================================

CREATE TABLE activities (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  source            TEXT NOT NULL DEFAULT 'strava' CHECK (source IN ('strava','manual','import')),
  source_activity_id TEXT, -- e.g. strava_activity_id as text if multi-provider later
  sport             TEXT NOT NULL CHECK (sport IN ('run','ride','swim','strength','walk','other')),
  starts_at         TIMESTAMPTZ NOT NULL,
  ends_at           TIMESTAMPTZ,
  duration_seconds  INTEGER NOT NULL CHECK (duration_seconds >= 0),
  distance_meters   DOUBLE PRECISION CHECK (distance_meters >= 0),
  elevation_gain_meters DOUBLE PRECISION CHECK (elevation_gain_meters >= 0),
  calories          DOUBLE PRECISION CHECK (calories >= 0),
  tss               DOUBLE PRECISION CHECK (tss >= 0),
  tss_version       TEXT,
  title             TEXT,
  notes             TEXT,
  metrics           JSONB NOT NULL DEFAULT '{}'::jsonb, -- HR, pace series, power, laps, etc.
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, source, source_activity_id)
);

CREATE INDEX idx_activities_user_time ON activities(user_id, starts_at DESC);

-- =========================================
-- 6) Planning (season plans + revisions + planned sessions)
-- =========================================

CREATE TABLE season_plans (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name          TEXT,
  season_start  DATE NOT NULL,
  season_end    DATE NOT NULL,
  sport_focus   TEXT CHECK (sport_focus IN ('run','tri','ride','swim','mixed')),
  goal          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_season_plans_user ON season_plans(user_id, season_start, season_end);

CREATE TABLE plan_revisions (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season_plan_id UUID REFERENCES season_plans(id) ON DELETE CASCADE,
  revision_num  INTEGER NOT NULL,
  status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived','rolled_back')),
  reason        TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (season_plan_id, revision_num)
);

CREATE TABLE workouts (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  sport         TEXT NOT NULL CHECK (sport IN ('run','ride','swim','strength','other')),
  name          TEXT NOT NULL,
  description   TEXT,
  structure     JSONB NOT NULL DEFAULT '{}'::jsonb,  -- intervals, targets, etc.
  tags          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workout_steps (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id     UUID NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  step_order    INTEGER NOT NULL,
  step_type     TEXT NOT NULL, -- warmup/interval/recovery/cooldown/etc
  targets       JSONB NOT NULL DEFAULT '{}'::jsonb,
  notes         TEXT,
  UNIQUE (workout_id, step_order)
);

CREATE TABLE planned_sessions (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id          UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  season_plan_id   UUID REFERENCES season_plans(id) ON DELETE CASCADE,
  revision_id      UUID REFERENCES plan_revisions(id) ON DELETE SET NULL,

  -- Canonical time
  starts_at        TIMESTAMPTZ NOT NULL,
  ends_at          TIMESTAMPTZ,

  sport            TEXT NOT NULL CHECK (sport IN ('run','ride','swim','strength','other')),
  session_type     TEXT, -- easy/tempo/long/interval/brick/etc
  title            TEXT,
  notes            TEXT,

  duration_seconds INTEGER CHECK (duration_seconds >= 0),
  distance_meters  DOUBLE PRECISION CHECK (distance_meters >= 0),
  intensity        TEXT, -- Z1..Z5, RPE, etc
  intent           TEXT, -- optional "weekly_intents" link if you keep it

  workout_id       UUID REFERENCES workouts(id) ON DELETE SET NULL,

  status           TEXT NOT NULL DEFAULT 'planned'
                  CHECK (status IN ('planned','completed','skipped','moved','canceled')),

  tags             JSONB NOT NULL DEFAULT '{}'::jsonb,

  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_planned_sessions_user_time ON planned_sessions(user_id, starts_at DESC);
CREATE INDEX idx_planned_sessions_revision  ON planned_sessions(revision_id);

-- =========================================
-- 7) Linking (canonical pairing between planned + completed)
-- =========================================

CREATE TABLE session_links (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  planned_session_id UUID NOT NULL REFERENCES planned_sessions(id) ON DELETE CASCADE,
  activity_id        UUID NOT NULL REFERENCES activities(id) ON DELETE CASCADE,

  status             TEXT NOT NULL DEFAULT 'proposed'
                    CHECK (status IN ('proposed','confirmed','rejected')),
  confidence         DOUBLE PRECISION CHECK (confidence >= 0 AND confidence <= 1),
  method             TEXT NOT NULL DEFAULT 'auto' CHECK (method IN ('auto','manual')),
  notes              TEXT,

  created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

  UNIQUE (planned_session_id),
  UNIQUE (activity_id)
);

CREATE INDEX idx_session_links_user ON session_links(user_id, status);

-- =========================================
-- 8) Execution + compliance
-- =========================================

CREATE TABLE workout_executions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workout_id         UUID REFERENCES workouts(id) ON DELETE SET NULL,
  planned_session_id UUID REFERENCES planned_sessions(id) ON DELETE SET NULL,
  activity_id        UUID REFERENCES activities(id) ON DELETE SET NULL,

  started_at         TIMESTAMPTZ,
  completed_at       TIMESTAMPTZ,
  status             TEXT NOT NULL DEFAULT 'completed'
                    CHECK (status IN ('completed','partial','aborted','failed')),

  summary            JSONB NOT NULL DEFAULT '{}'::jsonb, -- computed results
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_workout_exec_user_time ON workout_executions(user_id, COALESCE(completed_at, started_at) DESC);

CREATE TABLE step_compliance (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workout_execution_id UUID NOT NULL REFERENCES workout_executions(id) ON DELETE CASCADE,
  workout_step_id     UUID REFERENCES workout_steps(id) ON DELETE SET NULL,
  compliance          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE workout_compliance_summary (
  id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workout_execution_id UUID NOT NULL REFERENCES workout_executions(id) ON DELETE CASCADE,
  summary             JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (workout_execution_id)
);

-- =========================================
-- 9) Analytics (derived, rebuildable)
-- =========================================

CREATE TABLE daily_training_summary (
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day         DATE NOT NULL,
  summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, day)
);

CREATE TABLE daily_training_load (
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day         DATE NOT NULL,
  ctl         DOUBLE PRECISION,
  atl         DOUBLE PRECISION,
  tsb         DOUBLE PRECISION,
  load_model  TEXT NOT NULL DEFAULT 'default',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, day)
);

CREATE TABLE weekly_training_summary (
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start  DATE NOT NULL, -- Monday start
  summary     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, week_start)
);

CREATE TABLE weekly_reports (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start  DATE NOT NULL,
  report      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, week_start)
);

CREATE TABLE weekly_intents (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  week_start  DATE NOT NULL,
  intent      JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, week_start)
);

-- =========================================
-- 10) Decisions (pairing + daily)
-- =========================================

CREATE TABLE pairing_decisions (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id            UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  planned_session_id UUID REFERENCES planned_sessions(id) ON DELETE CASCADE,
  activity_id        UUID REFERENCES activities(id) ON DELETE CASCADE,
  decision           TEXT NOT NULL CHECK (decision IN ('accept','reject','manual_link','manual_unlink')),
  reason             TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE daily_decisions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  day         DATE NOT NULL,
  decision    JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, day)
);

-- =========================================
-- 11) Conversations (assistant/coaching)
-- =========================================

CREATE TABLE conversations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title       TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversation_messages (
  id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  sender          TEXT NOT NULL CHECK (sender IN ('user','assistant','coach','system')),
  content         TEXT NOT NULL,
  metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_conv_msgs_conv_time ON conversation_messages(conversation_id, created_at);

CREATE TABLE conversation_summaries (
  conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  summary         TEXT NOT NULL,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversation_progress (
  conversation_id UUID PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
  progress        JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================================
-- 12) Exports
-- =========================================

CREATE TABLE workout_exports (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  workout_id  UUID REFERENCES workouts(id) ON DELETE SET NULL,
  export_type TEXT NOT NULL CHECK (export_type IN ('garmin','zwift','trainerroad','json')),
  payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================================
-- 13) Convenience view: unified calendar feed
-- =========================================

CREATE VIEW calendar_items AS
SELECT
  p.user_id,
  p.id AS item_id,
  'planned'::text AS kind,
  p.starts_at,
  p.ends_at,
  p.sport,
  p.title,
  p.status,
  jsonb_build_object(
    'planned_session_id', p.id,
    'workout_id', p.workout_id,
    'distance_meters', p.distance_meters,
    'duration_seconds', p.duration_seconds,
    'tags', p.tags
  ) AS payload
FROM planned_sessions p

UNION ALL

SELECT
  a.user_id,
  a.id AS item_id,
  'activity'::text AS kind,
  a.starts_at,
  a.ends_at,
  a.sport,
  a.title,
  'completed'::text AS status,
  jsonb_build_object(
    'activity_id', a.id,
    'source', a.source,
    'distance_meters', a.distance_meters,
    'duration_seconds', a.duration_seconds,
    'tss', a.tss,
    'metrics', a.metrics
  ) AS payload
FROM activities a;
