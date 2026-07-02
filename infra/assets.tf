resource "aws_s3_bucket" "assets" {
  bucket = "${var.project}-assets-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "assets" {
  bucket                  = aws_s3_bucket.assets.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "compose" {
  bucket = aws_s3_bucket.assets.id
  key    = "docker-compose.prod.yml"
  source = "${path.module}/../docker-compose.prod.yml"
  etag   = filemd5("${path.module}/../docker-compose.prod.yml")
}

resource "aws_s3_object" "caddyfile" {
  bucket = aws_s3_bucket.assets.id
  key    = "Caddyfile"
  source = "${path.module}/../Caddyfile"
  etag   = filemd5("${path.module}/../Caddyfile")
}
