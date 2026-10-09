PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
  doc_id        TEXT PRIMARY KEY,
  title         TEXT NOT NULL,
  pdf_sha256    TEXT NOT NULL,
  parser_name   TEXT NOT NULL,
  schema_version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS chapters (
  doc_id        TEXT NOT NULL REFERENCES documents(doc_id),
  chapter_id    TEXT NOT NULL,
  title         TEXT NOT NULL,
  page_start    INTEGER NOT NULL,
  page_end      INTEGER NOT NULL,
  PRIMARY KEY (doc_id, chapter_id)
);

CREATE TABLE IF NOT EXISTS anchors (
  anchor_id     TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL,
  chapter_id    TEXT NOT NULL,
  page          INTEGER NOT NULL,
  bbox_x0 REAL NOT NULL, bbox_y0 REAL NOT NULL,
  bbox_x1 REAL NOT NULL, bbox_y1 REAL NOT NULL,
  content_type  TEXT NOT NULL,
  reading_order INTEGER NOT NULL,
  heading_level INTEGER NOT NULL DEFAULT 0,
  content       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_anchors_page ON anchors(doc_id, page);
CREATE INDEX IF NOT EXISTS idx_anchors_chapter ON anchors(doc_id, chapter_id);

CREATE TABLE IF NOT EXISTS blocks (
  block_id      TEXT PRIMARY KEY,
  doc_id        TEXT NOT NULL,
  chapter_id    TEXT NOT NULL,
  type          TEXT NOT NULL,
  title         TEXT NOT NULL,
  status        TEXT NOT NULL,
  "order"       REAL NOT NULL,
  revision      INTEGER NOT NULL,
  generation_protected INTEGER NOT NULL DEFAULT 0,
  deleted_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_blocks_chapter ON blocks(doc_id, chapter_id, "order");

CREATE TABLE IF NOT EXISTS anchor_blocks (
  anchor_id     TEXT NOT NULL,
  block_id      TEXT NOT NULL,
  PRIMARY KEY (anchor_id, block_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS block_links (
  from_block_id TEXT NOT NULL,
  to_block_id   TEXT NOT NULL,
  PRIMARY KEY (from_block_id, to_block_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS index_meta (
  singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
  source_revision INTEGER NOT NULL,
  source_hash TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS anchors_fts USING fts5(
  content, content='anchors', content_rowid='rowid'
);
