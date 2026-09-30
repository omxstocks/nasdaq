import argparse
import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

'''
-- UP-TREND RESULTS
SELECT 
    symbol AS ticker, 
    date_time AS date,  
    low AS entry, 
    (donchian_support - adr_20) AS sl, 
    ROUND((donchian_resistance + (donchian_resistance / 3.0)), 2) AS target,
	DATE(date_time, '+28 days') AS target_date
FROM price_action 
WHERE symbol = 'ABB'  
  AND date_time BETWEEN '2021-01-01' AND '2026-12-31'  
  AND ema_21 > ema_50 
  AND rvol_50 > 1.5

UNION ALL

-- DOWN TREND RESULTS
SELECT 
    symbol AS ticker, 
    date_time AS date,  
    low AS entry, 
    (donchian_support - adr_20) AS sl, 
    ROUND(donchian_resistance, 2) AS target ,
	DATE(date_time, '+28 days') AS target_date
FROM price_action 
WHERE symbol = 'ABB'  
  AND date_time BETWEEN '2021-01-01' AND '2026-12-31'  
  AND ema_50 > ema_21  
  AND rsi_14 < 40
  AND rvol_50 > 1.5

ORDER BY date ASC;

'''


DB_PATH = Path(__file__).parent / "data" / "nasdaq_nordic.db"



def detect_delimiter(csv_file):
    """Detect the delimiter used in the CSV file (comma, tab, or space)."""
    try:
        with open(csv_file, 'r') as f:
            first_line = f.readline().strip()

        # Count occurrences of each potential delimiter
        comma_count = first_line.count(',')
        tab_count = first_line.count('\t')
        space_count = first_line.count(' ')

        # Return the most likely delimiter based on counts
        if tab_count > 0 and tab_count >= comma_count:
            return '\t'
        elif comma_count > 0:
            return ','
        elif space_count > 0:
            return ' '
        else:
            return ','  # Default to comma
    except Exception as e:
        print(f"Warning: Could not detect delimiter, using comma: {e}")
        return ','


def read_csv(csv_file):
    """Read CSV file (supports comma, tab, and space delimiters) and return list of trades."""
    trades = []
    try:
        # Detect the delimiter automatically
        delimiter = detect_delimiter(csv_file)

        with open(csv_file, 'r') as f:
            reader = csv.DictReader(f, delimiter=delimiter)
            for row_num, row in enumerate(reader, start=2):  # Start at 2 because header is row 1
                try:
                    # Trim and validate all fields
                    ticker = row['ticker'].strip() if row['ticker'] else None
                    date = row['date'].strip() if row['date'] else None
                    entry_str = row['entry'].strip() if row['entry'] else None
                    sl_str = row['sl'].strip() if row['sl'] else None
                    target_str = row['target'].strip() if row['target'] else None

                    # Optional: target_date (custom exit date)
                    target_date = None
                    if 'target_date' in row:
                        target_date_str = row['target_date'].strip() if row['target_date'] else None
                        target_date = target_date_str if target_date_str else None

                    # Skip row if any required field is empty
                    if not all([ticker, date, entry_str, sl_str, target_str]):
                        print(f"⚠️  Skipping row {row_num}: missing or empty required field(s)")
                        continue

                    trades.append({
                        'ticker': ticker,
                        'date': date,
                        'entry': float(entry_str),
                        'sl': float(sl_str),
                        'target': float(target_str),
                        'target_date': target_date  # Optional column
                    })
                except (ValueError, TypeError) as e:
                    print(f"⚠️  Skipping row {row_num}: invalid data - {e}")
                    continue

        # Sort by date ascending
        trades.sort(key=lambda x: x['date'])
        return trades
    except Exception as e:
        print(f"Error reading CSV: {e}")
        return []


def fetch_price_data(ticker, start_date, end_date):
    """Fetch daily price data for a stock between dates."""
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return []

    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        query = """
            SELECT date_time, close, high, low
            FROM price_action
            WHERE symbol = ? AND date(date_time) >= ? AND date(date_time) <= ?
            ORDER BY date_time ASC
        """

        cursor.execute(query, (ticker, start_date, end_date))
        rows = cursor.fetchall()
        conn.close()

        return [dict(row) for row in rows]
    except Exception as e:
        print(f"Error fetching price data for {ticker}: {e}")
        return []


