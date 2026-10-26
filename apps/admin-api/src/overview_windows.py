"""Canonical UTC calendar windows for saved analytical reports."""

import calendar
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

MAX_REPORT_DAYS = 3660


@dataclass(frozen=True)
class ReportWindow:
    """An inclusive start and exclusive end, or the current operational snapshot."""

    start: date | None
    end: date | None

    def __post_init__(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Both report dates are required")
        if (
            self.start is not None
            and self.end is not None
            and not 1 <= (self.end - self.start).days <= MAX_REPORT_DAYS
        ):
            raise ValueError("Overview ranges must contain between 1 and 3660 days")

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start is not None and self.end else 1

    @property
    def key(self) -> str:
        return (
            f"{self.start.isoformat()}:{self.end.isoformat()}"
            if self.start and self.end
            else "current"
        )

    @property
    def inclusive_end(self) -> date | None:
        return self.end - timedelta(days=1) if self.end else None


def current_window() -> ReportWindow:
    return ReportWindow(None, None)


def recent_window(days: int, now: datetime) -> ReportWindow:
    end = now.astimezone(UTC).date() + timedelta(days=1)
    return ReportWindow(end - timedelta(days=days), end)


_DATE_MATH = re.compile(r"now((?:[+-]\d+[dwMy])*)(?:/([dwMy]))?")
_OFFSETS = re.compile(r"([+-])(\d+)([dwMy])")


def _shift_months(value: date, months: int) -> date:
    year, month = divmod(value.year * 12 + value.month - 1 + months, 12)
    return date(year, month + 1, min(value.day, calendar.monthrange(year, month + 1)[1]))


def _date_value(value: str, today: date, *, upper: bool) -> date:
    if not value.startswith("now"):
        return date.fromisoformat(value)
    match = _DATE_MATH.fullmatch(value)
    if match is None:
        raise ValueError("Invalid date math")
    result = today
    for sign, amount, unit in _OFFSETS.findall(match[1]):
        count = int(amount) * (1 if sign == "+" else -1)
        if unit in {"M", "y"}:
            result = _shift_months(result, count * (12 if unit == "y" else 1))
        else:
            result += timedelta(days=count * (7 if unit == "w" else 1))
    rounding = match[2]
    if rounding == "w":
        result += timedelta(days=(6 - result.weekday()) if upper else -result.weekday())
    elif rounding == "M":
        result = result.replace(
            day=calendar.monthrange(result.year, result.month)[1] if upper else 1
        )
    elif rounding == "y":
        result = result.replace(month=12 if upper else 1, day=31 if upper else 1)
    return result


def parse_report_window(
    from_value: str | None, to_value: str | None, now: datetime
) -> ReportWindow:
    """Resolve inclusive ISO dates or Grafana date math into UTC calendar bounds."""
    today = now.astimezone(UTC).date()
    try:
        end = _date_value(to_value, today, upper=True) if to_value is not None else today
        start = (
            _date_value(from_value, today, upper=False)
            if from_value is not None
            else end - timedelta(days=29)
        )
        if end > today:
            raise ValueError("Overview interval cannot include future dates")
        return ReportWindow(start, end + timedelta(days=1))
    except (ValueError, OverflowError) as error:
        raise ValueError(
            "Use ISO dates or date math for 1 to 3660 days ending no later than today"
        ) from error
