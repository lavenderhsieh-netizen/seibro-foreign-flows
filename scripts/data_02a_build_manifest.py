"""
data_02a_build_manifest.py

Build a manifest of all Seibro bronze flows parquets and create a DuckDB 
view/table for easy querying. Follows the medallion pattern.

Schema:
    obs_date | file_path | ingestion_ts | code_git_hash | row_count
"""

import polars as pl
from pathlib import Path
import duckdb
import re
from datetime import datetime

BRONZE_DIR = Path("/home/workspace/Data/seibro/bronze/flows")
MANIFEST_PATH = Path("/home/workspace/Data/seibro/manifest.parquet")
DB_PATH = Path("/home/workspace/Data/seibro/seibro.duckdb")

def parse_filename(p: Path) -> dict:
    # Pattern: data_01a__flows__YYYY-MM-DD__<ts>__<git>.parquet
    name = p.stem
    parts = name.split("__")
    if len(parts) < 5:
        return None
    
    obs_date_str = parts[2]
    ingestion_ts_str = parts[3]
    git_hash = parts[4]
    
    return {
        "obs_date": datetime.strptime(obs_date_str, "%Y-%m-%d").date(),
        "file_path": str(p),
        "ingestion_ts": datetime.strptime(ingestion_ts_str, "%Y%m%dT%H%M%SZ"),
        "code_git_hash": git_hash
    }

def build_manifest():
    print(f"[manifest] scanning {BRONZE_DIR} ...")
    if not BRONZE_DIR.exists():
        print("  [error] bronze directory missing")
        return

    files = list(BRONZE_DIR.glob("*.parquet"))
    records = []
    for f in files:
        meta = parse_filename(f)
        if meta:
            # Get row count from metadata if possible, else read
            # For small files, reading is fine.
            df_small = pl.scan_parquet(f).select(pl.len()).collect()
            meta["row_count"] = df_small.item()
            records.append(meta)

    if not records:
        print("  [warn] no valid files found")
        return

    df = pl.DataFrame(records).sort(["obs_date", "ingestion_ts"], descending=[False, True])
    
    # Keep only the latest version per obs_date in the manifest
    df_latest = df.unique(subset=["obs_date"], keep="first")
    
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    df_latest.write_parquet(MANIFEST_PATH)
    print(f"  [success] wrote manifest to {MANIFEST_PATH} ({df_latest.height} dates)")

    # Sync to DuckDB
    print(f"[duckdb] syncing to {DB_PATH} ...")
    con = duckdb.connect(str(DB_PATH))
    
    latest_files = df_latest["file_path"].to_list()
    
    # Use default schema for simplicity to avoid catalog/schema name collision
    con.execute("DROP TABLE IF EXISTS flows")
    if latest_files:
        # Polars can read them all at once
        con.execute(f"CREATE TABLE flows AS SELECT * FROM read_parquet({latest_files})")
        print(f"  [success] created table flows ({con.execute('SELECT COUNT(*) FROM flows').fetchone()[0]} rows)")
    
    con.close()

if __name__ == "__main__":
    build_manifest()
