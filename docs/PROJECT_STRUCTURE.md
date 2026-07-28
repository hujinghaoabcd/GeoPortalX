# GeoPortalX Project Structure

```text
GeoPortalX/
├── backend/
│   ├── manage.py
│   ├── pyproject.toml
│   ├── geoportalx/
│   │   ├── settings/
│   │   │   ├── base.py
│   │   │   ├── development.py
│   │   │   ├── test.py
│   │   │   └── production.py
│   │   ├── urls.py
│   │   ├── api.py
│   │   ├── asgi.py
│   │   ├── wsgi.py
│   │   └── celery.py
│   ├── modules/
│   │   ├── accounts/
│   │   ├── organizations/
│   │   ├── permissions/
│   │   ├── resources/
│   │   ├── metadata/
│   │   ├── catalog/
│   │   ├── datasets/
│   │   ├── vectors/
│   │   ├── rasters/
│   │   ├── maps/
│   │   ├── scenes/
│   │   ├── dashboards/
│   │   ├── stories/
│   │   ├── applications/
│   │   ├── services/
│   │   ├── jobs/
│   │   ├── processing/
│   │   ├── lineage/
│   │   ├── audit/
│   │   └── notifications/
│   ├── protocols/
│   │   ├── ogc_records/
│   │   ├── ogc_features/
│   │   ├── ogc_tiles/
│   │   ├── csw/
│   │   └── stac/
│   └── tests/
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── src/
│   │   ├── app/
│   │   ├── api/
│   │   ├── router/
│   │   ├── stores/
│   │   ├── components/
│   │   ├── layouts/
│   │   ├── pages/
│   │   ├── map/
│   │   │   ├── core/
│   │   │   ├── sources/
│   │   │   ├── layers/
│   │   │   ├── styles/
│   │   │   ├── interactions/
│   │   │   ├── controls/
│   │   │   ├── popups/
│   │   │   ├── legends/
│   │   │   ├── filters/
│   │   │   ├── time/
│   │   │   ├── print/
│   │   │   └── serialization/
│   │   ├── types/
│   │   └── main.ts
│   └── tests/
├── services/
│   ├── titiler/
│   ├── gateway/
│   └── geoserver/             # optional profile
├── deploy/
│   ├── compose.yaml
│   ├── compose.geoserver.yaml
│   ├── compose.monitoring.yaml
│   └── env/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TECH_STACK.md
│   ├── PROJECT_STRUCTURE.md
│   ├── DEVELOPMENT_PLAN.md
│   ├── ROADMAP.md
│   ├── adr/
│   └── handoff/
├── scripts/
├── .github/
│   └── workflows/
├── .editorconfig
├── .gitignore
├── Makefile
└── README.md
```

## Backend module conventions

Each domain module may contain:

```text
module/
├── apps.py
├── models.py
├── admin.py
├── api.py
├── schemas.py
├── selectors.py
├── services.py
├── permissions.py
├── tasks.py
├── events.py
├── migrations/
└── tests/
```

Responsibilities:

- `models.py`: persistence structure and local invariants;
- `selectors.py`: read/query operations;
- `services.py`: state-changing business operations and transactions;
- `api.py`: transport adaptation only;
- `schemas.py`: external request/response contracts;
- `tasks.py`: Celery entry points that call services;
- `permissions.py`: module-specific policies using the central permission engine;
- `events.py`: explicit domain events without hidden signal-based workflows.

Django signals are restricted to framework integration. Multi-step business workflows must be visible in service methods.

## Frontend map boundary

`frontend/src/map/` is an internal GeoPortalX feature module, not an independent SDK.

It wraps MapLibre GL JS only where platform behavior requires abstraction:

- document loading and saving;
- layer tree synchronization;
- resource-aware source creation;
- permission-aware editing;
- popup, legend, filter and time configuration;
- stable cleanup and event lifecycle.

Direct MapLibre types remain available internally. GeoPortalX does not reproduce the entire MapLibre API.

## Documentation continuity

After each major development task, create or update a file under `docs/handoff/` containing:

- current architecture decisions;
- files and behavior added;
- validation performed;
- known limitations;
- next tasks in priority order.

This allows work to continue safely across conversations and contributors.
