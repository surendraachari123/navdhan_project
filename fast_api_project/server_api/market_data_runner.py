"""One-shot daily refresh entry point for cron / Windows Task Scheduler."""
import json
import logging

from database import close_pool, init_pool
from market_service import refresh_market_data

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


if __name__ == "__main__":
    init_pool()
    try:
        print(json.dumps(refresh_market_data(), default=str, indent=2))
    finally:
        close_pool()
