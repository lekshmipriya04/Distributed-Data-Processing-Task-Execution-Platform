-- Ensure the main database exists (created by POSTGRES_DB env var already)
-- Create additional schemas for service isolation
CREATE SCHEMA IF NOT EXISTS storage;
CREATE SCHEMA IF NOT EXISTS preprocessing;
CREATE SCHEMA IF NOT EXISTS training;
CREATE SCHEMA IF NOT EXISTS evaluation;
CREATE SCHEMA IF NOT EXISTS worker_registry;
CREATE SCHEMA IF NOT EXISTS resource_manager;
CREATE SCHEMA IF NOT EXISTS scheduler;

-- MLflow uses the default public schema
-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Grant privileges
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA storage TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA preprocessing TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA training TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA evaluation TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA worker_registry TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA resource_manager TO platform;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA scheduler TO platform;
