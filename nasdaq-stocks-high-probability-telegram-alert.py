import argparse
import csv
import glob
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from service.price_action_service import fetch_price_history_from_nasdaq
from service.price_enrichment_service import enrich_price_data, get_enriched_fields

# Retrieve secrets from environment variables
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHAT_ID")

STOCKS_CSV = Path(__file__).parent / "data" / "TOP-25-SE-STOCKS.csv"
MAX_WORKERS = 5  # Limit concurrent requests to avoid overwhelming the API

# Risk Management Parameters
CAPITAL = 10000  # Trading capital (in SEK for NASDAQ Nordic stocks)
RISK_PERCENT = 0.02  # 2% risk per trade
MAX_RISK_PER_TRADE = CAPITAL * RISK_PERCENT  # 200 SEK
RISK_REWARD_RATIO = 2  # 1:2 ratio (for every 1 risk, gain 2)
CURRENCY_SYMBOL = "kr"  # Swedish Krona (SEK) for NASDAQ Nordic stocks


def load_stocks(csv_path=STOCKS_CSV):
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _fetch_single_stock_price_history(stock, from_date=None, to_date=None):
    """Helper function to fetch price history for a single stock."""
    orderbook_id = stock["orderbook_id"]
    symbol = stock["symbol"]
    try:
        df = fetch_price_history_from_nasdaq(orderbook_id, from_date=from_date, to_date=to_date)
        if df.empty:
            print(f"No price data for {symbol} ({orderbook_id})")
            return None

        # Enrich with technical indicators and market analysis
        # Pass orderbook_id so it can fetch historical data for seeding if needed
        df = enrich_price_data(df, orderbook_id=orderbook_id)

        return (orderbook_id, df)
    except Exception as e:
        print(f"Failed to fetch {symbol} ({orderbook_id}): {e}")
        return None


def calculate_position_sizing(close_price, atr_value, ema21_pct=None):
    """Calculate SL, Target, and position size based on ATR, EMA21, and risk parameters."""
    try:
        close_price = float(str(close_price).replace(",", ""))
        atr_value = float(str(atr_value).replace(",", ""))

        # Extract EMA21 price from percentage difference if available
        ema21_price = None
        if ema21_pct is not None:
            try:
                ema21_pct_value = float(str(ema21_pct).replace(",", "").replace("%", ""))
                # ema21_pct is the percentage difference from close
                # So: ema21_price = close_price / (1 + ema21_pct/100)
                ema21_price = close_price / (1 + ema21_pct_value / 100)
            except (ValueError, TypeError):
                pass

        if atr_value <= 0 or close_price <= 0:
            return None

        # Enhanced Stop Loss and Target calculations using EMA21
        if ema21_price is not None:
            # SL = Min(close - 1.5*ATR, EMA21)
            sl_option1 = close_price - (1.5 * atr_value)
            sl_option2 = ema21_price
            sl = min(sl_option1, sl_option2)

            # Target = Close + Max(1.5*(Close - EMA21), 1.5*ATR)
            target_option1 = close_price + (1.5 * (close_price - ema21_price))
            target_option2 = close_price + (1.5 * atr_value)
            target = close_price + max(target_option1 - close_price, target_option2 - close_price)
        else:
            # Fallback to ATR-only if EMA21 not available
            sl = close_price - (1.5 * atr_value)
            target = close_price + (1.5 * atr_value)

        # Position sizing based on actual SL distance
        # Risk per share = Close - SL
        sl_distance = close_price - sl

        if sl_distance <= 0:
            return None

        # Number of shares = Max Risk per Trade / Risk per share (SL distance)
        num_shares = MAX_RISK_PER_TRADE / sl_distance

        # Position size in currency
        position_size = num_shares * close_price

        # Realized profit/loss at target and SL
        risk_amount = sl_distance * num_shares
        target_distance = target - close_price
        reward_amount = target_distance * num_shares

        # Calculate actual Risk:Reward ratio
        actual_rr_ratio = target_distance / sl_distance if sl_distance > 0 else 0

        return {
            "entry": close_price,
            "sl": sl,
            "target": target,
            "sl_distance": sl_distance,
            "target_distance": target_distance,
            "position_size": position_size,
            "num_shares": num_shares,
            "risk_amount": risk_amount,
            "reward_amount": reward_amount,
            "risk_reward_ratio": f"1:{RISK_REWARD_RATIO}",
            "actual_rr_ratio": actual_rr_ratio
        }
    except (ValueError, TypeError, ZeroDivisionError):
        return None


