# Deploying LP Radar (GCP Compute Engine + Docker)

One small VM runs three containers from one image: `web` (dashboard), `worker` (polls Nansen and
scores tokens) and `caddy` (HTTPS and the login). SQLite lives on a Docker volume shared by `web`
and `worker`. A merge to `main` deploys automatically once the VM is configured (section 7).

Estimated cost: an `e2-small` is about $13 a month, the static IP is free while it is attached,
the 20 GB disk about $1, backups a few cents. Section 12 shows how to stop paying.

Every `gcloud` command below names the project explicitly, so it can never run against another one:

```bash
export PROJECT=project-fusion-509817
export REGION=europe-west8
export ZONE=europe-west8-a
```

Check that you are where you think you are before you start:

```bash
gcloud config get-value project
```

## 1. Enable the APIs (once)

```bash
gcloud services enable compute.googleapis.com storage.googleapis.com --project=$PROJECT
```

Billing must be linked to the project (Console, Billing). Check that a `default` network exists
(some organisations disable its creation):

```bash
gcloud compute networks list --project=$PROJECT
```

## 2. Static IP, VM and firewall

```bash
gcloud compute addresses create lp-radar-ip --region=$REGION --project=$PROJECT
```

```bash
gcloud compute instances create lp-radar \
  --project=$PROJECT --zone=$ZONE --machine-type=e2-small \
  --image-family=ubuntu-2404-lts-amd64 --image-project=ubuntu-os-cloud \
  --boot-disk-size=20GB --tags=lp-radar-web \
  --address=lp-radar-ip --scopes=storage-rw
```

`--scopes=storage-rw` lets the VM write its backups to Cloud Storage (section 8).

```bash
gcloud compute firewall-rules create lp-radar-web \
  --project=$PROJECT --allow=tcp:80,tcp:443,udp:443 --target-tags=lp-radar-web
```

The address you get is the VM's public IP:

```bash
gcloud compute addresses describe lp-radar-ip --region=$REGION --project=$PROJECT --format="value(address)"
```

Your dashboard host name is that IP with dots replaced by dashes plus `.sslip.io`, for example
`34-12-56-78.sslip.io`. sslip.io is a free service that resolves such names to the IP, and Caddy gets
a real HTTPS certificate for it. Use your own domain later by pointing an A record at the IP and
changing `DOMAIN`.

## 3. Prepare the VM

Copy the two scripts up and run the setup:

```bash
gcloud compute scp deploy/setup-vm.sh deploy/backup.sh lp-radar:~ --zone=$ZONE --project=$PROJECT
```

Create an SSH key pair that only GitHub Actions will use (do not reuse a personal key), and keep
the private key out of the repository:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/lp-radar-deploy -N "" -C github-deploy
```

Run the setup on the VM, passing the public key so that the `deploy` user accepts it. Add
`BACKUP_BUCKET=...` if you already made the bucket (section 8):

```bash
gcloud compute ssh lp-radar --zone=$ZONE --project=$PROJECT --command "sudo DEPLOY_PUBKEY='$(cat ~/.ssh/lp-radar-deploy.pub)' bash setup-vm.sh"
```

The script installs Docker and the compose plugin, creates the `deploy` user (in the `docker`
group), creates `/opt/lp-radar` with a private `.env` template, enables unattended security
upgrades and, when `BACKUP_BUCKET` is set, schedules the daily backup.

## 4. Fill in `/opt/lp-radar/.env`

Open a shell on the VM as the deploy user's owner and edit the file:

```bash
gcloud compute ssh lp-radar --zone=$ZONE --project=$PROJECT
```

```bash
sudo -u deploy nano /opt/lp-radar/.env
```

Set at least:

| Variable | Value |
|---|---|
| `NANSEN_API_KEY` | your production key (only ever typed here, never committed or pasted into chat) |
| `NANSEN_MODE` | `live` once the key is set; `replay` has no fixtures in the image and does nothing useful |
| `DAILY_CREDIT_BUDGET` | `3000` to start; the worker spends at most a 24th of it per hour and stops at the budget |
| `CHAINS` | `solana,base,bnb,robinhood` |
| `DOMAIN` | for example `34-12-56-78.sslip.io` |
| `DASHBOARD_BASE_URL` | `https://` plus the domain, used for links in alerts |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional, for alerts |
| `DASHBOARD_USERS_B64` | the dashboard logins, see below |

**Dashboard logins.** Caddy refuses to start without at least one login (it fails closed). Make a
bcrypt hash per user with Caddy itself, on the VM:

```bash
sudo docker run --rm caddy:2 caddy hash-password
```

Then base64-encode lines of the form `username bcrypt-hash` (one per user, hashes contain `$`, which
is why the value is base64) and put the result in `DASHBOARD_USERS_B64`:

```bash
printf 'alice $2a$14$...\nbob $2a$14$...\n' | base64 -w0
```

Everything except `/healthz` needs a login. `/healthz` shows only `ok` or a short reason.

## 5. Let the VM pull the image

The repository is private, so its container image is private too. Create a GitHub personal access
token (classic) with only the `read:packages` scope, and log in once as the deploy user:

```bash
sudo -u deploy docker login ghcr.io -u <your-github-username>
```

Paste the token when asked (it is stored in `~deploy/.docker/config.json`). If you would rather
not keep a token on the VM, build on the VM instead (section 6).

## 6. First bring-up from source (before anything is merged)

Useful to prove the stack on the real VM before the pull requests are merged. From the repository
root, copy the files the image needs and build on the VM:

```bash
gcloud compute scp --recurse app config requirements.lock Dockerfile docker-compose.yml lp-radar:/tmp/lp-radar-src --zone=$ZONE --project=$PROJECT
```

