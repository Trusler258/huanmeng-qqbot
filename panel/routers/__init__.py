"""路由包

约定：除 `auth` 与 `health` 外，**所有接口必须挂 `Depends(require_user)`**。
做法是在每个 router 定义时写 `dependencies=[Depends(require_user)]`，
这样新增接口默认就是受保护的——不会因为忘记加装饰器而裸奔。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from panel.auth import require_user
from panel import config

# 受保护 router 的统一依赖列表
PROTECTED = [Depends(require_user)]

# 允许直接编辑的文件白名单（来自配置）
EDITABLE = config.load().editable_globs
SENSITIVE = config.load().sensitive_config_globs
