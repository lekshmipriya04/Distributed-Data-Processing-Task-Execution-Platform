-- Ensure the main database exists (created by POSTGRES_DB env var already)
-- Create additional schemas for service isolation
CREATE SCHEMA IF NOT EXISTS storage;

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant privileges
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA storage TO platform;
