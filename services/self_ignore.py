import time

# user_id -> 解除忽略的时间戳（秒）
_ignored = {}

_IGNORE_DURATION = 300  # 默认 5 分钟
MIN_MINUTES = 1
MAX_MINUTES = 120


def is_ignored(user_id):
    uid = str(user_id)
    if uid in _ignored:
        if time.time() < _ignored[uid]:
            return True
        del _ignored[uid]
    return False


def ignore_user(user_id, minutes: float | None = None):
    """忽略某个用户。

    v2.3.106：支持自定义时长（分钟）。原来固定 5 分钟且**全项目无人调用**，
    属于死代码；现在给 LLM 一个 ignore_user 工具，由它自己决定要不要静默某人。
    时长夹在 1~120 分钟之间，避免模型写出离谱的值。
    """
    dur = _IGNORE_DURATION if minutes is None else minutes * 60
    dur = max(MIN_MINUTES * 60, min(MAX_MINUTES * 60, float(dur)))
    _ignored[str(user_id)] = time.time() + dur
    return int(dur // 60)


def unignore_user(user_id):
    _ignored.pop(str(user_id), None)


def remaining_seconds(user_id):
    uid = str(user_id)
    if uid in _ignored:
        left = _ignored[uid] - time.time()
        return int(left) if left > 0 else 0
    return 0


def snapshot():
    """当前忽略表（排查用）。"""
    now = time.time()
    return {uid: int(exp - now) for uid, exp in _ignored.items() if exp > now}
