-- Krea2 WebUI — Cloudflare D1 schema
-- Aplicar con: npx wrangler d1 execute krea2-sync --remote --file=./schema.sql

CREATE TABLE IF NOT EXISTS settings (
  sync_key    TEXT PRIMARY KEY,
  data        TEXT NOT NULL,          -- JSON con toda la config (URLs, keys, hosts)
  updated_at  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
  id            TEXT PRIMARY KEY,
  sync_key      TEXT NOT NULL,
  tab           TEXT NOT NULL,        -- generate | variation | edit | depth
  payload       TEXT NOT NULL,        -- JSON completo (prompts, params, seed, loras, modelos)
  hosts         TEXT NOT NULL DEFAULT '{}',  -- JSON: { catbox, imgchest, koofr }
  beam_filename TEXT,
  image_url     TEXT,                 -- URL del manager para mostrar sin uploads
  created_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generations_sync
  ON generations (sync_key, created_at DESC);

-- Snapshot cacheado del manager (/list_all: modelos + outputs) compartido entre
-- dispositivos: se guarda acá para hidratar la UI sin pegarle al manager en cada carga.
CREATE TABLE IF NOT EXISTS cache (
  cache_key   TEXT PRIMARY KEY,      -- '<sync_key>:list_all'
  data        TEXT NOT NULL,         -- JSON { models, outputs, ts }
  updated_at  INTEGER NOT NULL
);
