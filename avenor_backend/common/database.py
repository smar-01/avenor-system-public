# avenor_backend/common/database.py

import psycopg
from psycopg import sql
from psycopg.errors import UniqueViolation
from typing import Dict, Any, List, Optional

# Import  centralized configuration and logger
from . import config
from .logger import get_logger

# Get a logger instance specifically for this module
db_logger = get_logger("database_helper")

def get_db_connection():
    """Establishes and returns a connection to the PostgreSQL database."""
    try:
        conn = psycopg.connect(config.DATABASE_URL)
        return conn
    except psycopg.OperationalError as e:
        db_logger.error(f"Could not connect to the database: {e}")
        raise

def initialize_database():
    """
    Ensures the 'trades' table exists in the database.
    This function is idempotent; it's safe to run multiple times.
    """
    db_logger.info("Initializing database and ensuring 'trades' table exists...")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            # Use TEXT for trade_type and status for flexibility.
            # NUMERIC(19, 8) is used for price for financial accuracy.
            # UUID is the correct type for idempotency keys.
            # The UNIQUE constraint on idempotency_key is what prevents duplicate trades.
            cur.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id SERIAL PRIMARY KEY,
                    timestamp_utc TIMESTAMPTZ DEFAULT (NOW() AT TIME ZONE 'utc'),
                    idempotency_key UUID UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price NUMERIC(19, 8),
                    status TEXT NOT NULL,
                    is_test_trade BOOLEAN NOT NULL DEFAULT FALSE
                );
            """)
            conn.commit()
            db_logger.info("'trades' table is ready.")
    except Exception as e:
        db_logger.error(f"Failed to initialize database table: {e}")
        conn.rollback() # Roll back any changes if an error occurs
        raise
    finally:
        conn.close()

def record_trade(trade_data: Dict[str, Any]) -> bool:
    """
    Records a trade in the database, preventing duplicates using an idempotency key.

    Args:
        trade_data: A dictionary containing the trade details, including a
                    unique 'idempotency_key'.

    Returns:
        True if the trade was successfully recorded, False if it was a duplicate.
        Raises an exception for other database errors.
    """
    required_keys = ["idempotency_key", "symbol", "trade_type", "quantity", "status"]
    if not all(key in trade_data for key in required_keys):
        db_logger.error(f"Missing required keys in trade data: {trade_data}")
        return False

    db_logger.info(f"Attempting to record trade with key: {trade_data['idempotency_key']}")
    
    insert_sql = sql.SQL("""
        INSERT INTO trades (idempotency_key, symbol, trade_type, quantity, price, status, is_test_trade)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """)
    
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                insert_sql,
                (
                    trade_data["idempotency_key"],
                    trade_data["symbol"],
                    trade_data["trade_type"],
                    trade_data["quantity"],
                    trade_data.get("price"), # .get() safely handles if price is not present
                    trade_data["status"],
                    trade_data.get("is_test_trade", False)
                ),
            )
        conn.commit()
        db_logger.info(f"Successfully recorded trade with key: {trade_data['idempotency_key']}")
        return True
    except UniqueViolation:
        # This is the expected error for a duplicate key. It's not a failure.
        conn.rollback()
        db_logger.warning(f"Duplicate trade ignored with key: {trade_data['idempotency_key']}")
        return False
    except Exception as e:
        # Any other exception is a genuine error.
        conn.rollback()
        db_logger.error(f"Failed to record trade due to an unexpected error: {e}", exc_info=True)
        raise
    finally:
        conn.close()



def update_trade_status(idempotency_key: str, new_status: str) -> bool:
    """
    Updates the status of an existing trade in the database.
    This is the final step after a trade is confirmed by the broker.
    """
    db_logger.info(f"Updating status for trade key {idempotency_key} to '{new_status}'")
    update_sql = sql.SQL("UPDATE trades SET status = %s WHERE idempotency_key = %s")
    conn = get_db_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(update_sql, (new_status, idempotency_key))
            conn.commit()
            # Check if any row was actually updated
            if cur.rowcount == 0:
                db_logger.warning(f"No trade found with key {idempotency_key} to update.")
                return False
        return True
    except Exception as e:
        conn.rollback()
        db_logger.error(f"Failed to update trade status for key {idempotency_key}: {e}", exc_info=True)
        raise
    finally:
        conn.close()

def get_pending_trades() -> Optional[List[Dict[str, Any]]]:
    """
    Retrieves all trades from the database that are still in 'PENDING' status.
    This is called by the execution service on startup to recover from a crash.
    """
    db_logger.info("Checking for pending trades from a previous run...")
    select_sql = "SELECT * FROM trades WHERE status = 'PENDING'"
    conn = get_db_connection()
    try:
        # Use a DictCursor to get results as dictionaries
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(select_sql)
            pending_trades = cur.fetchall()
            if pending_trades:
                db_logger.warning(f"Found {len(pending_trades)} pending trades to recover.")
            else:
                db_logger.info("No pending trades found.")
            return pending_trades
    except Exception as e:
        db_logger.error(f"Failed to retrieve pending trades: {e}", exc_info=True)
        return None
    finally:
        conn.close()