def send_telegram_alert(bot_token, chat_id, message):

    """Log message that we send to telegram."""
    print(f"{'='*118}")
    print(f"{message}")
    print(f"{'='*118}")
   

    """Send alert message to Telegram."""
    if not bot_token or not chat_id:
        return False

    try:
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML"
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("✅ Telegram alert sent successfully!")
            return True
        else:
            print(f"❌ Failed to send Telegram alert: {response.text}")
            return False
    except Exception as e:
        print(f"❌ Error sending Telegram alert: {e}")
        return False


def cleanup_csv_files():
    """Delete all enriched_price_data_*.csv files in current directory."""
    csv_pattern = "enriched_price_data_*.csv"
    csv_files = glob.glob(csv_pattern)
    if csv_files:
        for filepath in csv_files:
            try:
                Path(filepath).unlink()
            except Exception as e:
                print(f"  ⚠ Could not delete {Path(filepath).name}: {e}")


def export_enriched_data_to_csv(price_history, to_date=None):
    """Export all enriched data to CSV file."""
    # Combine all DataFrames
    all_data = pd.concat(price_history.values(), ignore_index=True)

    if all_data.empty:
        return None

    # Create filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    date_suffix = f"_{to_date.replace('-', '')}" if to_date else ""
    filename = f"enriched_price_data_{timestamp}{date_suffix}.csv"
    filepath = Path(__file__).parent / filename

    # Export to CSV
    all_data.to_csv(filepath, index=False)
    return filepath


def fetch_price_history_for_stocks(stocks, from_date=None, to_date=None):
    """Fetch price history for multiple stocks in parallel."""
    price_history = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        # Submit all tasks
        futures = {
            executor.submit(_fetch_single_stock_price_history, stock, from_date, to_date): stock
            for stock in stocks
        }

        # Process results as they complete
        for future in as_completed(futures):
            result = future.result()
            if result:
                orderbook_id, df = result
                price_history[orderbook_id] = df

    return price_history


