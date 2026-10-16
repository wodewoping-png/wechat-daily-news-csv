# 公众号新闻行业分类

以用户提供的《行业图景.xlsx》为唯一分类框架，使用 GitHub Actions Secret `ZAI_API_KEY` 调用 **glm-5.3-flash** 进行语义判断。125条路径、最多8级，名称与层级按原表保留（包括原表中的“能源测”）。[框架表](分类框架.csv)可直接查看，机器配置位于 `config/industry_taxonomy.json`。

- `daily/YYYY-MM-DD.csv`：每日分类表，UTF-8 BOM，可用 Excel 打开。前8列为框架层级，随后是主分类路径、其他相关类别及置信度/依据/状态，原始标题、公众号、时间、URL、完整正文等字段随后保留；不输出 `digest`。
- `daily/YYYY-MM-DD.json`：原始条数、排除数、去重数、各分类状态数及源文件指纹，用于核查是否完整。
- `weekly/YYYY-Www.csv`：周一至周日的完整分类合并表；不再调用模型。同一篇文章只占一行，按框架路径排序。
- `weekly/YYYY-Www.partial.csv`：尚未收齐7天，或有分类失败/过期日报时的部分周表。日期缺口明确记录在同名 JSON 中，不冒充完整周报；补齐后自动替换成完整文件。
- `weekly/YYYY-Www.counts.csv`：每条主分类路径的文章数量，其他相关类别不重复累计。
- `cache/results.json`：成功模型结果缓存；重跑、重复抓取和跨日重复文章复用结果，正文、标题、模型或框架变化时重新判断。API失败不缓存，后续运行继续重试。

触发点是 `channel-monitor` 在每日CSV入库后完成（即使其渠道告警或通知步骤失败，分类仍可运行），没有固定时间触发。程序检查启用日期以来所有已入库文件，因此晚到、修订和此前失败的文件都会自动补跑；未变的日报不重复调用API。初始启用日期为 **2026-10-15**，可在配置中调整。分类代码或框架变更也会触发检查。分类结果提交不会触发抓取、钉钉通知或自身循环。

模型按完整标题和正文判断核心事件，最多3个实质相关类别，第一项为主分类；禁止仅靠公众号名称或关键词归类。主分类置信度低于0.65时记为“待复核”，候选类别保留在“其他分类路径”；没有可靠匹配时记为“不在框架内”，不擅自扩展原表。接口失败、漏项、非法类别、错误JSON会记为“分类失败”并使Action报错，成功结果和失败记录仍会提交以便重试；不会用关键词规则伪装成AI分类。

同一日报和周表按规范化URL去重，忽略微信公众号跟踪参数；没有URL时使用标题、公众号、发文时间识别。同一日报重复记录优先保留较长正文。误抓取渠道沿用 `channels/monitor_config.json` 的精确fakeid排除配置。暂停发文监控的渠道仍保留实际新闻分类，暂停统计不等于删除新闻。

CSV中的 `clean_text` 不设置字符截断。默认模型输入也使用完整正文；`max_content_chars: 0` 表示不限制输入长度，`batch_chars` 只控制同批打包量，单篇较长时单独提交。若今后配置非零输入预算，仅影响发给模型的内容，不会截短CSV。上游未提供的正文无法补全。

接口配置参考 `literature-rss-spider/zai_client.py`：优先 `https://api.z.ai/api/coding/paas/v4`，失败时尝试 BigModel OpenAI、Anthropic 兼容路由，模型固定 `glm-5.3-flash`，启用thinking并在OpenAI路由设置reasoning_effort=max。可用仓库 Variables `ZAI_BASE_URL`、`BIGMODEL_BASE_URL`、`ZAI_FALLBACK_BASE_URL`覆盖地址，密钥始终从Secret读取。限流、网络错误有限重试，缺密钥或额度失败时记录真实失败状态。

历史补跑：进入 Actions → **wechat-industry-classification** → Run workflow，填写 `start` 和 `end`（YYYY-MM-DD，包含首尾），补跑前须先有对应原始CSV。不填写则检查启用日期至昨天的已入库数据；勾选 `merge_only` 可仅合并已有分类日报。完整历史周表须先补齐该周7天分类。

本地验证：

```bash
python -m unittest discover -s tests -p 'test_industry_classification.py' -v
python scripts/classify_wechat_news.py --start 2026-10-15 --end 2026-10-15
python scripts/classify_wechat_news.py --merge-only
```
