terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = { source = "hashicorp/aws", version = ">= 5.0, < 7.0" }
  }
}
variable "region" { type = string }
variable "bucket_name" { type = string }
variable "expected_account_id" {
  type = string
  default = null
  description = "Restrict provisioning to this AWS account when supplied."
  validation {
    condition = var.expected_account_id == null || can(regex("^[0-9]{12}$", var.expected_account_id))
    error_message = "expected_account_id must contain exactly 12 digits."
  }
}
variable "existing_kms_key_arn" {
  type = string
  default = null
  description = "Existing symmetric customer-managed encryption key; null creates a dedicated key."
  validation {
    condition = var.existing_kms_key_arn == null || can(regex("^arn:aws:kms:[a-z0-9-]+:[0-9]{12}:key/[A-Za-z0-9-]+$", var.existing_kms_key_arn))
    error_message = "Supply a full KMS key ARN, not an alias or secret key material."
  }
}
provider "aws" {
  region = var.region
  allowed_account_ids = var.expected_account_id == null ? null : [var.expected_account_id]
}
locals {
  document_kms_key_arn = var.existing_kms_key_arn == null ? aws_kms_key.documents[0].arn : var.existing_kms_key_arn
}
moved {
  from = aws_kms_key.documents
  to = aws_kms_key.documents[0]
}

resource "aws_kms_key" "documents" {
  count = var.existing_kms_key_arn == null ? 1 : 0
  description = "HROS private employee documents"
  enable_key_rotation = true
  deletion_window_in_days = 30
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket" "documents" {
  bucket = var.bucket_name
  force_destroy = false
  lifecycle { prevent_destroy = true }
}
resource "aws_s3_bucket_ownership_controls" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_public_access_block" "documents" {
  bucket = aws_s3_bucket.documents.id
  block_public_acls = true
  block_public_policy = true
  ignore_public_acls = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  lifecycle {
    precondition {
      condition = var.existing_kms_key_arn == null ? true : startswith(var.existing_kms_key_arn, "arn:aws:kms:${var.region}:")
      error_message = "The existing KMS key must be in the bucket region."
    }
    precondition {
      condition = var.existing_kms_key_arn == null || var.expected_account_id == null ? true : split(":", var.existing_kms_key_arn)[4] == var.expected_account_id
      error_message = "The existing KMS key must belong to the expected AWS account."
    }
  }
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
      kms_master_key_id = local.document_kms_key_arn
    }
    bucket_key_enabled = true
  }
}
resource "aws_s3_bucket_policy" "transport" {
  bucket = aws_s3_bucket.documents.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid = "DenyInsecureTransport", Effect = "Deny", Principal = "*",
      Action = "s3:*",
      Resource = [aws_s3_bucket.documents.arn, "${aws_s3_bucket.documents.arn}/*"],
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}
resource "aws_iam_policy" "application_documents" {
  name_prefix = "hros-private-documents-"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:ListBucket", "s3:GetBucketPublicAccessBlock",
          "s3:GetEncryptionConfiguration", "s3:GetBucketVersioning"],
        Resource = aws_s3_bucket.documents.arn },
      { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
        Resource = "${aws_s3_bucket.documents.arn}/hr-documents/*" },
      { Effect = "Allow", Action = ["kms:Encrypt", "kms:Decrypt", "kms:GenerateDataKey", "kms:DescribeKey"],
        Resource = local.document_kms_key_arn,
        Condition = { StringEquals = { "kms:ViaService" = "s3.${var.region}.amazonaws.com" } } }
    ]
  })
}
output "bucket" { value = aws_s3_bucket.documents.id }
output "region" { value = var.region }
output "kms_key_arn" { value = local.document_kms_key_arn }
output "application_policy_arn" { value = aws_iam_policy.application_documents.arn }
