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
            SELECT date_time, open, close, high, low
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

    # Validate trade parameters
    if sl >= entry_price:
        # SL must be less than entry price
        return None

    if target <= entry_price:
        # Target must be greater than entry price
        return None

    # Validate target_date: if it's not greater than entry_date, ignore it
    if target_date:
        if target_date <= entry_date:
            target_date = None  # Treat as if no target_date was specified

    # Find the entry date in price data
    entry_date_idx = None
    for idx, price_point in enumerate(price_data):
        if price_point['date_time'].split()[0] == entry_date:
            entry_date_idx = idx
            break

    if entry_date_idx is None:
        # Entry date is not a trading day (weekend/holiday); skip the trade
        return None

    # Check if there are at least 5 more trading days available
    if entry_date_idx + 5 >= len(price_data):
        # Not enough trading days available; skip the trade
        return None

    # Look for the first day within 5 trading days where order can fill
    # Entry is a limit order: fills if entry_price >= LOW price on that day
    fill_day_idx = None
    for days_ahead in range(1, 6):  # Check next 5 trading days (1 to 5)
        check_idx = entry_date_idx + days_ahead
        if check_idx >= len(price_data):
            break

        day_low = float(str(price_data[check_idx]['low']).replace(',', ''))
        if entry_price >= day_low:
            # Order can fill on this day
            fill_day_idx = check_idx
            break

    if fill_day_idx is None:
        # Order could not fill within 5 trading days
        return None

    # Determine actual entry price based on fill day's open
    # If entry_price < open: Buy at entry_price (better fill)
    # If entry_price >= open: Buy at open price
    fill_day = price_data[fill_day_idx]
    fill_day_open = float(str(fill_day['open']).replace(',', '')) if 'open' in fill_day else None

    if fill_day_open:
        if entry_price < fill_day_open:
            actual_entry_price = entry_price
        else:
            actual_entry_price = fill_day_open
    else:
        actual_entry_price = entry_price

    # Filter price data from the fill day onwards (where trade actually fills)
    price_data_from_entry = price_data[fill_day_idx:]

    # Determine if target_date is in the future (beyond available data)
    last_available_date = price_data_from_entry[-1]['date_time'].split()[0]
    target_date_in_future = target_date and target_date > last_available_date

    # Track for up to 21 trading days or until target_date
    # price_data_from_entry starts from the next trading day (where trade fills)
    exit_price = None
    exit_date = None
    exit_reason = None
    trading_days_held = 0
    high_on_target_date = None  # Track high price on target_date for debugging

    for idx, price_point in enumerate(price_data_from_entry):
        current_date = price_point['date_time'].split()[0]
        current_close = float(str(price_point['close']).replace(',', ''))
        current_high = float(str(price_point['high']).replace(',', ''))
        current_low = float(str(price_point['low']).replace(',', ''))

        trading_days_held = idx + 1  # idx 0 = day 1, idx 1 = day 2, etc.

        # PRIORITY 1: Check if SL is hit - SL takes priority ALWAYS!
        # If low price touches or goes below SL, exit at SL level
        # Otherwise if close is below SL, exit at close
        if current_low <= sl:
            # Price went below SL during the day, exit at SL level
            exit_price = sl
            exit_date = current_date
            exit_reason = 'SL'
            break
        elif current_close <= sl:
            # Close is below SL, exit at close price
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
            if current_high >= target:
                exit_price = target
                exit_date = current_date
                exit_reason = 'TARGET'
                break

        # PRIORITY 5: Check if 21 trading days have passed (only if no target_date is set)
        if not target_date and idx >= 20:  # idx starts at 0; idx=20 means 21st trading day
            exit_price = current_close
            exit_date = current_date
            exit_reason = 'TIMEOUT'
            break

    if exit_price is None:
        # If we reach here, check if target_date is in future (beyond available data)
        last_available_date = price_data_from_entry[-1]['date_time'].split()[0]
        trading_days_held = len(price_data_from_entry)

        # If target_date is in future AND SL has not been hit, keep trade as UNREALIZED (don't sell)
        if target_date_in_future:
            # Get current close price (last available price)
            current_close = float(str(price_data_from_entry[-1]['close']).replace(',', ''))
            unrealized_pnl = current_close - actual_entry_price
            unrealized_pnl_pct = (unrealized_pnl / actual_entry_price) * 100

            exit_reason = 'UNREALIZED'
            # Don't set exit_price/exit_date - keep trade open
            return {
                'ticker': trade['ticker'],
                'entry_date': entry_date,
                'entry_price': actual_entry_price,
                'sl': sl,
                'target': target,
                'target_date': target_date,
                'high_on_target_date': high_on_target_date,
                'current_close': round(current_close, 2),
                'unrealized_pnl_pct': round(unrealized_pnl_pct, 2),
                'exit_date': None,
                'exit_price': None,
                'exit_reason': 'UNREALIZED',
                'pnl': None,
                'pnl_pct': None,
                'days_held': trading_days_held
            }

        # Otherwise, exit at last available price with TIMEOUT
        exit_price = float(str(price_data_from_entry[-1]['close']).replace(',', ''))
        exit_date = last_available_date
        exit_reason = 'TIMEOUT'

        # Try to find high price on target_date if it exists in data
        if target_date:
            for price_point in price_data_from_entry:
                if price_point['date_time'].split()[0] == target_date:
                    high_on_target_date = float(str(price_point['high']).replace(',', ''))
                    break

    # Calculate P&L using actual entry price (OPEN price of next day)
    pnl = exit_price - actual_entry_price
    pnl_pct = (pnl / actual_entry_price) * 100

    # Get the actual buy date (when order fills)
    buy_date = fill_day['date_time'].split()[0]

    return {
        'ticker': trade['ticker'],
        'entry_date': entry_date,
        'buy_date': buy_date,
        'entry_price': actual_entry_price,
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

        # Validate trade parameters before processing
        sl = trade['sl']
        entry = trade['entry']
        target = trade['target']

        skip_reason = None
        if sl >= entry:
            skip_reason = f'INVALID_SL (SL: {sl:.2f} >= Entry: {entry:.2f})'
        elif target <= entry:
            skip_reason = f'INVALID_TARGET (Target: {target:.2f} <= Entry: {entry:.2f})'

        if skip_reason:
            skipped_trades.append({
                'entry_date': entry_date,
                'ticker': ticker,
                'entry': trade['entry'],
                'sl': trade['sl'],
                'target': trade['target'],
                'skip_reason': skip_reason
            })
            skipped_entry_validation += 1
            continue

        # Process trade (will count trading days from price_action table)
        result = process_trade(trade, price_data)
        if result:
            results.append(result)
        else:
            # Trade was skipped due to entry validation
            # Find entry_date in price_data to get next trading day low
            entry_date_idx = None
            for idx, price_point in enumerate(price_data):
                if price_point['date_time'].split()[0] == entry_date:
                    entry_date_idx = idx
                    break

            if entry_date_idx is not None and entry_date_idx + 1 < len(price_data):
                next_day_low = float(str(price_data[entry_date_idx + 1]['low']).replace(',', ''))
                skip_reason = f'ENTRY_TOO_LOW (Entry: {trade["entry"]:.2f} < Next Low: {next_day_low:.2f})'
            elif entry_date_idx is None:
                skip_reason = 'ENTRY_DATE_NOT_TRADING_DAY'
            else:
                skip_reason = 'NO_NEXT_TRADING_DAY'

            skipped_trades.append({
                'entry_date': entry_date,
                'ticker': ticker,
                'entry': trade['entry'],
                'sl': trade['sl'],
                'target': trade['target'],
                'skip_reason': skip_reason
            })
            skipped_entry_validation += 1

    # Get stock symbol from trades (for display)
    stock_symbol = trades[0]['ticker'] if trades else 'UNKNOWN'

    # Separate completed trades from unrealized trades
    completed_trades = [r for r in results if r['exit_price'] is not None] if results else []
    unrealized_trades = [r for r in results if r['exit_price'] is None] if results else []

    # Display entry validation summary
    print(f"\n📊 ENTRY VALIDATION SUMMARY - {stock_symbol}")
    print(f"Total trades loaded:          {len(trades)}")
    print(f"Trades that passed entry validation (filled): {len(results)}")
    print(f"Trades skipped (entry price too low):         {skipped_entry_validation}")
    print(f"Trades skipped (no price data):               {skipped_no_data}")
    print(f"Pass rate: {len(results)/len(trades)*100:.1f}%\n")

    # Sort completed trades by buy date in ascending order
    completed_trades.sort(key=lambda x: x['buy_date'])

    # Display completed trades results
    if completed_trades:
        print(f"\n{'Buy Date':<12} {'Exit Date':<12} {'Ticker':<8} {'Entry':>10} {'SL':>10} {'Target':>10} {'Sell':>10} {'P&L %':>8} {'Days':>5}")
        print("-" * 115)

        for result in completed_trades:
            p_l_symbol = "+" if result['pnl_pct'] >= 0 else ""
            print(f"{result['buy_date']:<12} {result['exit_date']:<12} {result['ticker']:<8} {result['entry_price']:>10.2f} {result['sl']:>10.2f} {result['target']:>10.2f} {result['exit_price']:>10.2f} {p_l_symbol}{result['pnl_pct']:>7.2f}% {result['days_held']:>5}")

    # Display unrealized trades section (if any)
    if unrealized_trades:
        unrealized_trades.sort(key=lambda x: x['buy_date'])
        print(f"\n📈 UNREALIZED TRADES - {stock_symbol} ({len(unrealized_trades)} total)")
        print("=" * 145)
        print(f"{'Buy Date':<12} {'Target Date':<12} {'Ticker':<8} {'Entry':>10} {'SL':>10} {'Target':>10} {'Current':>10} {'Unrealized %':>12} {'Days Held':>10}")
        print("-" * 145)

        for trade in unrealized_trades:
            target_date_str = trade.get('target_date', '-') or '-'
            current_close = trade.get('current_close', 0)
            unrealized_pnl_pct = trade.get('unrealized_pnl_pct', 0)
            pnl_symbol = "+" if unrealized_pnl_pct >= 0 else ""
            print(f"{trade['buy_date']:<12} {target_date_str:<12} {trade['ticker']:<8} {trade['entry_price']:>10.2f} {trade['sl']:>10.2f} {trade['target']:>10.2f} {current_close:>10.2f} {pnl_symbol}{unrealized_pnl_pct:>11.2f}% {trade['days_held']:>10}")
        print("=" * 145)

    # Year-wise P&L Analysis (only for completed trades)
    year_stats = {}
    for result in completed_trades:
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

    # Calculate summary statistics (only for completed trades)
    total_pnl_pct = sum(r['pnl_pct'] for r in completed_trades)
    winning_trades = sum(1 for r in completed_trades if r['pnl_pct'] > 0)
    losing_trades = sum(1 for r in completed_trades if r['pnl_pct'] < 0)
    breakeven_trades = sum(1 for r in completed_trades if r['pnl_pct'] == 0)
    total_trades = len(completed_trades)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    avg_win = sum(r['pnl_pct'] for r in completed_trades if r['pnl_pct'] > 0) / winning_trades if winning_trades > 0 else 0
    avg_loss = sum(r['pnl_pct'] for r in completed_trades if r['pnl_pct'] < 0) / losing_trades if losing_trades > 0 else 0

    # Display summary (only for completed trades)
    if total_trades > 0:
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
    else:
        print("\n" + "=" * 100)
        print(f"📈 BACKTEST SUMMARY - {stock_symbol}")
        print("=" * 100)
        print("No completed trades - only unrealized trades present.")

    # Calculate profit factor (only for completed trades)
    if losing_trades > 0:
        profit_factor = abs(sum(r['pnl_pct'] for r in completed_trades if r['pnl_pct'] > 0) / sum(r['pnl_pct'] for r in completed_trades if r['pnl_pct'] < 0))
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
