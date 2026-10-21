import datetime
import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from service.routers.auth import RequireAuth

router = APIRouter(tags=["User data backup"], dependencies=[RequireAuth])

# Personal data (users with age/weight, favorites, tasks): never inside the Git
# repository. Default is data/userdata, which .gitignore already excludes.
USERDATA_DIR = Path(
    os.getenv(
        "USERDATA_DIR",
        str(Path(__file__).resolve().parents[2] / "data" / "userdata"),
    )
)
MAX_BYTES = 2_000_000
KEEP_DAYS = 60


class UserDataResponse(BaseModel):
    """The last backup of the page's browser data."""

    data: dict[str, Any] | None = Field(..., description="Backed-up data, or null.")
    saved_at: str | None = Field(..., description="When it was saved (local time).")
    location: str = Field(..., description="Folder that holds the backup files.")


def _main_file(folder: Path) -> Path:
    return folder / "userdata.json"


def save_userdata(folder: Path, data: dict[str, Any], now: datetime.datetime) -> str:
    """
    Write the backup: userdata.json, the previous version as userdata.prev.json,
    and one copy per day (userdata-YYYY-MM-DD.json, last KEEP_DAYS days kept).
    """
    folder.mkdir(parents=True, exist_ok=True)
    saved_at = now.isoformat(timespec="seconds")
    text = json.dumps(
        {"saved_at": saved_at, "data": data}, ensure_ascii=False, indent=1
    )

    main = _main_file(folder)
    if main.exists():
        main.replace(folder / "userdata.prev.json")
    tmp = folder / "userdata.json.tmp"
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(main)
    (folder / f"userdata-{now:%Y-%m-%d}.json").write_text(text, encoding="utf-8")

    cutoff = (now - datetime.timedelta(days=KEEP_DAYS)).strftime("%Y-%m-%d")
    for old in folder.glob("userdata-????-??-??.json"):
        if old.stem.removeprefix("userdata-") < cutoff:
            old.unlink()
    return saved_at


def load_userdata(folder: Path) -> tuple[dict[str, Any] | None, str | None]:
    main = _main_file(folder)
    if not main.exists():
        return None, None
    content = json.loads(main.read_text(encoding="utf-8"))
    return content.get("data"), content.get("saved_at")


@router.get("/userdata/", summary="Read the backup of the page's browser data")
async def get_userdata() -> UserDataResponse:
    """Return the last backup made by prices.html, if any."""
    data, saved_at = load_userdata(USERDATA_DIR)
    return UserDataResponse(data=data, saved_at=saved_at, location=str(USERDATA_DIR))


@router.put("/userdata/", summary="Back up the page's browser data")
async def put_userdata(data: dict[str, Any] = Body(...)) -> UserDataResponse:
    """Save favorites, users, tasks etc. sent by prices.html."""
    if len(json.dumps(data)) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="Backup is too large.")
    saved_at = save_userdata(USERDATA_DIR, data, datetime.datetime.now())
    return UserDataResponse(data=None, saved_at=saved_at, location=str(USERDATA_DIR))
