import os
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo


def _ids(raw: str) -> set[int]:
    return {int(x) for x in raw.replace(" ", "").split(",") if x.strip().lstrip("-").isdigit()}


@dataclass
class Settings:
    data_dir: str = field(default_factory=lambda: os.getenv("DATA_DIR", "./data"))
    tz_name: str = field(default_factory=lambda: os.getenv("TZ", "UTC"))
    host: str = field(default_factory=lambda: os.getenv("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8080")))
    api_token: str = field(default_factory=lambda: os.getenv("API_TOKEN", ""))
    telegram_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_users: set[int] = field(
        default_factory=lambda: _ids(os.getenv("TELEGRAM_ALLOWED_USERS", "")))
    summary_time: str = field(default_factory=lambda: os.getenv("SUMMARY_TIME", "08:00"))
    reminder_lead_minutes: int = field(
        default_factory=lambda: int(os.getenv("REMINDER_LEAD_MINUTES", "0")))
    upcoming_days: int = field(default_factory=lambda: int(os.getenv("UPCOMING_DAYS", "7")))

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.tz_name)

    @property
    def db_path(self) -> str:
        return os.path.join(self.data_dir, "tasks.db")


settings = Settings()
