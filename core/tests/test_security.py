import os
import subprocess
import sys

from django.test import Client, SimpleTestCase, TestCase, override_settings

from .test_settings import GOOD, ROOT, run_check

SECURE = dict(
    SECURE_SSL_REDIRECT=True,
    SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"),
    SECURE_HSTS_SECONDS=3600,
    SECURE_CONTENT_TYPE_NOSNIFF=True,
    X_FRAME_OPTIONS="DENY",
    ALLOWED_HOSTS=["hr.example.com"],
)


@override_settings(**SECURE)
class TransportSecurityTests(TestCase):
    def test_http_redirects_to_https(self):
        r = Client().get("/login/", HTTP_HOST="hr.example.com")
        self.assertEqual(r.status_code, 301)
        self.assertTrue(r["Location"].startswith("https://hr.example.com/"))

    def test_https_via_proxy_header_is_not_redirected(self):
        r = Client().get("/login/", HTTP_HOST="hr.example.com", HTTP_X_FORWARDED_PROTO="https")
        self.assertNotEqual(r.status_code, 301)

    def test_security_headers_and_hsts(self):
        r = Client().get("/login/", HTTP_HOST="hr.example.com", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(r["Strict-Transport-Security"], "max-age=3600")
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["X-Frame-Options"], "DENY")

    def test_unknown_host_is_rejected(self):
        r = Client().get("/login/", HTTP_HOST="evil.example.org", HTTP_X_FORWARDED_PROTO="https")
        self.assertEqual(r.status_code, 400)


class ProductionConfigTests(SimpleTestCase):
    def test_http_origin_in_csrf_trusted_origins_fails(self):
        self.assertNotEqual(run_check({"DJANGO_CSRF_TRUSTED_ORIGINS": "http://hr.example.com"}).returncode, 0)

    def test_preload_without_prerequisites_fails(self):
        self.assertNotEqual(run_check({"DJANGO_HSTS_PRELOAD": "True"}).returncode, 0)

    def test_deployment_check_passes_with_hsts(self):
        env = {"PATH": os.environ["PATH"], **{k: os.environ[k] for k in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if k in os.environ}, **GOOD, "DJANGO_HSTS_SECONDS": "31536000",
               "DJANGO_HSTS_INCLUDE_SUBDOMAINS": "True"}
        r = subprocess.run([sys.executable, "manage.py", "check", "--deploy", "--fail-level", "WARNING"],
                           cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
