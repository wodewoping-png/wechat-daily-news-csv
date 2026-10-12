# WeChat News Exports

This public repository stores daily WeChat public-account CSV exports and periodic Markdown summaries. The daily feed combines the 21C API primary source with a whitelisted RIOnews supplement, then deduplicates articles by URL.

## Downloads

Daily CSV files are stored in `csv/` and named by article publish date:

```text
csv/YYYY-MM-DD.csv
```

Weekly and monthly rollups, when published, are stored here:

```text
csv/weekly/YYYY-Www.csv
csv/monthly/YYYY-MM.csv
reports/weekly/YYYY-Www.md
reports/monthly/YYYY-MM.md
```

## Collection Quality Monitoring

### Continuous account monitoring

The channel inventory merges the uploaded 163-account list with accounts discovered in every daily CSV. It updates automatically, records daily observed post counts and unknown/missing-data states, detects suspected interruptions, and maintains one GitHub issue with current incidents and repair suggestions.

- [Channel inventory](channels/channel_summary.csv)
- [Current monitoring report](quality/channel_monitor/latest.md)
- [Daily history](quality/channel_monitor/daily_history.csv)
- [Setup, thresholds and crawler-event contract](channels/README.md)

`channel-monitor` runs after relevant data pushes and at 15:23 / 19:23 Beijing time. Zero observed articles are not proof of crawler failure; confirmed failures require structured crawler events. Missing daily feeds are reported as unknown, not zero activity.

The local crawler also publishes collection-volume checks for operational review:

```text
quality/daily_collection_counts.csv
quality/account_daily_counts.csv
reports/quality/YYYY-MM-DD.md
```

- `daily_collection_counts.csv` tracks total articles, active/tracked accounts, duplicate indicators, and source concentration by day.
- `account_daily_counts.csv` contains one row per account per day, including zero-post days, daily share, and suspected same-title duplicates.
- The daily quality report compares collection volume with the prior 14-day weekday/weekend median, explains recently added-account contributions, and lists suspected duplicate titles.

Empty daily CSV files are blocked from automatic publication unless an operator explicitly overrides the safety check.

## Published Fields

The CSV files include article metadata, source account, publish time, digest, original URL, and cleaned text fields produced by the local pipeline.

This repository does not publish crawler credentials, exporter auth keys, SQLite databases, raw article snapshots, or local filesystem paths.

## Update Flow

The source download and merge run locally. After each daily run, the generated CSV file is committed here for public download. Weekly summaries are generated for the previous Monday-Sunday window, and monthly summaries are generated for the previous calendar month.

## Automated Weekly and Monthly Summaries

This repository generates periodic summaries directly from its own `csv/YYYY-MM-DD.csv` files. It does not need the crawler's private SQLite database.

- Weekly: Monday-Wednesday at 14:23 Beijing time, covering the previous Monday-Sunday; Tuesday and Wednesday retry late daily uploads.
- Monthly: the first day of each month at 08:50 Beijing time, covering the previous calendar month.
- Manual: open **Actions → periodic-summary → Run workflow** and choose week, month, or both.

The generator fails if a daily CSV in the requested period is missing, preventing an incomplete report from being published silently. An explicitly partial local report can be generated with `--allow-missing`.

Run locally with Python 3.11+:

```bash
python scripts/generate_periodic_summary.py --period week --week last-week
python scripts/generate_periodic_summary.py --period month --month last-month
python -m unittest discover -s tests -v
```

### Optional Z.AI trend analysis

The deterministic source statistics, tags, article ranking, and archive are always generated. To add an AI-generated trend analysis section, configure these GitHub Actions settings:

- Secret `ZAI_API_KEY`: your Z.AI API key; never commit it to the repository.
- Variable `ZAI_BASE_URL`: optional API base URL; defaults to `https://api.z.ai/api/paas/v4`.
- Variable `ZAI_MODEL`: optional model name; defaults to `glm-5.1`.
- Variable `ZAI_REQUIRED`: optional. Set to `true` to fail the workflow when the model request fails; otherwise the statistical report is still published.

The model request sends representative article titles, sources, and short summaries to Z.AI through `POST /chat/completions`. Full local crawler credentials and private SQLite data are never sent.

## Cross-repository AI trend reports

The `cross-repo-ai-trend-summary` workflow combines matching weekly/monthly summaries from this repository and [`wodewoping-png/news-spider`](https://github.com/wodewoping-png/news-spider), then asks Z.AI to produce one overall trend report.

- Weekly output: `reports/trends/weekly/YYYY-Www.md`
- Monthly output: `reports/trends/monthly/YYYY-MM.md`
- Model: `ZAI_MODEL`, defaulting to the requested `glm-5.2`
- Required secret: `ZAI_API_KEY`
- Replaceable prompt template: `templates/cross-repo-trend-prompt.md`

The workflow stops when either upstream summary is missing. The current prompt is a provisional structure and can be replaced after the final report template is provided, while keeping the workflow and source-loading logic unchanged.