```bash
gcloud compute ssh lp-radar --zone=$ZONE --project=$PROJECT --command "sudo -u deploy bash -c 'cp -r /tmp/lp-radar-src/. /opt/lp-radar/ && cd /opt/lp-radar && docker compose up -d --build'"
```

(`deploy/Caddyfile` and `deploy/caddy-entrypoint.sh` must be in `/opt/lp-radar/deploy/` too: copy
them the same way.)

## 7. Continuous deploy

`.github/workflows/deploy.yml` runs after CI succeeds on `main`: it builds the image, pushes it to
GitHub Container Registry (tags: the commit SHA and `latest`), copies `docker-compose.yml` and the
Caddy files to the VM, runs `docker compose pull && docker compose up -d` and then polls
`/healthz` for up to five minutes. It fails the job if the site does not become healthy.

Until the secrets below exist the job prints a notice and does nothing, so `main` stays green.
In the repository, Settings, Secrets and variables, Actions:

| Kind | Name | Value |
|---|---|---|
| Secret | `VM_HOST` | the static IP |
| Secret | `VM_USER` | `deploy` |
| Secret | `VM_SSH_KEY` | the contents of `~/.ssh/lp-radar-deploy` (the private key) |
| Secret | `VM_KNOWN_HOSTS` | output of `ssh-keyscan -t ed25519 <IP>` (pins the VM's host key) |
| Variable | `DEPLOY_URL` | `https://<your-domain>` |

The merge order does not matter for safety: the workflow only exists once the Phase 9 pull request
is merged.

## 8. Backups

The worker keeps everything in one SQLite file. `deploy/backup.sh` makes a consistent copy with
SQLite's online backup, compresses it and uploads it to a bucket every day at 03:15 UTC.

```bash
gcloud storage buckets create gs://$PROJECT-lp-radar-backups --project=$PROJECT --location=$REGION --uniform-bucket-level-access
```

Keep 30 days:

```bash
printf '{"rule":[{"action":{"type":"Delete"},"condition":{"age":30}}]}' > lifecycle.json
```

```bash
gcloud storage buckets update gs://$PROJECT-lp-radar-backups --lifecycle-file=lifecycle.json --project=$PROJECT
```

The VM's service account needs to write to the bucket. Find it and grant it object creation on
this bucket only:

```bash
gcloud compute instances describe lp-radar --zone=$ZONE --project=$PROJECT --format="value(serviceAccounts[0].email)"
```

```bash
gcloud storage buckets add-iam-policy-binding gs://$PROJECT-lp-radar-backups --member="serviceAccount:<that email>" --role=roles/storage.objectCreator --project=$PROJECT
```

Then schedule it by re-running the setup with the bucket name (it is safe to repeat):

```bash
gcloud compute ssh lp-radar --zone=$ZONE --project=$PROJECT --command "sudo BACKUP_BUCKET=$PROJECT-lp-radar-backups bash setup-vm.sh"
```

Test once by hand: `sudo -u deploy BACKUP_BUCKET=... /opt/lp-radar/backup.sh`, then check the bucket.

**Restore:** download a `.db.gz`, `gunzip` it, stop the stack (`docker compose stop web worker`), copy
the file to `/data/lp-radar.db` in the `data` volume (`docker compose cp file web:/data/lp-radar.db`
after starting `web` only), then `docker compose up -d`.

## 9. Checks after a deploy

```bash
curl -fsS https://<your-domain>/healthz
```

Should print `{"status":"ok"}` (503 with a reason during the first minute, before the worker's
first heartbeat). Then open the dashboard, log in, and look at `/status`: mode `live`, the credits
used today, and the worker heartbeat. On the VM:

```bash
cd /opt/lp-radar && docker compose ps
```

All three services should be `running`. A crashed worker (an exception, an out-of-memory kill) is
restarted automatically (`restart: unless-stopped`) and `/healthz` recovers; CI proves this by
killing the worker's process. To see it yourself:

```bash
sudo kill -9 $(docker inspect -f '{{.State.Pid}}' lp-radar-worker-1)
```

`docker kill` and `docker stop` are different: Docker treats them as a deliberate stop and does not
restart the container until the next `docker compose up -d`. A worker that hangs without exiting is
not restarted either; `/healthz` goes to 503 (stale heartbeat), so that is what to alert on.

## 10. Rollback

Every deploy is tagged with its commit SHA. To go back:

```bash
cd /opt/lp-radar && IMAGE_TAG=<previous-sha> docker compose up -d
```

`cat /opt/lp-radar/.deployed-tag` shows what is running. The database is not changed by a rollback.

## 11. Operating notes

- **Credits:** `DAILY_CREDIT_BUDGET` is a hard daily stop. Watch `/status`. Raising it is an edit of
  `.env` and `docker compose up -d`.
- **Robinhood Chain:** the live endpoints work for it, but Nansen's historical endpoints do not
  cover it, so it cannot be backtested. Its explorer links go to robinhoodchain.blockscout.com.
- **Nansen's data terms:** the dashboard is behind a login, which counts as internal use. It shows
  a "Powered by Nansen API" attribution. Do not make it public without Nansen's written
  confirmation; the terms treat smart-money data and address labels as internal-only.
- **Updating logins:** change `DASHBOARD_USERS_B64` in `.env`, then `docker compose up -d caddy`.
- **Logs:** `docker compose logs -f worker`. The Nansen API key is never logged.

## 12. Stopping the costs

Stop the VM (keeps disk and IP, small charge): `gcloud compute instances stop lp-radar --zone=$ZONE
--project=$PROJECT`. To remove everything, delete the instance, the address `lp-radar-ip`, the
firewall rule `lp-radar-web` and the backup bucket, each with `--project=$PROJECT`. An unattached
static IP is billed, so delete the address too.
