from __future__ import annotations

from abc import ABC, abstractmethod

from asset_mcp.domain.models import AccountStatus, Asset

AssetScope = tuple[str, str]


class PartialAssetFetchError(RuntimeError):
    """携带已成功资产的部分 Provider 读取异常。

    输入：成功读取的资产、失败范围集合和不含账户秘密的错误摘要。失败范围使用
    ``(accountId, wallet)``，仅供 Service 从本地缓存选择对应旧资产，不会对外输出。
    输出：可被 Service 识别的异常对象；``assets`` 与 ``failed_scopes`` 保存为副本，
    允许返回部分实时结果并为失败范围回退 STALE。
    """

    def __init__(
        self,
        assets: list[Asset],
        failed_scopes: set[AssetScope],
        message: str,
    ):
        self.assets = list(assets)
        self.failed_scopes = frozenset(failed_scopes)
        super().__init__(message)


class AssetProvider(ABC):
    @abstractmethod
    async def fetch_assets(self) -> list[Asset]:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> list[AccountStatus]:
        raise NotImplementedError
