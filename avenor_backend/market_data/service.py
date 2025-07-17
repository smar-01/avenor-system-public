# avenor_backend/market_data/service.py

import zmq
import time
import json
import random
import os
import signal
import sdnotify

from avenor_backend.common import config
from avenor_backend.common.logger import get_logger

# Initialize a logger specific to this service
log = get_logger("market_data_service")

TOPIC_PRICE_UPDATE = b"PRICE.TLT"
TOPIC_HEARTBEAT = b"HEARTBEAT.MARKET_DATA"

class GracefulShutdown(Exception):
    """Custom exception to signal a clean shutdown."""
    pass

def handle_shutdown_signal(signum, frame):
    """
    When a signal is received, this handler raises our custom exception
    to cleanly interrupt the main loop.
    """
    log.info(f"Received shutdown signal: {signal.Signals(signum).name}. Initiating graceful shutdown...")
    raise GracefulShutdown()


def main():
    """
    Initializes and runs the market data service's main publishing loop.
    """
    # Send ready and ping signals to systemd.
    notifier = sdnotify.SystemdNotifier()

    # Call handle_shutdown_signal function when
    # SIGTERM (from systemd) or SIGINT (from Ctrl+C) is received.
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)


    log.info("Starting market data service...")
    context = zmq.Context()

    # Socket 1: Data Publisher
    publisher = context.socket(zmq.PUB)
    publisher.connect(config.ZMQ_INBOUND_BUS_URL)
    log.info(f"Publisher connected to message bus at: {config.ZMQ_INBOUND_BUS_URL}")

    time.sleep(1)
    
    notifier.notify("READY=1")
    last_heartbeat_time = 0

    try:
        while True:
            # Sd Notifier
            notifier.notify("WATCHDOG=1")

            # Heartbeat Logic
            current_time = time.time()
            if current_time - last_heartbeat_time > config.HEARTBEAT_INTERVAL_S:
                log.info("Sending heartbeat...")
                heartbeat_payload = {
                    'service': 'market_data',
                    'timestamp_utc': current_time,
                    'pid': os.getpid()
                }
                publisher.send_multipart([TOPIC_HEARTBEAT, json.dumps(heartbeat_payload).encode('utf-8')])
                last_heartbeat_time = current_time # Reset timer

            # 1. Create a placeholder data payload.
            # In a real system, this would come from a live API feed.
            if os.getenv("TEST_MODE") == "1":
                # In test mode, send a price that is guaranteed to trigger the strategy.
                price = 95.00
            else:
                # Add a small random fluctuation to the price to trigger the strategy's logic
                price = 95.50 + random.uniform(-0.25, 0.25)

            payload = {
                "symbol": "TLT",
                "price": round(price, 4), # Round to 4 decimal places
                "timestamp_utc": time.time()
            }

            # 2. Publish the data as a multipart message.
            # The first part is the "topic," which subscribers use for filtering.
            # The second part is the actual data payload.
            
            publisher.send_multipart([TOPIC_PRICE_UPDATE, json.dumps(payload).encode('utf-8')])
            log.info(f"Published Price Update: {payload}")

            # 3. Wait for a couple of seconds before sending the next message.
            time.sleep(2)

    except GracefulShutdown:
        # This is the expected exit path when a signal is received.
        log.info("Graceful shutdown exception caught. Proceeding to cleanup.")
    except Exception as e:
        # Catch any other unexpected crash
        # This ensures that if any other error happens, we log it with
        # the full traceback before the service dies.
        log.critical(f"A fatal, unhandled error occurred in the main loop: {e}", exc_info=True)
    finally:
        # Cleanly close the socket and terminate the context.
        log.info("Closing socket and terminating context...")
        publisher.close()
        context.term()
        log.info("Market data service stopped.")


if __name__ == "__main__":
    main()
