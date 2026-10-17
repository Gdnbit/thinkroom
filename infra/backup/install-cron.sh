#!/usr/bin/env bash
# G6 · 个人使用：一键注册 cron 任务，每天自动备份 dev.db / Postgres.
#
# 用法:
#   bash infra/backup/install-cron.sh                 # 默认 daily 02:00
#   bash infra/backup/install-cron.sh --weekly        # 同时加 weekly
#   bash infra/backup/install-cron.sh --uninstall     # 卸载
#
# 注意:
# - 此脚本只往当前用户的 crontab 加任务，不需要 sudo
# - 若已有同名 marker 行，会先删除再加，避免重复
# - 项目根路径自动从脚本位置推算

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKUP_SCRIPT="$SCRIPT_DIR/backup.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
MARKER="# ROS-BACKUP-MANAGED"

WEEKLY=0
UNINSTALL=0
for arg in "$@"; do
    case "$arg" in
        --weekly) WEEKLY=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
    esac
done

# 校验
if [[ ! -f "$BACKUP_SCRIPT" ]]; then
    echo "ERROR: backup.py not found at $BACKUP_SCRIPT" >&2
    exit 1
fi
if ! command -v "$PYTHON_BIN" &>/dev/null; then
    echo "ERROR: '$PYTHON_BIN' not found. Set PYTHON_BIN env var." >&2
    exit 1
fi

# 卸载
if [[ "$UNINSTALL" == "1" ]]; then
    if crontab -l 2>/dev/null | grep -q "$MARKER"; then
        crontab -l 2>/dev/null | grep -v "$MARKER" | crontab -
        echo "[OK] Removed all ROS-BACKUP-MANAGED entries from crontab"
    else
        echo "(nothing to uninstall)"
    fi
    exit 0
fi

# 拼接新 crontab
TMP=$(mktemp)
crontab -l 2>/dev/null | grep -v "$MARKER" > "$TMP" || true

# daily 02:00
echo "0 2 * * * cd '$PROJECT_ROOT' && $PYTHON_BIN '$BACKUP_SCRIPT' --type daily --quiet $MARKER" >> "$TMP"

if [[ "$WEEKLY" == "1" ]]; then
    # weekly 03:00 周一
    echo "0 3 * * 1 cd '$PROJECT_ROOT' && $PYTHON_BIN '$BACKUP_SCRIPT' --type weekly --quiet $MARKER" >> "$TMP"
fi

crontab "$TMP"
rm -f "$TMP"

echo "[OK] Installed cron entries:"
crontab -l | grep "$MARKER" || true
echo ""
echo "Verify next run:  crontab -l"
echo "Run once now:     $PYTHON_BIN '$BACKUP_SCRIPT' --type daily"
echo "Uninstall:        bash '$0' --uninstall"
