"""Private HR storage configuration. Credentials use the AWS SDK credential chain."""
from django.core.exceptions import ImproperlyConfigured
from .env import env, require_env


def private_document_storage(base_dir):
    backend = env("DJANGO_DOCUMENT_STORAGE", "neon" if env("AWS_ENDPOINT_URL_S3") else "filesystem").strip().lower()
    if backend == "filesystem":
        root = env("DJANGO_PRIVATE_DOCUMENT_ROOT", "").strip() or str(base_dir / "private_documents")
        return {"BACKEND": "django.core.files.storage.FileSystemStorage",
                "OPTIONS": {"location": root, "base_url": None}}
    if backend == 'neon':
        endpoint = require_env('AWS_ENDPOINT_URL_S3')
        from urllib.parse import urlsplit
        parsed = urlsplit(endpoint)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.query:
            raise ImproperlyConfigured('AWS_ENDPOINT_URL_S3 must be an HTTPS Neon storage endpoint.')
        return {
            'BACKEND': 'storages.backends.s3.S3Storage',
            'OPTIONS': {
                'bucket_name': env('DJANGO_DOCUMENT_BUCKET', 'assets'),
                'endpoint_url': endpoint,
                'region_name': require_env('AWS_REGION'),
                'addressing_style': 'path',
                'signature_version': 's3v4',
                'default_acl': None, 'querystring_auth': True,
                'querystring_expire': 60, 'use_ssl': True, 'verify': True,
                'custom_domain': None, 'file_overwrite': False,
                'location': 'hr-documents', 'max_memory_size': 1024 * 1024,
                'object_parameters': {'CacheControl': 'private, no-store'},
            },
        }
    if backend != "s3":
        raise ImproperlyConfigured("DJANGO_DOCUMENT_STORAGE must be filesystem, s3 or neon.")
    return {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": require_env("DJANGO_DOCUMENT_BUCKET"),
            "region_name": require_env("DJANGO_DOCUMENT_REGION"),
            "default_acl": None,
            "querystring_auth": True,
            "querystring_expire": 60,
            "signature_version": "s3v4",
            "use_ssl": True,
            "verify": True,
            "custom_domain": None,
            "file_overwrite": False,
            "location": "hr-documents",
            "max_memory_size": 1024 * 1024,
            "object_parameters": {"ServerSideEncryption": "aws:kms",
                                  "SSEKMSKeyId": require_env("DJANGO_DOCUMENT_KMS_KEY_ID"),
                                  "CacheControl": "private, no-store"},
        },
    }
