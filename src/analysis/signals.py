import logging
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from src.config import ANTHROPIC_API_KEY, MIN_TRADE_CONFIDENCE
from src.trading.risk import TradingSignal
from src.utils import monitoring


logger = logging.getLogger(__name__)

MODEL = "claude-opus-5"
MAX_TOKENS = 4096

SYSTEM_PROMPT = f"""You are a disciplined trading analyst for a paper-trading system. \
You produce a single trading signal (BUY, SELL, or HOLD) for one stock at a time, \
based only on the technical indicators and news headlines given to you in the user \
message. Never invent data, prices, or news you were not given.

Indicator reference:
- Moving averages: 20-day above 50-day suggests an uptrend; below suggests a downtrend.
- RSI (0-100): below 30 is oversold (potential bounce), above 70 is overbought \
(potential pullback), 40-60 is neutral.
- MACD: positive favors bullish momentum, negative favors bearish momentum.
- Volume ratio: above roughly 1.5x average suggests the current move is \
well-participated; below roughly 0.5x suggests it lacks conviction.
- Volatility: annualized daily-return volatility. Use it to set risk_level - \
roughly below 25% is LOW, 25-50% is MEDIUM, above 50% is HIGH.

Decision policy:
- Weigh the technical indicators and the headlines together. A strong technical \
setup with no supporting news, or news with no technical confirmation, should \
lower your confidence.
- Confidence must reflect your genuine estimate of the probability this signal is \
correct, not enthusiasm. This system only executes trades at confidence >= \
{MIN_TRADE_CONFIDENCE:.2f}, so reserve high confidence for cases where indicators \
and news clearly agree.
- When signals conflict, headlines are sparse or irrelevant, or you are unsure, \
respond HOLD with a lower confidence rather than guessing a direction.
- reasoning should be 1-3 sentences citing the specific indicators and/or \
headlines that drove the decision."""


class _SignalOutput(BaseModel):
    """Schema Claude's response is validated against by client.messages.parse."""

    action: Literal["BUY", "SELL", "HOLD"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH"]


def get_client() -> anthropic.Anthropic:
    """
    Build an Anthropic client for signal generation.

    Returns:
        An Anthropic client configured with the API key from .env.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set.
    """
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY must be set in .env.")

    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def format_indicators(indicators: dict[str, float]) -> str:
    """
    Render technical indicators as a labeled list for the prompt.

    Args:
        indicators: Output of calculate_indicators.

    Returns:
        A newline-separated, human-readable indicator summary.
    """
    return (
        f"- 20-day moving average: ${indicators['moving_average_20']:.2f}\n"
        f"- 50-day moving average: ${indicators['moving_average_50']:.2f}\n"
        f"- RSI (14-day): {indicators['rsi']:.1f}\n"
        f"- MACD: {indicators['macd']:.2f}\n"
        f"- Volume ratio (current / 20-day avg): {indicators['volume_ratio']:.2f}\n"
        f"- Annualized volatility: {indicators['volatility']:.1%}"
    )


def format_news(articles: list[dict[str, str]]) -> str:
    """
    Render news articles as a labeled list for the prompt.

    Args:
        articles: Output of get_stock_news / format_articles.

    Returns:
        A newline-separated headline summary, or a placeholder if empty.
    """
    if not articles:
        return "No recent headlines available."

    return "\n".join(
        f"- [{article['source']}, {article['published_at']}] "
        f"{article['title']} - {article['description']}"
        for article in articles
    )


def generate_signal(
    ticker: str,
    indicators: dict[str, float],
    articles: list[dict[str, str]],
) -> TradingSignal:
    """
    Ask Claude to turn technical indicators and news into a trading signal.

    Args:
        ticker: Stock ticker symbol.
        indicators: Output of calculate_indicators.
        articles: Output of get_stock_news / format_articles (may be empty).

    Returns:
        A validated TradingSignal for downstream risk evaluation and execution.

    Raises:
        ValueError: If ANTHROPIC_API_KEY is not set, or Claude's output fails
            TradingSignal's own validation.
        anthropic.RateLimitError: If Claude is rate limited. Callers should
            treat this as "skip this stock for this run," not retry - see
            the docstring below for why.
        anthropic.APIError: If the request to Claude fails for any other
            reason.
    """
    client = get_client()

    user_prompt = (
        f"Ticker: {ticker}\n\n"
        f"Technical indicators:\n{format_indicators(indicators)}\n\n"
        f"Recent headlines (last 24h):\n{format_news(articles)}\n\n"
        "Based only on the data above, provide a trading signal."
    )

    monitoring.log_api_call("anthropic", "messages.parse")

    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
            output_format=_SignalOutput,
        )
    except anthropic.RateLimitError as error:
        # Retrying immediately would just burn another call against the
        # same limit. main.py's caller already skips this ticker on any
        # exception from generate_signal - this just makes the reason
        # unambiguous in the logs rather than a generic API error.
        logger.warning(
            "%s | Anthropic rate limit hit; skipping this stock for this run: %s",
            ticker,
            error,
        )
        raise

    monitoring.log_claude_usage(
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )

    parsed = response.parsed_output

    signal = TradingSignal(
        action=parsed.action,
        symbol=ticker,
        confidence=parsed.confidence,
        reasoning=parsed.reasoning,
        risk_level=parsed.risk_level,
    )

    logger.info(
        "%s | Claude signal: %s (confidence=%.2f, risk=%s) | %s",
        ticker,
        signal.action,
        signal.confidence,
        signal.risk_level,
        signal.reasoning,
    )

    return signal
