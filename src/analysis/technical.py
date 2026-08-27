import logging

import pandas as pd


logger = logging.getLogger(__name__)


def calculate_moving_average(
    data: pd.DataFrame,
    period: int,
) -> float:
    """
    Calculate the moving average of closing prices.

    Args:
        data: Historical market data containing a Close column.
        period: Number of trading days used for the moving average.

    Returns:
        The most recent moving average value.

    Raises:
        ValueError: If insufficient data is available.
    """
    if len(data) < period:
        raise ValueError(
            f"Need at least {period} days of data to calculate "
            f"the moving average."
        )

    moving_average = data["Close"].rolling(window=period).mean().iloc[-1]

    return float(moving_average)


def calculate_rsi(
    data: pd.DataFrame,
    period: int = 14,
) -> float:
    """
    Calculate the Relative Strength Index (RSI).

    Args:
        data: Historical market data containing a Close column.
        period: Number of periods used to calculate RSI.

    Returns:
        The most recent RSI value between 0 and 100.

    Raises:
        ValueError: If insufficient data is available.
    """
    if len(data) < period + 1:
        raise ValueError(
            f"Need at least {period + 1} days of data to calculate RSI."
        )

    price_change = data["Close"].diff()

    gains = price_change.clip(lower=0)
    losses = -price_change.clip(upper=0)

    average_gain = gains.rolling(window=period).mean()
    average_loss = losses.rolling(window=period).mean()

    relative_strength = average_gain / average_loss

    rsi = 100 - (100 / (1 + relative_strength))

    return float(rsi.iloc[-1])


def calculate_macd(
    data: pd.DataFrame,
) -> float:
    """
    Calculate the latest MACD value.

    Args:
        data: Historical market data containing a Close column.

    Returns:
        The most recent MACD value.

    Raises:
        ValueError: If insufficient data is available.
    """
    if len(data) < 26:
        raise ValueError(
            "Need at least 26 days of data to calculate MACD."
        )

    fast_average = data["Close"].ewm(
        span=12,
        adjust=False,
    ).mean()

    slow_average = data["Close"].ewm(
        span=26,
        adjust=False,
    ).mean()

    macd = fast_average - slow_average

    return float(macd.iloc[-1])


def calculate_volume_analysis(
    data: pd.DataFrame,
    period: int = 20,
) -> float:
    """
    Compare the latest trading volume with its recent average.

    Args:
        data: Historical market data containing a Volume column.
        period: Number of trading days used for average volume.

    Returns:
        The ratio of current volume to average volume.

    Raises:
        ValueError: If insufficient data is available.
    """
    if len(data) < period:
        raise ValueError(
            f"Need at least {period} days of data for volume analysis."
        )

    average_volume = data["Volume"].rolling(window=period).mean().iloc[-1]
    current_volume = data["Volume"].iloc[-1]

    if average_volume == 0:
        raise ValueError("Average trading volume cannot be zero.")

    return float(current_volume / average_volume)


def calculate_volatility(
    data: pd.DataFrame,
    period: int = 20,
) -> float:
    """
    Calculate historical volatility from daily returns.

    Args:
        data: Historical market data containing a Close column.
        period: Number of trading days used for the calculation.

    Returns:
        Annualized historical volatility as a decimal.

    Raises:
        ValueError: If insufficient data is available.
    """
    if len(data) < period + 1:
        raise ValueError(
            f"Need at least {period + 1} days of data for volatility."
        )

    daily_returns = data["Close"].pct_change()

    volatility = daily_returns.rolling(window=period).std().iloc[-1]

    annualized_volatility = volatility * (252 ** 0.5)

    return float(annualized_volatility)


def calculate_indicators(
    data: pd.DataFrame,
) -> dict[str, float]:
    """
    Calculate all technical indicators used by the trading system.

    Args:
        data: Historical market data containing price and volume columns.

    Returns:
        Dictionary containing the latest technical indicator values.
    """
    try:
        indicators = {
            "moving_average_20": calculate_moving_average(data, 20),
            "moving_average_50": calculate_moving_average(data, 50),
            "rsi": calculate_rsi(data),
            "macd": calculate_macd(data),
            "volume_ratio": calculate_volume_analysis(data),
            "volatility": calculate_volatility(data),
        }

        logger.info("Technical indicators calculated successfully.")

        return indicators

    except Exception as error:
        logger.error(
            "Failed to calculate technical indicators: %s",
            error,
        )
        raise