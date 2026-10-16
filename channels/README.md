# 公众号渠道总表与每日监测

[每日渠道监控表（每日一列）](channel_daily_counts.csv) · [渠道汇总表](channel_summary.csv) · [当前检测报告](../quality/channel_monitor/latest.md) · [逐日发文记录](../quality/channel_monitor/daily_history.csv) · [异常和恢复事件](../quality/channel_monitor/incident_events.csv)

总表以用户提供的《公众号名单 2026-08-18.xlsx》163条记录为初始名单，再从仓库每日CSV自动识别新增信息源。名单中的名称、fakeid、微信号及来源依据保存在 `seed_accounts.csv`，原始Excel不被修改。

## 使用方式

- 前一日抓取CSV提交到本库 `csv/YYYY-MM-DD.csv` 后触发检测，不依赖时钟定时任务。CSV尚未入库时不预先生成“昨天缺数据”的群消息。
- 检测日期取已入库、已经结束的最新发文日期，避免GitHub任务延迟跨日后错误选择尚不存在的“昨天”文件。修改/补抓旧CSV会重算至最新已入库日期。已入库文件为空或格式无效时显示“无法判断”并提出数据告警。
- 代码维护推送只运行测试和更新横向监控表，不向群发消息；名单/渠道配置变化会额外刷新当前统计及GitHub异常清单，也不向群发消息。日常钉钉通知仍只在每日CSV实际入库后触发。
- 新公众号自动写入总表，无需手动补录。fakeid相同的改名渠道合并并保留历史名称；同名但fakeid不同的渠道分别保留，提示核查。
- 总表为可用Excel打开的UTF-8 CSV，`registry.json`供程序读取。名称、状态和统计每天更新，人工调整应写入配置或seed名单，避免改动生成表后被下次运行覆盖。
- `daily_history.csv`按“发文日期×渠道”记录已观测文章数、零观测、未知状态、近期基线与异常线索。保留仓库每日数据覆盖的全部历史日期。每次重建也会纳入补抓或改正后的每日CSV。
- `channel_daily_counts.csv`是一行一个渠道、每天一个日期列的监控表。新日期首次入库后自动增加列；补抓会修正已有日期的数量。`0`表示数据完整但未抓到该渠道文章；`未知`表示对应每日数据缺失或无效；`未纳入`表示当时尚未开始跟踪该渠道。表格截至最后实际入库的日期，不提前添加尚未入库的新一天。
- 每日报告存于 `quality/channel_monitor/daily/YYYY-MM-DD.md`，当前报告位于 `latest.md`；事件表记录首次检测、级别变化及恢复。相同输入重复运行不会重复记事件。

## 告警和反馈

Actions页面的执行摘要显示当天检测结果。程序维护一个标题为“公众号渠道监测：当前异常与核查清单”的GitHub Issue：出现问题自动创建/更新；全部解除后关闭。同一次检测结果不会重复创建Issue或重复更新。

项目使用者可订阅该Issue，或在仓库Watch中启用Issues/Actions通知，接收GitHub已有通知。另已接入[钉钉群机器人日报](DINGTALK.md)，群内仅发送warning/critical项及每日渠道监控CSV链接，不逐条推送全部渠道。确认抓取失败、未配置抓取或已入库的每日文件不可用时，检测工作流也会标记失败；疑似沉默和数量骤降只作核查提示。

检测规则：

|信号|证据与默认阈值|处理方式|
|---|---|---|
|已入库数据为空/格式无效|实际提交的每日CSV未满足必要列或没有有效数据|全量高优先级告警，各渠道数量显示未知|
|名录渠道尚未观测|名单渠道在至少28个完整数据日都未抓到文章|核查是否纳入订阅、配置是否启用及fakeid是否匹配|
|疑似渠道中断|已具备足够历史，连续零观测天数达到历史发文间隔中位数的3倍，最少3天|先核对实际发文，排除停更和节假日；不能直接判定抓取失败|
|疑似数量骤降|过去28天至少14个完整数据日；同类工作日/周末基线至少3篇，当天低于基线20%|核查分页、时间范围、去重及数据源响应|
|确认抓取失败/未配置|抓取器提供最终error/not_configured事件|检查配置、认证/限流和日志，修复并补抓|
|身份冲突/重复文章/日期不符|同名不同fakeid，文章URL身份重复或日期不属文件日期|保留证据，核对名单或导出流程|

