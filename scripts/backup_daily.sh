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

# ── git 身份自愈（服务器重装后不再踩 Author identity unknown）──
git config user.email "backup@bot.local" 2>/dev/null || true
git config user.name "qqbot-backup" 2>/dev/null || true

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

# ── 2. 打包全量：/root/bot/data 全目录（排除临时/缓存/.bak）+ config + bg_tasks 补丁 ──
TARBALL="qqbot-full-$DATE.tar.gz"
STAGE="/tmp/qqbot_backup_stage"
# 2026-10-26：插件的核心实现（plugins/<name>/*_core.py）原来不在备份范围，
#   迁移后 modules/ 里的逻辑全部搬进 plugins/，只备份 bg_tasks/main.py 等于没备份。
#   plugins/phi/card_assets 是 212M 字体+插画，排除（单文件 >100M 会被 GitHub 拒收），
#   资产另存 3T 盘一份。
tar czf "$WORK/$TARBALL" \
    --exclude="data/img_temp"     --exclude="data/tts_temp"     --exclude="data/tmp" \
    --exclude="data/__pycache__" \
    --exclude="*.bak*"     --exclude="data/backup_daily.log"     --exclude="*/__pycache__" \
    --exclude="plugins/phi/card_assets" \
    -C /root/bot data config plugins >> "$LOG" 2>&1
rm -rf "$STAGE" "$SNAP"
[ -f "$WORK/$TARBALL" ] || { log "打包失败"; exit 1; }
SIZE=$(du -h "$WORK/$TARBALL" | cut -f1)
log "打包完成: $TARBALL ($SIZE)"

# ── 2b. KOOK bot 全量（同项目衍生，数据/配置/skills 共用）──
K_ROOT="/root/kook_bot"
if [ -d "$K_ROOT" ]; then
    KTARBALL="kook-full-$DATE.tar.gz"
    tar czf "$WORK/$KTARBALL"         --exclude="data/img_temp"         --exclude="data/tts_temp"         --exclude="data/tmp"         --exclude="data/__pycache__"         --exclude="*.bak*"         --exclude="logs"         --exclude="server_config_backup"         -C "$K_ROOT" data config skills db plugins >> "$LOG" 2>&1 || true
    log "KOOK 打包: $KTARBALL ($(du -h "$WORK/$KTARBALL" | cut -f1))"
fi

# ── 3a. 永久存 3T 盘（服务器本地全量，不删；2026-10-26 加）──
# GitHub 侧只留 7 天滚动窗口，3T 盘是永久层
KEEP3T="/mnt/3tdata/bot_data/backups"
mkdir -p "$KEEP3T"
cp -f "$WORK/qqbot-full-$DATE.tar.gz" "$KEEP3T/" 2>/dev/null
cp -f "$WORK/kook-full-$DATE.tar.gz"  "$KEEP3T/" 2>/dev/null
log "3T 永久存档: $KEEP3T/qqbot-full-$DATE.tar.gz"

# ── 3b. GitHub 侧 TTL 7 天 + 最多留 10 个 ──
find "$WORK" -name "qqbot-full-*.tar.gz" -mtime +7 -delete
find "$WORK" -name "kook-full-*.tar.gz" -mtime +7 -delete
find "$WORK" -name "qqbot-full-*.tar.gz" | sort | head -n -10 | xargs -r rm -f
find "$WORK" -name "kook-full-*.tar.gz" | sort | head -n -10 | xargs -r rm -f

# ── 4. 提交推送 ──
git add -A
git commit -m "daily backup $DATE ($SIZE)" >> "$LOG" 2>&1 || log "无变化，跳过提交"
git push origin main >> "$LOG" 2>&1 || { log "push 失败（密钥/网络）"; exit 1; }
log "备份已推送: $TARBALL ($SIZE)"
