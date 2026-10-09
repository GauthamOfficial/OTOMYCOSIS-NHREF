# Setup Guide

Installing the Ear Disease Detection System on a Windows computer.
Nawaloka Hospital PLC, Research and Education Foundation.

Allow about 30 minutes, most of it downloads.

## 1. What you need

| | Minimum | Recommended |
| --- | --- | --- |
| Operating system | Windows 10, 64 bit | Windows 11 |
| Memory | 8 GB | 16 GB if retraining will be run on this computer |
| Disk | 5 GB free | More for stored images: about 0.5 MB per examination |
| Python | 3.11 (3.9 and 3.10 also work) | 3.11 |
| PostgreSQL | 14 or later | 16 |

Python 3.12 and later will **not** work: TensorFlow 2.15, which the model
needs, does not support them.

## 2. Install PostgreSQL

1. Download the Windows installer from
   <https://www.postgresql.org/download/windows/> (the EDB installer).
2. Run it and accept the defaults, with these choices:
   - **Components:** PostgreSQL Server and Command Line Tools are required.
     pgAdmin is optional. Stack Builder is not needed.
   - **Password:** choose a strong password for the `postgres` superuser and
     store it somewhere safe. The setup wizard asks for it once.
   - **Port:** keep `5432`.
3. When it finishes, PostgreSQL runs as a Windows service that starts with the
   computer. You can confirm it in *Services* as `postgresql-x64-16`.

## 3. Install Python 3.11

Download Python 3.11 from
<https://www.python.org/downloads/release/python-3119/> and, on the first
installer screen, tick **Add python.exe to PATH**.

## 4. Run the setup

Copy the project folder to the computer, then double click **`setup.bat`**.

It installs the dependencies (TensorFlow is about 300 MB) and then starts the
setup wizard, which asks:

| Question | Answer |
| --- | --- |
| Host | `localhost` (press Enter) |
| Port | `5432` (press Enter) |
| Superuser | `postgres` (press Enter) |
| Superuser password | the password from step 2 |
| New database name | `ear_screening` (press Enter) |
| New database user | `ear_app` (press Enter) |
| Administrator username, name, password | the first administrator's account |

The wizard creates a dedicated database and a database user that can access
only that database, with a random password. The service always runs as that
limited user; the `postgres` superuser password is not stored anywhere. It
also generates the secret that signs login tokens, and writes all of this to a
`.env` file in the project folder.

> **Keep `.env` private.** It contains the database password and the token
> signing key. It is excluded from git and must not be copied elsewhere.

## 5. Start and add users

Double click **`start.bat`**. The model loads in a few seconds and the
interface opens at <http://localhost:5000>. Keep the black window open; closing
it stops the service.

Sign in as the administrator, open **Admin**, and add an account for each
surgeon. Each new user receives a temporary password, which they must replace
with their own at first sign in. Give temporary passwords in person.

## 6. Back up

Two things together make up the record, and both must be backed up:

1. **The database**: patients, examinations, results and reviews.
2. **The image store**: the `storage` folder in the project, or wherever
   `STORAGE_DIR` points.

A database backup, run from a Command Prompt:

```
"C:\Program Files\PostgreSQL\16\bin\pg_dump.exe" -U postgres -F c -f ear_screening.dump ear_screening
```

Restore with `pg_restore`. Schedule both backups with Windows Task Scheduler,
and keep copies off this computer. A backup that has never been restored is
not known to work, so test a restore.

## 7. Using it from other computers

By default the service only accepts connections from the computer it runs on,
which is the safest arrangement. To serve a clinic network:

1. Start it with `.venv\Scripts\python.exe app.py --host 0.0.0.0`.
2. Put it behind a reverse proxy that provides HTTPS (for example IIS or nginx)
   and allow only the proxy through the Windows firewall.
3. Set `COOKIE_SECURE=true` in `.env` so login cookies are only sent over HTTPS.
4. Have the proxy set the `X-Real-IP` header and set `TRUST_PROXY=true` in
   `.env`, so the audit log records each user's real address.

Do not expose it to the internet.

## 8. Maintenance commands

Run these from a Command Prompt in the project folder:

| Task | Command |
| --- | --- |
| Check the database connection | `.venv\Scripts\python.exe scripts\manage.py check` |
| Add a user | `.venv\Scripts\python.exe scripts\manage.py create-user` |
| Reset a password | `.venv\Scripts\python.exe scripts\manage.py reset-password USERNAME` |
| Unlock an account after failed sign ins | `.venv\Scripts\python.exe scripts\manage.py unlock USERNAME` |
| Run a backtest now | `.venv\Scripts\python.exe scripts\backtest.py` |
| Run the test suite | `.venv\Scripts\python.exe -m pytest` |

If every administrator is locked out, `reset-password` works without signing
in.

## 9. Cameras and endoscopes

Images can be captured live from any device that Windows sees as a camera.
Most USB video otoscopes and endoscopes do; for a video tower, use an HDMI to
USB capture adapter. To check a device, open the Windows **Camera** app: if
the picture appears there, it will work in the system.

Browsers only allow camera access over a secure connection. Using the system
on the computer it runs on (`http://localhost:5000`) counts as secure. From
other computers on the network it must be served over HTTPS (section 7), or
the camera option will explain that it cannot start.

## 10. Daily backtest

While the service is running it re-runs the model in service on every
surgeon reviewed case once a day and shows the results on the Dashboard. It
runs as a separate background process, needs no extra setup, and takes about
a second per reviewed case. If the service was not running at the scheduled
time, the backtest runs a few minutes after it next starts.

The defaults suit most clinics. To change them, add any of these lines to
`.env` and restart the service:

| Setting | Default | Meaning |
| --- | --- | --- |
| `BACKTEST_ENABLED` | `true` | Set `false` to stop the daily run. Administrators can still run it from the Dashboard. |
| `BACKTEST_HOUR` | `2` | Hour of the day (0 to 23, this computer's clock) from which the daily run starts. |
| `BACKTEST_WINDOW_DAYS` | `30` | The recent period reported next to the all time figures. |
| `BACKTEST_MIN_SENSITIVITY` | `0.90` | Alert when referral sensitivity falls below this. |
| `BACKTEST_MIN_CASES` | `30` | Abnormal reviewed cases needed in the period before alerts are raised. |
| `BACKTEST_MAX_AGREEMENT_DROP` | `0.10` | Alert when recent agreement is this far below earlier agreement. |

Progress and errors are written to `storage\jobs\backtest_N.log`.

## 11. Retraining requirements

Retraining is started from **Admin, Model and Retraining**. It needs the
original `Dataset` folder on the same computer, because reviewed clinic cases
are added to the original training data rather than replacing it. A run takes
about 40 minutes on a four core processor and is easiest outside clinic hours,
since it competes with the service for memory.

## Troubleshooting

| Message | Cause and fix |
| --- | --- |
| `Python 3.9, 3.10 or 3.11 was not found` | Install Python 3.11 as in step 3, ticking *Add to PATH*. |
| `password authentication failed for user "postgres"` | The superuser password is wrong. Run `setup.bat` again. |
| `connection refused` | PostgreSQL is not running. Start the `postgresql-x64-16` service. |
| `No settings found. Run setup.bat` | The `.env` file is missing. Run `setup.bat`. |
| Account locked | Wait 15 minutes, or run the `unlock` command above. |
