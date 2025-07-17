# avenor_backend/common/logger.py

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
PROD_LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"

def get_logger(logger_name: str, log_dir: Optional[Path] = None) -> logging.Logger:
    """
    Configures and returns a logger instance that writes to both the console
    and a time-rotated file.

    Args:
        logger_name: The name for the logger.
        log_dir: (Optional) The directory to save log files in.
                 If None, defaults to the project's 'logs' directory.
                 This is added for testability.

    Returns:
        A configured logging.Logger instance.
    """
    
    # Use the provided log_dir for testing, or the default production directory
    effective_log_dir = log_dir if log_dir is not None else PROD_LOG_DIR
    effective_log_dir.mkdir(exist_ok=True) # Ensure the directory exists

    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

    # This check prevents adding handlers multiple times, which would cause duplicate logs.
    if logger.hasHandlers():
        logger.handlers.clear()

    file_handler = TimedRotatingFileHandler(
        filename=effective_log_dir / f"{logger_name}.log",
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8"
    )
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter(LOG_FORMAT))

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    
    logger.propagate = False

    return logger

if __name__ == "__main__":
    print(f"Production log files will be saved in: {PROD_LOG_DIR}")
    
    main_logger = get_logger("main_test")
    worker_logger = get_logger("worker_test")
    
    main_logger.info("This is an informational message from the main test logger.")
    worker_logger.error("An error occurred!", exc_info=True)

    print("Test logs have been generated. Check the 'logs' directory.")

