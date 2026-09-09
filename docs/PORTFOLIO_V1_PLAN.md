# Portfolio V1 开发计划

## 1. 核心目标

基于 `oscnet/asset-mcp` 构建一个只读、本地优先的个人多平台数字资产数据层。V1 必须可靠回答“有什么、多少、在哪里”，并为历史、风险、情景分析和 AI 提供确定性接口。

计划周期：2026-09-09 至 2026-10-08。复杂度：中等偏高。默认单用户、本地部署，不包含交易、转账、提现和链上签名。

## 2. 技术约束

- 保留 Python、FastMCP、httpx 和现有 Provider 架构。
- `origin` 指向 `oscnet/asset-mcp`；`upstream` 指向 `cidzhao/asset-mcp`。
- 内部金额使用 `Decimal`；现有 MCP JSON 数字字段在迁移期保持兼容。
- 所有交易所能力只读；测试不依赖真实 API Key 或网络。
- SQLite 作为 V1 唯一数据库；不引入 PostgreSQL、队列或微服务。
- 每个里程碑先写失败测试，新增代码覆盖率不低于 80%。

## 3. 里程碑

| 里程碑 | 日期 | 可衡量产出 | 状态 |
|---|---|---|---|
| M0 基线与准入 | 09-09 | Fork、Spike、上游基线、开发分支和路线图 | 完成 |
| M1 精确统一模型 | 09-09～09-11 | Decimal、账户类型/链/位置/价格来源/同步状态，旧 MCP 兼容 | 完成 |
| M2 合约仓位 | 09-12～09-17 | Binance USD-M、OKX Futures/Perpetual 仓位与未实现盈亏 | 完成 |
| M3 安全与历史 | 09-18～09-23 | 凭据保险库、SQLite 当前状态、每日幂等快照、STALE | 完成 |
| M4 组合计算与 MCP | 09-24～09-28 | allocation、risk、scenario、history 五类确定性查询 | 完成 |
| M5 Web V1 与交付 | 09-29～10-05 | Streamlit Dashboard、配置、明细、CSV/JSON、Docker | 进行中 |
| M6 真实账户验收 | 10-06～10-08 | Binance/OKX/BTC/EVM/Solana 对账，金额覆盖率 ≥90% | 需 Oscar 本地参与 |

## 4. 可执行任务

### M1：精确统一模型

- [x] 固定上游提交并建立 Fork 开发分支。
- [x] 用 `Decimal` 替换领域层金额计算，同时保持 MCP JSON 可序列化。
- [x] 增加 `accountType`、`chain`、`location`、`priceSource`、`syncStatus` 可选字段。
- [x] 增加策略、风险与自定义标签的数据结构。
- [x] 通过集中默认推导更新所有 Provider 映射，并完成兼容回归测试。

### M2：合约仓位

- [x] 定义只读 `Position` 模型及净/毛敞口所需字段。
- [x] 接入 Binance USD-M `positionRisk`，解析方向、数量、入场价、标记价、杠杆、清算价、未实现盈亏。
- [x] 接入 OKX `account/positions`，统一 Futures/Perpetual 字段。
- [x] 增加签名、空仓、双向持仓、缺失价格和权限失败契约测试。

### M3：安全与历史

- [x] 实现 OS Keychain 优先、Fernet 文件保险库回退的凭据引用。
- [x] YAML 只保存 `credentialRef`，保留旧配置的一次性迁移入口。
- [x] 实现 SQLite schema、迁移、当前资产/合约仓位和每日不可变快照。
- [x] 来源失败时保留最后成功资产/合约仓位并标记 `STALE`。
- [x] 实现带完整性校验和显式确认的备份与恢复命令。

### M4：组合计算与 MCP

- [x] `get_allocation`：资产、来源、账户类型、链、位置、标签分组。
- [x] `get_risk`：币种集中、CEX 集中、稳定币、自托管、合约敞口与杠杆。
- [x] `run_scenario`：按资产应用价格冲击，并计入线性合约方向敞口。
- [x] `get_history`：7/30/90 天及自定义窗口净值序列。
- [x] 扩展现有 MCP；AI 只能调用确定性只读服务。

### M5：Web V1 与交付

- [x] Streamlit 首页八项指标和资产/位置分布。
- [x] 总资产 → 币种 → 平台 → 账户 → 类型 → 位置下钻。
- [ ] 账户、钱包、手工资产和标签配置页面。
- [ ] CSV/JSON 导出。
- [ ] Docker Compose、健康检查、备份说明和用户文档。

### M6：真实账户验收

- [ ] Oscar 在本地录入只读凭据，不通过聊天或 Git 传递。
- [ ] 与 Binance、OKX 官方页面及第二实现交叉核对。
- [ ] 验证 BTC、EVM、Solana 各至少一个地址。
- [ ] 总金额覆盖率达到 90%，核心账户类型不得缺失。
- [ ] 故障演练确认旧值显示为 STALE 而不是零。

## 5. 关键依赖路径

```text
精确统一模型
  → 合约仓位
  → SQLite / STALE
  → 风险与情景计算
  → Web Dashboard
  → 真实账户验收
```

凭据保险库可与合约仓位并行设计，但必须在真实账户验收前完成。

## 6. 决策点

- M1 完成后确认 MCP 数字字段的兼容策略。
- M2 完成后确认是否在 V1 纳入 COIN-M 仓位；默认只保证 USD-M。
- M3 完成后确认本机优先使用 OS Keychain 还是主密码保险库。
- M6 若金额覆盖率低于 90%，停止 Web 扩展，优先补连接器。
