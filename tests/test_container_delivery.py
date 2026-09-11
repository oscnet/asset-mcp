from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]


def test_runtime_dependency_keeps_fastmcp_v1_api_compatible():
    """输入项目运行时依赖声明；输出 MCP SDK 不会升级到破坏 FastMCP API 的 2.x。"""
    pyproject = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")

    assert '"mcp>=1.2.0,<2"' in pyproject


def test_compose_keeps_web_local_and_persists_private_data():
    """输入 Compose 交付文件；输出仅本机暴露、持久化及安全约束检查结果。"""
    compose = yaml.safe_load((REPOSITORY / "compose.yaml").read_text(encoding="utf-8"))
    service = compose["services"]["asset-mcp-web"]

    assert service["ports"] == ["127.0.0.1:${ASSET_MCP_WEB_PORT:-8501}:8501"]
    assert "asset-mcp-data:/data" in service["volumes"]
    assert service["environment"]["ASSET_MCP_CONFIG"] == "/data/config.local.yaml"
    assert service["environment"]["ASSET_MCP_DATABASE"] == "/data/portfolio.db"
    assert service["environment"]["ASSET_MCP_MASTER_PASSWORD_FILE"] == (
        "/run/secrets/asset_mcp_master_password"
    )
    assert "ASSET_MCP_MASTER_PASSWORD" not in service["environment"]
    assert "asset_mcp_master_password" in service["secrets"]
    assert compose["secrets"]["asset_mcp_master_password"]["file"].endswith(
        "/dev/null}"
    )
    assert service["read_only"] is True
    assert "no-new-privileges:true" in service["security_opt"]
    assert service["healthcheck"]["test"][0] == "CMD"
    assert "_stcore/health" in service["healthcheck"]["test"][-1]


def test_docker_image_runs_as_non_root_and_installs_web_extra():
    """输入 Dockerfile；输出非 root、Web 依赖及正式启动入口的静态契约检查。"""
    dockerfile = (REPOSITORY / "Dockerfile").read_text(encoding="utf-8")

    assert 'pip install --no-cache-dir ".[web]"' in dockerfile
    assert "USER assetmcp" in dockerfile
    assert "asset-mcp init" in dockerfile
    assert "asset-mcp-web" in dockerfile


def test_docker_context_excludes_local_secrets_and_portfolio_data():
    """输入 Docker 构建忽略文件；输出本地凭据、数据库与备份均不会进入镜像上下文。"""
    patterns = (REPOSITORY / ".dockerignore").read_text(encoding="utf-8")

    for expected in (".git", ".venv", "config.local.yaml", "*.db", "*.enc", "backups"):
        assert expected in patterns

    git_patterns = (REPOSITORY / ".gitignore").read_text(encoding="utf-8")
    for expected in (".env", "credentials*.json", "*.db", "*.enc", "backups/"):
        assert expected in git_patterns


def test_first_run_template_never_enables_sample_money():
    """输入首次运行配置模板；输出示例手工资产和借贷资产均默认禁用的安全检查。"""
    template = yaml.safe_load(
        (REPOSITORY / "src/asset_mcp/templates/config.local.yaml").read_text(encoding="utf-8")
    )

    assert template["manual"]["accounts"]
    assert all(account["enabled"] is False for account in template["manual"]["accounts"])
    assert template["loans"]
    assert all(loan["enabled"] is False for loan in template["loans"])
