"""SQLite transaccional: IDs estables para recursos y clave temporal para snapshots."""
import json
import sqlite3
from pathlib import Path

DDL = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS steam_games (
 steam_appid TEXT PRIMARY KEY, steam_name TEXT NOT NULL, release_date TEXT,
 reviews_total INTEGER, reviews_positive INTEGER, reviews_negative INTEGER,
 verified_utc TEXT, payload_json TEXT NOT NULL, updated_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS twitch_games (
 twitch_game_id TEXT PRIMARY KEY, twitch_name TEXT NOT NULL, box_art_url TEXT,
 discovered_via TEXT NOT NULL, updated_utc TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS game_matches (
 steam_appid TEXT PRIMARY KEY REFERENCES steam_games(steam_appid),
 twitch_game_id TEXT REFERENCES twitch_games(twitch_game_id), confidence REAL,
 status TEXT NOT NULL, method TEXT NOT NULL, checked_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS match_candidates (
 steam_appid TEXT NOT NULL, twitch_game_id TEXT NOT NULL, confidence REAL NOT NULL,
 candidate_rank INTEGER NOT NULL, checked_utc TEXT NOT NULL,
 PRIMARY KEY(steam_appid,twitch_game_id)
);
CREATE TABLE IF NOT EXISTS runs (
 run_id TEXT PRIMARY KEY, started_utc TEXT NOT NULL, finished_utc TEXT,
 status TEXT NOT NULL, games_attempted INTEGER DEFAULT 0, games_succeeded INTEGER DEFAULT 0,
 games_failed INTEGER DEFAULT 0, error_summary TEXT
);
CREATE TABLE IF NOT EXISTS stream_snapshots (
 capture_id TEXT NOT NULL, stream_id TEXT NOT NULL, twitch_game_id TEXT NOT NULL,
 captured_utc TEXT NOT NULL, user_id TEXT, user_name TEXT, title TEXT,
 language TEXT, started_at TEXT, viewer_count INTEGER NOT NULL,
 payload_json TEXT NOT NULL, PRIMARY KEY(capture_id,stream_id)
);
CREATE TABLE IF NOT EXISTS game_snapshots (
 capture_id TEXT NOT NULL, twitch_game_id TEXT NOT NULL, captured_utc TEXT NOT NULL,
 viewers_observed INTEGER NOT NULL, streams_observed INTEGER NOT NULL,
 streams_complete INTEGER NOT NULL, run_id TEXT NOT NULL,
 PRIMARY KEY(capture_id,twitch_game_id)
);
CREATE TABLE IF NOT EXISTS twitch_videos (
 video_id TEXT PRIMARY KEY, twitch_game_id TEXT NOT NULL, user_id TEXT,
 title TEXT, description TEXT, language TEXT, created_at TEXT, published_at TEXT,
 duration TEXT, video_type TEXT, url TEXT, first_seen_utc TEXT NOT NULL,
 last_seen_utc TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_observations (
 video_id TEXT NOT NULL, capture_id TEXT NOT NULL, captured_utc TEXT NOT NULL,
 view_count INTEGER NOT NULL, PRIMARY KEY(video_id,capture_id)
);
CREATE TABLE IF NOT EXISTS twitch_clips (
 clip_id TEXT PRIMARY KEY, twitch_game_id TEXT NOT NULL, broadcaster_id TEXT,
 creator_id TEXT, title TEXT, language TEXT, created_at TEXT,
 duration_seconds REAL, url TEXT, first_seen_utc TEXT NOT NULL,
 last_seen_utc TEXT NOT NULL, payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clip_observations (
 clip_id TEXT NOT NULL, capture_id TEXT NOT NULL, captured_utc TEXT NOT NULL,
 view_count INTEGER NOT NULL, PRIMARY KEY(clip_id,capture_id)
);
CREATE TABLE IF NOT EXISTS daily_metrics (
 utc_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL,
 snapshot_count INTEGER NOT NULL, avg_viewers REAL, max_viewers INTEGER,
 avg_streams REAL, streams_complete INTEGER NOT NULL,
 new_videos INTEGER NOT NULL, new_clips INTEGER NOT NULL,
 PRIMARY KEY(utc_day,twitch_game_id)
);
CREATE TABLE IF NOT EXISTS attention_alerts (
 evaluated_utc_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL,
 prior_avg REAL NOT NULL, recent_avg REAL NOT NULL, delta_viewers REAL NOT NULL,
 growth_pct REAL NOT NULL, eligible INTEGER NOT NULL,
 PRIMARY KEY(evaluated_utc_day,twitch_game_id)
);
CREATE INDEX IF NOT EXISTS idx_game_snapshots_time ON game_snapshots(twitch_game_id,captured_utc);
CREATE INDEX IF NOT EXISTS idx_videos_game ON twitch_videos(twitch_game_id,created_at);
CREATE INDEX IF NOT EXISTS idx_clips_game ON twitch_clips(twitch_game_id,created_at);
"""

def connect(path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(p, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA busy_timeout=60000')
    c.executescript(DDL)
    c.commit()
    return c

def payload(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True)
