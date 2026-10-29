CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS activities (
  id              text PRIMARY KEY,
  name            text NOT NULL,
  city            text NOT NULL,
  country         text NOT NULL,
  lat             double precision NOT NULL,
  lng             double precision NOT NULL,
  category        text NOT NULL,
  tags            text[] NOT NULL DEFAULT '{}',
  price_from_usd  numeric NOT NULL,
  price_band      text NOT NULL,
  duration_hours  numeric NOT NULL,
  rating          numeric NOT NULL,
  description     text NOT NULL,
  provider        text NOT NULL,
  booking_url     text NOT NULL,
  search_text     text NOT NULL,
  tsv             tsvector GENERATED ALWAYS AS (to_tsvector('english'::regconfig, search_text)) STORED,
  embedding       vector(256) NOT NULL,
  refreshed_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS activities_city_idx ON activities (lower(city));
CREATE INDEX IF NOT EXISTS activities_tsv_idx ON activities USING gin (tsv);
CREATE INDEX IF NOT EXISTS activities_embedding_idx ON activities USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS catalog_meta (
  key   text PRIMARY KEY,
  value text NOT NULL
);

CREATE TABLE IF NOT EXISTS answer_cache (
  id               bigserial PRIMARY KEY,
  intent_key       text NOT NULL,
  question         text NOT NULL,
  embedding        vector(256) NOT NULL,
  answer           jsonb NOT NULL,
  model            text NOT NULL,
  catalog_version  text NOT NULL,
  hits             integer NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now(),
  expires_at       timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS answer_cache_key_idx ON answer_cache (intent_key, catalog_version, expires_at);
