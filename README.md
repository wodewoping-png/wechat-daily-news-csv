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
- [Daily channel count matrix](channels/channel_daily_counts.csv): one channel per row, one date per column; adds dates as CSVs arrive and updates past counts after backfills.
- [Current monitoring report](quality/channel_monitor/latest.md)
- [Daily history](quality/channel_monitor/daily_history.csv)
- [Setup, thresholds and crawler-event contract](channels/README.md)
- [DingTalk group robot setup](channels/DINGTALK.md): warning/critical items and a link to the daily channel count matrix; signing, chunking, retries and delivery deduplication are included. Keyword-only robots use the default keyword `wechat` and do not need `DINGTALK_SECRET`.

`channel-monitor` monitors and notifies after a completed daily CSV is published to this repository; it has no clock-based schedule. It selects the latest actual CSV date instead of assuming that yesterday's CSV already exists. Maintenance code pushes only test and refresh the matrix; account-list/config changes also refresh current reports and the managed issue, without group notifications. Zero observed articles are not proof of crawler failure; confirmed failures require structured crawler events. Missing dates inside the historical matrix are marked unknown, not zero activity.

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

New daily, weekly and monthly article CSVs omit `digest` and retain `clean_text`. Legacy digest-only rows use that text as a fallback when read or republished. Historical archives are preserved. The public ingestion workflow also removes the redundant column from legacy-format future uploads.

The CSV files include article metadata, source account, publish time, digest, original URL, and cleaned text fields produced by the local pipeline.

This repository does not publish crawler credentials, exporter auth keys, SQLite databases, raw article snapshots, or local filesystem paths.

## Update Flow

The source download and merge run locally. After each daily run, the generated CSV file is committed here for public download. Weekly summaries are generated for the previous Monday-Sunday window, and monthly summaries are generated for the previous calendar month.

## Automated Weekly and Monthly Summaries

### Industry classifications from 行业图景.xlsx

Daily news is semantically classified with **glm-5.3-flash** against the uploaded framework's 125 paths and 8 levels. The workflow runs after daily CSV publication/normalization and reuses cached decisions. See [分类文件夹](classified/README.md) for daily tables, category counts, weekly merges, completeness checks and historical backfills. Classified weekly tables merge the daily results without another AI request; full article bodies remain in the CSVs.

This repository generates periodic summaries directly from its own `csv/YYYY-MM-DD.csv` files. It does not need the crawler's private SQLite database.

- Weekly: after daily CSV publication, automatically publish completed Monday-Sunday weeks when all seven source files exist; Monday-Wednesday 14:23 Beijing time remains a backup check.
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
