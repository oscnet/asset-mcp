# Asset MCP Docker 部署与运维

本指南面向单用户、本地自托管场景。Web Dashboard 只监听 `127.0.0.1`；配置、
SQLite 和加密凭据保存在 Docker 命名卷 `asset-mcp-data` 中。容器以非 root 用户运行，
根文件系统只读。

## 1. 前置条件

- Docker Desktop 或 Docker Engine 已启动。
- Docker Compose V2 可用：`docker compose version`。
- 本机端口 `8501` 未被占用，或通过 `ASSET_MCP_WEB_PORT` 改用其他端口。

## 2. 首次启动

### 2.1 仅手工资产或公开钱包

这类数据源不需要 API 凭据，可直接启动：

```bash
docker compose up -d --build
docker compose ps
```

首次启动会在命名卷创建 `/data/config.local.yaml`。所有示例账户默认禁用，不会产生
虚假的 Portfolio 金额。打开 <http://127.0.0.1:8501>，在“配置中心”启用需要的账户。

### 2.2 使用 Binance 或 OKX

容器不能使用宿主机 Keychain，因此使用主密码保护的 Fernet 加密文件。创建一个不纳入
Git 和 Docker 构建上下文的密码文件：

```bash
mkdir -p .secrets
chmod 700 .secrets
read -s -p "Asset MCP 主密码: " ASSET_MCP_PASSWORD; printf '\n'
printf '%s' "$ASSET_MCP_PASSWORD" > .secrets/master-password
unset ASSET_MCP_PASSWORD
chmod 600 .secrets/master-password
printf '%s\n' 'ASSET_MCP_MASTER_PASSWORD_FILE=.secrets/master-password' > .env
docker compose up -d --build
```

`.env` 只保存文件路径，密码值不会出现在 `docker compose config`、容器环境变量或命令
历史中。请把同一主密码另存于密码管理器；密码丢失后无法解密 `credentials.enc`。

如果容器已经按 2.1 启动，创建密码文件后执行 `docker compose up -d --force-recreate`。
普通 `docker compose restart` 会沿用现有 secret 挂载；重建容器时 `.env` 指向的密码文件
必须仍存在。如需换端口，在 `.env` 追加 `ASSET_MCP_WEB_PORT=18501` 后重新启动。

## 3. 健康检查

```bash
docker compose ps
curl -fsS http://127.0.0.1:8501/_stcore/health
```

预期响应为 `ok`，Compose 状态最终变为 `healthy`。这只证明 Web 进程可用；各数据源
是否同步成功，还应检查 Dashboard 的 `Data Status`、风险警告或 MCP 工具
`health_check_sources`。

排查启动问题：

```bash
docker compose logs --tail=200 asset-mcp-web
docker compose config
```

## 4. 安全导入 API 凭据

不要把秘密写进 Web YAML、命令参数或临时 JSON 文件。下面的命令在终端静默读取字段，
通过 stdin 直接写入加密保险库：

```bash
python3 - <<'PY' | docker compose exec -T asset-mcp-web \
  asset-mcp set-credential --reference binance/binance-main
import getpass
import json

print(json.dumps({
    "apiKey": getpass.getpass("Binance API Key: "),
    "apiSecret": getpass.getpass("Binance API Secret: "),
}))
PY
```

OKX 使用相同方式，并额外提供 `passphrase`。然后在“配置中心”给对应账户设置：

```yaml
credentialRef: binance/binance-main
enabled: true
```

API 权限必须保持只读，不得开启交易、转账或提现。若使用密码管理器 CLI，可让它直接
生成符合要求的 JSON 并管道给 `set-credential`，避免秘密落盘。

## 5. 完整备份

容器内 `/data/backups` 与业务数据位于同一命名卷，只是生成一致性 SQLite 副本，不能
算灾难恢复备份。必须再导出到宿主机或其他备份介质。

