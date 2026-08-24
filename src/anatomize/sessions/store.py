"""Bounded session I/O and an atomic, recoverable derived-generation store."""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from anatomize._artifacts import (
    BoundedJsonError,
    JsonLimits,
    atomic_write_bytes,
    parse_bounded_json_object,
    sha256_digest,
)
from anatomize._errors import AnatomizeError
from anatomize.sessions.models import (
    SESSION_BUNDLE_ARTIFACT_TYPE,
    SESSION_BUNDLE_SCHEMA_VERSION,
    ReviewSessionBundle,
    SessionStatus,
    canonical_session_bundle_bytes,
)

DEFAULT_MAX_SESSION_BUNDLE_BYTES = 128 * 1024 * 1024
DEFAULT_MAX_SESSION_BUNDLE_DEPTH = 64
DEFAULT_MAX_SESSION_BUNDLE_VALUES = 2_000_000
DEFAULT_MAX_SESSION_BUNDLE_STRING_BYTES = DEFAULT_MAX_SESSION_BUNDLE_BYTES


class SessionArtifactError(AnatomizeError):
    """Stable, actionable session artifact failure."""

class SessionStoreError(AnatomizeError):
    """Stable derived-store publication or recovery failure."""

def parse_session_bundle(
    raw: bytes,
    *,
    max_bytes: int = DEFAULT_MAX_SESSION_BUNDLE_BYTES,
    max_depth: int = DEFAULT_MAX_SESSION_BUNDLE_DEPTH,
    max_values: int = DEFAULT_MAX_SESSION_BUNDLE_VALUES,
    max_string_bytes: int = DEFAULT_MAX_SESSION_BUNDLE_STRING_BYTES,
) -> ReviewSessionBundle:
    """Parse one bounded, exact-schema, self-validating portable session."""
    try:
        payload = parse_bounded_json_object(
            raw,
            limits=JsonLimits(
                max_bytes=max_bytes,
                max_depth=max_depth,
                max_values=max_values,
                max_string_bytes=max_string_bytes,
            ),
        )
    except BoundedJsonError as error:
        raise _session_json_error(error) from error
    if payload.get("artifact_type") != SESSION_BUNDLE_ARTIFACT_TYPE:
        raise SessionArtifactError(
            "session_bundle_type_incompatible",
            f"Expected artifact type {SESSION_BUNDLE_ARTIFACT_TYPE!r}",
            remediation="Supply an Anatomize portable session bundle.",
        )
    if payload.get("schema_version") != SESSION_BUNDLE_SCHEMA_VERSION:
        raise SessionArtifactError(
            "session_bundle_schema_incompatible",
            f"Expected session bundle schema {SESSION_BUNDLE_SCHEMA_VERSION!r}",
            remediation="Rebuild the session with the current Anatomize release.",
        )
    try:
        return ReviewSessionBundle.model_validate(payload)
    except ValidationError as error:
        raise SessionArtifactError(
            "session_bundle_invalid",
            f"Session bundle failed validation ({error.error_count()} errors)",
            remediation="Inspect its artifact digests and regenerate the session.",
        ) from error


def load_session_bundle(
    path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_SESSION_BUNDLE_BYTES,
) -> ReviewSessionBundle:
    """Read and validate one bounded portable session."""
    try:
        size = path.stat().st_size
        if size > max_bytes:
            raise SessionArtifactError(
                "session_bundle_too_large",
                f"Session bundle is {size} bytes; limit is {max_bytes} bytes",
                remediation="Reduce the session scope or raise an explicit trusted limit.",
            )
        return parse_session_bundle(path.read_bytes(), max_bytes=max_bytes)
    except SessionArtifactError:
        raise
    except OSError as error:
        raise SessionArtifactError(
            "session_bundle_unreadable",
            f"Cannot read session bundle: {path.name}",
            remediation="Check the artifact path and permissions, then retry.",
        ) from error


def write_session_bundle(bundle: ReviewSessionBundle, path: Path) -> None:
    """Atomically export one canonical portable session."""
    _write_bytes(path, canonical_session_bundle_bytes(bundle))


