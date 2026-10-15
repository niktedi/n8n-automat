-- Database for the AI agents: chat memory and knowledge base.
--
-- Run as the Postgres superuser:
--   docker exec -i <postgres-container> psql -U <superuser> -d postgres < automation-db.sql
-- Then set the password interactively (it never lands in a file or shell history):
--   docker exec -it <postgres-container> psql -U <superuser> -d postgres -c '\password support_agent'
--
-- The knowledge base tables are created separately, as support_agent,
-- from 01-it-support-ai-agent/kb/schema.sql.

CREATE ROLE support_agent LOGIN;
CREATE DATABASE automation OWNER support_agent;

\c automation

CREATE SCHEMA chat_memory AUTHORIZATION support_agent;
CREATE SCHEMA kb AUTHORIZATION support_agent;

-- Postgres Chat Memory creates its own table without a schema; this puts it into chat_memory
ALTER ROLE support_agent IN DATABASE automation SET search_path = chat_memory, kb;
