import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile, is_zipfile

from django.conf import settings

from .exceptions import UnsafeArchiveError


@dataclass(frozen=True, slots=True)
class ArchiveInspection:
    member_count: int
    uncompressed_size: int
    compressed_size: int
    shapefile_count: int
    warnings: tuple[str, ...]


def inspect_shapefile_archive(path: Path) -> ArchiveInspection:
    """Validate a Shapefile ZIP without extracting any member to disk."""

    if not is_zipfile(path):
        raise UnsafeArchiveError("The uploaded .zip file is not a valid ZIP archive")

    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
    except (BadZipFile, OSError) as exc:
        raise UnsafeArchiveError("The uploaded ZIP archive cannot be read") from exc

    if not members:
        raise UnsafeArchiveError("The uploaded ZIP archive is empty")
    if len(members) > settings.DATASET_INSPECTION_MAX_ARCHIVE_MEMBERS:
        raise UnsafeArchiveError("The archive contains too many members")

    files: set[str] = set()
    uncompressed_size = 0
    compressed_size = 0
    for member in members:
        normalized = member.filename.replace("\\", "/")
        candidate = PurePosixPath(normalized)
        if (
            candidate.is_absolute()
            or ".." in candidate.parts
            or (candidate.parts and ":" in candidate.parts[0])
        ):
            raise UnsafeArchiveError(f"Unsafe archive member path: {member.filename}")
        if member.flag_bits & 0x1:
            raise UnsafeArchiveError("Encrypted archive members are not supported")
        if stat.S_ISLNK(member.external_attr >> 16):
            raise UnsafeArchiveError("Symbolic links are not allowed in uploaded archives")
        if member.is_dir():
            continue

        files.add(candidate.as_posix().lower())
        uncompressed_size += member.file_size
        compressed_size += member.compress_size

    if uncompressed_size > settings.DATASET_INSPECTION_MAX_UNCOMPRESSED_SIZE:
        raise UnsafeArchiveError("The archive expands beyond the configured safety limit")
    compression_ratio = uncompressed_size / max(compressed_size, 1)
    if compression_ratio > settings.DATASET_INSPECTION_MAX_COMPRESSION_RATIO:
        raise UnsafeArchiveError("The archive compression ratio exceeds the safety limit")

    shapefiles = sorted(name for name in files if name.endswith(".shp"))
    if not shapefiles:
        raise UnsafeArchiveError("The archive does not contain a Shapefile .shp member")

    warnings: list[str] = []
    for shapefile in shapefiles:
        stem = shapefile[:-4]
        missing = [suffix for suffix in (".dbf", ".shx") if f"{stem}{suffix}" not in files]
        if missing:
            raise UnsafeArchiveError(
                f"Shapefile {shapefile} is missing required sidecars: {', '.join(missing)}"
            )
        if f"{stem}.prj" not in files:
            warnings.append(f"Shapefile {shapefile} has no .prj coordinate reference file")

    return ArchiveInspection(
        member_count=len(members),
        uncompressed_size=uncompressed_size,
        compressed_size=compressed_size,
        shapefile_count=len(shapefiles),
        warnings=tuple(warnings),
    )