def process_trade(trade, price_data):
    """Process a single trade for up to 21 trading days (from price_action table).

    Priority order for exits:
    1. SL hit (Stop Loss takes priority over everything)
    2. Target hit
    3. Target date reached (if provided)
    4. 21 trading days timeout (if no target date)

    Note: Uses actual trading days from price_action table, not calendar days.
    This accounts for weekends and market holidays automatically.

    Returns:
        {
            'ticker': str,
            'entry_date': str,
            'entry_price': float,
            'exit_date': str,
            'exit_price': float,
            'exit_reason': 'TARGET' | 'SL' | 'TIMEOUT' | 'TARGET_DATE',
            'pnl': float,
            'pnl_pct': float,
            'days_held': int (trading days, not calendar days)
        }
    """
    entry_date = trade['date']
    entry_price = trade['entry']
    sl = trade['sl']
    target = trade['target']
    target_date = trade.get('target_date')  # Optional: custom exit date

    if not price_data:
        return None

    # Filter price data from entry date onwards
    price_data_from_entry = [p for p in price_data if p['date_time'].split()[0] >= entry_date]

    if not price_data_from_entry:
        return None

    # Validate entry: entry_price must be >= low price of next trading day
    # This simulates a "Good Till" order placed in evening for next day
    # If entry_price < low of next day, the order won't be filled
    if len(price_data_from_entry) > 0:
        next_day_low = float(str(price_data_from_entry[0]['low']).replace(',', ''))
        if entry_price < next_day_low:
            # Entry price is too low; order cannot be filled at that price
            return None

    # Determine if target_date is in the future (beyond available data)
    last_available_date = price_data_from_entry[-1]['date_time'].split()[0]
    target_date_in_future = target_date and target_date > last_available_date

    # Track for up to 21 trading days or until target_date
    exit_price = None
    exit_date = None
    exit_reason = None
    trading_days_held = 0
    high_on_target_date = None  # Track high price on target_date for debugging

    for idx, price_point in enumerate(price_data_from_entry):
        current_date = price_point['date_time'].split()[0]
        current_close = float(str(price_point['close']).replace(',', ''))
        current_high = float(str(price_point['high']).replace(',', ''))

        trading_days_held = idx + 1  # 1-indexed: day 1, day 2, etc.

        # PRIORITY 1: Check if SL is hit (using close price) - SL takes priority ALWAYS!
        # Exit at actual close price (actual loss may be worse than SL if gap down)
        if current_close <= sl:
            exit_price = current_close
            exit_date = current_date
            exit_reason = 'SL'
            break

        # PRIORITY 2: If target_date is SPECIFIED, wait for it (ignore TARGET price)
        # Check if we've reached target_date - if yes, exit using high price
        if target_date and current_date == target_date:
            exit_price = current_high
            exit_date = current_date
            exit_reason = 'TARGET_DATE'
            high_on_target_date = current_high  # Capture high price on target_date
            break

        # PRIORITY 3: If target_date is SPECIFIED and we've passed it, exit on that date
        if target_date and current_date > target_date:
            exit_price = current_high
            exit_date = current_date
            exit_reason = 'TARGET_DATE'
            # Find and capture the high price on the original target_date if it exists
            for prev_price_point in price_data_from_entry:
                if prev_price_point['date_time'].split()[0] == target_date:
                    high_on_target_date = float(str(prev_price_point['high']).replace(',', ''))
                    break
            break

        # PRIORITY 4: Only check target price if NO target_date is specified
        if not target_date:
            if current_close >= target:
                exit_price = current_close
                exit_date = current_date
                exit_reason = 'TARGET'
                break

        # PRIORITY 5: Check if 21 trading days have passed (only if no target_date is set)
        if not target_date and idx >= 20:  # 0-indexed, so 20 means 21st trading day
            exit_price = current_close
            exit_date = current_date
            exit_reason = 'TIMEOUT'
            break

    if exit_price is None:
        # If we reach here, check if target_date is in future (beyond available data)
        last_available_date = price_data_from_entry[-1]['date_time'].split()[0]
        exit_price = float(str(price_data_from_entry[-1]['close']).replace(',', ''))
        exit_date = last_available_date
        trading_days_held = len(price_data_from_entry)

        # If target_date is beyond last available data, mark as UNREALIZED
        if target_date and target_date > last_available_date:
            exit_reason = 'UNREALIZED'
        else:
            exit_reason = 'TIMEOUT'

        # Try to find high price on target_date if it exists in data
        if target_date:
            for price_point in price_data_from_entry:
                if price_point['date_time'].split()[0] == target_date:
                    high_on_target_date = float(str(price_point['high']).replace(',', ''))
                    break

    # Calculate P&L
    pnl = exit_price - entry_price
    pnl_pct = (pnl / entry_price) * 100

    return {
        'ticker': trade['ticker'],
        'entry_date': entry_date,
        'entry_price': entry_price,
        'sl': sl,
        'target': target,
        'target_date': target_date,
        'high_on_target_date': high_on_target_date,
        'exit_date': exit_date,
        'exit_price': exit_price,
        'exit_reason': exit_reason,
        'pnl': round(pnl, 2),
        'pnl_pct': round(pnl_pct, 2),
        'days_held': trading_days_held
    }


