"""
One-time, idempotent creation of every DynamoDB table this system uses.

Not run automatically on app boot (see src/data/db.py's module docstring)
- app startup only ever reads from these tables, and creating them
requires broader IAM permissions than the running app needs day to day.
Run manually once per environment:

    venv\\Scripts\\python.exe -m scripts.setup_dynamodb

All tables use PAY_PER_REQUEST billing, since this system's write volume
is small and bursty (one trading bot, market hours only) - there's no
steady load to size provisioned capacity against.
"""

import logging

import boto3
from botocore.exceptions import ClientError

from src.config import (
    AWS_REGION,
    DYNAMODB_DAILY_RISK_STATE_TABLE,
    DYNAMODB_ENDPOINT_URL,
    DYNAMODB_MONITORING_STATE_TABLE,
    DYNAMODB_TRADE_HISTORY_TABLE,
    DYNAMODB_TRAILING_STOPS_TABLE,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

TABLE_DEFINITIONS = [
    {
        "TableName": DYNAMODB_DAILY_RISK_STATE_TABLE,
        "KeySchema": [{"AttributeName": "trading_date", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "trading_date", "AttributeType": "S"}
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": DYNAMODB_TRAILING_STOPS_TABLE,
        "KeySchema": [{"AttributeName": "symbol", "KeyType": "HASH"}],
        "AttributeDefinitions": [{"AttributeName": "symbol", "AttributeType": "S"}],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": DYNAMODB_MONITORING_STATE_TABLE,
        "KeySchema": [{"AttributeName": "tracking_date", "KeyType": "HASH"}],
        "AttributeDefinitions": [
            {"AttributeName": "tracking_date", "AttributeType": "S"}
        ],
        "BillingMode": "PAY_PER_REQUEST",
    },
    {
        "TableName": DYNAMODB_TRADE_HISTORY_TABLE,
        "KeySchema": [
            {"AttributeName": "symbol", "KeyType": "HASH"},
            {"AttributeName": "executed_at", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "symbol", "AttributeType": "S"},
            {"AttributeName": "executed_at", "AttributeType": "S"},
            {"AttributeName": "trading_date", "AttributeType": "S"},
        ],
        "BillingMode": "PAY_PER_REQUEST",
        "GlobalSecondaryIndexes": [
            {
                "IndexName": "by-date-index",
                "KeySchema": [
                    {"AttributeName": "trading_date", "KeyType": "HASH"},
                    {"AttributeName": "executed_at", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    },
]


def create_tables() -> None:
    """
    Create every table in TABLE_DEFINITIONS that doesn't already exist,
    then wait for each to become ACTIVE.

    Returns:
        None.
    """
    kwargs = {"region_name": AWS_REGION}
    if DYNAMODB_ENDPOINT_URL:
        kwargs["endpoint_url"] = DYNAMODB_ENDPOINT_URL

    client = boto3.client("dynamodb", **kwargs)

    for definition in TABLE_DEFINITIONS:
        table_name = definition["TableName"]
        try:
            client.create_table(**definition)
            logger.info("Creating %s ...", table_name)
            client.get_waiter("table_exists").wait(TableName=table_name)
            logger.info("%s is ACTIVE.", table_name)
        except ClientError as error:
            if error.response["Error"]["Code"] == "ResourceInUseException":
                logger.info("%s already exists, skipping.", table_name)
            else:
                raise


if __name__ == "__main__":
    create_tables()
