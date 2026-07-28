# GeoPortalX Handoff — Dataset Version Lifecycle

Date: 2026-07-28

Branch: `agent/dataset-version-lifecycle`

Pull request: #9 — `Add dataset version replacement, activation and rollback`

## Purpose

This phase adds safe version replacement for vector datasets. A new upload is registered as a candidate `DatasetVersion`, imported into independent PostGIS tables, and activated only after every selected layer is ready. The currently active version remains online throughout inspection, registration, import, cancellation and failure.

`Dataset.current_version` is the single publication pointer. Historical versions retain their tables, layer metadata and default styles so rollback is a pointer switch rather than a re-import.

## Core invariants

1. Registering or importing a candidate never changes `Dataset.current_version`.
2. A ready Dataset and Resource remain `READY` while a candidate imports.
3. Activation occurs only when the target version and all selected layers are `READY` and publication metadata is complete.
4. Activation runs inside one transaction while the Dataset and target version rows are locked.
5. Candidate failure or cancellation affects only the candidate version, candidate layers and candidate tables.
6. Historical ready tables are retained and can be reactivated.
7. Tiles, styles, features and new exports resolve only through the active version.
8. Every activation and rollback is recorded permanently.

## Data model

`DatasetVersion` now stores:

- `activated_at`
- `deactivated_at`
- `activation_count`

A new immutable `DatasetVersionActivation` model stores:

- Dataset;
- previous version, nullable for initial activation;
- target version;
- action;
- actor;
- note;
- timestamp.

Actions are `INITIAL`, `REPLACEMENT`, `ROLLBACK` and `MANUAL`.

Migration:

`backend/modules/datasets/migrations/0003_dataset_version_lifecycle.py`

## Candidate registration

Service:

`register_dataset_replacement_from_inspection(...)`

Rules:

- actor must have Resource `EDIT` permission;
- replacement currently supports vector datasets only;
- inspection Job must have succeeded and belong to the actor;
- the inspected UploadSession must reference the existing Dataset Resource;
- inspection kind must match Dataset kind;
- one unfinished candidate is permitted at a time;
- version numbers are allocated while the Dataset row is locked;
- registering the same UploadSession again is idempotent;
- each version receives new VectorLayer UUIDs and therefore independent staging tables, final tables and Martin source IDs.

## Import behavior

The existing `vector-import` Handler distinguishes initial and replacement imports.

For an initial version with no active version, Dataset becomes `IMPORTING` and Resource becomes `PROCESSING`.

For a replacement, Dataset and Resource remain `READY`. Existing tiles, features, styles, preview and downloads continue using the current version while the candidate is processed.

After all candidate layers are ready, the Handler marks the version ready and calls the common activation service. If a Worker retries after tables were imported but before pointer activation completed, the ready-version path calls activation again, repairing that crash window without re-importing data.

## Atomic activation

Service:

`activate_ready_dataset_version(...)`

Transaction sequence:

1. lock Dataset row;
2. lock target DatasetVersion row;
3. verify target belongs to Dataset;
4. require target status `READY`;
5. require every vector layer to have ready status, schema, table and tile source metadata;
6. mark previous version deactivated;
7. update target activation timestamp and count;
8. switch `Dataset.current_version`;
9. keep Dataset and Resource `READY`;
10. update vector layer counts and Resource spatial extent;
11. append a `DatasetVersionActivation` event.

PostgreSQL note: do not combine `select_for_update()` with `select_related("current_version")`. The nullable relation creates an outer join, which PostgreSQL cannot lock. The implementation locks the Dataset row while joining only the non-null Resource and reads the current version separately.

## Rollback

Rollback uses the same activation service. A historical version can be reactivated only when it belongs to the Dataset, is ready, and all its layers retain complete publication metadata.

Rollback does not run `ogr2ogr`, copy data or delete the newer version. It switches the active pointer and Resource extent, updates activation timestamps and counts, and records a `ROLLBACK` event.

## Failure and cancellation isolation

For a candidate replacement:

- generated candidate storage is removed;
- candidate layers become `FAILED` or return to `REGISTERED` after cancellation;
- candidate version becomes `FAILED` or `REGISTERED`;
- current version is unchanged;
- Dataset and Resource remain `READY`;
- active Resource extent is unchanged.

For the first version, prior initial-import failure semantics remain intact.

## Publication boundary

`backend/modules/vector_tiles/selectors.py` now requires:

`VectorLayer.version_id == Dataset.current_version_id`

This central selector is reused by TileJSON, MVT, source descriptors, quality, feature queries, identify, styles and vector export creation. Historical layer IDs therefore return `404` through publication APIs until their version is reactivated.

## API

Register replacement:

`POST /api/v1/datasets/{dataset_id}/versions`

```json
{
  "inspection_job_id": "UUID",
  "selected_layers": ["roads"],
  "start_import": true
}
```

Activate or roll back:

`POST /api/v1/datasets/{dataset_id}/versions/{version_id}/activate`

```json
{
  "note": "Rollback after validation"
}
```

Dataset detail now includes version active flags, activation timestamps, activation counts, activation history and per-layer active flags. Publication paths are returned only for active-version layers.

## Validation

Tests cover:

- candidate registration preserving the active version;
- Dataset and Resource remaining ready during candidate work;
- current layer remaining publishable before activation;
- candidate layer remaining hidden before activation;
- successful replacement switching publication visibility;
- rollback restoring historical publication;
- activation timestamps, counts and durable audit events;
- candidate failure leaving active Dataset online;
- registration and activation API responses;
- existing initial vector import compatibility;
- existing raster registration compatibility.

The full CI continues to run migration drift, Ruff, Python compilation, PostGIS pytest, real `ogr2ogr`, MinIO, Martin, Pyogrio, Rasterio, TypeScript and Vite regressions.

## Known limitations

- Replacement registration is vector-only in this phase.
- Historical tables are retained indefinitely; retention policy is not implemented.
- There is no reconciliation command yet for orphan staging/final tables or versions stuck without a live Job.
- Version comparison and schema-diff UI are not implemented.
- Only one unfinished candidate is permitted per Dataset.
- Styles are independent per version; automatic style inheritance is not implemented.

## Next phase

Implement import reconciliation and orphan cleanup:

1. inventory staging and final tables;
2. identify tables not referenced by VectorLayer;
3. detect versions stuck in importing states without a live Job;
4. provide dry-run and explicit cleanup commands;
5. reconcile stale Job, DatasetVersion and VectorLayer states;
6. protect active and historical ready tables from cleanup;
7. document scheduled cleanup operations.

After reconciliation, proceed to raster COG conversion, MinIO publication and protected TiTiler integration.
