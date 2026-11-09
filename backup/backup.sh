#!/bin/sh
# Database backups for the local stack: docker-compose.yml's `backup` service runs this.
#
# Every BACKUP_EVERY_SECONDS (default a day) it writes ./backups/leadgate-<UTC time>.dump
# (pg_dump's custom format) and keeps the newest BACKUP_KEEP (default 7). A failed dump
# is tried again an hour later. `sh /backup.sh once` makes one dump and exits.
#
# The dumps hold every lead's contact data, the users and the audit log, so they're
# written readable by their owner only (umask 077) and given to whoever owns ./backups
# (you, after a clone), not root. ./backups is kept out of git. Restoring: see the
# README, "Backups".
set -eu
umask 077

KEEP="${BACKUP_KEEP:-7}"
EVERY="${BACKUP_EVERY_SECONDS:-86400}"
DIR=/backups

dump() {
    file="$DIR/leadgate-$(date -u +%Y-%m-%d_%H%M%S).dump"
    # Written under another name first, so a half-written dump never looks like a backup.
    pg_dump --format=custom --file="$file.partial" || return 1
    mv "$file.partial" "$file"
    chown "$(stat -c %u:%g "$DIR")" "$file" 2>/dev/null || true
    # Keep the newest $KEEP dumps.
    ls -1t "$DIR"/leadgate-*.dump | tail -n +"$((KEEP + 1))" | xargs -r rm -f
    echo "backup: wrote $file"
}

if [ "${1:-}" = "once" ]; then
    dump
    exit
fi

while true; do
    if dump; then
        sleep "$EVERY"
    else
        rm -f "$DIR"/leadgate-*.dump.partial
        echo "backup: pg_dump failed; trying again in an hour" >&2
        sleep 3600
    fi
done
