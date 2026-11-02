"""
Runs the ETL pipeline (calucaltor.py) in the background and records the
outcome in ETL_JOBS, so POST /run-etl can return immediately instead of
the caller's request blocking for however long the pipeline takes.

Called via FastAPI's BackgroundTasks - this function starts AFTER the
HTTP response for POST /run-etl has already gone back to the admin who
triggered it.
"""
import logging
import subprocess
from datetime import datetime, timezone

from database import get_connection

logger = logging.getLogger("app.etl")


def _update_job(job_id: str, **fields):
    """Updates one or more columns on the ETL_JOBS row for this job_id.
    Uses its own connection (borrowed from the pool) because this runs
    in the background, after the request's own connection has already
    been returned to the pool."""
    set_clause = ", ".join(f"{col} = :{col}" for col in fields)
    fields["job_id"] = job_id
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(f"UPDATE etl_jobs SET {set_clause} WHERE job_id = :job_id", fields)
        conn.commit()
        cursor.close()


def run_etl_job(job_id: str):
    """Entry point called by BackgroundTasks. Runs calucaltor.py, then
    marks the job SUCCESS (with captured stdout) or FAILED (with the
    error) in ETL_JOBS."""
    try:
        logger.info("ETL job %s starting.", job_id)

        result = subprocess.run(
            ["python", "calucaltor.py"],
            capture_output=True,
            text=True,
            timeout=600,  # safety net: kill it if it somehow hangs past 10 minutes
        )

        if result.returncode != 0:
            raise RuntimeError(result.stderr[:4000] or "Pipeline exited with a non-zero status.")

        _update_job(
            job_id,
            status="SUCCESS",
            output=result.stdout[:4000],
            finished_at=datetime.now(timezone.utc),
        )
        logger.info("ETL job %s finished successfully.", job_id)

    except Exception as e:
        logger.exception("ETL job %s failed.", job_id)
        _update_job(
            job_id,
            status="FAILED",
            error_message=str(e)[:4000],  # column is capped at 4000 chars
            finished_at=datetime.now(timezone.utc),
        )
