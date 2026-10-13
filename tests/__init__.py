"""
Unit tests for the risk logic, run with:

    venv/Scripts/python.exe -m unittest discover -s tests -t . -v

Every test uses fake prices and a fake Alpaca client, so nothing here
touches the network, places an order, or needs API keys.
"""

import logging
import os

# Must happen before any src import: config.py reads this at import time,
# and load_dotenv() never overrides a variable that's already set, so this
# wins over a .env that has DYNAMODB_ENABLED=true. With it off, every
# db.py function is a no-op and the tests can't write to the real tables.
os.environ["DYNAMODB_ENABLED"] = "false"

# The code under test logs every rejection and failed exit at WARNING or
# above; without a handler Python prints them all over the test output.
logging.getLogger().addHandler(logging.NullHandler())
