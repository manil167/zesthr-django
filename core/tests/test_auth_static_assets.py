"""Exercise provider-enabled pages against real collected production assets."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse


class AuthenticationAssetTests(TestCase):
    @override_settings(NEON_AUTH_BASE_URL="https://test.neonauth.example/hros/auth")
    def test_provider_signup_renders_with_a_served_manifest_bundle(self):
        with TemporaryDirectory() as directory, override_settings(
            STATIC_ROOT=directory,
            STORAGES={**settings.STORAGES, "staticfiles": {
                "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"}},
        ):
            call_command("collectstatic", interactive=False, verbosity=0)
            manifest = json.loads((Path(directory) / "staticfiles.json").read_text())
            asset = manifest["paths"]["core/neon-signup.bundle.js"]
            self.assertTrue((Path(directory) / asset).is_file())
            response = self.client.get(reverse("signup"))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, settings.STATIC_URL + asset)
            self.assertContains(response, "data-neon-signup")
            self.assertEqual(self.client.get(reverse("login")).status_code, 200)
