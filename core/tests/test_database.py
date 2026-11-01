import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import date, time
from decimal import Decimal
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase

from core.models import Attendance, Department, Employee, LeaveApplication, Salary, WorkSchedule
from payroll_project.settings.env import parse_database_url


def make_emp(name="Ann", **kw):
    kw.setdefault("joined_on", date(2025, 1, 1))
    return Employee.objects.create(name=name, designation="x", basic_salary=Decimal("1000"), **kw)


class DatabaseConstraintTests(TestCase):
    """These rules are enforced by the database itself, not just by forms and services."""

    def fails(self, fn):
        with self.assertRaises(IntegrityError), transaction.atomic():
            fn()

    def test_one_attendance_row_per_employee_per_day(self):
        e = make_emp()
        Attendance.objects.create(emp=e, date=date(2026, 1, 5))
        self.fails(lambda: Attendance.objects.create(emp=e, date=date(2026, 1, 5)))

    def test_one_salary_row_per_employee_per_month(self):
        e = make_emp()
        Salary.objects.create(emp=e, month=1, year=2026, gross=10, net=10)
        self.fails(lambda: Salary.objects.create(emp=e, month=1, year=2026, gross=10, net=10))

    def test_negative_money_is_rejected(self):
        e = make_emp()
        self.fails(lambda: Salary.objects.create(emp=e, month=2, year=2026, gross=10, net=-1))
        self.fails(lambda: Salary.objects.create(emp=e, month=3, year=2026, gross=10, net=1, deduction=-5))
        self.fails(lambda: make_emp("Neg", basic_salary=Decimal("-1")) if False else
                   Employee.objects.create(name="Neg", designation="x", basic_salary=Decimal("-1")))

    def test_leave_cannot_end_before_it_starts(self):
        e = make_emp()
        self.fails(lambda: LeaveApplication.objects.create(
            emp=e, start_date=date(2026, 3, 5), end_date=date(2026, 3, 4), reason="x"))

    def test_employee_cannot_be_own_manager(self):
        e = make_emp()
        e.manager = e
        self.fails(lambda: Employee.objects.filter(pk=e.pk).update(manager=e))

    def test_separation_rules_hold_even_if_validation_is_bypassed(self):
        e = make_emp()
        # raw UPDATEs skip save() and full_clean(); the database must still say no
        self.fails(lambda: Employee.objects.filter(pk=e.pk).update(employment_status="RESIGNED", is_active=False))
        self.fails(lambda: Employee.objects.filter(pk=e.pk).update(
            employment_status="RESIGNED", is_active=False, separation_date=date(2024, 1, 1)))

    def test_active_flag_cannot_contradict_status(self):
        e = make_emp()
        self.fails(lambda: Employee.objects.filter(pk=e.pk).update(is_active=False))
        self.fails(lambda: Employee.objects.filter(pk=e.pk).update(employment_status="SUSPENDED"))

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            with connection.cursor() as cur:
                cur.execute("INSERT INTO attendance (emp_id, date, status) VALUES (999999, '2026-01-01', 'P')")
                cur.execute("SET CONSTRAINTS ALL IMMEDIATE") if connection.vendor == "postgresql" else None
                connection.check_constraints()

    def test_history_protects_employee_and_unique_names(self):
        d = Department.objects.create(name="Fin", code="FIN")
        self.fails(lambda: Department.objects.create(name="Fin", code="X"))
        self.fails(lambda: Department.objects.create(name="Other", code="FIN"))
        WorkSchedule.objects.create(name="S", start_time=time(9), end_time=time(17))
        self.fails(lambda: WorkSchedule.objects.create(name="S", start_time=time(9), end_time=time(17)))
        self.assertTrue(Department.objects.filter(pk=d.pk).exists())


class DatabaseUrlTests(SimpleTestCase):
    def test_parses_a_hosted_url(self):
        cfg = parse_database_url("postgresql://app_user:p%40ss%2Fw@db.example.com:6543/hros?sslmode=require")
        self.assertEqual(cfg, {"NAME": "hros", "USER": "app_user", "PASSWORD": "p@ss/w",
                               "HOST": "db.example.com", "PORT": "6543", "SSLMODE": "require"})

    def test_default_port_and_no_sslmode(self):
        cfg = parse_database_url("postgres://u:p@h/db")
        self.assertEqual((cfg["PORT"], cfg["SSLMODE"]), ("5432", None))

    def test_rejects_other_schemes_and_incomplete_urls(self):
        for bad in ("mysql://u:p@h/db", "postgres://u:p@h/", "postgres:///db", "nonsense"):
            with self.assertRaises(ImproperlyConfigured, msg=bad):
                parse_database_url(bad)


