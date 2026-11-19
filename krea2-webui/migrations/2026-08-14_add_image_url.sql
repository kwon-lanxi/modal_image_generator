-- Migración: agregar la columna image_url a la tabla generations.
--
-- La tabla fue creada con el schema viejo (sin image_url) y CREATE TABLE IF NOT EXISTS
-- no altera tablas existentes → el endpoint /api/sync/generations fallaba con
-- "D1_ERROR: no such column: image_url".
--
-- Aplicar una sola vez contra la DB remota:
--   npx wrangler d1 execute krea2-sync --remote --file=./migrations/2026-08-14_add_image_url.sql
--
-- (No es idempotente: si ya se aplicó, SQLite responde "duplicate column name".)

ALTER TABLE generations ADD COLUMN image_url TEXT;
