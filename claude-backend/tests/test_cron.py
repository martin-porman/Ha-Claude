"""Cron field parsing in scheduled_tasks.CronExpression (weekday = Python numbering, Monday 0)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduled_tasks import CronExpression  # noqa: E402


@pytest.mark.parametrize("expr, weekdays", [
    ("0 8 * * mon-fri", {0, 1, 2, 3, 4}),
    ("0 8 * * 1-5", {0, 1, 2, 3, 4}),
    ("0 8 * * 0-6", set(range(7))),
    ("0 8 * * 1-5/2", {0, 2, 4}),
    ("0 8 * * 0", {6}),
    ("0 8 * * 7", {6}),
    ("0 8 * * sun,sat", {5, 6}),
    ("0 8 * * *", set(range(7))),
])
def test_weekday_field(expr, weekdays):
    assert CronExpression(expr).weekday == weekdays


def test_steps_and_month_aliases():
    c = CronExpression("5/20 */6 1-31/10 jan,jul *")
    assert c.minute == {5, 25, 45}
    assert c.hour == {0, 6, 12, 18}
    assert c.day == {1, 11, 21, 31}
    assert c.month == {1, 7}


def test_out_of_range_rejected():
    with pytest.raises(ValueError):
        CronExpression("61 * * * *")
