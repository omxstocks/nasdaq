import argparse
import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

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
            for row in reader:
                trades.append({
                    'ticker': row['ticker'].strip(),
                    'date': row['date'].strip(),
                    'entry': float(row['entry'].strip()),
                    'sl': float(row['sl'].strip()),
                    'target': float(row['target'].strip())
                })
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

    Note: Uses actual trading days from price_action table, not calendar days.
    This accounts for weekends and market holidays automatically.

    Returns:
        {
            'ticker': str,
            'entry_date': str,
            'entry_price': float,
            'exit_date': str,
            'exit_price': float,
            'exit_reason': 'TARGET' | 'SL' | 'TIMEOUT',
            'pnl': float,
            'pnl_pct': float,
            'days_held': int (trading days, not calendar days)
        }
    """
    entry_date = trade['date']
    entry_price = trade['entry']
    sl = trade['sl']
    target = trade['target']

    if not price_data:
        return None

    # Filter price data from entry date onwards
    price_data_from_entry = [p for p in price_data if p['date_time'].split()[0] >= entry_date]

    if not price_data_from_entry:
        return None

    # Track for up to 21 trading days
    exit_price = None
    exit_date = None
    exit_reason = None
    trading_days_held = 0

    for idx, price_point in enumerate(price_data_from_entry):
        current_date = price_point['date_time'].split()[0]
        current_close = float(str(price_point['close']).replace(',', ''))

        trading_days_held = idx + 1  # 1-indexed: day 1, day 2, etc.

        # Check if target is hit (using close price)
        if current_close >= target:
            exit_price = target
            exit_date = current_date
            exit_reason = 'TARGET'
            break

        # Check if SL is hit (using close price)
        if current_close <= sl:
            exit_price = sl
            exit_date = current_date
            exit_reason = 'SL'
            break

        # Check if 21 trading days have passed (counting actual trading days from price_action table)
        if idx >= 20:  # 0-indexed, so 20 means 21st trading day
            exit_price = current_close
            exit_date = current_date
            exit_reason = 'TIMEOUT'
            break

    if exit_price is None:
        # If we reach here, exit at last available price
        exit_price = float(str(price_data_from_entry[-1]['close']).replace(',', ''))
        exit_date = price_data_from_entry[-1]['date_time'].split()[0]
        exit_reason = 'TIMEOUT'
        trading_days_held = len(price_data_from_entry)

    # Calculate P&L
    pnl = exit_price - entry_price
    pnl_pct = (pnl / entry_price) * 100

    return {
        'ticker': trade['ticker'],
        'entry_date': entry_date,
        'entry_price': entry_price,
        'sl': sl,
        'target': target,
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


def backtest(csv_file):
    """Run backtest on CSV trades."""
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
    for trade in trades:
        ticker = trade['ticker']
        entry_date = trade['date']

        # Fetch price data from entry date onwards (fetch much more to get 21 trading days)
        # We fetch 60 calendar days to be safe, as it should cover 21 trading days
        entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
        end_dt = entry_dt + timedelta(days=60)  # Fetch 60 calendar days to ensure we get 21 trading days
        end_date = end_dt.strftime("%Y-%m-%d")

        # Fetch price data
        price_data = fetch_price_data(ticker, entry_date, end_date)

        if not price_data:
            print(f"⚠️  No price data for {ticker} on {entry_date}")
            continue

        # Process trade (will count trading days from price_action table)
        result = process_trade(trade, price_data)
        if result:
            results.append(result)

    if not results:
        print("No trades completed")
        return

    # Sort results by entry date in ascending order
    results.sort(key=lambda x: x['entry_date'])

    # Display results
    print(f"\n{'Entry Date':<12} {'Exit Date':<12} {'Ticker':<8} {'Entry':>10} {'SL':>10} {'Target':>10} {'Exit':>10} {'Reason':<10} {'P&L %':>8} {'Days':>5}")
    print("-" * 140)

    for result in results:
        p_l_symbol = "+" if result['pnl_pct'] >= 0 else ""
        print(f"{result['entry_date']:<12} {result['exit_date']:<12} {result['ticker']:<8} {result['entry_price']:>10.2f} {result['sl']:>10.2f} {result['target']:>10.2f} {result['exit_price']:>10.2f} {result['exit_reason']:<10} {p_l_symbol}{result['pnl_pct']:>7.2f}% {result['days_held']:>5}")

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
    print("📊 YEAR-WISE P&L STATEMENT")
    print("=" * 110)
    print(f"{'Year':<8} {'Trades':>8} {'Winning':>10} {'Losing':>10} {'Breakeven':>10} {'Win Rate':>12} {'Total P&L %':>12} {'Avg Win %':>12} {'Avg Loss %':>12}")
    print("-" * 110)

    for year in sorted(year_stats.keys()):
        stats = year_stats[year]
        win_rate = (stats['winning'] / stats['trades'] * 100) if stats['trades'] > 0 else 0
        avg_win = (stats['win_pnl'] / stats['winning']) if stats['winning'] > 0 else 0
        avg_loss = (stats['loss_pnl'] / stats['losing']) if stats['losing'] > 0 else 0

        pnl_symbol = "+" if stats['total_pnl'] >= 0 else ""
        print(f"{year:<8} {stats['trades']:>8} {stats['winning']:>10} {stats['losing']:>10} {stats['breakeven']:>10} {win_rate:>11.1f}% {pnl_symbol}{stats['total_pnl']:>11.2f}% {avg_win:>11.2f}% {avg_loss:>11.2f}%")

    print("=" * 110)

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
    print("📈 BACKTEST SUMMARY")
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


def main():
    parser = argparse.ArgumentParser(
        description="Backtest trading strategy from CSV file"
    )
    parser.add_argument(
        "csv_file",
        type=str,
        help="Path to CSV file with trades (ticker, date, entry, sl, target)",
    )
    args = parser.parse_args()

    csv_path = Path(args.csv_file)
    if not csv_path.exists():
        print(f"CSV file not found: {csv_path}")
        return

    backtest(csv_path)


if __name__ == "__main__":
    main()
