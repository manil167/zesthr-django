import os
from pathlib import Path
from unittest.mock import patch
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase
from payroll_project.settings.document_storage import private_document_storage


class PrivateStorageConfigurationTests(SimpleTestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_local_files_are_private_and_outside_static_assets(self):
        storage = private_document_storage(Path("/application"))
        self.assertEqual(storage["OPTIONS"]["location"], str(Path("/application/private_documents")))
        self.assertIsNone(storage["OPTIONS"]["base_url"])

    def test_s3_requires_explicit_bucket_region_and_kms_key(self):
        with patch.dict(os.environ, {"DJANGO_DOCUMENT_STORAGE": "s3"}, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                private_document_storage(Path("/application"))

    def test_s3_uses_encryption_tls_and_no_public_acl(self):
        with patch.dict(os.environ, {"DJANGO_DOCUMENT_STORAGE": "s3", "DJANGO_DOCUMENT_BUCKET": "private-hr",
                "DJANGO_DOCUMENT_REGION": "eu-west-1", "DJANGO_DOCUMENT_KMS_KEY_ID": "arn:aws:kms:example"}, clear=True):
            options = private_document_storage(Path("/application"))["OPTIONS"]
        self.assertIsNone(options["default_acl"])
        self.assertTrue(options["querystring_auth"])
        self.assertTrue(options["use_ssl"])
        self.assertTrue(options["verify"])
        self.assertEqual(options["object_parameters"]["ServerSideEncryption"], "aws:kms")
        self.assertFalse(options["file_overwrite"])
