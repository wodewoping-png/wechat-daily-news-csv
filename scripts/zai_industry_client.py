"""Z.AI routes matching literature-rss-spider, with bounded retries and safe errors."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request


MODEL = "glm-5.3-flash"

# Account/route failures do not depend on article content. A blocked backup
# route must never poison a working primary route after a content-level 400.
ROUTE_BLOCK_CODES = {'1000', '1001', '1003', '1005', '1113', '1211', '1220',
                     '1308', '1309', '1310', '1311', '1313', '1314', '1315',
                     '1316', '1317', '1318', '1319', '1320', '1321'}
ERROR_LABELS = {
    '1000': '鉴权失败', '1001': '缺少鉴权', '1003': '凭证已过期',
    '1113': '余额或资源包不足', '1210': '请求参数错误', '1211': '模型不存在',
    '1212': '调用方式不支持', '1213': '缺少参数', '1214': '参数值不支持',
    '1215': '参数互斥', '1220': '接口无权限', '1261': '输入过长',
    '1301': '当前输入或输出被内容审核拦截', '1302': '请求速率受限',
    '1305': '服务暂时过载', '1308': '套餐时段额度已用完', '1309': '套餐已到期',
    '1310': '周或月额度已用完', '1311': '套餐不包含当前模型',
    '1313': '账号请求频率受限', '1314': '企业套餐失效', '1315': 'API密钥类型不匹配',
    '1316': '五小时额度及超额余额不足', '1317': '七天额度及超额余额不足',
    '1318': '五小时额度及消费上限受限', '1319': '七天额度及消费上限受限',
    '1320': '五小时额度及企业消费上限受限', '1321': '七天额度及企业消费上限受限',
}


def safe_error_details(body):
    """Return only a business code and our own label, never raw provider text."""
    try:
        data = json.loads(body)
        error = data.get('error', data) if isinstance(data, dict) else {}
        code = str(error.get('code', 'unknown')) if isinstance(error, dict) else 'unknown'
    except (ValueError, TypeError):
        code = 'unknown'
    if not code.isdigit() or len(code) > 8:
        code = 'unknown'
    return code, ERROR_LABELS.get(code, '未识别的接口错误')


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
        self.terminal_error = None
        self.blocked_routes = {}
        self.diagnostics = []
        self.routes = [
            (os.getenv("ZAI_BASE_URL") or "https://api.z.ai/api/coding/paas/v4", "openai"),
            (os.getenv("BIGMODEL_BASE_URL") or "https://open.bigmodel.cn/api/paas/v4", "openai"),
            (os.getenv("ZAI_FALLBACK_BASE_URL") or "https://open.bigmodel.cn/api/anthropic", "anthropic"),
        ]

    def generate(self, system, articles):
        if self.terminal_error:
            raise TerminalModelError(self.terminal_error)
        if not self.key:
            raise TerminalModelError("未配置 ZAI_API_KEY")
        routes = list(dict.fromkeys(self.routes))
        if self.preferred in routes:
            routes.remove(self.preferred)
            routes.insert(0, self.preferred)
        failures = []
        user = json.dumps({"articles": articles}, ensure_ascii=False, separators=(",", ":"))
        for base, protocol in routes:
            route = (base, protocol)
            route_name = ('coding' if '/coding/' in base else 'bigmodel') + '-' + protocol
            if route in self.blocked_routes:
                failures.append(self.blocked_routes[route])
                continue
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
                    code, label = safe_error_details(exc.read().decode("utf-8", errors="replace"))
                    detail = f"{route_name}: HTTP {exc.code}, code={code}（{label}）"
                    failures.append(detail)
                    self.diagnostics.append({'route': route_name, 'http_status': exc.code,
                                             'business_code': code, 'description': label})
                    self.diagnostics = self.diagnostics[-100:]
                    print('[zai] ' + detail, flush=True)
                    if code in ROUTE_BLOCK_CODES or exc.code in {401, 403}:
                        self.blocked_routes[route] = detail
                        break
                    if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                        break
                except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, ModelError):
                    failures.append(f"{protocol}: 网络或模型响应格式异常")
                if attempt < 2:
                    self.sleep(min(2 ** (attempt + 1), 8))
        message = "Z.AI 路由均不可用：" + "; ".join(failures[-9:])
        if all(route in self.blocked_routes for route in routes):
            self.terminal_error = message
            raise TerminalModelError(message)
        raise ModelError(message)

    def probe(self):
        """Check the route with neutral input before touching thousands of rows."""
        try:
            self.generate('请仅返回合法JSON对象，不要输出其他内容。',
                          [{'article_id': '0', 'title': '接口连通性检测', 'content': '电池技术新闻语义分类。请返回 {"ok":true}。'}])
            return {'usable': True, 'model': self.model, 'errors': self.diagnostics}
        except ModelError as exc:
            return {'usable': False, 'model': self.model, 'errors': self.diagnostics, 'reason': str(exc)}
