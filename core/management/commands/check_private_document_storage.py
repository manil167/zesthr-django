from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.files.storage import storages


class Command(BaseCommand):
    help = "Verify the production document bucket is private, versioned, and encrypted with the configured KMS key."

    def handle(self, *args, **options):
        config = settings.STORAGES["private_documents"]
        if config["BACKEND"] != "storages.backends.s3.S3Storage":
            raise CommandError("Configure private S3 storage before running this production check.")
        backend = storages["private_documents"]
        client, bucket = backend.connection.meta.client, backend.bucket_name
        try:
            block = client.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
            if not all(block.get(key) for key in (
                    "BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
                raise CommandError("All four bucket public-access blocks must be enabled.")
            if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
                raise CommandError("Document bucket versioning must be enabled.")
            rules = client.get_bucket_encryption(Bucket=bucket)["ServerSideEncryptionConfiguration"]["Rules"]
            configured = config["OPTIONS"]["object_parameters"]["SSEKMSKeyId"]
            if not any(rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm") == "aws:kms"
                       and rule["ApplyServerSideEncryptionByDefault"].get("KMSMasterKeyID") == configured for rule in rules):
                raise CommandError("Bucket encryption must use the configured KMS key ARN.")
        except CommandError:
            raise
        except Exception as exc:
            raise CommandError("Storage verification failed. Check region and restricted AWS credentials.") from exc
        self.stdout.write(self.style.SUCCESS("Private bucket access blocks, KMS encryption, and versioning verified."))
