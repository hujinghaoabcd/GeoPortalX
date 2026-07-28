# GeoPortalX Storage and Upload Sessions Handoff — 2026-07-28

## Branch and pull request

- Base: `main` at `91ec57e56f58f3b1f8bd838263ac3dd1efdada71`.
- Development branch: `agent/storage-upload-sessions`.
- Pull request: `#3 Add object storage and multipart upload sessions`.

## Fixed design decisions

- Django remains the control plane and never proxies large file bytes.
- Browsers upload directly to MinIO or S3 through short-lived SigV4 URLs.
- PostgreSQL stores permanent upload-session state.
- One canonical S3-compatible service layer is used by uploads, imports, exports,
  thumbnails, COG publication and later processing outputs.
- User filenames are display metadata only. Object keys are generated from UUIDs.
- Multipart completion is accepted only after contiguous part validation and an S3
  `HEAD` size check.
- AWS/native S3 uses bucket-level CORS and incomplete-multipart lifecycle rules.
- S3-compatible providers that do not implement those bucket APIs can use provider-level
  CORS and stale-upload cleanup; the committed MinIO profile is configured this way.

## Object storage implementation

Module: `backend/modules/object_storage/`

- `client.py`: cached Boto3 client using SigV4, configurable endpoint and addressing style.
- `keys.py`: conservative extension extraction and UUID-based source keys.
- `services.py`: bucket bootstrap, provider-aware CORS and cleanup configuration, multipart
  initiation, part signing, completion, abort, object inspection and deletion.
- `manage.py ensure_storage`: idempotently creates and configures the canonical bucket.

Development defaults use path-style addressing for MinIO. AWS deployments can set
`S3_ADDRESSING_STYLE=virtual`.

The bootstrap rules are intentionally provider-aware:

- a native AWS endpoint treats bucket CORS or lifecycle failures as fatal;
- a custom endpoint may return `NotImplemented`, `NotSupported` or `InvalidArgument` for
  unsupported compatibility APIs;
- in that case GeoPortalX continues only because the deployment profile must configure the
  equivalent provider-level controls.

## Upload session model

Module: `backend/modules/uploads/`

`UploadSession` stores:

- owner and optional related Resource;
- original filename, content type and declared size;
- optional declared SHA-256 checksum;
- bucket, generated object key and multipart upload ID;
- part size and expected part count;
- completed part ETags;
- actual object size, ETag and version ID;
- status, failures and timestamps;
- user-supplied metadata capped at 16 KiB.

State flow:

```text
INITIATING -> UPLOADING -> COMPLETING -> COMPLETED
                    |           |
                    |           -> FAILED when post-completion verification fails
                    -> ABORTING -> ABORTED
                    -> EXPIRED
                    -> FAILED
```

Completion failures before S3 assembles the object restore `UPLOADING` so the client may
retry. Verification failures after S3 assembly become permanent `FAILED` records. Abort
failures restore the previous state and record `UPLOAD_ABORT_FAILED`.

## API surface

```text
GET  /api/v1/uploads/
POST /api/v1/uploads/
GET  /api/v1/uploads/{upload_id}
POST /api/v1/uploads/{upload_id}/parts/{part_number}
POST /api/v1/uploads/{upload_id}/complete
POST /api/v1/uploads/{upload_id}/abort
```

Rules:

- Session authentication is currently required.
- Ordinary users can only access their own upload sessions.
- Superusers can inspect all sessions.
- A related Resource must be accessible with `EDIT` permission.
- Part numbers must be unique and contiguous from 1 through the expected count.
- Final stored size must exactly match the declared size.
- Oversized uploads, invalid SHA-256 values, unsafe filenames and oversized metadata are rejected.

## Storage settings

```text
S3_ENDPOINT_URL
S3_ACCESS_KEY
S3_SECRET_KEY
S3_SESSION_TOKEN
S3_BUCKET
S3_REGION
S3_ADDRESSING_STYLE
S3_SERVER_SIDE_ENCRYPTION
S3_PRESIGNED_URL_EXPIRY
S3_UPLOAD_SESSION_EXPIRY
S3_MULTIPART_PART_SIZE
S3_MAX_UPLOAD_SIZE
S3_ABORT_INCOMPLETE_DAYS
```

## Docker Compose

- MinIO has a health check.
- `storage-init` runs `python manage.py ensure_storage` after MinIO becomes healthy.
- Backend and Worker wait for successful storage initialization.
- The MinIO profile configures browser CORS through `MINIO_API_CORS_ALLOW_ORIGIN`.
- It configures stale multipart cleanup through `MINIO_API_STALE_UPLOADS_EXPIRY` and
  `MINIO_API_STALE_UPLOADS_CLEANUP_INTERVAL`.
- The committed MinIO image is a reproducible development/CI fixture, not a production
  recommendation. Production must use a currently supported S3 provider or a reviewed,
  security-patched MinIO build.

## Validation

Automated tests cover:

- path-traversal-resistant object keys;
- multipart part sizing;
- successful completion and permanent object metadata;
- missing-part rejection;
- size mismatch cleanup and failure recording;
- successful abort;
- abort failure recovery;
- API owner isolation;
- bucket creation and native CORS/lifecycle configuration;
- provider fallback when bucket CORS or lifecycle APIs are unsupported.

The isolated `storage-integration` CI job starts a real MinIO container and performs:

```text
ensure bucket -> initiate multipart -> generate presigned URL -> HTTP PUT part
-> complete multipart -> HEAD object -> verify size -> delete object
```

It uses `geoportalx.settings.storage_integration`, so it does not load PostGIS or require GDAL.

## Known limitations

- A declared SHA-256 checksum is recorded but not yet calculated against the completed object.
- Expired database sessions do not yet have a scheduled cleanup command; native S3 lifecycle
  or the selected provider's stale-upload cleanup removes incomplete multipart data.
- Upload completion does not yet trigger vector or raster format inspection.
- Browser upload UI, resumable client state and parallel part orchestration are not implemented.
- Resource creation and upload-session creation remain separate operations.
- API keys and OIDC clients are not yet supported.

## Next tasks

1. Register `vector-inspect` and `raster-inspect` Job handlers.
2. Materialize completed objects into a worker-readable temporary workspace.
3. Inspect GeoJSON, Shapefile ZIP and GeoPackage with GDAL/Pyogrio.
4. Inspect GeoTIFF metadata with Rasterio/GDAL.
5. Calculate SHA-256 asynchronously when the client supplies a checksum.
6. Add scheduled expiration cleanup and reconciliation commands.
7. Add the frontend multipart uploader and Job Center progress integration.
