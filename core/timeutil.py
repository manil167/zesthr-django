"""Time-zone helpers. Imports no models, so models and services can both use it.

The company time zone is `settings.TIME_ZONE` (env DJANGO_TIME_ZONE). A work location may
override it, so staff at an office in another zone punch against their own local day.
"""
from zoneinfo import ZoneInfo, available_timezones

from django.conf import settings
from django.utils import timezone


def is_valid_zone(name):
    return name in available_timezones()


def zone_for(emp):
    """The zone this employee's attendance day is measured in."""
    location = emp.work_location if emp.work_location_id else None
    return ZoneInfo(location.time_zone if location and location.time_zone else settings.TIME_ZONE)


def now_for(emp):
    return timezone.now().astimezone(zone_for(emp))


def today_for(emp):
    return now_for(emp).date()


def time_for(emp):
    return now_for(emp).time().replace(microsecond=0)
