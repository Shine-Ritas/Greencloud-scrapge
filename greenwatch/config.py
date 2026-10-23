import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE_URL = "https://greencloudvps.com/billing/store/budget-kvm-sale"


@dataclass(frozen=True)
class Settings:
    database: Path = Path("data/greenwatch.sqlite3")
    timezone: str = "Asia/Bangkok"
    retention_days: int = 30
    telegram_token: str = ""
    telegram_chat_id: str = ""
    dashboard_user: str = "admin"
    dashboard_password: str = ""
    notify_scrape_summary: bool = True
    apac_keywords: tuple[str, ...] = (
        "singapore", "tokyo", "osaka", "japan", "hong kong", "seoul", "south korea",
        "sydney", "melbourne", "australia", "auckland", "new zealand", "mumbai",
        "chennai", "bangalore", "bengaluru", "hyderabad", "delhi", "india", "taipei",
        "taiwan", "bangkok", "thailand", "jakarta", "indonesia", "kuala lumpur",
        "malaysia", "manila", "philippines", "ho chi minh", "hanoi", "vietnam",
    )

    def __post_init__(self):
        ZoneInfo(self.timezone)
        if not 1 <= self.retention_days <= 3650:
            raise ValueError("RETENTION_DAYS must be between 1 and 3650")
        if bool(self.telegram_token) != bool(self.telegram_chat_id):
            raise ValueError("Set both TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID, or neither")

    @property
    def telegram_configured(self):
        return bool(self.telegram_token and self.telegram_chat_id)

    @classmethod
    def from_env(cls):
        extra = tuple(x.strip().lower() for x in os.getenv("APAC_EXTRA_KEYWORDS", "").split(",") if x.strip())
        return cls(
            database=Path(os.getenv("DATABASE_PATH", "data/greenwatch.sqlite3")),
            timezone=os.getenv("TZ", "Asia/Bangkok"),
            retention_days=int(os.getenv("RETENTION_DAYS", "30")),
            telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
            telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            dashboard_user=os.getenv("DASHBOARD_USER", "admin"),
            dashboard_password=os.getenv("DASHBOARD_PASSWORD", ""),
            notify_scrape_summary=os.getenv("NOTIFY_SCRAPE_SUMMARY", "true").lower() in ("true", "1", "yes"),
            apac_keywords=cls.apac_keywords + extra,
        )