def merge_overlapping_trades(trades_with_results):
    """Merge trades with overlapping 20-day periods for same ticker.

    If multiple trades for same ticker overlap within 20 days:
    - Average entry price and SL
    - Take highest target
    - Keep track of all exit results
    """
    if not trades_with_results:
        return trades_with_results

    # Group trades by ticker
    trades_by_ticker = {}
    for trade in trades_with_results:
        ticker = trade['ticker']
        if ticker not in trades_by_ticker:
            trades_by_ticker[ticker] = []
        trades_by_ticker[ticker].append(trade)

    # For now, return as-is (individual trades)
    # Advanced merging logic would go here if needed
    return trades_with_results


def backtest(csv_file, return_stats=False):
    """Run backtest on CSV trades.

    Args:
        csv_file: Path to CSV file
        return_stats: If True, return year-wise stats instead of just printing
    """
    print("=" * 120)
    print(f"📊 BACKTEST ANALYSIS - CSV: {csv_file}")
    print("=" * 120)

    # Read CSV
    trades = read_csv(csv_file)
    if not trades:
        print("No trades found in CSV")
        return

    print(f"\nLoaded {len(trades)} trades from CSV")
    print(f"Date range: {trades[0]['date']} to {trades[-1]['date']}\n")

    # Process each trade
    results = []
    skipped_trades = []
    skipped_entry_validation = 0
    skipped_no_data = 0

    for trade in trades:
        ticker = trade['ticker']
        entry_date = trade['date']
        target_date = trade.get('target_date')

        # Calculate fetch window
        # For target_date: try to fetch until target_date + 5 days buffer
        # But the actual fetch will be constrained by what's available in the database
        entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")

        if target_date:
            target_dt = datetime.strptime(target_date, "%Y-%m-%d")
            # Request up to target_date + 5 days, but DB query will return only what exists
            end_dt = target_dt + timedelta(days=5)
        else:
            # Default: fetch 60 calendar days (covers 21 trading days + buffer)
            end_dt = entry_dt + timedelta(days=60)

        end_date = end_dt.strftime("%Y-%m-%d")

        # Fetch price data
        price_data = fetch_price_data(ticker, entry_date, end_date)

        if not price_data:
            skipped_trades.append({
                'entry_date': entry_date,
                'ticker': ticker,
                'entry': trade['entry'],
                'sl': trade['sl'],
                'target': trade['target'],
                'skip_reason': 'NO_PRICE_DATA'
            })
            skipped_no_data += 1
            continue

        # Process trade (will count trading days from price_action table)
        result = process_trade(trade, price_data)
        if result:
            results.append(result)
        else:
            # Trade was skipped due to entry validation (entry_price < next_day_low)
            if len(price_data) > 0:
                next_day_low = float(str(price_data[0]['low']).replace(',', ''))
                skip_reason = f'ENTRY_TOO_LOW (Entry: {trade["entry"]:.2f} < Next Low: {next_day_low:.2f})'
            else:
                skip_reason = 'ENTRY_TOO_LOW'

            skipped_trades.append({
                'entry_date': entry_date,
                'ticker': ticker,
                'entry': trade['entry'],
                'sl': trade['sl'],
                'target': trade['target'],
                'skip_reason': skip_reason
            })
            skipped_entry_validation += 1

    if not results:
        print("No trades completed")
        return

    # Get stock symbol from results
    stock_symbol = results[0]['ticker'] if results else 'UNKNOWN'

    # Display entry validation summary
    print(f"\n📊 ENTRY VALIDATION SUMMARY - {stock_symbol}")
    print(f"Total trades loaded:          {len(trades)}")
    print(f"Trades that passed entry validation (filled): {len(results)}")
    print(f"Trades skipped (entry price too low):         {skipped_entry_validation}")
    print(f"Trades skipped (no price data):               {skipped_no_data}")
    print(f"Pass rate: {len(results)/len(trades)*100:.1f}%\n")

    # Sort results by entry date in ascending order
    results.sort(key=lambda x: x['entry_date'])

    # Display results
    print(f"\n{'Entry Date':<12} {'Exit Date':<12} {'Ticker':<8} {'Entry':>10} {'SL':>10} {'Target':>10} {'Sell':>10} {'P&L %':>8} {'Days':>5}")
    print("-" * 115)

    for result in results:
        p_l_symbol = "+" if result['pnl_pct'] >= 0 else ""
        print(f"{result['entry_date']:<12} {result['exit_date']:<12} {result['ticker']:<8} {result['entry_price']:>10.2f} {result['sl']:>10.2f} {result['target']:>10.2f} {result['exit_price']:>10.2f} {p_l_symbol}{result['pnl_pct']:>7.2f}% {result['days_held']:>5}")

    # Year-wise P&L Analysis
    year_stats = {}
    for result in results:
        year = result['exit_date'][:4]  # Extract year from exit_date

        if year not in year_stats:
            year_stats[year] = {
                'trades': 0,
                'winning': 0,
                'losing': 0,
                'breakeven': 0,
                'total_pnl': 0,
                'win_pnl': 0,
                'loss_pnl': 0
            }

        year_stats[year]['trades'] += 1
        year_stats[year]['total_pnl'] += result['pnl_pct']

        if result['pnl_pct'] > 0:
            year_stats[year]['winning'] += 1
            year_stats[year]['win_pnl'] += result['pnl_pct']
        elif result['pnl_pct'] < 0:
            year_stats[year]['losing'] += 1
            year_stats[year]['loss_pnl'] += result['pnl_pct']
        else:
            year_stats[year]['breakeven'] += 1

    # Display Year-wise P&L
    print("\n" + "=" * 110)
    print(f"📊 YEAR-WISE P&L STATEMENT - {stock_symbol}")
    print("=" * 110)
    print(f"{'Year':<8} {'Trades':>8} {'Winning':>10} {'Losing':>10} {'Breakeven':>10} {'Win Rate':>12} {'Total P&L %':>12} {'Avg Win %':>12} {'Avg Loss %':>12}")
    print("-" * 110)

    # Calculate totals
    total_trades = 0
    total_winning = 0
    total_losing = 0
    total_breakeven = 0
    total_pnl = 0
    total_win_pnl = 0
    total_loss_pnl = 0

    for year in sorted(year_stats.keys()):
        stats = year_stats[year]
        win_rate = (stats['winning'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        avg_win = (stats['win_pnl'] / stats['winning']) if stats['winning'] > 0 else 0
        avg_loss = (stats['loss_pnl'] / stats['losing']) if stats['losing'] > 0 else 0

        pnl_symbol = "+" if stats['total_pnl'] >= 0 else ""
        print(f"{year:<8} {stats['trades']:>8} {stats['winning']:>10} {stats['losing']:>10} {stats['breakeven']:>10} {win_rate:>11.1f}% {pnl_symbol}{stats['total_pnl']:>11.2f}% {avg_win:>11.2f}% {avg_loss:>11.2f}%")

        # Accumulate totals
        total_trades += stats['trades']
        total_winning += stats['winning']
        total_losing += stats['losing']
        total_breakeven += stats['breakeven']
        total_pnl += stats['total_pnl']
        total_win_pnl += stats['win_pnl']
        total_loss_pnl += stats['loss_pnl']

    # Display total row
    print("-" * 110)
    total_win_rate = (total_winning / total_trades * 100) if total_trades > 0 else 0
    total_avg_win = (total_win_pnl / total_winning) if total_winning > 0 else 0
    total_avg_loss = (total_loss_pnl / total_losing) if total_losing > 0 else 0
    pnl_symbol = "+" if total_pnl >= 0 else ""
    print(f"{'TOTAL':<8} {total_trades:>8} {total_winning:>10} {total_losing:>10} {total_breakeven:>10} {total_win_rate:>11.1f}% {pnl_symbol}{total_pnl:>11.2f}% {total_avg_win:>11.2f}% {total_avg_loss:>11.2f}%")

    print("=" * 110)

    # Display skipped trades if any
    if skipped_trades:
        print(f"\n❌ SKIPPED TRADES - {stock_symbol} ({len(skipped_trades)} total)")
        print("=" * 130)
        print(f"{'Entry Date':<12} {'Ticker':<8} {'Entry':>10} {'SL':>10} {'Target':>10} {'Skip Reason':<80}")
        print("-" * 130)

        for trade in sorted(skipped_trades, key=lambda x: x['entry_date']):
            print(f"{trade['entry_date']:<12} {trade['ticker']:<8} {trade['entry']:>10.2f} {trade['sl']:>10.2f} {trade['target']:>10.2f} {trade['skip_reason']:<80}")

        print("=" * 130)

    # Calculate summary statistics
    total_pnl_pct = sum(r['pnl_pct'] for r in results)
    winning_trades = sum(1 for r in results if r['pnl_pct'] > 0)
    losing_trades = sum(1 for r in results if r['pnl_pct'] < 0)
    breakeven_trades = sum(1 for r in results if r['pnl_pct'] == 0)
    total_trades = len(results)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    avg_win = sum(r['pnl_pct'] for r in results if r['pnl_pct'] > 0) / winning_trades if winning_trades > 0 else 0
    avg_loss = sum(r['pnl_pct'] for r in results if r['pnl_pct'] < 0) / losing_trades if losing_trades > 0 else 0

    # Display summary
    print("\n" + "=" * 100)
    print(f"📈 BACKTEST SUMMARY - {stock_symbol}")
    print("=" * 100)
    print(f"Total Trades:        {total_trades}")
    print(f"Winning Trades:      {winning_trades} ({winning_trades/total_trades*100:.1f}%)")
    print(f"Losing Trades:       {losing_trades} ({losing_trades/total_trades*100:.1f}%)")
    print(f"Breakeven Trades:    {breakeven_trades}")
    print(f"\nWin Rate:            {win_rate:.2f}%")
    print(f"Total P&L %:         {total_pnl_pct:+.2f}%")
    print(f"Average Win %:       {avg_win:+.2f}%")
    print(f"Average Loss %:      {avg_loss:+.2f}%")

    # Calculate profit factor
    if losing_trades > 0:
        profit_factor = abs(sum(r['pnl_pct'] for r in results if r['pnl_pct'] > 0) / sum(r['pnl_pct'] for r in results if r['pnl_pct'] < 0))
        print(f"Profit Factor:       {profit_factor:.2f}")
    else:
        print(f"Profit Factor:       N/A (no losing trades)")

    print("=" * 100)

    # Return stats if requested (for consolidation)
    if return_stats:
        return {
            'ticker': stock_symbol,
            'year_stats': year_stats,
            'total_trades': total_trades,
            'total_winning': total_winning,
            'total_losing': total_losing,
            'total_breakeven': total_breakeven,
            'total_pnl': total_pnl,
            'total_win_pnl': total_win_pnl,
            'total_loss_pnl': total_loss_pnl
        }


def write_consolidated_pnl(all_stats):
    """Write consolidated YEAR-WISE P&L STATEMENT for all stocks to a text file."""
    output_folder = Path(__file__).parent / "output"
    output_file = output_folder / "CONSOLIDATED_YEAR_WISE_PNL.txt"

    with open(output_file, 'w') as f:
        f.write("=" * 130 + "\n")
        f.write("CONSOLIDATED YEAR-WISE P&L STATEMENT - ALL STOCKS\n")
        f.write("=" * 130 + "\n\n")

        for stock_data in all_stats:
            ticker = stock_data['ticker']
            year_stats = stock_data['year_stats']

            f.write(f"\n{'='*110}\n")
            f.write(f"📊 {ticker}\n")
            f.write(f"{'='*110}\n")
            f.write(f"{'Year':<8} {'Trades':>8} {'Winning':>10} {'Losing':>10} {'Breakeven':>10} {'Win Rate':>12} {'Total P&L %':>12} {'Avg Win %':>12} {'Avg Loss %':>12}\n")
            f.write(f"{'-'*110}\n")

            # Collect totals for this stock
            total_trades = 0
            total_winning = 0
            total_losing = 0
            total_breakeven = 0
            total_pnl = 0
            total_win_pnl = 0
            total_loss_pnl = 0

            for year in sorted(year_stats.keys()):
                stats = year_stats[year]
                win_rate = (stats['winning'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
                avg_win = (stats['win_pnl'] / stats['winning']) if stats['winning'] > 0 else 0
                avg_loss = (stats['loss_pnl'] / stats['losing']) if stats['losing'] > 0 else 0

                pnl_symbol = "+" if stats['total_pnl'] >= 0 else ""
                f.write(f"{year:<8} {stats['trades']:>8} {stats['winning']:>10} {stats['losing']:>10} {stats['breakeven']:>10} {win_rate:>11.1f}% {pnl_symbol}{stats['total_pnl']:>11.2f}% {avg_win:>11.2f}% {avg_loss:>11.2f}%\n")

                # Accumulate totals
                total_trades += stats['trades']
                total_winning += stats['winning']
                total_losing += stats['losing']
                total_breakeven += stats['breakeven']
                total_pnl += stats['total_pnl']
                total_win_pnl += stats['win_pnl']
                total_loss_pnl += stats['loss_pnl']

            # Display total row for this stock
            f.write(f"{'-'*110}\n")
            total_win_rate = (total_winning / total_trades * 100) if total_trades > 0 else 0
            total_avg_win = (total_win_pnl / total_winning) if total_winning > 0 else 0
            total_avg_loss = (total_loss_pnl / total_losing) if total_losing > 0 else 0
            pnl_symbol = "+" if total_pnl >= 0 else ""
            f.write(f"{'TOTAL':<8} {total_trades:>8} {total_winning:>10} {total_losing:>10} {total_breakeven:>10} {total_win_rate:>11.1f}% {pnl_symbol}{total_pnl:>11.2f}% {total_avg_win:>11.2f}% {total_avg_loss:>11.2f}%\n")

        f.write(f"\n{'='*130}\n")
        f.write("End of Consolidated Report\n")
        f.write(f"{'='*130}\n")

    print(f"\n✅ Consolidated P&L report written to: {output_file}")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest trading strategy from CSV file(s)"
    )
    parser.add_argument(
        "csv_file",
        type=str,
        nargs='?',
        help="Path to CSV file with trades (ticker, date, entry, sl, target, [target_date])",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run backtest on all CSV files in the output folder"
    )
    args = parser.parse_args()

    if args.all:
        # Run backtest on all CSV files in output folder
        output_folder = Path(__file__).parent / "output"
        csv_files = sorted(output_folder.glob("*.csv"))

        if not csv_files:
            print(f"No CSV files found in {output_folder}")
            return

        print(f"Found {len(csv_files)} CSV file(s) in {output_folder}\n")

        # Collect stats from all stocks
        all_stats = []
        for csv_file in csv_files:
            print("\n" + "=" * 120)
            stats = backtest(csv_file, return_stats=True)
            if stats:
                all_stats.append(stats)

        # Write consolidated file
        if all_stats:
            write_consolidated_pnl(all_stats)
    else:
        if not args.csv_file:
            parser.print_help()
            return

        csv_path = Path(args.csv_file)
        if not csv_path.exists():
            print(f"CSV file not found: {csv_path}")
            return

        backtest(csv_path)


if __name__ == "__main__":
    main()
