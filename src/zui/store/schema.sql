-- zui storage schema. Apply via store/migrations/.

PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS instances (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    root_path     TEXT NOT NULL,
    comfy_dir     TEXT,
    venv_dir      TEXT,
    base_python   TEXT,
    source        TEXT,             -- adopt | created | portable
    comfy_version TEXT,
    active        INTEGER DEFAULT 0,
    created_at    TEXT
);

CREATE TABLE IF NOT EXISTS instance_env (
    instance_id TEXT NOT NULL UNIQUE,
    python_ver  TEXT,
    torch_ver   TEXT,
    cuda_ver    TEXT,
    attr        TEXT,               -- ok | broken | unknown
    issues_json TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS profiles (
    id          TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    name        TEXT NOT NULL,
    args_json   TEXT,
    env_json    TEXT
);

CREATE TABLE IF NOT EXISTS snapshots (
    id           TEXT PRIMARY KEY,
    instance_id  TEXT NOT NULL,
    kind         TEXT,              -- boot | pre-change | post-change | manual
    parent_id    TEXT,
    payload_json TEXT,
    created_at   TEXT
);

CREATE TABLE IF NOT EXISTS assets (
    id           TEXT PRIMARY KEY,
    instance_id  TEXT NOT NULL,
    rel_path     TEXT NOT NULL,
    size         INTEGER,
    family       TEXT,
    dtype        TEXT,
    param_count  INTEGER,
    hash         TEXT,
    last_used_at TEXT,
    first_seen   TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    instance_id TEXT NOT NULL,
    prompt_id   TEXT,
    started_at  TEXT,
    ended_at    TEXT,
    phases_json TEXT,
    metrics_json TEXT
);

CREATE TABLE IF NOT EXISTS argspecs (
    instance_id  TEXT NOT NULL,
    comfy_hash   TEXT NOT NULL,
    spec_json    TEXT,
    captured_at  TEXT,
    PRIMARY KEY (instance_id, comfy_hash)
);

CREATE TABLE IF NOT EXISTS proc_state (
    instance_id  TEXT PRIMARY KEY,
    pid          INTEGER,
    started_at   TEXT,
    heartbeat_at TEXT,
    port         INTEGER,
    log_path     TEXT,
    argv_json    TEXT,
    cwd          TEXT
);
