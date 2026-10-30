"""Run a neutral Z.AI request and publish only safe, actionable diagnostics."""
from datetime import datetime, timezone
from pathlib import Path

try:
    from scripts.classify_wechat_news import write_json
    from scripts.zai_industry_client import ZAIClient
except ModuleNotFoundError:
    from classify_wechat_news import write_json
    from zai_industry_client import ZAIClient


def main():
    root = Path(__file__).resolve().parents[1]
    result = ZAIClient().probe()
    result['checked_at'] = datetime.now(timezone.utc).isoformat()
    write_json(root / 'classified/api_diagnostics.json', result)
    if not result['usable']:
        raise SystemExit(result['reason'])
    print('Z.AI neutral probe succeeded; processing real articles next.', flush=True)


if __name__ == '__main__':
    main()
