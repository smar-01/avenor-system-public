# avenor_backend/common/config.py

import os
from dotenv import load_dotenv, find_dotenv

# This line robustly finds the .env file by searching upwards from the current file
# and loads it into the environment for this specific process.
# This makes each service self-sufficient.
load_dotenv(find_dotenv())

ZMQ_INBOUND_BUS_URL = os.getenv("ZMQ_INBOUND_BUS_URL")
ZMQ_OUTBOUND_BUS_URL = os.getenv("ZMQ_OUTBOUND_BUS_URL")

DB_NAME = os.getenv("DB_NAME")
DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")

HEARTBEAT_INTERVAL_S = int(os.getenv("HEARTBEAT_INTERVAL_S", "15"))

# Construct the database connection string from the loaded variables.
# This is the format that the psycopg library expects.
# Construct the database connection string if the components exist.
if all([DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT]):
    DATABASE_URL = f"dbname='{DB_NAME}' user='{DB_USER}' password='{DB_PASSWORD}' host='{DB_HOST}' port='{DB_PORT}'"
else:
    DATABASE_URL = None
