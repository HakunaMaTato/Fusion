#!/usr/bin/env bash
# One-time setup of a fresh Ubuntu 24.04 VM for LP Radar. Run it as a user with sudo:
#
#   sudo DEPLOY_PUBKEY="ssh-ed25519 AAAA... github-deploy" bash setup-vm.sh
#
# It installs Docker Engine and the compose plugin, creates a `deploy` user in the docker group,
# creates /opt/lp-radar with a private .env template, turns on unattended security upgrades and
# installs the daily database backup. It does not start the app: that is the first deploy.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
	echo "Run this with sudo." >&2
	exit 1
fi

DEPLOY_USER="${DEPLOY_USER:-deploy}"
APP_DIR="${APP_DIR:-/opt/lp-radar}"
DEPLOY_PUBKEY="${DEPLOY_PUBKEY:-}"
BACKUP_BUCKET="${BACKUP_BUCKET:-}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export DEBIAN_FRONTEND=noninteractive

echo "==> Packages"
apt-get update -y
apt-get install -y ca-certificates curl gnupg jq unattended-upgrades

echo "==> Docker Engine and the compose plugin"
if ! command -v docker >/dev/null 2>&1; then
	install -m 0755 -d /etc/apt/keyrings
	curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
	chmod a+r /etc/apt/keyrings/docker.asc
	echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
		>/etc/apt/sources.list.d/docker.list
	apt-get update -y
	apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

echo "==> Deploy user"
if ! id "$DEPLOY_USER" >/dev/null 2>&1; then
	useradd --create-home --shell /bin/bash --groups docker "$DEPLOY_USER"
else
	usermod --append --groups docker "$DEPLOY_USER"
fi
install -d -m 700 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh"
touch "/home/$DEPLOY_USER/.ssh/authorized_keys"
chmod 600 "/home/$DEPLOY_USER/.ssh/authorized_keys"
if [ -n "$DEPLOY_PUBKEY" ] && ! grep -qF "$DEPLOY_PUBKEY" "/home/$DEPLOY_USER/.ssh/authorized_keys"; then
	echo "$DEPLOY_PUBKEY" >>"/home/$DEPLOY_USER/.ssh/authorized_keys"
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "/home/$DEPLOY_USER/.ssh/authorized_keys"

echo "==> $APP_DIR"
install -d -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$APP_DIR" "$APP_DIR/deploy"
if [ ! -f "$APP_DIR/.env" ]; then
	cat >"$APP_DIR/.env" <<'ENVEOF'
# Fill this in, then run the first deploy. Never commit or share this file.
NANSEN_API_KEY=replace-me
NANSEN_MODE=replay              # switch to live once the key is set
DAILY_CREDIT_BUDGET=3000
CHAINS=solana,base,bnb,robinhood
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
DATABASE_URL=sqlite:////data/lp-radar.db
LOG_LEVEL=INFO
DASHBOARD_BASE_URL=https://REPLACE-WITH-DOMAIN
DOMAIN=REPLACE-WITH-DOMAIN      # for example 34.12.56.78.sslip.io
DASHBOARD_USERS_B64=            # base64 of lines "username bcrypt-hash"; see docs/deploy.md
ENVEOF
fi
chown "$DEPLOY_USER:$DEPLOY_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

echo "==> Unattended security upgrades"
cat >/etc/apt/apt.conf.d/20auto-upgrades <<'AUTOEOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
AUTOEOF

echo "==> Daily database backup"
if [ -f "$HERE/backup.sh" ]; then
	install -m 750 -o "$DEPLOY_USER" -g "$DEPLOY_USER" "$HERE/backup.sh" "$APP_DIR/backup.sh"
	if [ -n "$BACKUP_BUCKET" ]; then
		echo "15 3 * * * $DEPLOY_USER BACKUP_BUCKET=$BACKUP_BUCKET APP_DIR=$APP_DIR $APP_DIR/backup.sh >>/var/log/lp-radar-backup.log 2>&1" \
			>/etc/cron.d/lp-radar-backup
		chmod 644 /etc/cron.d/lp-radar-backup
		touch /var/log/lp-radar-backup.log
		chown "$DEPLOY_USER:$DEPLOY_USER" /var/log/lp-radar-backup.log
	else
		echo "BACKUP_BUCKET is not set; backups are not scheduled. Re-run with it to enable them."
	fi
else
	echo "backup.sh not found next to this script; backups are not scheduled."
fi

echo
echo "Done. Next: edit $APP_DIR/.env, then run the first deploy (docs/deploy.md)."