class MigrateLegacyDataTests(SimpleTestCase):
    """End-to-end, the way it is really used: separate processes on real database files."""

    ROOT = Path(__file__).resolve().parents[2]
    POPULATE = """
from datetime import date
from decimal import Decimal
from core.models import *
u = User.objects.create_user("amy", password="pw-12345-x")
u.groups.add(Group.objects.get(name="Employee"))
boss = Employee.objects.create(name="Boss", designation="x", basic_salary=Decimal("5000.50"), joined_on=date(2025, 1, 1))
amy = Employee.objects.create(name="Amy", designation="x", basic_salary=Decimal("3000.25"), joined_on=date(2025, 1, 1), user=u, manager=boss)
Attendance.objects.create(emp=amy, date=date(2026, 1, 5), status="P")
Salary.objects.create(emp=amy, month=1, year=2026, gross=3000.25, net=2900.10)
LeaveApplication.objects.create(emp=amy, start_date=date(2026, 2, 2), end_date=date(2026, 2, 3), reason="r", status="APPROVED")
from datetime import time
from core.services import attendance
attendance.check_in(amy, now=time(9), today=date(2026, 1, 6), by=u)
"""

    @classmethod
    def manage(cls, db_path, *args):
        env = {"PATH": os.environ["PATH"], **{k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ}, "DJANGO_SETTINGS_MODULE": "payroll_project.settings.development",
               "DJANGO_LOAD_DOTENV": "False", "DJANGO_SQLITE_PATH": str(db_path), "DJANGO_SECRET_KEY": "x" * 60}
        return subprocess.run([sys.executable, "manage.py", *args], cwd=cls.ROOT, env=env,
                              capture_output=True, text=True)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.source_tpl, cls.target_tpl = cls.dir / "source.tpl", cls.dir / "target.tpl"
        for tpl in (cls.source_tpl, cls.target_tpl):
            assert cls.manage(tpl, "migrate", "-v0").returncode == 0
        assert cls.manage(cls.source_tpl, "shell", "-c", cls.POPULATE).returncode == 0

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.source, self.target = self.dir / "source.db", self.dir / "target.db"
        shutil.copy(self.source_tpl, self.source)
        shutil.copy(self.target_tpl, self.target)

    def copy(self, *flags, source=None):
        return self.manage(self.target, "migrate_legacy_data", "--source-sqlite", str(source or self.source), *flags)

    def count(self, model):
        out = self.manage(self.target, "shell", "-c", f"from core.models import {model}; print({model}.objects.count())")
        return int(out.stdout.strip().splitlines()[-1])

    def test_copies_and_verifies_everything_including_links(self):
        r = self.copy()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Verification passed", r.stdout)
        check = self.manage(self.target, "shell", "-c", (
            "from core.models import *; a = Employee.objects.get(name='Amy'); "
            "print(a.user.username, a.manager.name, a.basic_salary, a.user.groups.filter(name='Employee').count(), "
            "a.user.check_password('pw-12345-x'), Salary.objects.get().net, LeaveApplication.objects.get().status, "
            "AttendanceAudit.objects.count(), AttendanceAudit.objects.get().changed_by.username)"))
        self.assertEqual(check.stdout.strip().splitlines()[-1], "amy Boss 3000.25 1 True 2900.10 APPROVED 1 amy")

    def test_dry_run_copies_nothing(self):
        r = self.copy("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Dry run OK", r.stdout)
        self.assertEqual(self.count("Employee"), 0)

    def test_refuses_a_target_that_already_has_data(self):
        self.assertEqual(self.copy().returncode, 0)
        r = self.copy()  # second copy into the now-populated target
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already contains", r.stderr)
        self.assertEqual(self.count("Employee"), 2)

    def test_refuses_an_unmigrated_source(self):
        bare = self.dir / "bare.db"
        sqlite3.connect(bare).close()
        r = self.copy(source=bare)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unapplied migrations", r.stderr)

    def test_refuses_missing_file_and_same_database(self):
        r = self.copy(source=self.dir / "nope.db")
        self.assertIn("not found", r.stderr)
        r = self.copy(source=self.target)
        self.assertIn("same database", r.stderr)


    def test_backup_preserves_employee_links_and_money(self):
        import hashlib
        import json
        folder = self.dir / "backups"
        result = self.manage(self.source, "backup_database", "--directory", str(folder))
        self.assertEqual(result.returncode, 0, result.stderr)
        backup, = folder.glob("*.sqlite3")
        manifest = json.loads(backup.with_suffix(".sqlite3.json").read_text())
        self.assertEqual(manifest["sha256"], hashlib.sha256(backup.read_bytes()).hexdigest())
        from contextlib import closing
        with closing(sqlite3.connect(backup)) as restored:
            self.assertEqual(restored.execute("SELECT COUNT(*) FROM employee").fetchone()[0], 2)
            self.assertEqual(str(restored.execute("SELECT net FROM salary").fetchone()[0]), "2900.1")
            self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
