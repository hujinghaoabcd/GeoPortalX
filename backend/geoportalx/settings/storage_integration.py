import environ

env = environ.Env(
    S3_PRESIGNED_URL_EXPIRY=(int, 15 * 60),
    S3_ABORT_INCOMPLETE_DAYS=(int, 2),
)

SECRET_KEY = "storage-integration-only"
INSTALLED_APPS: list[str] = []
DATABASES: dict[str, object] = {}
USE_TZ = True

S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="http://127.0.0.1:9000")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", default="geoportalx")
S3_SECRET_KEY = env("S3_SECRET_KEY", default="change-me-now")
S3_SESSION_TOKEN = env("S3_SESSION_TOKEN", default="")
S3_BUCKET = env("S3_BUCKET", default="geoportalx-integration")
S3_REGION = env("S3_REGION", default="us-east-1")
S3_ADDRESSING_STYLE = env("S3_ADDRESSING_STYLE", default="path")
S3_SERVER_SIDE_ENCRYPTION = env("S3_SERVER_SIDE_ENCRYPTION", default="")
S3_PRESIGNED_URL_EXPIRY = env("S3_PRESIGNED_URL_EXPIRY")
S3_ABORT_INCOMPLETE_DAYS = env("S3_ABORT_INCOMPLETE_DAYS")
S3_CORS_ALLOWED_ORIGINS: list[str] = []
