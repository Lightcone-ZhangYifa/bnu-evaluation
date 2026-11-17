#!/bin/sh
# Restore ONLY into an isolated installation with a newly created database volume.
# Never run against the live installation. Copy environment.secret to .env BEFORE starting DB.
set -eu
umask 077
SOURCE=${1:?Usage: CONFIRM_EMPTY_RESTORE=YES sh deploy/restore.sh /absolute/backup}
[ "${CONFIRM_EMPTY_RESTORE:-}" = YES ] || { echo '必须明确确认在隔离、空白环境恢复' >&2; exit 1; }
case "$SOURCE" in /*) ;; *) echo '备份路径必须绝对路径' >&2; exit 1;; esac
python3 manage.py verify-backup "$SOURCE"
cmp -s .env "$SOURCE/environment.secret" || { echo '.env必须预先由本次备份environment.secret恢复；切勿覆盖运行中配置' >&2; exit 1; }
docker compose stop app gateway || true
docker compose up -d --wait database
# Refuse to merge with existing business tables.
N=$(docker compose exec -T database psql -U zc_migrator -d clearledger -Atc "select count(*) from information_schema.tables where table_schema='public'")
[ "$N" = 0 ] || { echo '目标数据库不是空白，拒绝恢复。使用新的隔离安装，不要删除在用数据卷。' >&2; exit 1; }
docker compose exec -T database pg_restore -U zc_migrator -d clearledger --no-owner --exit-on-error < "$SOURCE/database.dump"
docker compose run --rm -T --no-deps app sh -c 'test ! -d /data/objects || test -z "$(ls -A /data/objects)"' || { echo '对象目录不为空，拒绝覆盖' >&2; exit 1; }
docker compose run --rm -T --no-deps app tar -C /data -xf - < "$SOURCE/objects.tar"
docker compose run --rm -T --no-deps migrate
# Production scanner must be healthy before app starts; do not skip its health requirement.
docker compose up -d --wait scanner app
# Original signatures use the original APP_SECRET from the backup.
docker compose exec -T app python -m app.manage verify
printf '恢复与完整性校验命令已执行。人工核验业务快照后再执行 docker compose up -d gateway。\n'
