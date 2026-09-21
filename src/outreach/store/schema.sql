CREATE TABLE IF NOT EXISTS companies (
  id INTEGER PRIMARY KEY,
  canonical_domain TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  headcount INTEGER,
  headcount_source TEXT
);

CREATE TABLE IF NOT EXISTS contacts (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  full_name TEXT NOT NULL,
  title TEXT NOT NULL,
  profile_url TEXT,
  email TEXT,
  email_status TEXT NOT NULL,
  provider TEXT,
  looked_up_at TEXT,
  contacted_at TEXT,
  UNIQUE (company_id, full_name)
);

CREATE TABLE IF NOT EXISTS source_documents (
  id INTEGER PRIMARY KEY,
  company_id INTEGER NOT NULL REFERENCES companies(id),
  url TEXT NOT NULL,
  source_class TEXT NOT NULL,
  publisher_domain TEXT NOT NULL,
  published_at TEXT,
  fetched_at TEXT NOT NULL,
  http_status INTEGER NOT NULL,
  content_hash TEXT NOT NULL,
  UNIQUE (url, content_hash)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY,
  role_title TEXT NOT NULL,
  sector TEXT NOT NULL,
  region TEXT NOT NULL,
  headcount_min INTEGER NOT NULL,
  headcount_max INTEGER NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_companies (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  stage TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT,
  UNIQUE (run_id, company_id, stage)
);

CREATE TABLE IF NOT EXISTS evidence_items (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  source_document_id INTEGER NOT NULL REFERENCES source_documents(id),
  claim TEXT NOT NULL,
  quote TEXT NOT NULL,
  source_class TEXT NOT NULL,
  publisher_domain TEXT NOT NULL,
  published_at TEXT,
  theme TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bottlenecks (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  claim TEXT NOT NULL,
  summary TEXT NOT NULL,
  passed INTEGER NOT NULL,
  reason TEXT NOT NULL,
  evidence_ids TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_attempts (
  id INTEGER PRIMARY KEY,
  run_id INTEGER NOT NULL REFERENCES runs(id),
  company_id INTEGER NOT NULL REFERENCES companies(id),
  source_class TEXT NOT NULL,
  url TEXT NOT NULL,
  outcome TEXT NOT NULL,
  http_status INTEGER,
  document_count INTEGER NOT NULL DEFAULT 0
);
