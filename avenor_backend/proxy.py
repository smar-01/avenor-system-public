# avenor_backend/proxy.py

# This script is very simple. It uses a built-in ZeroMQ 
# device to act like a signal booster: it listens for 
# messages on one socket and immediately re-broadcasts 
# them on another.

import zmq
import signal
from avenor_backend.common import config
from avenor_backend.common.logger import get_logger

log = get_logger("zmq_proxy")

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
    Initializes and runs the ZeroMQ proxy device.
    This acts as a central message bus for the entire application.
    """
    signal.signal(signal.SIGTERM, handle_shutdown_signal)
    signal.signal(signal.SIGINT, handle_shutdown_signal)

    log.info("Starting ZeroMQ message bus proxy...")
    context = zmq.Context()

    # The 'frontend' socket receives messages from all publishing services.
    # It uses an XSUB socket, which allows it to see subscription messages
    # from the backend and manage them automatically.
    frontend = context.socket(zmq.XSUB)
    frontend.bind(config.ZMQ_INBOUND_BUS_URL)
    log.info(f"Proxy frontend (inbound) is listening on: {config.ZMQ_INBOUND_BUS_URL}")

    # The 'backend' socket broadcasts messages to all subscribing services.
    # It uses an XPUB socket, which handles subscription management.
    backend = context.socket(zmq.XPUB)
    backend.bind(config.ZMQ_OUTBOUND_BUS_URL)
    log.info(f"Proxy backend (outbound) is broadcasting on: {config.ZMQ_OUTBOUND_BUS_URL}")

    # zmq.proxy() is a blocking call that efficiently forwards messages
    # from the frontend to the backend. We run it in a loop with a
    # poller to allow for a graceful shutdown.
    poller = zmq.Poller()
    poller.register(frontend, zmq.POLLIN)
    poller.register(backend, zmq.POLLIN)
    try:
        while True:
            events = dict(poller.poll(1000)) # Poll with 1s timeout
            if not events:
                continue # No events, loop to check shutdown_flag

            # This is a standard proxy device loop
            if frontend in events:
                message = frontend.recv_multipart()
                backend.send_multipart(message)
            if backend in events:
                message = backend.recv_multipart()
                frontend.send_multipart(message)
    except GracefulShutdown:
        # This is the expected exit path when a signal is received.
        log.info("Graceful shutdown exception caught. Proceeding to cleanup.")
    except Exception as e:
        log.critical(f"Proxy crashed with an unexpected error: {e}", exc_info=True)
    finally:
        log.info("Proxy stopped.")
        frontend.close()
        backend.close()
        context.term()

if __name__ == "__main__":
    main()