```bash
mkdir -p backups
chmod 700 backups
docker compose exec asset-mcp-web \
  asset-mcp backup --path /data/backups/portfolio-2026-09-10.db
docker compose exec -T asset-mcp-web \
  cat /data/backups/portfolio-2026-09-10.db \
  > backups/portfolio-2026-09-10.db
docker compose exec -T asset-mcp-web cat /data/config.local.yaml \
  > backups/config.local.yaml
docker compose exec -T asset-mcp-web test -f /data/credentials.enc && \
  docker compose exec -T asset-mcp-web cat /data/credentials.enc \
  > backups/credentials.enc
chmod 600 backups/*
shasum -a 256 backups/* > backups/SHA256SUMS
```

如果从未保存 API 凭据，`credentials.enc` 不存在是正常情况。主密码应单独保存在密码
管理器中，不要和加密文件放在一起。

验证 SQLite 备份完整性：

```bash
python3 - <<'PY'
import sqlite3

path = "backups/portfolio-2026-09-10.db"
with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
    assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
print("SQLite backup: ok")
PY
```

## 6. 恢复演练

恢复前先核对校验和并停止 Web，防止并发写入：

```bash
shasum -a 256 -c backups/SHA256SUMS
docker compose stop asset-mcp-web
docker compose run --rm -T asset-mcp-web \
  asset-mcp restore --stdin --yes \
  < backups/portfolio-2026-09-10.db
```

恢复命令会把 stdin 暂存到容器临时目录，验证 SQLite 完整性和 Asset MCP schema，
完成迁移与复检后才原子替换当前数据库；失败时保留当前数据库。

按需恢复配置和凭据。通过 stdin 写入可确保文件由容器内的非 root 用户创建：

```bash
docker compose run --rm -T asset-mcp-web sh -c \
  'umask 077; cat > /data/config.local.yaml' \
  < backups/config.local.yaml
docker compose run --rm -T asset-mcp-web sh -c \
  'umask 077; cat > /data/credentials.enc' \
  < backups/credentials.enc
docker compose up -d
```

只有备份中存在 `credentials.enc` 时才执行第二条命令，并确保使用创建该文件时的同一个
主密码。恢复后检查：

```bash
docker compose ps
curl -fsS http://127.0.0.1:8501/_stcore/health
docker compose logs --tail=100 asset-mcp-web
```

最后打开 Dashboard，核对总资产、账户数量和数据源状态；真实交易所与钱包余额还应与
官方页面抽样比对。建议每次升级前做完整备份，并定期在独立环境完成恢复演练。

## 7. MCP 客户端

Web 容器和 MCP stdio Server 共享命名卷。支持自定义命令的 MCP 客户端可以使用：

```bash
docker compose -f /absolute/path/to/asset-mcp/compose.yaml \
  run --rm -T asset-mcp-web asset-mcp serve
```

MCP 使用 stdio，不要分配 TTY，也不要把日志写到 stdout。

## 8. 升级、停止与删除

```bash
git pull
docker compose up -d --build
docker compose ps
```

普通停止使用 `docker compose down`，不会删除命名卷。不要随意执行
`docker compose down -v`；`-v` 会删除配置、SQLite 和加密凭据，只有完成外部备份且
明确要彻底重置时才能使用。

## 9. 交付验收清单

- `docker compose config` 无错误且不显示主密码值。
- `docker compose ps` 显示 `healthy`，健康端点返回 `ok`。
- Dashboard 不显示默认样例金额。
- 配置中心拒绝明文秘密，只接受 `credentialRef`。
- 当前筛选结果可下载 CSV 和 JSON。
- 数据库、配置和可选凭据已导出命名卷，并生成校验和。
- SQLite `quick_check` 与 stdin 恢复演练通过。
- 恢复后 Dashboard 总额、账户数和数据源状态符合预期。
- Binance、OKX 和钱包真实金额将在 M6 与官方页面交叉核对。