class SessionStore:
    """Immutable generations selected by atomic pointers with one-writer locking."""

    def __init__(
        self,
        root: Path,
        *,
        max_bundle_bytes: int = DEFAULT_MAX_SESSION_BUNDLE_BYTES,
    ) -> None:
        if max_bundle_bytes <= 0:
            raise ValueError("session store bundle limit must be positive")
        self.root = root.resolve()
        self.max_bundle_bytes = max_bundle_bytes

    def load(self, *, recover: bool = True) -> ReviewSessionBundle:
        """Load current immutable state, optionally falling back to the prior valid generation."""
        failures: list[str] = []
        pointers = ("CURRENT", "PREVIOUS") if recover else ("CURRENT",)
        for pointer_name in pointers:
            try:
                generation = self._read_pointer(pointer_name)
                return self._load_generation(generation)
            except (OSError, SessionArtifactError, SessionStoreError) as error:
                failures.append(f"{pointer_name.lower()}={error}")
        raise SessionStoreError(
            "session_cache_unavailable",
            "No valid session cache generation is available (" + "; ".join(failures) + ")",
            remediation="Import or rebuild a complete portable session bundle.",
        )

    def publish(self, bundle: ReviewSessionBundle, *, lock_timeout: float = 30.0) -> str:
        """Publish exactly one complete generation or leave the previous pointer unchanged."""
        with self._writer_lock(timeout=lock_timeout):
            return self._publish_locked(bundle)

    def refresh(
        self,
        builder: Callable[[ReviewSessionBundle | None], ReviewSessionBundle],
        *,
        lock_timeout: float = 30.0,
    ) -> ReviewSessionBundle:
        """Serialize refresh attempts and publish only a complete builder result."""
        with self._writer_lock(timeout=lock_timeout):
            try:
                previous = self.load()
            except SessionStoreError:
                previous = None
            candidate = builder(previous)
            self._publish_locked(candidate)
            return candidate

    def materialize(self, bundle: ReviewSessionBundle, destination: Path) -> None:
        """Reconstruct all portable artifacts under a selected destination."""
        target_root = destination.resolve()
        blobs = {item.artifact_id: item for item in bundle.blobs}
        for artifact in bundle.manifest.artifacts:
            target = (target_root / artifact.portable_path).resolve()
            try:
                target.relative_to(target_root)
            except ValueError as error:
                raise SessionStoreError(
                    "session_artifact_path_escape",
                    f"Artifact path escapes materialization root: {artifact.portable_path}",
                    remediation="Reject the bundle and regenerate portable paths.",
                ) from error
            _write_bytes(target, blobs[artifact.artifact_id].decoded())

    def _publish_locked(self, bundle: ReviewSessionBundle) -> str:
        if bundle.manifest.status is not SessionStatus.COMPLETE:
            raise SessionStoreError(
                "session_incomplete",
                f"Refusing to publish {bundle.manifest.status.value} session as current",
                remediation="Resolve, omit explicitly, or cancel failed provider work before publication.",
            )
        raw = canonical_session_bundle_bytes(bundle)
        if len(raw) > self.max_bundle_bytes:
            raise SessionStoreError(
                "session_bundle_too_large",
                f"Session bundle is {len(raw)} bytes; limit is {self.max_bundle_bytes} bytes",
                remediation="Reduce the session scope or raise an explicit trusted limit.",
            )
        generation = sha256_digest(raw).removeprefix("sha256:")
        self.root.mkdir(parents=True, exist_ok=True)
        generations = self.root / "generations"
        generations.mkdir(parents=True, exist_ok=True)
        destination = generations / generation
        staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=generations))
        try:
            _write_bytes(staging / "bundle.json", raw)
            staged = load_session_bundle(staging / "bundle.json", max_bytes=self.max_bundle_bytes)
            if canonical_session_bundle_bytes(staged) != raw:
                raise SessionStoreError(
                    "session_staging_corrupt",
                    "Staged session bytes changed before publication",
                    remediation="Check the storage medium and rebuild from the portable bundle.",
                )
            if destination.exists():
                existing = self._load_generation(generation)
                if canonical_session_bundle_bytes(existing) != raw:
                    raise SessionStoreError(
                        "session_generation_collision",
                        f"Generation {generation} contains different bytes",
                        remediation="Quarantine the corrupt cache and rebuild from the portable bundle.",
                    )
            else:
                os.replace(staging, destination)
                staging = destination
            current = self._valid_pointer("CURRENT")
            if current is not None and current != generation:
                _write_bytes(self.root / "PREVIOUS", (current + "\n").encode("ascii"))
            _write_bytes(self.root / "CURRENT", (generation + "\n").encode("ascii"))
        finally:
            if staging.exists() and staging != destination:
                shutil.rmtree(staging, ignore_errors=True)
        return generation

    def _valid_pointer(self, name: str) -> str | None:
        try:
            generation = self._read_pointer(name)
            self._load_generation(generation)
        except (OSError, SessionArtifactError, SessionStoreError):
            return None
        return generation

    def _read_pointer(self, name: str) -> str:
        try:
            value = (self.root / name).read_text(encoding="ascii").strip()
        except OSError as error:
            raise SessionStoreError(
                "session_pointer_unreadable",
                f"Cannot read {name} session pointer",
                remediation="Recover the prior generation or import a portable session bundle.",
            ) from error
        if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
            raise SessionStoreError(
                "session_pointer_corrupt",
                f"{name} session pointer is corrupt",
                remediation="Recover the prior generation or import a portable session bundle.",
            )
        return value

    def _load_generation(self, generation: str) -> ReviewSessionBundle:
        path = self.root / "generations" / generation / "bundle.json"
        bundle = load_session_bundle(path, max_bytes=self.max_bundle_bytes)
        actual = sha256_digest(canonical_session_bundle_bytes(bundle)).removeprefix("sha256:")
        if actual != generation:
            raise SessionStoreError(
                "session_generation_corrupt",
                f"Session generation digest does not match directory identity: {generation}",
                remediation="Recover the previous generation or rebuild from the portable bundle.",
            )
        return bundle

    @contextmanager
    def _writer_lock(self, *, timeout: float) -> Iterator[None]:
        """Hold an operating-system lock; process death releases it automatically."""
        if timeout < 0:
            raise ValueError("session lock timeout cannot be negative")
        self.root.mkdir(parents=True, exist_ok=True)
        lock_path = self.root / ".refresh.lock"
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        if os.name == "nt" and os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while True:
                if _try_lock(descriptor):
                    acquired = True
                    break
                if time.monotonic() >= deadline:
                    raise SessionStoreError(
                        "session_refresh_busy",
                        "Another session refresh holds the writer lock",
                        remediation="Wait for the active refresh to finish or retry with a longer timeout.",
                    )
                time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            yield
        finally:
            if acquired:
                _unlock(descriptor)
            os.close(descriptor)


def _try_lock(descriptor: int) -> bool:
    """Acquire one portable non-blocking exclusive file lock."""
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(descriptor, 0, os.SEEK_SET)
            getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        return False
    return True


def _unlock(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(descriptor, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_UNLCK"), 1)
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def _write_bytes(path: Path, raw: bytes) -> None:
    """Narrow publication seam used by recovery qualification."""
    atomic_write_bytes(path, raw)


def _session_json_error(error: BoundedJsonError) -> SessionArtifactError:
    codes = {
        "too_large": "session_bundle_too_large",
        "corrupt": "session_bundle_corrupt",
        "incomplete": "session_bundle_incomplete",
        "depth_limit": "session_bundle_depth_limit",
        "value_limit": "session_bundle_value_limit",
        "string_limit": "session_bundle_string_limit",
    }
    return SessionArtifactError(
        codes[error.code],
        f"Session bundle {error}",
        remediation="Re-export a bounded session or increase the explicit trusted limit.",
    )
