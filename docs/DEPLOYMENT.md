# Deployment

How NHREF OtoScan is installed on the Linux server, and how the interface is
published. Nawaloka Hospital PLC, Research and Education Foundation.

For a single Windows computer in a clinic room, read `SETUP_GUIDE.md` instead.
This document describes the shared installation that surgeons reach over the
internet.

## What runs where

| | Where | What it does |
| --- | --- | --- |
| Interface | Vercel, `nhref-otoscan.vercel.app` | Serves the HTML, CSS and JavaScript, and forwards every `/api` request to the server |
| Service | Oracle Cloud, `132.145.62.47` | The API, the model, the database and the image store |
| Certificate | Caddy on the server | Terminates HTTPS as `132-145-62-47.sslip.io` |

The browser only ever talks to one origin, the Vercel address. Vercel forwards
the API calls on. This is not decoration: the refresh token that keeps a
surgeon signed in is a `SameSite=Strict` cookie, which a browser will not send
to a different site, so an interface that called the server directly would
sign everyone out every fifteen minutes. Nothing in the page code knows the
server's address.

`132-145-62-47.sslip.io` is not a registered domain. sslip.io answers any
name of that shape with the address written into it, which is enough for
Let's Encrypt to issue a certificate. To move to a hospital domain, point an
A record at the server, change the one name at the top of
`/etc/caddy/Caddyfile`, change the `destination` in `frontend/vercel.json`,
and reload both. Nothing else refers to the name.

## The server

Ubuntu 26.04 LTS on ARM (Ampere), four cores, 46 GB of memory.

| | Path | Owner |
| --- | --- | --- |
| Code | `/opt/otoscan` | `ubuntu`, group `otoscan` |
| Settings | `/opt/otoscan/.env` | `ubuntu:otoscan`, mode 640 |
| Virtual environment | `/opt/otoscan/.venv` | Python 3.11.17 |
| Python 3.11 | `/opt/python` | installed with `uv`, shared |
| Patient images | `/var/lib/otoscan/storage` | `otoscan`, mode 750 |
| Database | PostgreSQL 18, database `ear_screening`, role `ear_app` | |

Ubuntu 26.04 ships Python 3.14, which TensorFlow 2.15 does not support, so
Python 3.11 is installed separately with `uv` into `/opt/python` where the
service account can read it.

The image store sits outside the code tree on purpose. `git pull` can then
never touch patient data, and the two things that make up the record are
backed up separately.

### The service

`otoscan.service` runs `app.py`, which serves with waitress on
`127.0.0.1:5000`. One process, deliberately: the model is loaded once, about
280 MB, and the daily backtest has exactly one scheduler. Nothing outside the
machine can reach that port.

```
sudo systemctl status otoscan
sudo systemctl restart otoscan
sudo journalctl -u otoscan -f
```

The unit runs as the unprivileged `otoscan` account with the filesystem
read only apart from the image store and the two directories retraining
writes to.

### Caddy

Caddy holds the certificate, renews it by itself, redirects HTTP to HTTPS, and
forwards everything to the service. It also sends the headers a page needs
when the service is what serves it, matching what Vercel sends.

The certificate is deliberately RSA. Let's Encrypt cross signs that chain up
to ISRG Root X1, which every trust store carries; the ECDSA chain it issues by
default goes up to ISRG Root X2, which some older Windows installations do
not have.

`X-Real-IP` is what the audit log records. A request that arrives through
Vercel carries the surgeon's own address in `X-Forwarded-For`, and one that
arrives directly carries none, so Caddy sets `X-Real-IP` from whichever
applies. Someone who went to the server address directly could put any
address in that header and have it logged against their own actions; they
cannot affect anybody else's.

```
sudo systemctl reload caddy
sudo journalctl -u caddy -f
```

### Firewall

Ports 22, 80 and 443 only, in `iptables`, saved with `netfilter-persistent`.
PostgreSQL listens on the loopback interface and is not reachable from
outside. The Oracle Cloud security list for the subnet must also allow 80 and
443.

## The interface on Vercel

`frontend/vercel.json` holds the whole configuration. In the Vercel dashboard,
import `GauthamOfficial/OTOMYCOSIS-NHREF` and set:

| Setting | Value |
| --- | --- |
| Framework preset | Other |
| Root directory | `frontend` |
| Build command | leave empty |
| Output directory | leave empty |
| Install command | leave empty |

There is no build step. The files are served as they are.

Once it is deployed, add the Vercel address to the Google Cloud Console, under
Google Auth Platform, Clients, the OAuth client ending
`...v7m8.apps.googleusercontent.com`, as an **Authorised JavaScript origin**.
Sign in with Google fails on an origin Google has not been told about, and the
error the browser shows does not say so clearly. Add the server address
`https://132-145-62-47.sslip.io` as well, so the fallback below works.

The service still serves the same interface itself, at
`https://132-145-62-47.sslip.io`. That is the way in if Vercel is ever
unavailable.

## Keeping it up to date

```
cd /opt/otoscan
git pull
.venv/bin/python -m pytest -q          # optional, about 40 seconds
sudo systemctl restart otoscan
```

Restarting takes a few seconds, most of it loading the model. Anyone mid
examination sees one failed request.

If `requirements.txt` changed:

```
PATH="$HOME/.local/bin:$PATH" uv pip install --python .venv/bin/python -r requirements.txt
```

The interface updates itself: Vercel deploys on every push to `main`.

## Back up

Both halves of the record, as in `SETUP_GUIDE.md` section 6, but here:

```
sudo -u postgres pg_dump -F c -f /var/backups/ear_screening.dump ear_screening
sudo tar czf /var/backups/otoscan-images.tar.gz -C /var/lib/otoscan storage
```

Keep copies off the machine, and restore one somewhere before trusting it.
`/opt/otoscan/.env` holds the database password and the token signing key; it
is not in git and a lost copy cannot be recovered, so keep it with the
backups.

## What is not set up

**Retraining is waiting only on reviewed cases.** `Dataset/` is not in git,
because the images belong to the public datasets they came from, so it was
copied to `/opt/otoscan/Dataset` by hand: 4,763 files, 353 MB, every row of
`artifacts/manifest.csv` accounted for. Retraining then needs 50 surgeon
reviewed cases, which is the clinical gate, not a technical one.

To replace it from a Windows machine, send it in pieces rather than in one
stream, because a single long transfer tends to be cut:

```
find Dataset -type f | split -l 150 -d -a 3 - chunk_
for c in chunk_*; do tar cf - -T $c | ssh -i KEY ubuntu@SERVER 'tar xf - -C /opt/otoscan'; done
ssh -i KEY ubuntu@SERVER 'sudo chgrp -R otoscan /opt/otoscan/Dataset && sudo chmod -R g+rX /opt/otoscan/Dataset'
```

The service account must be able to read it, which is what that last line is
for. The first retraining run rebuilds the normalised copy of every image
under `artifacts/normalized/`, which takes some minutes and about a gigabyte;
later runs reuse it.

**Uploads are capped at 15 MB by the service and 16 MB by Caddy.** Vercel's
documented 4.5 MB request body limit applies to its functions, not to a
rewrite like this one: a 12 MB photograph was accepted through
`nhref-otoscan.vercel.app` and analysed normally. Anything over 16 MB is
refused by Caddy with a 413 before the service reads it.

**There is no off machine log retention.** The service and Caddy both log to
the journal, which rotates. The audit log of who did what is in the database
and is kept.
