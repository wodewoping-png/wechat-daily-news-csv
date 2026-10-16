# 钉钉群机器人日报

`channel-monitor` 已集成钉钉自定义机器人通知。日报发送北京时间昨天、已经结束的发文日期；每日CSV更新后触发，另有15:23、19:23的定时检查。GitHub定时任务可能排队，实际发送时间以Actions执行记录为准。

## 一次性配置

1. 在要接收日报的钉钉群添加“自定义机器人”，建议在安全设置中选择“加签”，复制该机器人的Webhook和加签密钥。
2. 打开本仓库 **Settings → Secrets and variables → Actions → Secrets**，新建Repository secrets：

   |名称|内容|
   |---|---|
   |`DINGTALK_WEBHOOK`|完整的 `https://oapi.dingtalk.com/robot/send?access_token=...` 地址，必填|
   |`DINGTALK_SECRET`|该机器人安全设置中的加签密钥，采用加签时必填|

   直接入口：[配置GitHub Actions Secrets](https://github.com/wodewoping-png/wechat-daily-news-csv/settings/secrets/actions)。地址中的access_token和加签密钥均按凭据处理，不写入代码、公共Issue或CSV。
3. 如果机器人使用“自定义关键词”，到同页的 **Variables** 新建 `DINGTALK_KEYWORD`，与机器人设置的关键词一致。默认消息关键词为 `公众号监测`。选择加签且未启用关键词限制时，可保持默认值。
4. 在 **Actions → channel-monitor → Run workflow** 运行一次，日期保持 `yesterday`。正常情况下无需勾选 `force_dingtalk`。

Webhook对应的群就是接收群；通知不会@所有人。未配置Webhook时，工作流仍正常生成监测报告和GitHub异常清单，执行摘要会明确显示“未配置，本次未发送”。不把这类跳过报告为发送成功。

如果机器人启用了IP白名单，需要保证GitHub runner出口满足该白名单，否则钉钉会拒绝请求。

## 日报内容

- 日期、渠道总数、当天有文章的渠道数、已抓取文章总数及数据完整性状态。
- 当前所有异常信号、处理建议，以及当天异常解除事件。
- 全部渠道的发文抓取数量、状态和渠道ID，包含零观测、未知及已停用渠道；不只发送有文章的渠道。
- 完整报告和渠道总表链接。

日报只表示CSV中抓到的文章，不是公众号实际全部发文数。数据缺失时显示“未知”，不当作0篇。沉默/数量骤降为疑似信号，确认失败需抓取器事件提供证据。

长日报自动分段发送，每段都有日期、分段序号和关键词。采用保守的UTF-8字节预算分段，发送间隔控制在机器人频率限制以下。某一渠道的单行内容过长时明确报错，不静默省略该渠道。

## 重试、去重与手动重发

- 根据接收Webhook、发文日期和各段内容生成稳定消息ID，通过钉钉 `msgUuid` 提交；网络超时重试使用相同ID。
- 只有HTTP请求成功且钉钉返回 `errcode=0` 才记为已发送，收到HTTP 200但业务码失败不会记账。
- 发送成功回执保存在 `quality/channel_monitor/dingtalk_delivery_state.json`，不保存Webhook、令牌或加签密钥。保留近期90天回执；同一天19:23复查内容未变时跳过已发送段。
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
