# avenor_backend/execution/service.py

import zmq
import time
import json
import os
import signal
import sdnotify

from avenor_backend.common import config
from avenor_backend.common.logger import get_logger
from avenor_backend.common import database

log = get_logger("execution_service")

# This service SUBSCRIBES to orders...
SUB_TOPIC_TRADE_ORDER = b"TRADE_ORDER."
# ...and PUBLISHES its own heartbeats and trade confirmations.
PUB_TOPIC_HEARTBEAT = b"HEARTBEAT.EXECUTION"
PUB_TOPIC_TRADE_CONFIRMATION = b"TRADE_CONFIRMATION"

class GracefulShutdown(Exception):
    """Custom exception to signal a clean shutdown."""
    pass

# --- NEW: Updated Signal Handler ---
def handle_shutdown_signal(signum, frame):
    """
    When a signal is received, this handler raises our custom exception
    to cleanly interrupt the main loop.
    """
    log.info(f"Received shutdown signal: {signal.Signals(signum).name}. Initiating graceful shutdown...")
    raise GracefulShutdown()

def recover_pending_trades():
    """
    On startup, check for any trades that were left in a 'PENDING' state
    from a previous run, which indicates a crash occurred.
    """
    pending_trades = database.get_pending_trades()
    if pending_trades is None:
        log.error("Could not retrieve pending trades from database. Check DB connection.")
        return

    if not pending_trades:
        return # This is the normal case.

    log.warning(f"RECOVERY MODE: Found {len(pending_trades)} pending trades. Checking their status with the (simulated) broker...")
    for trade in pending_trades:
        # In a real system, you would query the broker API here with the trade's ID.
        # For our simulation, we'll just assume they all failed.
        log.info(f"Recovering trade {trade['idempotency_key']}. Simulating it as FAILED.")
        database.update_trade_status(trade['idempotency_key'], "FAILED_RECOVERED")

def main():
    """
    Initializes and runs the execution service to process trade orders.
    """

    # Call our handle_shutdown_signal function when
    # SIGTERM (from systemd) or SIGINT (from Ctrl+C) is received.
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)
    # Send ready and ping signals to systemd.
    notifier = sdnotify.SystemdNotifier()

    log.info("Starting execution service...")
    
    # Initialize the database ---
    # This ensures the 'trades' table exists before we start the loop.
    # It's safe to run this every time the service starts.
    try:
        database.initialize_database()
        recover_pending_trades() # <-- Run recovery logic on startup
        log.info("Database initialized successfully.")
    except Exception as e:
        log.error(f"FATAL: Could not initialize database. Shutting down. Error: {e}", exc_info=True)
        return # Exit if the database isn't ready.


    context = zmq.Context()

    subscriber = context.socket(zmq.SUB)
    subscriber.connect(config.ZMQ_OUTBOUND_BUS_URL)
    subscriber.setsockopt(zmq.SUBSCRIBE, SUB_TOPIC_TRADE_ORDER)
    publisher = context.socket(zmq.PUB)
    publisher.connect(config.ZMQ_INBOUND_BUS_URL)

    time.sleep(1)
    notifier.notify("READY=1")
    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)
    last_heartbeat_time = 0

    try:
        while True:
            # Sd Notifier
            notifier.notify("WATCHDOG=1")

            # Heartbeat Logic
            current_time = time.time()
            if current_time - last_heartbeat_time > config.HEARTBEAT_INTERVAL_S:
                log.info("Sending heartbeat...")
                heartbeat_payload = {'service': 'execution', 'timestamp_utc': current_time, 'pid': os.getpid()}
                publisher.send_multipart([PUB_TOPIC_HEARTBEAT, json.dumps(heartbeat_payload).encode('utf-8')])
                last_heartbeat_time = current_time

            # Polling Logic
            socks = dict(poller.poll(timeout=1000))
            if subscriber in socks and socks[subscriber] == zmq.POLLIN:
                topic, payload_json_bytes = subscriber.recv_multipart()
                trade_order = json.loads(payload_json_bytes.decode('utf-8'))

                log.info(f"Received trade order on topic '{topic.decode()}': {trade_order}")

                # Record the trade to the database ---
                # --- The Safe Trade Lifecycle ---
                # 1. First, record our INTENT to trade with status 'PENDING'.
                trade_order['status'] = 'PENDING'
                was_recorded = database.record_trade(trade_order)

                if was_recorded:
                    # 2. If successfully recorded, THEN submit to the (simulated) broker API.
                    log.info("Simulating trade submission to broker...")
                    time.sleep(1) # Simulate network latency
                    final_status = "FILLED" # In a real system, this comes from the API response.
                    log.info(f"Broker confirmed trade as '{final_status}'.")

                    # 3. Update the database with the final status.
                    database.update_trade_status(trade_order['idempotency_key'], final_status)
                    
                    # 4. Publish the final confirmation back to the bus.
                    confirmation_payload = {
                        'idempotency_key': trade_order['idempotency_key'],
                        'status': final_status,
                        'timestamp_utc': time.time()
                    }
                    publisher.send_multipart([PUB_TOPIC_TRADE_CONFIRMATION, json.dumps(confirmation_payload).encode('utf-8')])
                    log.info("Published trade confirmation.")
                else:
                    # This means the idempotency key was a duplicate.
                    log.warning(f"IGNORED: Duplicate trade ignored with key: {trade_order['idempotency_key']}")
                # End recording trade

    except GracefulShutdown:
        # This is the expected exit path when a signal is received.
        log.info("Graceful shutdown exception caught. Proceeding to cleanup.")
    except Exception as e:
        # Catch any other unexpected crash
        # This ensures that if any other error happens, we log it with
        # the full traceback before the service dies.
        log.critical(f"Execution service crashed: {e}", exc_info=True)
    finally:
        log.info("Closing sockets and terminating context...")
        subscriber.close()
        publisher.close()
        context.term()
        log.info("Execution service stopped.")

if __name__ == "__main__":
    main()
