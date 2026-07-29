# GeoPortalX CI GeoDjango bootstrap stabilization handoff

Date: 2026-07-29

Repository: `hujinghaoabcd/GeoPortalX`

Branch: `agent/raster-cog-titiler`

Draft PR: #11 — Add COG raster publishing and protected TiTiler preview

## Purpose

This maintenance unit restores a reliable standard CI path for the raster publication branch. The functional raster implementation was already validated by standard CI run #151 and dedicated Raster integration run #13. The later standard CI run #153 failed before dependency installation and before any Django test because the GitHub-hosted runner exhausted a hard 180-second timeout while downloading an unnecessarily large set of GDAL development packages.

## Recovered project state

The authoritative GeoPortalX development line is:

- PR #1 platform foundation;
- PR #2 permanent PostgreSQL Job execution lifecycle;
- PR #3 S3/MinIO multipart upload sessions;
- PR #4 vector/raster inspection;
- PR #5 dataset registration and staged vector import;
- PR #6 protected vector publication, profiles and feature queries;
- PR #7 asynchronous vector exports;
- PR #8 persistent vector styles and preview;
- PR #9 dataset version activation and rollback;
- PR #10 import reconciliation and cleanup;
- PR #11 COG raster publication and protected TiTiler preview.

The previously recalled `0.0.16`, spatial bootstrap and KDE state belongs to another repository and must not be used as GeoPortalX continuation context.

## Failure diagnosis

Run #153 had five successful jobs:

- `backend-quality`;
- `frontend`;
- `dataset-inspection`;
- `storage-integration`;
- `martin-integration`.

Only `backend-test` failed. The failing step was `Ensure GeoDjango system libraries`.

The old step installed:

```text
gdal-bin libgdal-dev libgeos-dev libproj-dev
```

with a 180-second installation timeout. The runner was still downloading the transitive development dependency set when `timeout` returned exit code 124. `uv sync`, migration drift checking and pytest never started, so this was not a functional-code failure.

## Change made

Commit `1977965263185707d6c9ad99656d04d4a22591c2` changes `.github/workflows/ci.yml` as follows:

1. Detect the actual runtime capabilities required by the test job:
   - `ogr2ogr` executable;
   - `libgdal` shared library;
   - `libgeos_c` shared library.
2. Skip installation when those capabilities are already available on the runner.
3. Install only `gdal-bin` with `--no-install-recommends` when installation is needed.
4. Remove the direct development-package installation from the CI job.
5. Increase bounded network resilience:
   - APT retries: 5;
   - HTTP/HTTPS timeout: 30 seconds;
   - update timeout: 180 seconds;
   - install timeout: 300 seconds;
   - step timeout: 8 minutes.
6. Verify `ogr2ogr`, `libgdal` and `libgeos_c` before continuing.

`gdal-bin` provides the command-line tools required by vector import tests and pulls the necessary GDAL runtime libraries. Header and compiler development packages are not required for the standard test environment because the standard dependency sync does not build the optional Python GDAL package.

## Architecture and CI principles preserved

- CI remains bounded; no unbounded package installation or retry loop was introduced.
- The standard backend test still runs on the GitHub host against a real PostGIS service.
- The test does not silently skip when GeoDjango libraries are unavailable.
- Real file inspection stays isolated in the `dataset-inspection` job.
- Real COG conversion and TiTiler behavior stay isolated in the dedicated raster workflow.
- The change affects only CI bootstrap and does not alter application behavior, migrations, APIs or persistence.

## Validation state at handoff creation

The workflow-only commit automatically triggered:

- standard CI run #154;
- Raster integration run #14.

At the time this document was written, both runs had been accepted by GitHub Actions and were queued. The previous functional baselines remain:

- standard CI run #151: fully successful;
- Raster integration run #13: fully successful;
- standard CI run #153: five successful jobs, one package-install timeout before tests.

The next agent must inspect the workflows associated with the final PR head, not rely only on the earlier run numbers recorded here.

## Current PR state

PR #11 is open, mergeable and still marked draft. Its current product scope is complete:

- immutable `RasterPublication` records;
- validated `RasterRenderSettings`;
- asynchronous COG conversion and statistics;
- deterministic MinIO/S3 publication;
- permission-protected TiTiler proxy endpoints;
- raster TileJSON, tiles and point query;
- isolated MapLibre raster preview;
- real raster integration workflow.

## Required next actions

1. Check the standard CI and Raster integration workflows for the latest PR head.
2. If all jobs pass, update the PR validation section with the final head and run numbers.
3. Mark PR #11 ready for review and merge it into `main` using the repository's established merge convention.
4. Start the next branch from updated `main`, recommended name: `agent/map-document-foundation`.
5. Implement the first Map Studio unit:
   - persistent `MapDocument` and immutable `MapDocumentVersion` models;
   - one unified Resource-backed permission boundary;
   - validated versioned document schema;
   - ordered references to current or pinned vector/raster dataset versions;
   - transactional draft/save/version activation semantics;
   - backend APIs and tests before beginning the full map editor UI.

## Recovery instructions

```text
Repository: hujinghaoabcd/GeoPortalX
PR: #11
Branch: agent/raster-cog-titiler
Functional baseline head: 7096badc67b9fc0c63be4017b44cd27e6e4410ea
CI bootstrap fix: 1977965263185707d6c9ad99656d04d4a22591c2
Latest raster handoff: docs/handoff/2026-07-29-raster-cog-titiler.md
Roadmap: docs/ROADMAP.md
This handoff: docs/handoff/2026-07-29-ci-geodjango-bootstrap.md
```

Do not resume from pyKDEX version numbers or bootstrap-KDE classes. GeoPortalX currently uses milestone-based platform development and PR numbers, with Map Studio as the next product phase.
