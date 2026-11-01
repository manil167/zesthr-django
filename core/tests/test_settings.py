import os
import subprocess
import sys
from pathlib import Path

from django.test import SimpleTestCase

ROOT = Path(__file__).resolve().parents[2]
GOOD = {
    "DJANGO_LOAD_DOTENV": "False",
    "DJANGO_DEBUG": "False",
    "DJANGO_SETTINGS_MODULE": "payroll_project.settings.production",
    "DJANGO_SECRET_KEY": "q7Zk-R2mVx9LpT4wYb8NcD3hJf6GsA1eUo5iKy0XrWt2MvBnCzHj7PdQaLgE4S9u",
    "DJANGO_ALLOWED_HOSTS": "hr.example.com",
    "DB_NAME": "n", "DB_USER": "u", "DB_PASSWORD": "p", "DB_HOST": "h",
    "DJANGO_EMAIL_HOST": "smtp.example.com", "DJANGO_DEFAULT_FROM_EMAIL": "HROS <hr@example.com>",
}


def run_check(overrides):
    env = {"PATH": os.environ["PATH"], **{k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ}, **GOOD, **overrides}
    env = {k: v for k, v in env.items() if v is not None}
    return subprocess.run([sys.executable, "manage.py", "check"], cwd=ROOT,
                          env=env, capture_output=True, text=True)


class ProductionSettingsTests(SimpleTestCase):
    def test_valid_configuration_starts(self):
        self.assertEqual(run_check({}).returncode, 0)

    def test_missing_secret_key_fails(self):
        self.assertNotEqual(run_check({"DJANGO_SECRET_KEY": None}).returncode, 0)

    def test_debug_true_fails(self):
        self.assertNotEqual(run_check({"DJANGO_DEBUG": "True"}).returncode, 0)

    def test_wildcard_host_fails(self):
        self.assertNotEqual(run_check({"DJANGO_ALLOWED_HOSTS": "*"}).returncode, 0)

    def test_missing_database_config_fails(self):
        self.assertNotEqual(run_check({"DB_HOST": None}).returncode, 0)
