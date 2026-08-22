"""Describe how precisely a cached-data blob matches its V8 embedder."""

from __future__ import annotations

from .header import CacheHeader
from .profiles import Profile


def _has_distinct_runtime_variants(profile: Profile) -> bool:
    variants = {
        tuple(names)
        for names in profile.runtime_variants.values()
    }
    return len(variants) > 1


def build_compatibility_report(
    header: CacheHeader,
    profile: Profile,
    runtime_variant: str | None = None,
    snapshot_checksum: int | None = None,
) -> dict[str, object]:
    """Return a conservative compatibility result for a parsed header.

    A version hash identifies V8's bytecode contract, but it does not identify
    the embedder's CPU flags, startup snapshot, or runtime table variant. Those
    values are reported separately so callers can distinguish an exact match
    from a version-only recovery.
    """
    mapped_variant = profile.runtime_variant_by_flags_hash.get(header.flags_hash)
    if header.raw_payload:
        status = "version-only"
        selected_variant = runtime_variant or profile.runtime_default_variant
        variant_source = "override" if runtime_variant else "profile-default"
        flag_status = "not-present"
    elif mapped_variant is not None:
        status = "exact-runtime-variant"
        selected_variant = runtime_variant or mapped_variant
        variant_source = "override" if runtime_variant else "flags-hash"
        flag_status = "known"
    elif _has_distinct_runtime_variants(profile):
        status = "unknown-flags"
        selected_variant = runtime_variant or profile.runtime_default_variant
        variant_source = "override" if runtime_variant else "profile-default"
        flag_status = "unknown"
    else:
        status = "version-only"
        selected_variant = runtime_variant or profile.runtime_default_variant
        variant_source = "override" if runtime_variant else "profile-default"
        flag_status = "not-discriminating"


    if selected_variant not in profile.runtime_variants:
        choices = ", ".join(sorted(profile.runtime_variants))
        raise ValueError(
            f"unknown runtime variant {selected_variant}; choose from {choices}"
        )

    if header.ro_snapshot_checksum is None:
        snapshot_status = "not-applicable"
    elif snapshot_checksum is None:
        snapshot_status = "required"
    elif snapshot_checksum == header.ro_snapshot_checksum:
        snapshot_status = "matched"
    else:
        snapshot_status = "mismatch"

    if snapshot_status == "required":
        status = "snapshot-unverified"
    elif snapshot_status == "mismatch":
        status = "snapshot-mismatch"

    warnings: list[str] = []
    if flag_status == "unknown":
        warnings.append(
            "flags hash is not mapped to a unique runtime table; default variant used"
        )
    if snapshot_status == "required":
        warnings.append("cached data contains a read-only snapshot checksum; snapshot not verified")
    elif snapshot_status == "mismatch":
        warnings.append("provided startup snapshot checksum does not match cached data")

    return {
        "status": status,
        "version": profile.version,
        "version_hash": header.version_hash,
        "source_hash": header.source_hash,
        "flags_hash": header.flags_hash,
        "cpu_features": header.cpu_features,
        "external_reference_count": header.external_reference_count,
        "snapshot_checksum": header.ro_snapshot_checksum,
        "snapshot_status": snapshot_status,
        "runtime_variant": selected_variant,
        "runtime_variant_source": variant_source,
        "flags_status": flag_status,
        "header_format": header.format,
        "header_size": header.header_size,
        "warnings": warnings,
    }


__all__ = ["build_compatibility_report"]
