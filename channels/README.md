# 公众号渠道总表与每日监测

[渠道汇总表](channel_summary.csv) · [当前检测报告](../quality/channel_monitor/latest.md) · [逐日发文记录](../quality/channel_monitor/daily_history.csv) · [异常和恢复事件](../quality/channel_monitor/incident_events.csv)

总表以用户提供的《公众号名单 2026-08-18.xlsx》163条记录为初始名单，再从仓库每日CSV自动识别新增信息源。名单中的名称、fakeid、微信号及来源依据保存在 `seed_accounts.csv`，原始Excel不被修改。

## 使用方式

- 每日CSV、名单或监测配置更新时触发检测。定时兜底为北京时间每日15:23、19:23；GitHub定时任务可能排队，实际执行时间以Actions记录为准。
- 默认监测北京时间昨天的发文，不把尚未结束的当天当作零发文。每日文件迟到或无效时显示“无法判断”，并提出全量数据告警。
- 新公众号自动写入总表，无需手动补录。fakeid相同的改名渠道合并并保留历史名称；同名但fakeid不同的渠道分别保留，提示核查。
- 总表为可用Excel打开的UTF-8 CSV，`registry.json`供程序读取。名称、状态和统计每天更新，人工调整应写入配置或seed名单，避免改动生成表后被下次运行覆盖。
- `daily_history.csv`按“发文日期×渠道”记录已观测文章数、零观测、未知状态、近期基线与异常线索。保留仓库每日数据覆盖的全部历史日期。每次重建也会纳入补抓或改正后的每日CSV。
- 每日报告存于 `quality/channel_monitor/daily/YYYY-MM-DD.md`，当前报告位于 `latest.md`；事件表记录首次检测、级别变化及恢复。相同输入重复运行不会重复记事件。

## 告警和反馈

Actions页面的执行摘要显示当天检测结果。程序维护一个标题为“公众号渠道监测：当前异常与核查清单”的GitHub Issue：出现问题自动创建/更新；全部解除后关闭。同一次检测结果不会重复创建Issue或重复更新。

项目使用者可订阅该Issue，或在仓库Watch中启用Issues/Actions通知，接收GitHub已有通知。未配置额外邮件或群机器人。确认抓取失败、未配置抓取或全量每日文件不可用时，检测工作流也会标记失败；疑似沉默和数量骤降只作核查提示。

检测规则：

|信号|证据与默认阈值|处理方式|
|---|---|---|
|每日数据缺失/为空/格式无效|昨天每日CSV未满足必要列或没有有效数据|全量高优先级告警，各渠道数量显示未知|
|名录渠道尚未观测|名单渠道在至少28个完整数据日都未抓到文章|核查是否纳入订阅、配置是否启用及fakeid是否匹配|
|疑似渠道中断|已具备足够历史，连续零观测天数达到历史发文间隔中位数的3倍，最少3天|先核对实际发文，排除停更和节假日；不能直接判定抓取失败|
|疑似数量骤降|过去28天至少14个完整数据日；同类工作日/周末基线至少3篇，当天低于基线20%|核查分页、时间范围、去重及数据源响应|
|确认抓取失败/未配置|抓取器提供最终error/not_configured事件|检查配置、认证/限流和日志，修复并补抓|
|身份冲突/重复文章/日期不符|同名不同fakeid，文章URL身份重复或日期不属文件日期|保留证据，核对名单或导出流程|

缺失整个每日文件不增加渠道零观测天数，也不被用于发文基线；全量数据缺失期间不宣告此前渠道异常恢复。没有新抓取事件且仍无文章时，不会自动清除已确认的抓取失败。

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
python scripts/monitor_channels.py --date yesterday
python -m unittest discover -s tests -p 'test_monitor_channels.py' -v
```
