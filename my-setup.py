import argparse
import os
import sqlite3
from pathlib import Path
from datetime import datetime
import pandas as pd

# 1. Configuration & Setup
DB_PATH = Path(__file__).parent / "data" / "nasdaq_nordic.db"
OUTPUT_DIR = "output"

# Calculate default dates
TODAY = datetime.now().date()
CURRENT_YEAR = TODAY.year
LAST_DAY_OF_YEAR = datetime(CURRENT_YEAR, 12, 31).date()

def export_data(symbol=None, from_date=None, to_date=None, trend="all"):
    # Set default dates if not provided
    if from_date is None:
        from_date = str(TODAY)
    if to_date is None:
        to_date = str(LAST_DAY_OF_YEAR)

    # Create output directory if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 2. Connect to SQLite database
    conn = sqlite3.connect(DB_PATH)

    try:
        # Determine symbols to process
        if symbol:
            symbols = [symbol]
            print(f"Processing specific symbol: {symbol}")
        else:
            symbols_query = "SELECT DISTINCT symbol FROM price_action;"
            symbols_df = pd.read_sql_query(symbols_query, conn)
            symbols = symbols_df["symbol"].tolist()
            print(f"Found {len(symbols)} unique symbols to process.")

        # 3. Build SQL Query based on trend parameter
        trend_lower = trend.lower()
        
        if trend_lower == "up":
            trend_condition = "(ema_21 > ema_50)"
            print(f"Filtering for UPTREND only: {trend_condition}")
        elif trend_lower == "down":
            trend_condition = "(ema_50 > ema_21 AND rsi_14 < 40)"
            print(f"Filtering for DOWNTREND only: {trend_condition}")
        elif trend_lower == "all":
            trend_condition = "((ema_21 > ema_50) OR (ema_50 > ema_21 AND rsi_14 < 40))"
            print(f"Filtering for ALL trends (UPTREND OR DOWNTREND)")
        else:
            raise ValueError(f"Invalid trend parameter: '{trend}'. Must be 'up', 'down', or 'all'.")

        # Base SQL Query template with placeholders (?)
        query = f"""
        SELECT
            symbol AS ticker,
            date_time AS date,
            low AS entry,
            (donchian_support - adr_20) AS sl,
            CASE
                WHEN ema_21 > ema_50 THEN ROUND((donchian_resistance + (donchian_resistance / 3.0)), 2)
                ELSE ROUND(donchian_resistance, 2)
            END AS target,
            DATE(date_time, '+28 days') AS target_date,
            CASE
                WHEN ema_21 > ema_50 THEN 'UP'
                ELSE 'DOWN'
            END AS trend
        FROM price_action
        WHERE symbol = ?
          AND date_time BETWEEN ? AND ?
          AND rvol_50 > 1.5
          AND {trend_condition}
        ORDER BY date_time ASC;
        """

        # 4. Loop through symbols and export to CSV
        for sym in symbols:
            sanitized_symbol = sym.replace(" ", "_")
            file_name = f"{sanitized_symbol}-{from_date}-{to_date}.csv"
            file_path = os.path.join(OUTPUT_DIR, file_name)

            # Execute query for the specific symbol and date range
            df = pd.read_sql_query(
                query, conn, params=(sym, from_date, to_date)
            )

            # Convert date to string format (date only, no time)
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

            # Round entry, sl, target to max 2 decimal places
            if 'entry' in df.columns:
                df['entry'] = df['entry'].round(2)
            if 'sl' in df.columns:
                df['sl'] = df['sl'].round(2)
            if 'target' in df.columns:
                df['target'] = df['target'].round(2)

            # Export to CSV
            df.to_csv(file_path, index=False)
            print(f"Exported {len(df)} rows for '{sym}' -> {file_path}")

        print("Batch export completed successfully!")

    finally:
        conn.close()

if __name__ == "__main__":
    # Setup command-line arguments with custom help and examples
    parser = argparse.ArgumentParser(
        description="Export filtered stock price action analysis data from SQLite to CSV.",
        epilog="""Examples:
  1. Run for all symbols using default date range (2021-01-01 to 2026-12-31):
     python script.py

  2. Run for a single symbol:
     python script.py --symbol "ABB"

  3. Run for a single symbol with a custom date range:
     python script.py --s "ABB" --from_date "2024-01-01" --to_date "2024-12-31"

  4. Run for all symbols with a custom date range:
     python script.py --f "2025-01-01" --t "2025-06-30"

  5. Run for uptrend signals only (ema_21 > ema_50):
     python script.py --trend "up"

  6. Run for downtrend signals only (ema_50 > ema_21 AND rsi_14 < 40):
     python script.py --trend "down"

  7. Run for a specific symbol in uptrend only:
     python script.py --symbol "ABB" --trend "up"
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument(
        "-s", "--symbol",
        type=str,
        help="Optional single stock symbol (e.g., 'ABB'). If omitted, processes all symbols."
    )
    parser.add_argument(
        "-f", "--from_date",
        type=str,
        default=None,
        help=f"Start date filter in YYYY-MM-DD format (default: today's date = {TODAY})"
    )
    parser.add_argument(
        "-t", "--to_date",
        type=str,
        default=None,
        help=f"End date filter in YYYY-MM-DD format (default: last day of current year = {LAST_DAY_OF_YEAR})"
    )
    parser.add_argument(
        "--trend",
        type=str,
        default="all",
        choices=["up", "down", "all"],
        help="Market trend filter: 'up' for uptrend (ema_21 > ema_50), 'down' for downtrend (ema_50 > ema_21 AND rsi_14 < 40), 'all' for both (default: all)"
    )

    args = parser.parse_args()

    # Run function with parsed CLI arguments
    export_data(symbol=args.symbol, from_date=args.from_date, to_date=args.to_date, trend=args.trend)