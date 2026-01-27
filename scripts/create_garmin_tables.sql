-- SQL script to create Garmin integration tables
-- Run this in production if migrations haven't run yet
-- Usage: psql $DATABASE_URL -f scripts/create_garmin_tables.sql

-- Create user_integrations table
CREATE TABLE IF NOT EXISTS user_integrations (
    id VARCHAR NOT NULL PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    provider_user_id VARCHAR NOT NULL,
    access_token VARCHAR NOT NULL,
    refresh_token VARCHAR NOT NULL,
    token_expires_at TIMESTAMPTZ,
    scopes JSONB NOT NULL DEFAULT '{}',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    revoked_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_user_integrations_user_id ON user_integrations(user_id);
CREATE INDEX IF NOT EXISTS idx_user_integrations_provider ON user_integrations(provider);
CREATE INDEX IF NOT EXISTS idx_user_integration_provider_user ON user_integrations(provider, provider_user_id);

-- Create unique constraint (only if it doesn't exist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE table_name = 'user_integrations' 
        AND constraint_name = 'uq_user_integration_user_provider'
    ) THEN
        ALTER TABLE user_integrations 
        ADD CONSTRAINT uq_user_integration_user_provider 
        UNIQUE (user_id, provider);
    END IF;
END $$;

-- Create garmin_webhook_events table
CREATE TABLE IF NOT EXISTS garmin_webhook_events (
    id VARCHAR NOT NULL PRIMARY KEY,
    event_type VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    status VARCHAR NOT NULL DEFAULT 'pending'
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_garmin_webhook_events_event_type ON garmin_webhook_events(event_type);
CREATE INDEX IF NOT EXISTS idx_garmin_webhook_events_received_at ON garmin_webhook_events(received_at);
CREATE INDEX IF NOT EXISTS idx_garmin_webhook_events_status ON garmin_webhook_events(status);

-- Add Garmin fields to activities table (if they don't exist)
DO $$
BEGIN
    -- Add source_provider column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'activities' AND column_name = 'source_provider'
    ) THEN
        ALTER TABLE activities ADD COLUMN source_provider VARCHAR;
        CREATE INDEX IF NOT EXISTS idx_activities_source_provider ON activities(source_provider);
    END IF;

    -- Add external_activity_id column if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'activities' AND column_name = 'external_activity_id'
    ) THEN
        ALTER TABLE activities ADD COLUMN external_activity_id VARCHAR;
        CREATE INDEX IF NOT EXISTS idx_activities_external_activity_id ON activities(external_activity_id);
    END IF;

    -- Add unique constraint if it doesn't exist
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.table_constraints 
        WHERE table_name = 'activities' 
        AND constraint_name = 'uq_activity_source_provider_external_id'
    ) THEN
        ALTER TABLE activities 
        ADD CONSTRAINT uq_activity_source_provider_external_id 
        UNIQUE (source_provider, external_activity_id);
    END IF;
END $$;
