from datetime import datetime, timezone

from app.main import (
    compute_late_minutes,
    resolve_attendance_date,
)

UTC = timezone.utc


def test_day_shift_grace_period():
    # Shift: 09:30 to 18:00 IST

    # 09:40:00 IST — exactly 10 minutes late.
    punch_in = datetime(2026, 10, 10, 4, 10, 0, tzinfo=UTC)

    assert compute_late_minutes(
        punch_in, "09:30", "18:00"
    ) == 0


def test_day_shift_late_by_ten_minutes():
    # 09:40:01 IST — 10 minutes and 1 second late.
    punch_in = datetime(2026, 10, 10, 4, 10, 1, tzinfo=UTC)

    assert compute_late_minutes(
        punch_in, "09:30", "18:00"
    ) == 10


def test_overnight_attendance_date():
    # Shift: 22:00 to 06:00 IST.
    # 05:45 IST on October 10 belongs to the October 9 shift.
    punch_in = datetime(2026, 10, 10, 0, 15, 0, tzinfo=UTC)

    attendance_date = resolve_attendance_date(
        punch_in, "22:00", "06:00"
    )

    assert attendance_date.isoformat() == "2026-10-09"


def test_overnight_late_minutes():
    # Shift started October 9 at 22:00 IST.
    # Punch-in at 00:30 IST on October 10 is 150 minutes late.
    punch_in = datetime(2026, 10, 9, 19, 0, 0, tzinfo=UTC)

    assert compute_late_minutes(
        punch_in, "22:00", "06:00"
    ) == 150


if __name__ == "__main__":
    test_day_shift_grace_period()
    test_day_shift_late_by_ten_minutes()
    test_overnight_attendance_date()
    test_overnight_late_minutes()

    print("All time-helper tests passed!")