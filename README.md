# Ticklight

A small self-hosted task board for an always-on desk monitor. Add tasks from
the browser or Telegram in plain English, with flexible recurring tasks,
streaks, a morning summary, and due-time reminders.

- **Dashboard**: big clock, *Overdue / Today*, *Coming up* (next 7 days, by day), *Anytime*, *Done today*.
  Updates within ~3 s when you add or complete something from Telegram.
- **Quick add**: type `call dentist tomorrow 3pm !!` and a live preview shows what will be created.
- **Recurring tasks**: daily / every N days, weekly on chosen days, monthly by date or "2nd Tuesday",
  yearly, and *N days after I complete it*. Complete, skip, or undo any occurrence.
- **Bulk add**: paste or type a whole list, one task per line, with a live preview. Indented or `+`
  lines become subtasks.
- **Subtasks**: add a checklist to any task. Tick items right on the dashboard (Today/Overdue
  show the full list; other columns show `☑ 2/5`). On recurring tasks the checklist resets
  for each new occurrence.
- **Streaks** for recurring tasks (🔥), with history in the edit dialog.
- **Light and dark mode**: follows your system until you pick one with ☀/☾ (or `t`).
- **Telegram bot**: add tasks by messaging it, tap buttons to complete, get a morning summary and
  reminders with Done / Skip / Snooze buttons.
- Python (FastAPI) + a single SQLite file. No build step, about 60 MB RAM.

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

Press `b` or the ☰ button. Pasting several lines into the quick-add box opens the same dialog.
Each line is a task, written like quick add. Indent a line (Tab) or start it with `+` to make it
a subtask of the task above. List markers (`-`, `*`, `1.`, `- [ ]`) are stripped, so lists copied from
notes apps and markdown checklists work as they are.

```
Plan party saturday !!
  + send invites
  + order cake
call mum tomorrow 6pm
- water plants every 3 days
- [ ] book flights
```

**Add to every task** (e.g. `tomorrow !!`) applies a date, time or priority to every line that
doesn't set its own. The preview shows exactly what will be created, and Undo removes the whole batch.
In Telegram, send a multi-line message to do the same (start subtask lines with `+`).

**Keyboard** (dashboard): `/` quick add · `b` add many · `n` new task with full options · `d` toggle display mode ·
`t` light/dark · `Esc` clear.
Click a task's title to edit it, including its subtasks. Hover a task to add subtasks (☑+), skip, or delete it.
In the subtask box, paste several lines to add several items at once.

**Display mode** (⛶ button or `?display` in the URL) hides the controls and uses larger text.
For a kiosk-style tab: `chromium --app=http://ticklight:8080/?display&theme=dark` (or press F11).
`?theme=light` / `?theme=dark` sets the theme and remembers it.

## Telegram setup

1. Message [@BotFather](https://t.me/BotFather), send `/newbot`, and copy the token.
2. Put it in `TELEGRAM_BOT_TOKEN`, start the app, then send `/id` to your bot.
3. Put that number in `TELEGRAM_ALLOWED_USERS` and restart. The bot ignores everyone else.

Commands: `/today`, `/list`, `/done 12`, `/skip 12`, `/del 12`, `/undo 12`, `/sub 12 …`, `/help`.
Any plain message becomes a task. Tap `/12` in a list to see that task's checklist and buttons.

**Subtasks from Telegram:** *reply* to any bot message about a single task (the "➕ added" confirmation,
a task detail, a reminder) and each line of your reply becomes a subtask. Or use
`/sub 12 buy paint; masking tape`. Tap ☐ buttons in the task detail to tick items.

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

It asks for CT ID, storage, network, timezone and (optionally) Telegram details, then:
creates an unprivileged Debian LXC (512 MB RAM, 2 GB disk, start on boot), installs the app into
`/opt/ticklight` as a systemd service, and prints the URL.

- Config: `/etc/ticklight/ticklight.env` (then `systemctl restart ticklight`)
- Data: `/var/lib/ticklight/tasks.db`
- Logs: `journalctl -u ticklight -f`
- Update: `bash proxmox/ticklight-lxc.sh --update <ctid>` (keeps config and data)

All prompts can be preset with env vars for unattended runs, e.g.
`CTID=150 IP=192.168.1.50/24 GW=192.168.1.1 TELEGRAM_BOT_TOKEN=... bash proxmox/ticklight-lxc.sh`.

**One-liner install** (no checkout needed). Run on the Proxmox host:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/PresWiFi/ticklight/main/proxmox/ticklight-lxc.sh)"
```

`proxmox/install.sh` also works by itself on any Debian/Ubuntu VM.

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

The quick endpoint works with an iOS Shortcut or Home Assistant `rest_command`, e.g.
`curl -X POST http://ticklight:8080/api/quick -H 'Content-Type: application/json' -d '{"text":"buy milk"}'`.

Recurrence rules (for `POST`/`PATCH`):
`{"type":"daily","interval":2}`, `{"type":"weekly","days":[0,3]}` (Mon=0),
`{"type":"monthly","day":15}` (`-1` = last day), `{"type":"monthly","nth":2,"weekday":1}`,
`{"type":"yearly","month":3,"day":1}`, `{"type":"after","days":30}`.

## How recurring tasks behave

- A recurring task is a single task whose due date moves forward each time you complete or skip it.
- Completing it late moves it to the next occurrence after today, so missed occurrences don't pile up.
  Completing it early moves it to the occurrence after the current one.
- The streak goes up if you complete it before the next occurrence arrives. It breaks if an occurrence
  passes without a completion, or if you skip.
- Undo restores the previous due date, streak, and checklist.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
DATA_DIR=./data .venv/bin/python -m app.main
```
