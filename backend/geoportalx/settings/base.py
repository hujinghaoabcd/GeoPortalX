from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parents[2]
env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    DJANGO_ALLOWED_HOSTS=(list, ["localhost", "127.0.0.1"]),
    CORS_ALLOWED_ORIGINS=(list, ["http://localhost:5173"]),
    S3_PRESIGNED_URL_EXPIRY=(int, 15 * 60),
    S3_UPLOAD_SESSION_EXPIRY=(int, 24 * 60 * 60),
    S3_MULTIPART_PART_SIZE=(int, 64 * 1024 * 1024),
    S3_MAX_UPLOAD_SIZE=(int, 50 * 1024 * 1024 * 1024),
    S3_ABORT_INCOMPLETE_DAYS=(int, 2),
    DATASET_INSPECTION_MAX_ARCHIVE_MEMBERS=(int, 10_000),
    DATASET_INSPECTION_MAX_UNCOMPRESSED_SIZE=(int, 100 * 1024 * 1024 * 1024),
    DATASET_INSPECTION_MAX_COMPRESSION_RATIO=(float, 100.0),
    VECTOR_IMPORT_TIMEOUT=(int, 60 * 60),
    VECTOR_PROFILE_SAMPLE_SIZE=(int, 100_000),
    VECTOR_PROFILE_MAX_FIELDS=(int, 50),
    VECTOR_PROFILE_TOP_VALUES=(int, 5),
    VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT=(int, 1_000),
    VECTOR_TILE_MIN_ZOOM=(int, 0),
    VECTOR_TILE_MAX_ZOOM=(int, 14),
    MARTIN_REQUEST_TIMEOUT=(float, 10.0),
    MARTIN_MAX_TILE_BYTES=(int, 10 * 1024 * 1024),
    VECTOR_TILE_PUBLIC_CACHE_SECONDS=(int, 60),
)
environ.Env.read_env(BASE_DIR.parent / ".env")

SECRET_KEY = env("DJANGO_SECRET_KEY", default="unsafe-development-key")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "corsheaders",
    "modules.accounts",
    "modules.organizations",
    "modules.resources",
    "modules.permissions",
    "modules.jobs",
    "modules.object_storage",
    "modules.uploads",
    "modules.datasets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "geoportalx.urls"
WSGI_APPLICATION = "geoportalx.wsgi.application"
ASGI_APPLICATION = "geoportalx.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.request",
            ],
        },
    }
]

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgis://geoportalx:geoportalx@localhost:5432/geoportalx",
    )
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS")

GEOPORTAL_JOB_QUEUES = (
    "system",
    "import",
    "vector",
    "raster",
    "processing",
    "catalog",
)
CELERY_BROKER_URL = env("REDIS_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = None
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 60
CELERY_TASK_SOFT_TIME_LIMIT = 55 * 60
CELERY_TASK_DEFAULT_QUEUE = "system"
CELERY_TASK_DEFAULT_PRIORITY = 0
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BROKER_TRANSPORT_OPTIONS = {"queue_order_strategy": "priority"}
CELERY_TASK_ROUTES = {
    "modules.jobs.tasks.execute_job": {"queue": "system"},
}

S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default="http://localhost:9000")
S3_ACCESS_KEY = env("S3_ACCESS_KEY", default="geoportalx")
S3_SECRET_KEY = env("S3_SECRET_KEY", default="change-me")
S3_SESSION_TOKEN = env("S3_SESSION_TOKEN", default="")
S3_BUCKET = env("S3_BUCKET", default="geoportalx")
S3_REGION = env("S3_REGION", default="us-east-1")
S3_ADDRESSING_STYLE = env("S3_ADDRESSING_STYLE", default="path")
S3_SERVER_SIDE_ENCRYPTION = env("S3_SERVER_SIDE_ENCRYPTION", default="")
S3_PRESIGNED_URL_EXPIRY = env("S3_PRESIGNED_URL_EXPIRY")
S3_UPLOAD_SESSION_EXPIRY = env("S3_UPLOAD_SESSION_EXPIRY")
S3_MULTIPART_PART_SIZE = env("S3_MULTIPART_PART_SIZE")
S3_MAX_UPLOAD_SIZE = env("S3_MAX_UPLOAD_SIZE")
S3_ABORT_INCOMPLETE_DAYS = env("S3_ABORT_INCOMPLETE_DAYS")
S3_CORS_ALLOWED_ORIGINS = CORS_ALLOWED_ORIGINS

DATASET_INSPECTION_MAX_ARCHIVE_MEMBERS = env("DATASET_INSPECTION_MAX_ARCHIVE_MEMBERS")
DATASET_INSPECTION_MAX_UNCOMPRESSED_SIZE = env("DATASET_INSPECTION_MAX_UNCOMPRESSED_SIZE")
DATASET_INSPECTION_MAX_COMPRESSION_RATIO = env("DATASET_INSPECTION_MAX_COMPRESSION_RATIO")
DATASET_DB_SCHEMA = env("DATASET_DB_SCHEMA", default="geoportalx_data")
DATASET_STAGING_SCHEMA = env("DATASET_STAGING_SCHEMA", default="geoportalx_staging")
OGR2OGR_EXECUTABLE = env("OGR2OGR_EXECUTABLE", default="ogr2ogr")
VECTOR_IMPORT_TIMEOUT = env("VECTOR_IMPORT_TIMEOUT")

VECTOR_PROFILE_SAMPLE_SIZE = env("VECTOR_PROFILE_SAMPLE_SIZE")
VECTOR_PROFILE_MAX_FIELDS = env("VECTOR_PROFILE_MAX_FIELDS")
VECTOR_PROFILE_TOP_VALUES = env("VECTOR_PROFILE_TOP_VALUES")
VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT = env(
    "VECTOR_PROFILE_TOP_VALUES_MAX_DISTINCT"
)
VECTOR_TILE_MIN_ZOOM = env("VECTOR_TILE_MIN_ZOOM")
VECTOR_TILE_MAX_ZOOM = env("VECTOR_TILE_MAX_ZOOM")
if VECTOR_TILE_MIN_ZOOM < 0 or VECTOR_TILE_MAX_ZOOM < VECTOR_TILE_MIN_ZOOM:
    raise ValueError("Vector tile zoom configuration is invalid")

MARTIN_INTERNAL_URL = env("MARTIN_INTERNAL_URL", default="http://localhost:3000")
MARTIN_REQUEST_TIMEOUT = env("MARTIN_REQUEST_TIMEOUT")
MARTIN_MAX_TILE_BYTES = env("MARTIN_MAX_TILE_BYTES")
VECTOR_TILE_PUBLIC_CACHE_SECONDS = env("VECTOR_TILE_PUBLIC_CACHE_SECONDS")
