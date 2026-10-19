"""Publish every available completed week after daily CSV publication."""
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from scripts.classify_wechat_news import TZ, load_taxonomy, merge_weeks, read_json, write_json
    from scripts.generate_periodic_summary import generate_period
except ModuleNotFoundError:
    from classify_wechat_news import TZ, load_taxonomy, merge_weeks, read_json, write_json
    from generate_periodic_summary import generate_period


def reconcile(root, today=None):
    today = today or datetime.now(TZ).date()
    config = read_json(root / 'config/industry_classification.json')
    first = date.fromisoformat(config['start_date'])
    first -= timedelta(days=first.weekday())
    state_path = root / 'quality/weekly_summary_state.json'
    state = read_json(state_path, {})
    generated, unavailable = [], []
    start = first
    while start + timedelta(days=6) < today:
        days = [start + timedelta(days=i) for i in range(7)]
        year, week, _ = start.isocalendar()
        slug = f'{year}-W{week:02d}'
        paths = [root / 'csv' / f'{day}.csv' for day in days]
        missing = [str(day) for day, path in zip(days, paths) if not path.exists()]
        if missing:
            unavailable.append({'week': slug, 'missing_dates': missing})
        else:
            fingerprint = hashlib.sha256(b''.join(hashlib.sha256(p.read_bytes()).digest() for p in paths)).hexdigest()
            outputs = [root / 'csv/weekly' / f'{slug}.csv', root / 'reports/weekly' / f'{slug}.md']
            if state.get(slug) != fingerprint or not all(p.exists() for p in outputs):
                # Plain weekly exports are independent of the semantic API. Optional
                # AI trend generation runs downstream, after these files are published.
                generate_period(root, 'weekly', str(start), today=today, include_ai=False)
                state[slug] = fingerprint
                generated.append(slug)
        start += timedelta(days=7)
    write_json(state_path, state)
    taxonomy, _ = load_taxonomy(root)
    weeks = merge_weeks(root, config, taxonomy)
    print({'generated_weeks': generated, 'missing_source_weeks': unavailable,
           'classified_week_files': [w['file'] for w in weeks]})
    return generated, unavailable


if __name__ == '__main__':
    reconcile(Path(__file__).resolve().parents[1])