缺失整个每日文件不增加渠道零观测天数，也不被用于发文基线；全量数据缺失期间不宣告此前渠道异常恢复。没有新抓取事件且仍无文章时，不会自动清除已确认的抓取失败。

## 渠道名称、暂停与排除

固定显示名称为“能斯特新能源情报局”“风光储知识分享库”“化工碳中和”“HPF热泵在线”；旧名称作为别名识别，后续抓取不会覆盖回旧名称。

`monitor_config.json` 的 `paused_accounts` 保存暂停名单：仁烁光能、储能调研、大锌能源、小柯说能源、泛能源战略研究、电催化前沿、深圳欣界能源科技股份有限公司、知识褶皱、钙钛矿太阳能电池。暂停按名称匹配全部对应身份记录，不参与当前数量、横向监控表及告警。渠道汇总表保留“已暂停统计”记录，历史源CSV保留；恢复时从暂停名单移除即可。

`excluded_fakeids` 精确排除误抓取的 `MzkyNzU5MTc5OA==`（创新研究）、`Mzg3NDE1NjE5MA==`（空天信息）。保留正确身份 `MzAxMjY2OTkxOA==`、`MzA5OTcxODQ5Nw==`，不会仅按同名批量删除。后续每日CSV入库规范化也会剔除这两个指定身份。

## 正文字段

后续每日、每周、每月文章CSV移除 `digest` 列，保留 `clean_text`。旧版每日数据仍可读取；若只有 `digest` 而正文为空，则将摘要作为保底内容保留在 `clean_text`，避免丢失仅存文字。历史档案不批量改写。

当前21C/RIO合并流程的 `digest` 是清洗正文前300字符，`clean_text` 最多10000字符；本地数据库导出流程的digest可能是独立微信摘要，clean_text导出最多2000字符。两者并非所有历史行都相同，clean_text也不保证已保留原文全文。本次不改变原有正文截断上限。

## 人工配置与修复

在 `monitor_config.json` 的 `overrides` 中按总表中的渠道ID修改，例如：

```json
{
  "overrides": {
    "wx:EXAMPLE_FAKEID": {
      "enabled": true,
      "expected_interval_days": 7,
      "silence_warn_days": 21,
      "silence_critical_days": 42
    }
  }
}
```

正常低频/停更的渠道可调高阈值或设置 `enabled: false`。确认上游漏抓后，修复上游抓取器并重新提交相应每日CSV，再运行 **Actions → channel-monitor → Run workflow**。日期不可早于当前监测状态；历史实验使用独立目录，防止回滚当前状态。

## 抓取器事件接口

此仓库只收到每日导出数据，无法从“没有文章”判断公众号是否真正发文，也无法直接检查上游登录状态。要确认单渠道失败，上游抓取器需额外发布：

```text
quality/channel_fetch_events/YYYY-MM-DD.jsonl
```

日期为对应的发文日期。每行一个按尝试顺序排列的最终结果，用总表 `channel_id`、fakeid或唯一匹配的名称标识渠道；同渠道最后一条决定最终状态。例如：

```json
{"fakeid":"EXAMPLE_FAKEID","status":"error","error_code":"HTTP_401"}
{"fakeid":"EXAMPLE_FAKEID","status":"success"}
```

允许 `success`、`error`、`not_configured`。错误码只允许字母、数字、下划线和连字符；不发布原始错误消息。禁止把密码、访问令牌、Cookie或带凭据的原始日志提交到这个公共仓库。无有效事件时，报告只提供待核查线索。成功但零文章也不能证明公众号没有发布新文章。

本地运行：

```bash
python scripts/monitor_channels.py --date latest
python scripts/monitor_channels.py --date latest --matrix-only
python -m unittest discover -s tests -p 'test_monitor_channels.py' -v
```
