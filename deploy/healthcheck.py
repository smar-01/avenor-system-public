# deploy/healthcheck.py

import zmq
import json
import time
import signal
from avenor_backend.common import config
from avenor_backend.common.logger import get_logger

log = get_logger("healthcheck_monitor")

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
    Connects to the heartbeat channels, monitors for missing heartbeats,
    and logs critical alerts if a service goes silent.
    """
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    log.info("Starting healthcheck monitor...")
    context = zmq.Context()

    subscriber = context.socket(zmq.SUB)
    subscriber.connect(config.ZMQ_OUTBOUND_BUS_URL)
    # Subscribe to all messages that start with b'HEARTBEAT.'
    subscriber.setsockopt(zmq.SUBSCRIBE, b"HEARTBEAT.")
    log.info(f"Healthcheck monitor connected to message bus at: {config.ZMQ_OUTBOUND_BUS_URL}")

    
    # --- State dictionary to track last seen heartbeats ---
    # We initialize with the current time to give services a grace period to start up.
    last_seen = {
        'market_data': time.time(),
        'strategy': time.time(),
        'execution': time.time()
    }
    # Define the threshold for how long a service can be silent before we alert.
    # 3 * the interval is a safe margin.
    STALE_THRESHOLD_S = config.HEARTBEAT_INTERVAL_S * 3

    poller = zmq.Poller()
    poller.register(subscriber, zmq.POLLIN)

    try:
        while True:
            # Poll for incoming messages with a timeout. This makes the loop non-blocking.
            socks = dict(poller.poll(timeout=1000))
            for socket in socks:
                topic, payload_json_bytes = socket.recv_multipart()
                data = json.loads(payload_json_bytes.decode('utf-8'))
                service_name = data.get('service')
                
                if service_name in last_seen:
                    log.info(f"Received Heartbeat from: {service_name}")
                    last_seen[service_name] = time.time() # Update the timestamp
                else:
                    log.warning(f"Received heartbeat from unknown service: {service_name}")

            # --- Check for stale services periodically ---
            current_time = time.time()
            for service_name, last_seen_time in last_seen.items():
                if current_time - last_seen_time > STALE_THRESHOLD_S:
                    log.critical(f"ALERT: NO HEARTBEAT from '{service_name}' service in over {STALE_THRESHOLD_S} seconds!")
                    # In a real system, you would add a call here to send an email or Telegram alert.
                    # We reset the timer to prevent spamming alerts every second.
                    last_seen[service_name] = current_time
    except GracefulShutdown:
        # This is the expected exit path when a signal is received.
        log.info("Graceful shutdown exception caught. Proceeding to cleanup.")
    except Exception as e:
        log.critical(f"Healthcheck monitor crashed with an unexpected error: {e}", exc_info=True)
    finally:
        log.info("Healthcheck monitor stopped.")
        context.term()

if __name__ == '__main__':
    main()
