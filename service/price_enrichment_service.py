"""
Price data enrichment service that adds technical indicators and market analysis.

This module enriches raw NASDAQ price history data with:
- Technical indicators (EMA, RSI, ATR)
- Price action metrics (net change, percentage change, volume ratios)
- Market analysis (macro context, tactical state, momentum confluence)
- Buy conviction probability and reasoning
"""

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

# Add parent directory to path to import utils
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.price_action_calculator import enrich_price_action_rows

# Minimum historical data needed for indicators to work properly
MIN_ROWS_FOR_INDICATORS = 50


def _parse_price(value):
    """Parse price value, handling comma-separated numbers."""
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def enrich_price_data(df, orderbook_id=None, history=None):
    """
    Enrich price data with all technical indicators and market analysis fields.

    Args:
        df (pd.DataFrame): Raw price history DataFrame from NASDAQ API
        orderbook_id (str): Optional orderbook ID to fetch additional historical data if needed
        history (list): Optional list of (close, total_volume, high, low) tuples
                       to seed indicators for continuity across incremental fetches

    Returns:
        pd.DataFrame: Enriched DataFrame with all calculated fields
    """
    if df.empty:
        return df

    # If not enough data for indicators and we have orderbook_id, fetch historical data
    if len(df) < MIN_ROWS_FOR_INDICATORS and orderbook_id and history is None:
        from service.price_action_service import fetch_price_history_from_nasdaq

        try:
            # Get first date from current data
            first_date = df.iloc[0]["date_time"]
            first_dt = datetime.fromisoformat(first_date)
            # Fetch data before current range to seed indicators
            seed_start = (first_dt - timedelta(days=100)).strftime("%Y-%m-%d")

            print(
                f"  Fetching historical seed data from {seed_start} for indicators..."
            )
            seed_df = fetch_price_history_from_nasdaq(
                orderbook_id, from_date=seed_start, to_date=first_date
            )

            if not seed_df.empty:
                # Convert seed data to history tuples (close, total_volume, high, low)
                history = [
                    (
                        _parse_price(row.get("close")),
                        _parse_price(row.get("total_volume")),
                        _parse_price(row.get("high")),
                        _parse_price(row.get("low")),
                    )
                    for _, row in seed_df.iterrows()
                ]
                print(f"  ✓ Seeded with {len(history)} historical records")
        except Exception as e:
            print(f"  ⚠ Could not fetch historical seed data: {e}")
            history = []

    # Convert DataFrame to list of dicts for enrichment
    valid_rows = df.to_dict("records")

    # Use empty list if no history provided
    if history is None:
        history = []

    # Enrich the rows with all technical indicators
    enriched_rows = enrich_price_action_rows(valid_rows, history)

    # Convert back to DataFrame
    result_df = pd.DataFrame(enriched_rows)

    # Remove temporary internal columns used for calculation
    temp_cols = [col for col in result_df.columns if col.startswith("_raw_")]
    if temp_cols:
        result_df = result_df.drop(columns=temp_cols)

    return result_df


def get_enriched_fields():
    """
    Get list of all fields that will be added by enrichment.

    Returns:
        list: Field names that will be present in enriched data
    """
    return [
        "net_change",
        "percentage_change",
        "previous_close",
        "prev_close_pct_diff",
        "volume_avg_ratio",
        "ema_2_pct",
        "ema_5_pct",
        "ema_9_pct",
        "ema_14_pct",
        "ema_21_pct",
        "ema_50_pct",
        "ema_55_pct",
        "ema_100_pct",
        "ema_200_pct",
        "rsi_9",
        "rsi_13",
        "rsi_21",
        "atr_9",
        "atr_13",
        "atr_21",
        "rsi_slope_9",
        "rsi_slope_13",
        "rsi_slope_21",
        "market_macro_context",
        "market_tactical_state",
        "market_volume_description",
        "market_rsi_slope_description",
        "market_momentum_confluence",
        "market_human_readable",
        "buy_conviction_probability",
        "buy_conviction_reason",
    ]


def get_expected_output_columns():
    """
    Get complete list of columns expected in enriched output.

    Returns:
        list: All column names in enriched price action data
    """
    base_columns = [
        "date_time",
        "orderbook_id",
        "isin",
        "symbol",
        "company",
        "bid",
        "ask",
        "open",
        "high",
        "low",
        "close",
        "average",
        "total_volume",
        "turnover",
        "trades",
    ]
    return base_columns + get_enriched_fields()