def main():
    parser = argparse.ArgumentParser(
        description="Fetch price history for NASDAQ stocks"
    )
    parser.add_argument(
        "--orderbook-id",
        type=str,
        help="Fetch price history for a specific orderbook ID (for testing)",
    )
    parser.add_argument(
        "--from-date",
        type=str,
        help="Start date for price history in YYYY-MM-DD format (e.g., 2025-01-01)",
    )
    parser.add_argument(
        "--to-date",
        type=str,
        help="End date for price history in YYYY-MM-DD format (e.g., 2026-09-23). Defaults to today.",
    )
    parser.add_argument(
        "--min-conviction",
        type=int,
        default=55,
        help="Minimum buy conviction probability to display (default: 55). Set to 0 to show all.",
    )
    args = parser.parse_args()

    if args.orderbook_id:
        # Test mode: fetch single stock
        print(f"Testing with orderbook_id: {args.orderbook_id}, from_date: {args.from_date}, to_date: {args.to_date}")
        test_stock = {
            "orderbook_id": args.orderbook_id,
            "symbol": "TEST",
        }
        price_history = fetch_price_history_for_stocks([test_stock], from_date=args.from_date, to_date=args.to_date)
        if price_history:
            df = price_history[args.orderbook_id]
            print(f"\n✓ Successfully fetched and enriched {len(df)} records")

            # Display base columns
            base_cols = ['date_time', 'orderbook_id', 'symbol', 'close', 'open', 'high', 'low']
            print(f"\nBase columns:\n{df[base_cols].tail(5)}")

            # Display enriched technical indicators
            enriched_fields = get_enriched_fields()
            enriched_cols = [col for col in enriched_fields if col in df.columns]
            if enriched_cols:
                print(f"\nEnriched technical indicators ({len(enriched_cols)} fields):\n{df[enriched_cols].tail(3)}")

            print(f"\nAll columns in dataset: {sorted(df.columns.tolist())}")
        else:
            print("✗ No price data fetched")
    else:
        # Normal mode: fetch all stocks from CSV
        stocks = load_stocks()
        price_history = fetch_price_history_for_stocks(stocks, from_date=args.from_date, to_date=args.to_date)

        print(f"\n✓ Fetched price history for {len(price_history)}/{len(stocks)} stocks")

        if price_history:
            # Combine all enriched data
            all_enriched = pd.concat(price_history.values(), ignore_index=True)

            # Filter by to_date if provided
            if args.to_date:
                filtered_data = all_enriched[all_enriched['date_time'] == args.to_date]
                display_date = args.to_date
            else:
                # If no to_date, show only the latest date
                filtered_data = all_enriched[all_enriched['date_time'] == all_enriched['date_time'].max()]
                display_date = filtered_data['date_time'].iloc[0] if not filtered_data.empty else 'N/A'

            # Apply conviction threshold filter
            total_before_filter = len(filtered_data)
            if args.min_conviction > 0:
                # Convert conviction to numeric for comparison
                filtered_data['conviction_numeric'] = pd.to_numeric(
                    filtered_data['buy_conviction_probability'], errors='coerce'
                )
                filtered_data = filtered_data[filtered_data['conviction_numeric'] >= args.min_conviction]

            total_records = len(filtered_data)
            filtered_out = total_before_filter - total_records

            print(f"\n📊 ENRICHED RECORDS FOR {display_date} (Min Conviction: {args.min_conviction}%)")
            print(f"💰 Risk Management: Capital={CAPITAL} {CURRENCY_SYMBOL} | Risk/Trade={MAX_RISK_PER_TRADE:.0f} {CURRENCY_SYMBOL} (2%) | R:R Ratio 1:{RISK_REWARD_RATIO}\n")
            print(f"Showing {total_records}/{total_before_filter} records (filtered {filtered_out} with conviction < {args.min_conviction}%)\n")
            print(f"{'Date':<12} {'Symbol':<12} {'Entry':>10} {'SL':>10} {'Target':>10} {'Shares':>8} {'R:R':>6} {'Conv%':>6}")
            print("-" * 118)

            # Display records sorted by symbol with position sizing
            total_shares = 0
            for _, row in filtered_data.sort_values('symbol').iterrows():
                date = row.get('date_time', 'N/A')
                symbol = row.get('symbol', 'N/A')
                close = row.get('close', 'N/A')
                atr = row.get('atr_9', '')
                ema21_pct = row.get('ema_21_pct', None)
                conviction = row.get('buy_conviction_probability', 'N/A')

                # Calculate position sizing
                position_info = calculate_position_sizing(close, atr, ema21_pct)

                if position_info:
                    num_shares = position_info['num_shares']
                    total_shares += num_shares
                    rr_ratio = position_info['actual_rr_ratio']

                    print(f"{str(date):<12} {symbol:<12} {position_info['entry']:>10.2f} {position_info['sl']:>10.2f} {position_info['target']:>10.2f} {num_shares:>8.0f} {rr_ratio:>6.2f} {conviction:>6}%")

            # Export to CSV
            filepath = export_enriched_data_to_csv(price_history, args.to_date)

            print(f"\n{'='*118}")
            print(f"📊 Trading Summary:")
            print(f"   Records: {total_records}/{total_before_filter}")
            print(f"   Total Shares to Buy: {total_shares:.0f} (across {total_records} stocks)")
            print(f"   Max Risk per Trade: {MAX_RISK_PER_TRADE:.0f} {CURRENCY_SYMBOL}")
            print(f"   Capital: {CAPITAL} {CURRENCY_SYMBOL} | Risk %: {RISK_PERCENT*100}%")
            print(f"   Risk/Reward Ratio: 1:{RISK_REWARD_RATIO}")
            print(f"{'='*118}")

            # Send Telegram alert if credentials provided in environment variables
            if total_records > 0:
                print(f"\n📱 Sending Telegram alert...")
                # Build Telegram message with table format
                telegram_msg = "<code>\n"
                telegram_msg += f"{'Date':<11} {'Sym':<5} {'Entry':>9} {'SL':>9} {'Target':>9} {'Shares':>7} {'Conv':>5} {'Reward':>9} \n"
                telegram_msg += "─" * 75 + "\n"

                for _, row in filtered_data.sort_values('symbol').iterrows():
                    date = row.get('date_time', 'N/A')
                    symbol = row.get('symbol', 'N/A')
                    close = row.get('close', 'N/A')
                    atr = row.get('atr_9', '')
                    ema21_pct = row.get('ema_21_pct', None)
                    conviction = row.get('buy_conviction_probability', 'N/A')

                    # Calculate position sizing
                    position_info = calculate_position_sizing(close, atr, ema21_pct)
                    if position_info:
                        rr_ratio = position_info['actual_rr_ratio']
                        shares = position_info['num_shares']
                        entry = position_info['entry']
                        sl = position_info['sl']
                        target = position_info['target']

                        # Add emoji indicator based on R:R ratio
                        if rr_ratio < 1.5:
                            signal = "🔴"
                        elif rr_ratio < 2:
                            signal = "🟡"
                        else:
                            signal = "🟢"

                        telegram_msg += f"{str(date):<11} {symbol:<5} {entry:>9.2f} {sl:>9.2f} {target:>9.2f} {shares:>7.0f} {str(conviction):>5}% {rr_ratio:>6.2f} {signal}\n"

                telegram_msg += "</code>"

                send_telegram_alert(BOT_TOKEN, CHANNEL_ID, telegram_msg)
            elif BOT_TOKEN or CHANNEL_ID:
                if not (BOT_TOKEN and CHANNEL_ID):
                    print("⚠️  Telegram credentials incomplete: set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables")

    cleanup_csv_files()


if __name__ == "__main__":
    main()
