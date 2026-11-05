# Ticklight

Self-hosted task board for an always-on monitor. Add tasks in plain English from the browser or Telegram.

- **Dashboard:** clock, Overdue/Today, Coming up (7 days), Anytime, Done today. Refreshes within ~3 s.
- **Quick add** with live preview: `call dentist tomorrow 3pm !!`
- **Recurring:** every N days, weekdays, monthly by date or "2nd Tuesday", yearly, or N days after completion. Complete, skip or undo any occurrence.
- **Bulk add:** one task per line; indented or `+` lines become subtasks.
- **Subtasks:** checklists, tickable on the dashboard. Reset each occurrence on recurring tasks.
- **Streaks** for recurring tasks.
- **Light/dark** (follows system; toggle with ☀/☾ or `t`).
- **Telegram bot:** add, complete, morning summary, reminders with Done/Skip/Snooze.
- FastAPI + SQLite. No build step, ~60 MB RAM.

## Quick-add syntax

| You type | You get |
|---|---|
| `call dentist tomorrow at 3pm !!` | Tue · 15:00 · high priority |
| `water plants every 3 days` | every 3 days, starting today |
| `change water filter 30 days after done` | next due 30 days after each completion |
| `standup every weekday at 9:30` | Mon–Fri 09:30 |
| `bins every tue and fri at 7pm` | weekly on Tue, Fri |
| `gym every mon, wed & fri 6am` | weekly on Mon, Wed, Fri |
| `recycling every 2 weeks on thursday` | fortnightly Thursdays |
| `pay rent monthly on the 1st` | 1st of each month |
| `invoice every month on the last day` | last day of each month |
| `review budget every last sunday` | last Sunday of each month |
| `pay rates every 3 months on the 20th` | quarterly |
| `mum's birthday every year on march 3` | yearly |
| `email accountant thursday` / `next friday` / `in 2 weeks` / `dec 5` / `2027-03-01` | one-off date |
| `meet at 5` | today 17:00 (bare 1–6 means pm) |

Priority: `!!` or `!high` = high, `!low` = low.

## Adding many tasks at once

Press `b`, click ☰, or paste multiple lines into quick add. One task per line; indent or prefix `+` for a subtask. List markers (`-`, `*`, `1.`, `- [ ]`) are stripped.

```
Plan party saturday !!
  + send invites
  + order cake
call mum tomorrow 6pm
- water plants every 3 days
- [ ] book flights
```

**Add to every task** (e.g. `tomorrow !!`) applies to lines that don't set their own. Undo removes the whole batch.
In Telegram, send a multi-line message (`+` for subtasks).

**Keyboard** (dashboard): `/` quick add · `b` add many · `n` new task with full options · `d` toggle display mode ·
`t` light/dark · `Esc` clear.
Click a title to edit. Hover for add subtask (☑+), skip, delete. Paste multiple lines into the subtask box to add several.

**Display mode** (⛶ or `?display`): no controls, larger text. Kiosk: `chromium --app=http://ticklight:8080/?display&theme=dark`. `?theme=light|dark` sets and remembers the theme.

## Telegram setup

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Put it in `TELEGRAM_BOT_TOKEN`, start the app, then send `/id` to your bot.
3. Put that number in `TELEGRAM_ALLOWED_USERS` and restart. The bot ignores everyone else.

Commands: `/today`, `/list`, `/done 12`, `/skip 12`, `/del 12`, `/undo 12`, `/sub 12 …`, `/help`.
Plain messages become tasks. Tap `/12` for a task's checklist and buttons.

**Subtasks:** reply to any bot message about a task; each line becomes a subtask. Or `/sub 12 buy paint; masking tape`.

## Deploy with Docker

```bash
cp .env.example .env      # set TZ and (optionally) Telegram settings
docker compose up -d --build
# open http://<host>:8080
```

Data is stored in the `ticklight-data` volume (`/data/tasks.db`).

## Deploy on Proxmox (LXC)

On the Proxmox host, as root, from a checkout of this repo:

```bash
bash proxmox/ticklight-lxc.sh
```

Prompts for CT ID, storage, network, timezone and Telegram, then creates an unprivileged Debian LXC (512 MB, 2 GB, start on boot) running `/opt/ticklight` as a systemd service.

- Config: `/etc/ticklight/ticklight.env` (then `systemctl restart ticklight`)
- Data: `/var/lib/ticklight/tasks.db`
- Logs: `journalctl -u ticklight -f`
- Update: `bash proxmox/ticklight-lxc.sh --update <ctid>` (keeps config and data)

Unattended:
`CTID=150 IP=192.168.1.50/24 GW=192.168.1.1 TELEGRAM_BOT_TOKEN=... bash proxmox/ticklight-lxc.sh`.

Without a checkout:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/PresWiFi/ticklight/main/proxmox/ticklight-lxc.sh)"
```

`proxmox/install.sh` works on any Debian/Ubuntu VM.

## Configuration

| Variable | Default | |
|---|---|---|
| `TZ` | `UTC` | Timezone for due dates, reminders, summary |
| `TELEGRAM_BOT_TOKEN` | – | Enables the bot |
| `TELEGRAM_ALLOWED_USERS` | – | Comma-separated Telegram user ids |
| `SUMMARY_TIME` | `08:00` | Morning summary (empty = off). Sent once per day, up to 3 h late if the service was down |
| `REMINDER_LEAD_MINUTES` | `0` | Remind this many minutes before the due time |
| `UPCOMING_DAYS` | `7` | Range of the "Coming up" column |
| `API_TOKEN` | – | If set, the UI/API require it (the browser asks once) |
| `PORT` | `8080` | |
| `DATA_DIR` | `./data` | Where `tasks.db` lives |

## API

All JSON. Send `Authorization: Bearer <API_TOKEN>` if you set a token.

```
POST   /api/quick                 {"text": "water plants every 3 days"}
POST   /api/bulk                  {"text": "a\n+ sub\nb", "append": "tomorrow", "dry_run": false}
POST   /api/parse                 {"text": "..."}  preview only, nothing saved
GET    /api/board                 dashboard sections
GET    /api/tasks                 open tasks
POST   /api/tasks                 {"title", "notes", "priority", "due_date", "due_time", "recurrence"}
GET    /api/tasks/{id}            includes completion history
PATCH  /api/tasks/{id}
DELETE /api/tasks/{id}
POST   /api/tasks/{id}/complete | skip | undo | restore
POST   /api/tasks/{id}/subtasks   {"title": "..."} or {"titles": ["...", "..."]}
PATCH  /api/subtasks/{id}         {"done": true} and/or {"title": "..."}
DELETE /api/subtasks/{id}
```

Works with iOS Shortcuts or Home Assistant `rest_command`:
`curl -X POST http://ticklight:8080/api/quick -H 'Content-Type: application/json' -d '{"text":"buy milk"}'`.

Recurrence rules (for `POST`/`PATCH`):
`{"type":"daily","interval":2}`, `{"type":"weekly","days":[0,3]}` (Mon=0),
`{"type":"monthly","day":15}` (`-1` = last day), `{"type":"monthly","nth":2,"weekday":1}`,
`{"type":"yearly","month":3,"day":1}`, `{"type":"after","days":30}`.

## How recurring tasks behave

- One task; the due date advances on complete or skip.
- Completing late jumps to the next occurrence after today (no pile-up). Completing early jumps past the current one.
- Streak grows if completed before the next occurrence; breaks on a missed occurrence or skip.
- Undo restores due date, streak and checklist.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
DATA_DIR=./data .venv/bin/python -m app.main
```
