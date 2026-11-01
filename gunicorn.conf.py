"""Production process limits and logs without sensitive request URLs."""
import os

bind = f"0.0.0.0:{int(os.environ.get('PORT', '8000'))}"
workers = int(os.environ.get("GUNICORN_WORKERS", "2"))
threads = int(os.environ.get("GUNICORN_THREADS", "2"))
timeout = 60
graceful_timeout = 30
max_requests = 1000
max_requests_jitter = 100
accesslog = "-"
errorlog = "-"
# Password-reset tokens and query strings must not enter access logs.
access_log_format = '%(t)s method=%(m)s status=%(s)s duration=%(L)s'



def on_starting(server):
    # Gunicorn also auto-loads this file for the existing Render start command.
    # This keeps current deployments safe before Dashboard settings are changed.
    if os.environ.pop("HROS_SCHEMA_PREPARED", None) == "1":
        return
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "payroll_project.settings.production")
    import django
    from django.core.management import call_command
    from django.db import connections
    django.setup()
    try:
        call_command("prepare_deployment")
    finally:
        connections.close_all()
