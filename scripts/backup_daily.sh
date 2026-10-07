#!/bin/bash
# 每日全量备份 → 私有 GitHub 仓库（TTL 30 天）
# v2.3.81 (2026-10-07) — 服务器宕机事故后的数据侧闭环
set -u
REPO="git@github.com:Trusler258/qqbot-backup.git"
WORK="/root/qqbot-backup-repo"
KEY="/root/.ssh/qqbot_backup_ed25519"
DATE=$(date +%F)
LOG="/root/bot/data/backup_daily.log"

log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }

export GIT_SSH_COMMAND="ssh -i $KEY -o StrictHostKeyChecking=accept-new"

# ── 首次：clone 仓库 ──
if [ ! -d "$WORK/.git" ]; then
    git clone --depth 1 "$REPO" "$WORK" >> "$LOG" 2>&1 || { log "clone 失败（仓库不存在或密钥未配置）"; exit 1; }
fi
cd "$WORK" || exit 1

# ── 1. SQLite 一致性快照（直接 cp 可能拿到写一半的库）──
SNAP="/tmp/huanmeng_backup_$DATE.db"
python3 - <<PYEOF >> "$LOG" 2>&1
import sqlite3
src = sqlite3.connect("/root/bot/data/huanmeng.db")
dst = sqlite3.connect("$SNAP")
src.backup(dst)
dst.close(); src.close()
print("sqlite backup ok")
PYEOF
[ -f "$SNAP" ] || { log "sqlite 快照失败"; exit 1; }

# ── 2. 打包全量 ──
STAGE="/tmp/qqbot_backup_stage"
rm -rf "$STAGE"; mkdir -p "$STAGE/config" "$STAGE/plugins"
B="/root/bot"
cp "$SNAP" "$STAGE/huanmeng.db"
cp "$B/config/bot_config.toml" "$STAGE/config/" 2>/dev/null
cp "$B"/config/*.toml "$STAGE/config/" 2>/dev/null
cp "$B/config/.env" "$STAGE/config/" 2>/dev/null
cp "$B/config/roles.toml" "$STAGE/config/" 2>/dev/null
cp "$B/data/wdsj_player_name.json" "$STAGE/" 2>/dev/null
cp "$B/data/wdsj_collect_status.json" "$STAGE/" 2>/dev/null
cp "$B/data/user_profiles.json" "$STAGE/" 2>/dev/null
cp -r "$B/data/msglog" "$STAGE/" 2>/dev/null
cp -r "$B/data/stats_archive" "$STAGE/" 2>/dev/null
cp "$B"/data/memory_*.md "$STAGE/" 2>/dev/null
cp "$B/plugins/bg_tasks/main.py" "$STAGE/plugins/bg_tasks_main.py" 2>/dev/null
mkdir -p "$STAGE/plugins" 2>/dev/null
cp "$B/plugins/bg_tasks/main.py" "$STAGE/plugins/bg_tasks_main.py" 2>/dev/null

TARBALL="qqbot-full-$DATE.tar.gz"
tar czf "$WORK/$TARBALL" -C "$STAGE" . >> "$LOG" 2>&1
rm -rf "$STAGE" "$SNAP"
[ -f "$WORK/$TARBALL" ] || { log "打包失败"; exit 1; }
SIZE=$(du -h "$WORK/$TARBALL" | cut -f1)
log "打包完成: $TARBALL ($SIZE)"

# ── 3. TTL 30 天：删旧包 ──
find "$WORK" -name "qqbot-full-*.tar.gz" -mtime +30 -delete
find "$WORK" -name "qqbot-full-*.tar.gz" | sort | head -n -35 | xargs -r rm -f

# ── 4. 提交推送 ──
git add -A
git commit -m "daily backup $DATE ($SIZE)" >> "$LOG" 2>&1 || log "无变化，跳过提交"
git push origin main >> "$LOG" 2>&1 || { log "push 失败（密钥/网络）"; exit 1; }
log "备份已推送: $TARBALL ($SIZE)"
