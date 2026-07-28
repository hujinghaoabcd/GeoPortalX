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

## Foundation implemented

- Architecture, technology stack, project structure, development plan and roadmap.
- Django settings split, ASGI/WSGI entry points and Django Ninja health API.
- Custom UUID user model and reviewed initial migration.
- Organization, membership, role, organization-group and group-membership models.
- Transactional organization creation, role update and membership deactivation services.
- Unified Resource model with owner, optional organization, visibility, lifecycle and extents.
- Central ResourcePermission model for users, groups, organizations, authenticated users,
  anonymous users and share links.
- Central permission evaluator with permission implication, visibility rules and expiry checks.
- Persistent Job ledger related to users and optional resources.
- Row-locked Job state transitions for queueing, running, success, failure and cancellation.
- Vue/TypeScript frontend with MapLibre GL JS 6 map bootstrap.
- Docker Compose services for PostGIS, Redis, MinIO, backend, workers, Martin, TiTiler and frontend.
- Reproducible `uv.lock` and `pnpm-lock.yaml` files.
- GitHub Actions quality, migration, backend-test, frontend-typecheck and production-build checks.

## Tests implemented

- API health response.
- Organization owner membership invariant.
- Member role reactivation and owner deactivation protection.
- Resource owner permissions.
- Public visibility and organization visibility.
- Permission inheritance and expired grants.
- Job lifecycle, invalid transitions and failure details.

## CI validation completed

- Frozen backend and frontend dependency resolution.
- Ruff validation.
- Python source compilation.
- GeoDjango/PostGIS migration consistency check.
- Backend pytest suite against PostGIS.
- Vue TypeScript type checking.
- Vite production build.

## Known limitations

- Full Docker Compose service-to-service startup has not yet been exercised in CI.
- Authenticated organization and resource CRUD APIs are not yet exposed.
- Permission-filtered Resource querysets have not yet been optimized for catalog listings.
- Martin authorization gateway and source registration are not yet implemented.
- TiTiler currently uses direct development credentials; signed/authorized asset routing is pending.
- The sample frontend style uses MapLibre demo tiles and will be replaced by configurable basemaps.
- MinIO and TiTiler image tags require production pinning after integration testing.

## Next tasks

1. Add permission-filtered Resource queryset and selector services.
2. Add authenticated organization and resource APIs.
3. Integrate Celery tasks with persistent Job progress and cancellation.
4. Add MinIO/S3 storage abstraction and bucket bootstrap command.
5. Add upload-session models and safe multipart upload flow.
6. Run the full Compose stack and add service health checks.
7. Review and squash-merge bootstrap PR #1 when the foundation is accepted.
