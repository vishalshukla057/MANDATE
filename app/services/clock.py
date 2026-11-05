from datetime import datetime, timezone, timedelta, date
from typing import Optional


class ClockService:
    """
    Logical clock service for simulation and time-travel.
    All application code reads the current time via this service.
    """
    _time_offset: timedelta = timedelta(0)
    _frozen_time: Optional[datetime] = None

    @classmethod
    def now(cls) -> datetime:
        if cls._frozen_time is not None:
            return cls._frozen_time + cls._time_offset
        return datetime.now(timezone.utc) + cls._time_offset

    @classmethod
    def today(cls) -> date:
        return cls.now().date()

    @classmethod
    def time_travel(cls, days: float) -> datetime:
        """Advance the logical clock by N days (can be fractional or negative)."""
        cls._time_offset += timedelta(days=days)
        return cls.now()

    @classmethod
    def set_time(cls, target_dt: datetime) -> datetime:
        """Set an exact target time."""
        if target_dt.tzinfo is None:
            target_dt = target_dt.replace(tzinfo=timezone.utc)
        cls._frozen_time = target_dt
        cls._time_offset = timedelta(0)
        return cls.now()

    @classmethod
    def reset(cls) -> datetime:
        """Reset the clock back to real system UTC time."""
        cls._frozen_time = None
        cls._time_offset = timedelta(0)
        return cls.now()

    @classmethod
    def get_offset_days(cls) -> float:
        return cls._time_offset.total_seconds() / 86400.0


clock = ClockService

