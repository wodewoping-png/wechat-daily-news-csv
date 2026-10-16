# 钉钉群机器人日报

`channel-monitor` 已集成钉钉自定义机器人通知。前一日的抓取CSV实际提交到本库后，才进行监测统计和通知，已取消定时检查。发文日期取最新已入库且已经结束的日期，避免任务延迟或CSV迟到时误报。代码维护运行只测试、更新横向监控表；渠道配置变化会刷新当前统计和GitHub清单，均不向群发消息。

## 一次性配置

1. 在要接收日报的钉钉群添加“自定义机器人”，建议在安全设置中选择“加签”，复制该机器人的Webhook和加签密钥。
2. 打开本仓库 **Settings → Secrets and variables → Actions → Secrets**，新建Repository secrets：

   |名称|内容|
   |---|---|
   |`DINGTALK_WEBHOOK`|完整的 `https://oapi.dingtalk.com/robot/send?access_token=...` 地址，必填|
   |`DINGTALK_SECRET`|该机器人安全设置中的加签密钥，采用加签时必填|

   直接入口：[配置GitHub Actions Secrets](https://github.com/wodewoping-png/wechat-daily-news-csv/settings/secrets/actions)。地址中的access_token和加签密钥均按凭据处理，不写入代码、公共Issue或CSV。
3. 如果机器人使用“自定义关键词”，到同页的 **Variables** 新建 `DINGTALK_KEYWORD`，与机器人设置的关键词一致。本群机器人关键词为 `wechat`，默认消息关键词也设为 `wechat`。选择加签且未启用关键词限制时，可保持默认值。
4. 在 **Actions → channel-monitor → Run workflow** 运行一次，日期保持 `latest`，或填写已入库的具体日期。正常情况下无需勾选 `force_dingtalk`。

当前采用关键词匹配模式，无需配置 `DINGTALK_SECRET`，保持该Secret不存在或为空即可。每段消息都会包含 `wechat`。如另行设置了 `DINGTALK_KEYWORD` 变量，其值也应为 `wechat`。

Webhook对应的群就是接收群；通知不会@所有人。未配置Webhook时，工作流仍正常生成监测报告和GitHub异常清单，执行摘要会明确显示“未配置，本次未发送”。不把这类跳过报告为发送成功。

如果机器人启用了IP白名单，需要保证GitHub runner出口满足该白名单，否则钉钉会拒绝请求。

## 日报内容

- 发文日期、warning项数量及warning/critical项明细；critical一并保留，避免遗漏严重问题。
- [每日渠道监控CSV](channel_daily_counts.csv)链接：一行一个渠道、每日新增一个日期列，格子为该日已抓到的文章条目数。
- 不在群内逐条发送正常渠道数量、零观测渠道清单、恢复明细或冗长处理建议。全部信息仍可在仓库查看。
- 无warning/critical项时，只发送“无warning项”和监控表链接。

日报只表示CSV中抓到的文章，不是公众号实际全部发文数。数据缺失时显示“未知”，不当作0篇。沉默/数量骤降为疑似信号，确认失败需抓取器事件提供证据。

异常较多超过消息长度时自动分段，每段都有日期、分段序号、关键词和监控表链接。采用保守的UTF-8字节预算分段，发送间隔控制在机器人频率限制以下。单条warning内容过长时明确报错，不静默省略。

## 重试、去重与手动重发

- 根据接收Webhook、发文日期和各段内容生成稳定消息ID，通过钉钉 `msgUuid` 提交；网络超时重试使用相同ID。
- 只有HTTP请求成功且钉钉返回 `errcode=0` 才记为已发送，收到HTTP 200但业务码失败不会记账。
- 发送成功回执保存在 `quality/channel_monitor/dingtalk_delivery_state.json`，不保存Webhook、令牌或加签密钥。保留近期90天回执；同日CSV重复发布/工作流重跑且内容未变时跳过已发送段。
- 部分分段失败时，已确认成功的段仍会落盘并提交，重新运行时续发其余段；已发送内容发生变化时会发送更新后的分段。
- 网络错误、HTTP限流/服务错误和指定暂时性业务错误最多尝试3次；安全验证和无效机器人错误直接报错。发送失败会使相应Actions运行失败，且不会阻断GitHub异常清单的更新步骤。
- 需要人工重新发送全部分段时，手动Run workflow并勾选 `force_dingtalk`。它为该次手动运行生成新的消息ID；普通自动运行不允许强制重发。

发送回执在远程提交之前工作进程被强制终止、或回执提交失败，仍可能导致后续运行重发；消息ID是额外保护，不承诺端到端严格一次投递。

## 排查发送问题

查看 **Actions → channel-monitor → Send daily channel report to DingTalk**：

|提示|核查项|
|---|---|
|Webhook未配置|仓库的Repository secret名称是否为 `DINGTALK_WEBHOOK`|
|errcode=310000|关键词、加签密钥是否属于同一机器人，以及IP白名单|
|errcode=400101|Webhook令牌是否有效|
|errcode=400102 / 400106|机器人是否停用、删除或已移出群|
|errcode=410100|机器人被限流；等待后重跑，已确认的分段会跳过|
|网络/HTTP请求失败|检查runner网络及钉钉服务；日志不会输出含密钥的URL或原始API错误消息|

本地仅预览，不发送：

```bash
python scripts/notify_dingtalk.py --dry-run
python -m unittest discover -s tests -p 'test_notify_dingtalk.py' -v
```

预览文件：`quality/channel_monitor/dingtalk_preview.json`。预览不是送达证明；最终以钉钉群收到消息及工作流成功回执为准。

实现依据：[自定义机器人发送群消息](https://open.dingtalk.com/document/development/custom-robots-send-group-messages)、[自定义机器人安全设置](https://open.dingtalk.com/document/isvapp/customize-robot-security-settings)。
