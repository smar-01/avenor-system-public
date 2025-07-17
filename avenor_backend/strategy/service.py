# avenor_backend/strategy/service.py

import zmq
import time
import json
import uuid
import os
import signal
import sdnotify

from avenor_backend.common import config
from avenor_backend.common.logger import get_logger

log = get_logger("strategy_service")

# --- Define Topics ---
# This service SUBSCRIBES to prices and its own confirmations...
SUB_TOPIC_PRICE = b"PRICE."
SUB_TOPIC_CONFIRMATION = b"TRADE_CONFIRMATION"
# ...and PUBLISHES its own heartbeats and trade orders.
PUB_TOPIC_TRADE_ORDER = b"TRADE_ORDER.CREATE"
PUB_TOPIC_HEARTBEAT = b"HEARTBEAT.STRATEGY"

# Timeout in seconds for waiting on a trade confirmation.
ORDER_TIMEOUT_S = 300 # 5 minutes

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
    # Call our handle_shutdown_signal function when
    # SIGTERM (from systemd) or SIGINT (from Ctrl+C) is received.
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    # Send ready and ping signals to systemd.
    notifier = sdnotify.SystemdNotifier()

    log.info("Starting strategy service...")
    context = zmq.Context()

    subscriber = context.socket(zmq.SUB)
    subscriber.connect(config.ZMQ_OUTBOUND_BUS_URL)
    # Subscribe to both topics
    subscriber.setsockopt(zmq.SUBSCRIBE, SUB_TOPIC_PRICE)
    subscriber.setsockopt(zmq.SUBSCRIBE, SUB_TOPIC_CONFIRMATION)
    log.info(f"Subscriber connected to message bus at: {config.ZMQ_OUTBOUND_BUS_URL}")

    publisher = context.socket(zmq.PUB)
    publisher.connect(config.ZMQ_INBOUND_BUS_URL)
    log.info(f"Publisher connected to message bus at: {config.ZMQ_INBOUND_BUS_URL}")

    time.sleep(1)
    notifier.notify("READY=1")

    # Use a poller to handle incoming messages without blocking the main loop indefinitely.
    # This is crucial for allowing graceful shutdowns and handling multiple tasks like heartbeats.
    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)
    last_heartbeat_time = 0

    # This dictionary will hold orders we've sent but haven't been confirmed yet.
    # Key: idempotency_key, Value: {'order': trade_order, 'sent_at': timestamp}
    pending_orders = {}

    try:
        while True:
            # Sd Notifier
            notifier.notify("WATCHDOG=1")

            # Heatbeat Logic
            # Check if it's time to send a new heartbeat.
            current_time = time.time()
            if current_time - last_heartbeat_time > config.HEARTBEAT_INTERVAL_S:
                log.info("Sending heartbeat...")
                heartbeat_payload = {
                    'service': 'strategy',
                    'timestamp_utc': current_time,
                    'pid': os.getpid() # Process ID is useful for debugging on the server
                }
                publisher.send_multipart([PUB_TOPIC_HEARTBEAT, json.dumps(heartbeat_payload).encode('utf-8')])
                last_heartbeat_time = current_time # Reset the timer

            # Market Data Polling Logic
            # Poll for incoming data with a 1-second timeout.
            socks = dict(poller.poll(timeout=1000))
            if subscriber in socks and socks[subscriber] == zmq.POLLIN:
                
                topic, payload_json_bytes = subscriber.recv_multipart()
                data = json.loads(payload_json_bytes.decode('utf-8'))
                
                if topic.startswith(SUB_TOPIC_PRICE):
                    log.info(f"Received Price Update: {data}")
                    if data.get("price") < 95.30:
                        trade_order = {
                            "idempotency_key": str(uuid.uuid4()), "symbol": data["symbol"],
                            "trade_type": "BUY_TO_OPEN", "quantity": 100, "price": data["price"],
                            "status": "NEW", "is_test_trade": os.getenv("TEST_MODE") == "1"
                        }
                        log.info(f"Strategy condition met. Publishing trade order: {trade_order}")
                        publisher.send_multipart([PUB_TOPIC_TRADE_ORDER, json.dumps(trade_order).encode('utf-8')])
                        
                        # Add the order to our pending dictionary for tracking
                        pending_orders[trade_order['idempotency_key']] = {'order': trade_order, 'sent_at': time.time()}

                elif topic.startswith(SUB_TOPIC_CONFIRMATION):
                    log.info(f"Received Trade Confirmation: {data}")
                    key = data.get('idempotency_key')
                    if key in pending_orders:
                        log.info(f"Successfully matched confirmation for order {key}.")
                        del pending_orders[key] # Remove from pending list
                    else:
                        log.warning(f"Received confirmation for an unknown or already confirmed order: {key}")

            # --- Check for timed-out orders ---
            # This runs every loop, regardless of receiving a message.
            keys_to_remove = []
            for key, order_info in pending_orders.items():
                if time.time() - order_info['sent_at'] > ORDER_TIMEOUT_S:
                    log.critical(f"ALERT: Order {key} has been pending for over {ORDER_TIMEOUT_S} seconds without confirmation!")
                    keys_to_remove.append(key)
            # Clean up timed-out orders to prevent spamming alerts
            for key in keys_to_remove:
                del pending_orders[key]

    except GracefulShutdown:
        # This is the expected exit path when a signal is received.
        log.info("Graceful shutdown exception caught. Proceeding to cleanup.")
    except Exception as e:
        # Catch any other unexpected crash
        # This ensures that if any other error happens, we log it with
        # the full traceback before the service dies.
        log.critical(f"Strategy service crashed: {e}", exc_info=True)
    finally:
        # Close ALL sockets and terminate the context.
        log.info("Closing sockets and terminating context...")
        subscriber.close()
        publisher.close()
        context.term()
        log.info("Strategy service stopped.")

if __name__ == "__main__":
    main()
