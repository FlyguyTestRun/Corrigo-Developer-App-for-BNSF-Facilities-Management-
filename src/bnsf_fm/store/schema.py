"""SQLite schema.

SQLite rather than Postgres/DuckDB deliberately: the pilot has to run on a
maintenance mechanic's laptop with no server to install and no data leaving the
machine. Volumes here are small (single campus, low tens of thousands of work
orders) so the analytical queries stay well inside what SQLite handles.
"""

SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS locations (
    id          TEXT PRIMARY KEY,
    building    TEXT NOT NULL,
    floor       TEXT,
    room        TEXT,
    description TEXT
);

-- Identities are already anonymized on arrival: `id` is an opaque surrogate
-- and `name` is NULL for everyone except the person running the tool. The CI
-- gate and `bnsf-fm quality` both assert that.
--
-- `label` ("Tech 1", "Tech 2", …) is allocated once and never recomputed. If
-- it were derived by sorting on each load, one new hire would renumber
-- everyone and "Tech 3" would silently mean a different person than it did
-- last month.
CREATE TABLE IF NOT EXISTS technicians (
    id      TEXT PRIMARY KEY,
    name    TEXT,
    trade   TEXT,
    active  INTEGER NOT NULL DEFAULT 1,
    label   TEXT UNIQUE,
    is_self INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS manuals (
    id            TEXT PRIMARY KEY,
    manufacturer  TEXT NOT NULL,
    model         TEXT NOT NULL,
    title         TEXT NOT NULL,
    relative_path TEXT,
    page_count    INTEGER,
    text          TEXT
);
CREATE INDEX IF NOT EXISTS idx_manuals_key ON manuals (manufacturer, model);

CREATE TABLE IF NOT EXISTS assets (
    id                  TEXT PRIMARY KEY,
    tag                 TEXT NOT NULL,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL,
    location_id         TEXT REFERENCES locations (id),
    manufacturer        TEXT,
    model               TEXT,
    serial              TEXT,
    installed_on        TEXT,
    criticality         TEXT NOT NULL DEFAULT 'standard',
    expected_life_years INTEGER
);
CREATE INDEX IF NOT EXISTS idx_assets_location ON assets (location_id);
CREATE INDEX IF NOT EXISTS idx_assets_model    ON assets (manufacturer, model);

CREATE TABLE IF NOT EXISTS work_orders (
    id          TEXT PRIMARY KEY,
    number      TEXT NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    status      TEXT NOT NULL,
    type        TEXT NOT NULL,
    priority    TEXT NOT NULL,
    asset_id    TEXT REFERENCES assets (id),
    location_id TEXT REFERENCES locations (id),
    assigned_to TEXT REFERENCES technicians (id),
    opened_at   TEXT NOT NULL,
    closed_at   TEXT,
    resolution  TEXT
);
CREATE INDEX IF NOT EXISTS idx_wo_status   ON work_orders (status);
CREATE INDEX IF NOT EXISTS idx_wo_opened   ON work_orders (opened_at);
CREATE INDEX IF NOT EXISTS idx_wo_asset    ON work_orders (asset_id);
CREATE INDEX IF NOT EXISTS idx_wo_assignee ON work_orders (assigned_to);

CREATE TABLE IF NOT EXISTS labor_entries (
    id            TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders (id),
    technician_id TEXT NOT NULL REFERENCES technicians (id),
    hours         REAL NOT NULL,
    logged_at     TEXT NOT NULL,
    note          TEXT
);
CREATE INDEX IF NOT EXISTS idx_labor_wo   ON labor_entries (work_order_id);
CREATE INDEX IF NOT EXISTS idx_labor_tech ON labor_entries (technician_id);

CREATE TABLE IF NOT EXISTS parts (
    id               TEXT PRIMARY KEY,
    sku              TEXT NOT NULL,
    name             TEXT NOT NULL,
    unit             TEXT NOT NULL DEFAULT 'each',
    on_hand          INTEGER NOT NULL DEFAULT 0,
    reorder_point    INTEGER NOT NULL DEFAULT 0,
    reorder_quantity INTEGER NOT NULL DEFAULT 0,
    location_id      TEXT REFERENCES locations (id),
    unit_cost        REAL
);

CREATE TABLE IF NOT EXISTS part_usage (
    id            TEXT PRIMARY KEY,
    work_order_id TEXT NOT NULL REFERENCES work_orders (id),
    part_id       TEXT NOT NULL REFERENCES parts (id),
    quantity      INTEGER NOT NULL,
    used_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_part ON part_usage (part_id);
CREATE INDEX IF NOT EXISTS idx_usage_wo   ON part_usage (work_order_id);

-- Campus adjacency for route sequencing. Undirected; both directions stored so
-- lookups need no OR clause. Walking minutes, not distance — stairs and badge
-- doors matter more than metres.
CREATE TABLE IF NOT EXISTS campus_edges (
    from_building TEXT NOT NULL,
    to_building   TEXT NOT NULL,
    walk_minutes  REAL NOT NULL,
    PRIMARY KEY (from_building, to_building)
);
"""
