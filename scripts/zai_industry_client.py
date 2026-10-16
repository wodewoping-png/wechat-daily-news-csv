"""Z.AI routes matching literature-rss-spider, with bounded retries and safe errors."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


MODEL = "glm-5.3-flash"


class ModelError(RuntimeError):
    pass


class TerminalModelError(ModelError):
    pass


def endpoint_url(base, protocol):
    suffix = "v1/messages" if protocol == "anthropic" else "chat/completions"
    return base.rstrip("/") if base.rstrip("/").endswith("/" + suffix) else base.rstrip("/") + "/" + suffix


class ZAIClient:
    def __init__(self, model=MODEL, opener=None, sleep=time.sleep):
        self.model = model
        self.key = os.getenv("ZAI_API_KEY", "").strip()
        self.opener = opener or urllib.request.urlopen
        self.sleep = sleep
        self.preferred = None
        self.routes = [
            (os.getenv("ZAI_BASE_URL") or "https://api.z.ai/api/coding/paas/v4", "openai"),
            (os.getenv("BIGMODEL_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4", "openai"),
            (os.getenv("ZAI_FALLBACK_BASE_URL") or "https://open.bigmodel.cn/api/anthropic", "anthropic"),
        ]

    def generate(self, system, articles):
        if not self.key:
            raise TerminalModelError("未配置 ZAI_API_KEY")
        routes = list(dict.fromkeys(self.routes))
        if self.preferred in routes:
            routes.remove(self.preferred)
            routes.insert(0, self.preferred)
        failures = []
        user = json.dumps({"articles": articles}, ensure_ascii=False, separators=(",", ":"))
        quota_failure = False
        for base, protocol in routes:
            messages = [{"role": "user", "content": user}]
            payload = {"model": self.model, "messages": messages, "max_tokens": 16384,
                       "temperature": 0, "thinking": {"type": "enabled", "clear_thinking": False},
                       "stream": False}
            if protocol == "openai":
                payload.update(messages=[{"role": "system", "content": system}, *messages],
                               response_format={"type": "json_object"}, reasoning_effort="max")
                headers = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
            else:
                payload["system"] = system
                headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"}
            request = urllib.request.Request(endpoint_url(base, protocol),
                                             data=json.dumps(payload, ensure_ascii=False).encode(), headers=headers, method="POST")
            for attempt in range(3):
                try:
                    with self.opener(request, timeout=180) as response:
                        data = json.loads(response.read().decode("utf-8"))
                    if protocol == "openai":
                        content = data["choices"][0]["message"]["content"]
                    else:
                        content = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
                    if not isinstance(content, str) or not content.strip():
                        raise ModelError("模型仅返回推理或空内容")
                    self.preferred = (base, protocol)
                    return content
                except urllib.error.HTTPError as exc:
                    # Do not log request objects, credentials, response bodies or article text.
                    body = exc.read().decode("utf-8", errors="replace")
                    quota = exc.code == 429 and any(code in body for code in ('"1113"', '"1308"'))
                    quota_failure = quota_failure or quota
                    failures.append(f"{protocol}: HTTP {exc.code}")
                    if quota or exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                        break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, ModelError):
                    failures.append(f"{protocol}: 网络或模型响应格式异常")
                if attempt < 2:
                    self.sleep(min(2 ** (attempt + 1), 8))
        message = "Z.AI 路由均不可用：" + "; ".join(failures[-9:])
        if quota_failure or (failures and all("HTTP 40" in f for f in failures)):
            raise TerminalModelError(message)
        raise ModelError(message)
