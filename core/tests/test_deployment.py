import json
import os
import subprocess
import sys
from unittest import mock

from django.conf import settings
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .test_settings import GOOD, ROOT

DB_URL = "postgresql://u:p@ep-example-pooler.neon.tech/hros?sslmode=require"
BASE_ENV = {k: v for k, v in GOOD.items() if k not in ("DJANGO_ALLOWED_HOSTS", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_HOST")}


def prod_settings(extra, expr):
    """Boot the production settings in a clean process and return `expr` evaluated as JSON."""
    env = {"PATH": os.environ["PATH"], **{k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ}, **BASE_ENV, "DATABASE_URL": DB_URL, **extra}
    env = {k: v for k, v in env.items() if v is not None}
    code = f"import django, json; django.setup(); from django.conf import settings as s; print(json.dumps({expr}))"
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-600:]
    return json.loads(r.stdout.strip().splitlines()[-1])


class DeploymentFilesTests(SimpleTestCase):
    def read(self, name):
        return (ROOT / name).read_text()

    def test_render_blueprint_uses_real_wsgi_and_migrations(self):
        blueprint = self.read("render.yaml")
        self.assertIn("python scripts/start_server.py", blueprint)
        self.assertIn("healthCheckPath: /healthz/", blueprint)
        self.assertIn("prepare_deployment", self.read("scripts/start_server.py"))

    def test_wsgi_entrypoint_matches_what_vercel_reads_from_settings(self):
        self.assertEqual(settings.WSGI_APPLICATION, "payroll_project.wsgi.application")
        from payroll_project import wsgi
        self.assertTrue(callable(wsgi.application))
        self.assertTrue(settings.STATIC_ROOT)  # Vercel runs collectstatic when STATIC_ROOT is set

    def test_python_version_is_pinned_to_one_django_supports(self):
        self.assertEqual(self.read(".python-version").strip(), "3.12")

    def test_requirements_txt_is_complete_and_free_of_server_only_packages(self):
        lines = [l.strip().lower() for l in self.read("requirements.txt").splitlines() if l.strip() and not l.startswith("#")]
        names = {l.split(">")[0].split("[")[0].split("=")[0] for l in lines}
        self.assertTrue({"django", "psycopg", "whitenoise", "python-dotenv"} <= names, names)
        self.assertIn("gunicorn", names)
        self.assertFalse([l for l in lines if l.startswith("-r")], "Vercel installs this file by itself")

    def test_sensitive_files_never_ship(self):
        for ignore in (".gitignore",):
            text = self.read(ignore)
            for pattern in ("*.sqlite3", ".env", ".env.*"):
                self.assertIn(pattern, text, f"{ignore} must list {pattern}")
        self.assertIn(".vercel/", self.read(".gitignore"))
        self.assertIn("!.env.example", self.read(".gitignore"))

    def test_env_example_documents_the_pooled_switch(self):
        self.assertIn("DB_POOLED", self.read(".env.example"))


class ProductionHostTests(SimpleTestCase):
    def test_explicit_hosts_are_used_without_wildcards(self):
        hosts = prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com"}, "s.ALLOWED_HOSTS")
        self.assertEqual(hosts, ["hr.example.com"])

    def test_provider_variables_do_not_grant_implicit_host_access(self):
        hosts = prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com", "VERCEL": "1",
                               "VERCEL_URL": "untrusted.vercel.app"}, "s.ALLOWED_HOSTS")
        self.assertEqual(hosts, ["hr.example.com"])

    def test_missing_hosts_and_weak_secret_are_rejected(self):
        for overrides in ({"DJANGO_ALLOWED_HOSTS": None}, {"DJANGO_SECRET_KEY": "short"}):
            env = {"PATH": os.environ["PATH"],
                   **{k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ},
                   **GOOD, **overrides}
            env = {k: v for k, v in env.items() if v is not None}
            result = subprocess.run([sys.executable, "manage.py", "check"], cwd=ROOT,
                                    env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(result.returncode, 0)


class PooledDatabaseTests(SimpleTestCase):
    EXPR = ("{'age': s.DATABASES['default']['CONN_MAX_AGE'], 'cursors': s.DATABASES['default']['DISABLE_SERVER_SIDE_CURSORS'],"
            " 'opts': {k: v for k, v in s.DATABASES['default']['OPTIONS'].items()}, 'host': s.DATABASES['default']['HOST']}")

    def test_pooled_switch_sets_everything_a_pooler_needs(self):
        out = prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com", "DB_POOLED": "True"}, self.EXPR)
        self.assertEqual((out["age"], out["cursors"]), (0, True))
        self.assertIsNone(out["opts"]["prepare_threshold"])
        self.assertEqual(out["opts"]["sslmode"], "require")
        self.assertEqual(out["host"], "ep-example-pooler.neon.tech")

    def test_not_pooled_keeps_persistent_connections(self):
        out = prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com", "DB_POOLED": "False"}, self.EXPR)
        self.assertEqual((out["age"], out["cursors"]), (60, False))
        self.assertNotIn("prepare_threshold", out["opts"])

    def test_individual_overrides_still_win(self):
        out = prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com", "DB_POOLED": "True", "DB_CONN_MAX_AGE": "30",
                             "DB_DISABLE_SERVER_SIDE_CURSORS": "False"}, self.EXPR)
        self.assertEqual((out["age"], out["cursors"]), (30, False))

    def test_static_files_do_not_break_pages_if_the_manifest_is_missing(self):
        self.assertFalse(prod_settings({"DJANGO_ALLOWED_HOSTS": "hr.example.com"}, "s.WHITENOISE_MANIFEST_STRICT"))


class HealthCheckTests(TestCase):
    def test_public_and_reports_ok(self):
        r = self.client.get(reverse("healthz"))
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))
        self.assertEqual(r["Cache-Control"], "no-store")

    def test_reports_unavailable_without_leaking_details_when_the_database_is_down(self):
        with mock.patch("core.views.connection") as conn:
            conn.cursor.side_effect = DatabaseError("password authentication failed for user hros at 10.1.2.3")
            r = self.client.get(reverse("healthz"))
        self.assertEqual((r.status_code, r.json()), (503, {"status": "unavailable"}))
        self.assertNotIn("password", r.content.decode())

    def test_read_only(self):
        self.assertEqual(self.client.post(reverse("healthz")).status_code, 405)
        self.assertEqual(self.client.head(reverse("healthz")).status_code, 200)

    def test_works_for_a_signed_out_visitor_even_when_a_password_change_is_pending(self):
        self.assertEqual(self.client.get("/healthz/").status_code, 200)
