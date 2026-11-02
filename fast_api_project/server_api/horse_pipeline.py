"""
Horse-data pipeline, adapted from the original single_code.py:
reads horsedata.xlsx from S3 (instead of a local file), cleans each
piece the same way the original script did, and loads five things in
order: OWNER, BREEDER, HORSE_DAM, HORSE, HORSE_ALIAS.

Order matters: HORSE's SireID/DamID/BreederID are resolved by reading
BREEDER and HORSE_DAM back out of Oracle AFTER loading them, so those
stages must run first. HORSE_SIRE is NOT loaded by this pipeline (the
original script had that step commented out too) - it's read as
already-populated data, same assumption as before.

Runs as a FastAPI BackgroundTask via run_horse_pipeline_job(), which
POST /run-horse-etl kicks off - see main.py.
"""
import io
import logging
from datetime import datetime, timezone

import boto3
import numpy as np
import pandas as pd

from config import (
    AWS_ACCESS_KEY_ID,
    AWS_SECRET_ACCESS_KEY,
    AWS_REGION,
    S3_BUCKET_NAME,
    S3_HORSE_DATA_KEY,
)
from database import get_connection

logger = logging.getLogger("app.horse_etl")

# All five tables live in the shared team schema, same as the original
# script - except OWNER, which was moved to your own schema earlier.
SCHEMA = "sidharth_aroha"


def _update_job(job_id: str, **fields):
    """Same helper pattern as etl_runner.py - updates ETL_JOBS for this
    job_id using its own pooled connection (this runs in the background,
    after the request's own connection has already been returned)."""
    set_clause = ", ".join(f"{col} = :{col}" for col in fields)
    fields["job_id"] = job_id
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE etl_jobs SET {set_clause} WHERE job_id = :job_id", fields)
        conn.commit()
        cursor.close()


def _clean_name(series: pd.Series) -> pd.Series:
    """Same normalization used on SireName/DamName in the original
    script: lowercase, strip punctuation/digits, collapse whitespace,
    blank-ish values become NULL. Applied identically on both sides of
    a lookup (DB data and incoming data) so the join keys actually match."""
    return (
        series.astype(str)
        .str.strip()
        .str.lower()
        .replace(["unknown", "nan", "none", ""], pd.NA)
        .str.replace(r"\d+", "", regex=True)
        .str.replace(r"[^\w\s]", "", regex=True)
        .str.replace(r"\s+", " ", regex=True)
        .str.strip()
    )


def read_horse_list_from_s3() -> pd.DataFrame:
    """Downloads horsedata.xlsx from S3 and returns the 'Horse List' sheet."""
    if not S3_BUCKET_NAME or not S3_HORSE_DATA_KEY:
        raise ValueError("S3_BUCKET_NAME and S3_HORSE_DATA_KEY must be set.")

    s3 = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
        region_name=AWS_REGION,
    )
    logger.info("Downloading s3://%s/%s", S3_BUCKET_NAME, S3_HORSE_DATA_KEY)
    obj = s3.get_object(Bucket=S3_BUCKET_NAME, Key=S3_HORSE_DATA_KEY)
    return pd.read_excel(io.BytesIO(obj["Body"].read()), sheet_name="Horse List")


# ---------- Stage 1: OWNER (your own schema) ----------

