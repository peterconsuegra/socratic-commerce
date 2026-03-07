import os
import pandas as pd

def get_weekly_order_stats(csv_path):
    """
    Reads a CSV of orders and returns:
      - weekly_stats: list of dicts with:
          * week_start, week_end
          * order_count (total)
          * avg_up_to_week (running avg)
          * avg_daily (avg per day that week)
      If file missing or empty, returns [].
    """
    if not os.path.exists(csv_path):
        return []

    try:
        df = pd.read_csv(csv_path, parse_dates=['order_date'])
    except Exception:
        return []

    if df.empty:
        return []

    df.set_index('order_date', inplace=True)
    weekly_counts = df.resample('W').size()

    weekly_stats = []
    cumulative = 0
    for idx, (week_end, count) in enumerate(weekly_counts.items(), start=1):
        cumulative += count
        week_start = week_end - pd.Timedelta(days=6)
        weekly_stats.append({
            'week_start':     week_start.date().isoformat(),
            'week_end':       week_end.date().isoformat(),
            'order_count':    int(count),
            'avg_up_to_week': cumulative / idx,
            'avg_daily':      count / 7,
        })

    return weekly_stats