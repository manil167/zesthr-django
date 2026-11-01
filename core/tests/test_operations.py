from unittest import mock
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from scripts import start_server


class ProductionStartupTests(SimpleTestCase):
    @mock.patch("os.execvp")
    @mock.patch("django.core.management.call_command")
    @mock.patch("django.setup")
    def test_migrations_finish_before_gunicorn_starts(self, setup, command, execute):
        start_server.main()
        command.assert_called_once_with("prepare_deployment")
        self.assertEqual(execute.call_args.args[0], "gunicorn")

    @mock.patch("os.execvp")
    @mock.patch("django.core.management.call_command", side_effect=RuntimeError("Migration failed"))
    @mock.patch("django.setup")
    def test_failed_preparation_does_not_start_server(self, setup, command, execute):
        with self.assertRaises(RuntimeError):
            start_server.main()
        execute.assert_not_called()


class SchemaHealthTests(TestCase):
    def test_pending_migrations_make_readiness_fail(self):
        with mock.patch("django.db.migrations.executor.MigrationExecutor.migration_plan", return_value=[object()]):
            response = self.client.get(reverse("healthz"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})



class ExistingGunicornCommandTests(SimpleTestCase):
    @mock.patch("django.db.connections.close_all")
    @mock.patch("django.core.management.call_command")
    @mock.patch("django.setup")
    def test_existing_start_command_runs_migration_hook(self, setup, command, close):
        import importlib.util
        from pathlib import Path
        specification = importlib.util.spec_from_file_location("hros_gunicorn", Path(__file__).resolve().parents[2] / "gunicorn.conf.py")
        configuration = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(configuration)
        configuration.on_starting(None)
        command.assert_called_once_with("prepare_deployment")
        close.assert_called_once()
