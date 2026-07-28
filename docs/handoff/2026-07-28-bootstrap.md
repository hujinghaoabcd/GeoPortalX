# GeoPortalX Bootstrap Handoff — 2026-07-28

## Decisions fixed

- New repository: `hujinghaoabcd/GeoPortalX`.
- The project is independent from GeomapHub, OMap and OpenLayers projects.
- MapLibre GL JS is the only 2D map renderer.
- Backend uses a Django modular monolith.
- Celery and Redis are part of the standard deployment.
- PostGIS stores canonical editable vector data.
- COG on MinIO/S3 stores canonical published raster data.
- Martin publishes vector tiles; TiTiler publishes raster tiles.
- Django owns users, resources, permissions, metadata and permanent job state.
- pycsw provides CSW compatibility from the internal metadata model.
- GeoServer is optional and limited to legacy service compatibility.

## Foundation created

- Architecture, technology stack, project structure, development plan and roadmap.
- Django settings split, ASGI/WSGI entry points and Django Ninja health API.
- Custom UUID user model defined before first migration.
- Initial unified Resource model.
- Initial persistent Job model and Celery configuration.
- Vue/TypeScript frontend with MapLibre GL JS 6 map bootstrap.
- Docker Compose services for PostGIS, Redis, MinIO, backend, workers, Martin, TiTiler and frontend.
- Initial GitHub Actions workflow.

## Validation performed locally

- Python source compilation.
- YAML parsing for Compose and GitHub Actions.
- JSON parsing for frontend package manifest.
- Static inspection of imports and project paths.

Full dependency installation and container startup remain CI/runtime validation tasks after the files are committed.

## Known limitations

- Initial migrations have not yet been generated.
- Organization and permission models are not yet implemented.
- Martin permission gateway and source registration are not yet implemented.
- TiTiler currently uses direct development credentials; signed/authorized asset routing is pending.
- The sample frontend style uses MapLibre demo tiles and will be replaced by configurable basemaps.
- MinIO and TiTiler image tags require production pinning after first successful integration test.

## Next tasks

1. Generate and review initial migrations.
2. Add organization, membership and central permission models.
3. Add Resource selectors/services and authenticated API endpoints.
4. Add Job creation/progress services and worker lifecycle updates.
5. Add storage abstraction and bucket bootstrap command.
6. Make Compose startup deterministic and add service health checks.
7. Open and review the bootstrap pull request before merging.
