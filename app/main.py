from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import parser
from .config import settings
from .db import DB
from .tasks import NotFound, Tasks

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("ticklight")

STATIC = os.path.join(os.path.dirname(__file__), "static")

db = DB(settings.db_path)
tasks = Tasks(db, settings.tz)
bot = None


async def start_bot_with_retry():
    """Start the Telegram bot, retrying if the network isn't up yet."""
    global bot
    from telegram.error import InvalidToken

    from .bot import Bot
    delay = 5
    while True:
        candidate = Bot(settings, tasks)
        try:
            await candidate.start()
            bot = candidate
            return
        except InvalidToken:
            log.error("Telegram rejected TELEGRAM_BOT_TOKEN; bot disabled (web UI still running)")
            return
        except Exception as e:
            log.warning("Telegram bot failed to start (%s); retrying in %ss", e, delay)
            try:
                await candidate.stop()
            except Exception:
                pass
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


@asynccontextmanager
async def lifespan(app: FastAPI):
    starter = None
    if settings.telegram_token:
        starter = asyncio.create_task(start_bot_with_retry())
        if not settings.telegram_users:
            log.warning("TELEGRAM_ALLOWED_USERS is empty: the bot will reply with your "
                        "user id but ignore commands until you set it.")
    else:
        log.info("TELEGRAM_BOT_TOKEN not set; Telegram bot disabled")
    yield
    if starter and not starter.done():
        starter.cancel()
    if bot:
        await bot.stop()


app = FastAPI(title="Ticklight", lifespan=lifespan)


def auth(request: Request) -> None:
    if not settings.api_token:
        return
    header = request.headers.get("authorization", "")
    token = header.removeprefix("Bearer ").strip() or request.query_params.get("token", "")
    if token != settings.api_token:
        raise HTTPException(401, "invalid or missing API token")


@app.exception_handler(NotFound)
async def not_found(_: Request, exc: NotFound):
    return JSONResponse({"detail": f"task {exc} not found"}, status_code=404)


@app.exception_handler(ValueError)
async def bad_value(_: Request, exc: ValueError):
    return JSONResponse({"detail": str(exc)}, status_code=400)


class TaskIn(BaseModel):
    title: str | None = None
    notes: str | None = None
    priority: int | None = None
    due_date: date | None = None
    due_time: str | None = None
    recurrence: dict | None = None


class QuickIn(BaseModel):
    text: str


@app.get("/api/health")
def health():
    return {"ok": True, "telegram": bool(bot), "version": tasks.version}


@app.get("/api/config")
def config():
    return {"auth_required": bool(settings.api_token), "timezone": settings.tz_name,
            "upcoming_days": settings.upcoming_days}


api = [Depends(auth)]


@app.get("/api/version", dependencies=api)
def version():
    return {"version": tasks.version, "today": tasks.today().isoformat()}


@app.get("/api/board", dependencies=api)
def board():
    return tasks.board(settings.upcoming_days)


@app.get("/api/tasks", dependencies=api)
def list_tasks():
    return tasks.open_tasks()


@app.post("/api/tasks", dependencies=api, status_code=201)
def create_task(body: TaskIn):
    if not body.title:
        raise ValueError("title is required")
    return tasks.create(body.title, body.notes or "", 1 if body.priority is None else body.priority,
                        body.due_date, body.due_time, body.recurrence)


@app.post("/api/quick", dependencies=api, status_code=201)
def quick_add(body: QuickIn):
    return tasks.quick_add(body.text)


@app.post("/api/parse", dependencies=api)
def parse_preview(body: QuickIn):
    p = parser.parse(body.text, tasks.today())
    return {"title": p.title, "priority": p.priority,
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "due_time": p.due_time, "recurrence": p.recurrence, "summary": p.summary()}


@app.get("/api/tasks/{task_id}", dependencies=api)
def get_task(task_id: int):
    t = tasks.get(task_id)
    t["history"] = tasks.history(task_id)
    return t


@app.patch("/api/tasks/{task_id}", dependencies=api)
def update_task(task_id: int, body: TaskIn):
    changes = body.model_dump(exclude_unset=True)
    if "due_date" in changes and changes["due_date"]:
        changes["due_date"] = changes["due_date"].isoformat()
    return tasks.update(task_id, changes)


@app.delete("/api/tasks/{task_id}", dependencies=api)
def delete_task(task_id: int):
    return tasks.delete(task_id)


@app.post("/api/tasks/{task_id}/{action}", dependencies=api)
def task_action(task_id: int, action: str):
    fn = {"complete": tasks.complete, "skip": tasks.skip,
          "undo": tasks.undo_complete, "restore": tasks.restore}.get(action)
    if not fn:
        raise HTTPException(404, "unknown action")
    return fn(task_id)


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"),
                        headers={"Cache-Control": "no-cache"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def run():
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                log_level=os.getenv("LOG_LEVEL", "info").lower())


if __name__ == "__main__":
    run()
