"""
Session manager - proper timezone handling, addressing the exact gap
both audits (mine and the v0.3 prompt) flagged: session hours must be
NY-time and DST-aware, not a hardcoded broker-server-time window.

zoneinfo's "America/New_York" already handles DST transitions
automatically (EST/EDT switch) - that's the whole point of using it
instead of a fixed UTC offset.
"""

from dataclasses import dataclass
from datetime import datetime, time, timezone
from enum import Enum
from typing import Optional
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")


class Session(Enum):
    LONDON_AM = "london_am"
    NEW_YORK_AM = "new_york_am"
    LONDON_PM = "london_pm"
    NEW_YORK_PM = "new_york_pm"
    NONE = "none"


@dataclass
class SessionDefinitions:
    """
    All bounds are in NY time. These are configurable approximations -
    confirm against your own session definitions if they differ.
    """
    london_am_start: time = time(3, 0)
    london_am_end: time = time(8, 0)
    new_york_am_start: time = time(8, 0)
    new_york_am_end: time = time(12, 0)
    london_pm_start: time = time(10, 0)   # overlaps NY AM by design (London afternoon)
    london_pm_end: time = time(12, 0)
    new_york_pm_start: time = time(12, 0)
    new_york_pm_end: time = time(16, 0)

    # 1-5-9 model key hours, NY time - independent of the broad window above
    core_model_1am_hour: int = 1
    core_model_5am_hour: int = 5
    core_model_9am_hour: int = 9


@dataclass
class SessionDiagnostics:
    broker_time: datetime
    utc_time: datetime
    ny_time: datetime
    session: Session
    entries_permitted: bool


def server_to_utc(server_time: datetime, broker_utc_offset_hours: float) -> datetime:
    """Convert broker-local server time to UTC without double-shifting."""
    from datetime import timedelta
    broker_tz = timezone(timedelta(hours=broker_utc_offset_hours))
    if server_time.tzinfo is None:
        server_time = server_time.replace(tzinfo=broker_tz)
    return server_time.astimezone(timezone.utc)


def to_ny_time(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(NY_TZ)


def current_session(ny_time: datetime, defs: SessionDefinitions = SessionDefinitions()) -> Session:
    t = ny_time.time()
    if defs.london_am_start <= t < defs.london_am_end:
        return Session.LONDON_AM
    if defs.new_york_am_start <= t < defs.new_york_am_end:
        return Session.NEW_YORK_AM
    if defs.london_pm_start <= t < defs.london_pm_end:
        return Session.LONDON_PM
    if defs.new_york_pm_start <= t < defs.new_york_pm_end:
        return Session.NEW_YORK_PM
    return Session.NONE


def entries_permitted(session: Session, enabled_sessions: Optional[set] = None) -> bool:
    """
    enabled_sessions: set of Session enums allowed to trade, or None for
    "all four broad sessions" (the default per the confirmed instruction
    that 1-5-9 must NOT restrict the broader window).
    """
    if session == Session.NONE:
        return False
    if enabled_sessions is None:
        return True
    return session in enabled_sessions


def diagnose(
    broker_time: datetime,
    broker_utc_offset_hours: float,
    defs: SessionDefinitions = SessionDefinitions(),
    enabled_sessions: Optional[set] = None,
) -> SessionDiagnostics:
    """Produces the full diagnostic snapshot the v0.3 audit asked for -
    broker time, UTC, NY time, current session, and entry permission -
    in one call so it can be logged/dashboarded every new bar."""
    utc = server_to_utc(broker_time, broker_utc_offset_hours)
    ny = to_ny_time(utc)
    session = current_session(ny, defs)
    return SessionDiagnostics(
        broker_time=broker_time,
        utc_time=utc,
        ny_time=ny,
        session=session,
        entries_permitted=entries_permitted(session, enabled_sessions),
    )