def load_owner(horse_list_df: pd.DataFrame, conn) -> int:
    owner_df = horse_list_df[["OwnerID", "OwnerType", "OwnerName", "City", "State"]].copy()
    owner_df = owner_df.drop_duplicates(subset=["OwnerID"], keep="first")
    owner_df = owner_df.rename(columns={
        "OwnerID": "OWNERID", "OwnerType": "OWNERTYPE", "OwnerName": "OWNERNAME",
        "City": "CITY", "State": "STATE",
    })

    owner_df["OWNERID"] = pd.to_numeric(owner_df["OWNERID"], errors="coerce")
    owner_df["OWNERTYPE"] = owner_df["OWNERTYPE"].astype(str).str.strip().str.upper().str[0]
    owner_df["OWNERNAME"] = (
        owner_df["OWNERNAME"].astype(str).str.replace(",", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    )
    owner_df["CITY"] = owner_df["CITY"].astype(str).str.strip().str.replace(r"\s+", " ", regex=True).str.upper()
    owner_df["STATE"] = owner_df["STATE"].astype(str).str.strip().str.upper().str[:2]
    owner_df = owner_df.dropna(subset=["OWNERID", "OWNERNAME"])
    owner_df["INSERTEDBY"] = "SURENDRA"

    rows = list(owner_df[["OWNERID", "OWNERTYPE", "OWNERNAME", "CITY", "STATE", "INSERTEDBY"]]
                .itertuples(index=False, name=None))
    cursor = conn.cursor()
    cursor.executemany("""
        MERGE INTO "OWNER" tgt
        USING (SELECT :1 AS ownerid, :2 AS ownertype, :3 AS ownername,
                      :4 AS city, :5 AS state, :6 AS insertedby FROM dual) src
        ON (tgt.ownerid = src.ownerid)
        WHEN MATCHED THEN UPDATE SET tgt.ownertype = src.ownertype, tgt.ownername = src.ownername,
                                      tgt.city = src.city, tgt.state = src.state
        WHEN NOT MATCHED THEN INSERT (ownerid, ownertype, ownername, city, state, insertedby)
                          VALUES (src.ownerid, src.ownertype, src.ownername, src.city, src.state, src.insertedby)
    """, rows)
    cursor.close()
    return len(rows)


# ---------- Stage 2: BREEDER (sidharth_aroha, shared schema) ----------

def load_breeder(horse_list_df: pd.DataFrame, conn) -> int:
    breeder_df = horse_list_df[["BreederID", "BreederName"]].copy()
    breeder_df["BreederID"] = breeder_df["BreederID"].replace(0, np.nan)
    breeder_df = breeder_df.drop_duplicates(subset=["BreederID", "BreederName"])
    breeder_df = breeder_df.dropna(subset=["BreederName"])

    null_df = breeder_df[breeder_df["BreederID"].isna()]
    not_null_df = breeder_df[breeder_df["BreederID"].notna()].drop_duplicates(subset=["BreederID"])
    breeder_df = pd.concat([not_null_df, null_df], ignore_index=True)

    breeder_df.columns = breeder_df.columns.str.strip().str.upper()
    breeder_df["BREEDERNAME"] = (
        breeder_df["BREEDERNAME"].astype(str).str.replace(",", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    )
    breeder_df["BREEDERID"] = breeder_df["BREEDERID"].astype("Int64")

    cursor = conn.cursor()

    with_id = breeder_df[breeder_df["BREEDERID"].notna()]
    rows_with_id = list(with_id[["BREEDERID", "BREEDERNAME"]].itertuples(index=False, name=None))
    if rows_with_id:
        cursor.executemany(f"""
            MERGE INTO {SCHEMA}.breeder tgt
            USING (SELECT :1 AS breederid, :2 AS breedername FROM dual) src
            ON (tgt.breederid = src.breederid)
            WHEN MATCHED THEN UPDATE SET tgt.breedername = src.breedername
            WHEN NOT MATCHED THEN INSERT (breederid, breedername) VALUES (src.breederid, src.breedername)
        """, rows_with_id)

    # Rows with no BreederID at all - match/upsert on name instead, since
    # there's no ID to key off (same as the original script keeping them
    # as separate, ID-less rows).
    without_id = breeder_df[breeder_df["BREEDERID"].isna()]
    rows_without_id = list(without_id[["BREEDERNAME"]].itertuples(index=False, name=None))
    if rows_without_id:
        cursor.executemany(f"""
            MERGE INTO {SCHEMA}.breeder tgt
            USING (SELECT :1 AS breedername FROM dual) src
            ON (tgt.breedername = src.breedername AND tgt.breederid IS NULL)
            WHEN NOT MATCHED THEN INSERT (breedername) VALUES (src.breedername)
        """, rows_without_id)

    cursor.close()
    return len(rows_with_id) + len(rows_without_id)


# ---------- Stage 3: HORSE_DAM (sidharth_aroha) ----------

def load_dam(horse_list_df: pd.DataFrame, horse_df: pd.DataFrame, conn) -> int:
    name_map = horse_df.set_index("Name")["HorseID"].to_dict()
    alias_map = horse_list_df.set_index("Alias")["HorseID"].to_dict()

    dam_df = horse_list_df[["DamID", "DamName"]].copy()
    dam_df["DamID"] = dam_df["DamID"].replace(0, np.nan)
    dam_df["DamID"] = dam_df["DamID"].fillna(dam_df["DamName"].map(name_map))
    dam_df["DamID"] = dam_df["DamID"].fillna(dam_df["DamName"].map(alias_map))

    dam_df["DamName"] = _clean_name(dam_df["DamName"])
    dam_df = dam_df.dropna(subset=["DamName"]).drop_duplicates()

    dam_df = dam_df.sort_values(by="DamID", na_position="last")
    dam_df = dam_df.drop_duplicates(subset=["DamName"], keep="first")

    null_df = dam_df[dam_df["DamID"].isna()]
    not_null_df = dam_df[dam_df["DamID"].notna()].drop_duplicates(subset=["DamID"])
    dam_df = pd.concat([not_null_df, null_df], ignore_index=True)

    dam_df.columns = dam_df.columns.str.strip().str.upper()
    dam_df["DAMID"] = dam_df["DAMID"].astype("Int64")

    cursor = conn.cursor()

    with_id = dam_df[dam_df["DAMID"].notna()]
    rows_with_id = list(with_id[["DAMID", "DAMNAME"]].itertuples(index=False, name=None))
    if rows_with_id:
        cursor.executemany(f"""
            MERGE INTO {SCHEMA}.horse_dam tgt
            USING (SELECT :1 AS damid, :2 AS damname FROM dual) src
            ON (tgt.damid = src.damid)
            WHEN MATCHED THEN UPDATE SET tgt.damname = src.damname
            WHEN NOT MATCHED THEN INSERT (damid, damname) VALUES (src.damid, src.damname)
        """, rows_with_id)

    without_id = dam_df[dam_df["DAMID"].isna()]
    rows_without_id = list(without_id[["DAMNAME"]].itertuples(index=False, name=None))
    if rows_without_id:
        cursor.executemany(f"""
            MERGE INTO {SCHEMA}.horse_dam tgt
            USING (SELECT :1 AS damname FROM dual) src
            ON (tgt.damname = src.damname AND tgt.damid IS NULL)
            WHEN NOT MATCHED THEN INSERT (damname) VALUES (src.damname)
        """, rows_without_id)

    cursor.close()
    return len(rows_with_id) + len(rows_without_id)


# ---------- Stage 4: HORSE (sidharth_aroha) - reads BREEDER/HORSE_DAM/HORSE_SIRE back ----------

def load_horse(horse_list_df: pd.DataFrame, horse_df: pd.DataFrame, conn) -> int:
    horse_df = horse_df.copy()
    horse_df.columns = horse_df.columns.str.strip().str.upper()
    horse_df["MICROCHIP"] = pd.to_numeric(horse_df["MICROCHIP"], errors="coerce")

    # BreederID: look up by cleaned BreederName against the BREEDER table
    # we just loaded in load_breeder().
    breeder_in_db = pd.read_sql(f"SELECT breederid, breedername FROM {SCHEMA}.breeder", conn)
    breeder_in_db.columns = breeder_in_db.columns.str.upper()
    breeder_in_db["BREEDERNAME"] = (
        breeder_in_db["BREEDERNAME"].astype(str).str.replace(",", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    )
    breeder_map = breeder_in_db.set_index("BREEDERNAME")["BREEDERID"]

    horse_df["BREEDERNAME"] = (
        horse_df["BREEDERNAME"].astype(str).str.replace(",", " ", regex=False)
        .str.replace(r"\s+", " ", regex=True).str.strip().str.upper()
    )
    horse_df["BREEDERID"] = horse_df["BREEDERNAME"].map(breeder_map)
    horse_df = horse_df.drop(columns=["BREEDERNAME"])

    # SireID: HORSE_SIRE is assumed already populated elsewhere (this
    # pipeline never writes to it, matching the original script).
    sire_in_db = pd.read_sql(f"SELECT sireid, sirename FROM {SCHEMA}.horse_sire", conn)
    sire_in_db.columns = sire_in_db.columns.str.upper()
    sire_in_db["SIRENAME"] = _clean_name(sire_in_db["SIRENAME"])
    sire_map = sire_in_db.set_index("SIRENAME")["SIREID"]

    horse_df["SIREID"] = horse_df["SIREID"].replace(0, np.nan)
    horse_df["SIRENAME"] = _clean_name(horse_df["SIRENAME"])
    horse_df["SIREID"] = horse_df["SIRENAME"].map(sire_map)

    # DamID: against the HORSE_DAM table we just loaded in load_dam().
    dam_in_db = pd.read_sql(f"SELECT damid, damname FROM {SCHEMA}.horse_dam", conn)
    dam_in_db.columns = dam_in_db.columns.str.upper()
    dam_in_db["DAMNAME"] = _clean_name(dam_in_db["DAMNAME"])
    dam_map = dam_in_db.set_index("DAMNAME")["DAMID"]

    horse_df["DAMID"] = horse_df["DAMID"].replace(0, np.nan)
    horse_df["DAMNAME"] = _clean_name(horse_df["DAMNAME"])
    horse_df["DAMID"] = horse_df["DAMNAME"].map(dam_map)

    horse_df = horse_df.drop(columns=["SIRENAME", "DAMNAME"])

    columns = ["HORSEID", "NAME", "FOALDATE", "COLOR", "SEX", "BREED", "MICROCHIP",
               "BREEDERID", "SIREID", "DAMID", "OWNERID"]
    rows = list(horse_df[columns].itertuples(index=False, name=None))

    lower_cols = [c.lower() for c in columns]
    select_clause = ", ".join(f":{i+1} AS {col}" for i, col in enumerate(lower_cols))
    update_clause = ", ".join(f"tgt.{col} = src.{col}" for col in lower_cols if col != "horseid")
    insert_cols = ", ".join(lower_cols)
    insert_vals = ", ".join(f"src.{col}" for col in lower_cols)

    cursor = conn.cursor()
    cursor.executemany(f"""
        MERGE INTO {SCHEMA}.horse tgt
        USING (SELECT {select_clause} FROM dual) src
        ON (tgt.horseid = src.horseid)
        WHEN MATCHED THEN UPDATE SET {update_clause}
        WHEN NOT MATCHED THEN INSERT ({insert_cols})
                          VALUES ({insert_vals})
    """, rows)
    cursor.close()
    return len(rows)


# ---------- Stage 5: HORSE_ALIAS (sidharth_aroha) ----------

def load_alias(horse_list_df: pd.DataFrame, conn) -> int:
    alias_df = horse_list_df[["HorseID", "Alias"]].dropna()
    alias_df.columns = alias_df.columns.str.strip().str.upper()

    rows = list(alias_df[["HORSEID", "ALIAS"]].itertuples(index=False, name=None))
    cursor = conn.cursor()
    cursor.executemany(f"""
        MERGE INTO {SCHEMA}.horse_alias tgt
        USING (SELECT :1 AS horseid, :2 AS alias FROM dual) src
        ON (tgt.horseid = src.horseid AND tgt.alias = src.alias)
        WHEN NOT MATCHED THEN INSERT (horseid, alias) VALUES (src.horseid, src.alias)
    """, rows)
    cursor.close()
    return len(rows)


def run_horse_pipeline_job(job_id: str, triggered_by: str):
    """Entry point called by BackgroundTasks from POST /run-horse-etl.
    Runs only the OWNER stage - the other stage functions (load_breeder,
    load_dam, load_horse, load_alias) are still defined above if you
    want to turn this back into the full pipeline later."""
    try:
        logger.info("Horse ETL job %s starting (owner only).", job_id)
        horse_list_df = read_horse_list_from_s3()

        with get_connection() as conn:
            rows_written = load_owner(horse_list_df, conn)
            conn.commit()

        _update_job(
            job_id,
            status="SUCCESS",
            output=f"owner={rows_written}",
            finished_at=datetime.now(timezone.utc),
        )
        logger.info("Horse ETL job %s finished: owner=%s", job_id, rows_written)

    except Exception as e:
        logger.exception("Horse ETL job %s failed.", job_id)
        _update_job(
            job_id,
            status="FAILED",
            error_message=str(e)[:4000],
            finished_at=datetime.now(timezone.utc),
        )
