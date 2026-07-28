# GeoPortalX

GeoPortalX is a lightweight, full-featured geospatial data portal and web mapping platform.

> 当前状态：项目初始化阶段。二维地图引擎固定为 MapLibre GL JS；项目不依赖 OMap 或 OpenLayers。

## 目标

GeoPortalX 面向空间数据管理、目录检索、在线制图、地图发布、数据共享、空间分析与应用构建。项目强调功能完整、架构清晰和部署简洁，避免重复维护资源、权限、元数据和任务状态。

## 技术基线

- Frontend: Vue 3, TypeScript, Vite, Pinia, MapLibre GL JS
- Backend: Django, Django Ninja, PostgreSQL/PostGIS
- Tasks: Celery, Redis
- Object storage: MinIO/S3
- Vector publishing: PostGIS, Martin, MVT
- Raster publishing: COG, TiTiler
- Catalog standards: OGC API - Records, CSW 2.0.2 via pycsw, STAC
- Feature access: OGC API - Features
- Deployment: Docker Compose
- Optional compatibility: GeoServer

## 核心原则

1. 一个资源只保存一次。
2. 一个权限只由平台统一判断。
3. 一个任务状态只在 PostgreSQL 中永久记录。
4. 矢量数据进入 PostGIS，栅格数据优先转为 COG。
5. Martin、TiTiler、pycsw 和其他协议服务是统一资源模型之上的适配层。
6. 二维地图只使用 MapLibre GL JS。

详细设计将在 `docs/` 中持续维护。
