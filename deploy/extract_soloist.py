"""Extract the single Soloist executable without trusting archive paths."""

from __future__ import annotations

import gzip
import http.client
import os
import shutil
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

MAX_SOLOIST_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_DOCUMENTATION_BYTES = 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60
ALLOWED_NAMES = {"soloist", "./soloist"}
ALLOWED_DOCUMENTATION_NAMES = {"CHANGELOG.md", "THIRD_PARTY_LICENSES.txt"}
MAX_UNCOMPRESSED_ARCHIVE_BYTES = (
    MAX_SOLOIST_BYTES
    + len(ALLOWED_DOCUMENTATION_NAMES) * MAX_DOCUMENTATION_BYTES
    + 64 * 1024
)
SOLOIST_ARCHIVE_HOST = "soloist-builds.spotifycdn.com"


class _BoundedArchiveReader:
    """Bound decompressed bytes and individual reads passed to ``tarfile``."""

    def __init__(self, stream: gzip.GzipFile) -> None:
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            raise ValueError("Soloist archive requested an unbounded read")
        if size > DOWNLOAD_CHUNK_BYTES:
            raise ValueError("Soloist archive contains an oversized metadata record")
        remaining = MAX_UNCOMPRESSED_ARCHIVE_BYTES - self.tell()
        if remaining < 0:
            raise ValueError("Soloist archive exceeds the decompressed size limit")
        data = self._stream.read(min(size, remaining + 1))
        if len(data) > remaining:
            raise ValueError("Soloist archive exceeds the decompressed size limit")
        return data

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        if whence == os.SEEK_SET:
            target = offset
        elif whence == os.SEEK_CUR:
            target = self.tell() + offset
        else:
            raise ValueError("Soloist archive requested an unsupported seek")
        if target < 0:
            raise ValueError("Soloist archive requested a negative seek")
        if target > MAX_UNCOMPRESSED_ARCHIVE_BYTES:
            raise ValueError("Soloist archive exceeds the decompressed size limit")
        return self._stream.seek(offset, whence)

    def tell(self) -> int:
        return self._stream.tell()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True


@contextmanager
def _open_tar_archive(archive_path: Path) -> Iterator[tarfile.TarFile]:
    with (
        archive_path.open("rb") as compressed,
        gzip.GzipFile(fileobj=compressed, mode="rb") as decompressed,
    ):
        reader = _BoundedArchiveReader(decompressed)
        with tarfile.open(fileobj=reader, mode="r:") as archive:
            yield archive


def _is_official_soloist_archive_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme.lower() == "https"
        and parsed.hostname is not None
        and parsed.hostname.lower() == SOLOIST_ARCHIVE_HOST
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )


class _HTTPSRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        if urllib.parse.urlsplit(newurl).scheme.lower() != "https":
            raise urllib.error.HTTPError(
                newurl, code, "refusing non-HTTPS Soloist redirect", headers, fp
            )
        if not _is_official_soloist_archive_url(req.full_url) or not (
            _is_official_soloist_archive_url(newurl)
        ):
            raise urllib.error.HTTPError(
                newurl,
                code,
                "refusing redirect outside the official Spotify Soloist archive origin",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_archive_url(url: str):
    if urllib.parse.urlsplit(url).scheme.lower() != "https":
        raise ValueError("Soloist archive URL must use HTTPS")
    if not _is_official_soloist_archive_url(url):
        raise ValueError("Soloist archive URL must use the official Spotify HTTPS host")
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    response = urllib.request.build_opener(_HTTPSRedirectHandler).open(
        request, timeout=DOWNLOAD_TIMEOUT_SECONDS
    )
    if not _is_official_soloist_archive_url(response.geturl()):
        response.close()
        raise ValueError("Soloist archive download left the official Spotify HTTPS origin")
    return response


def _download_archive_once(url: str, archive_path: Path) -> None:
    response = _open_archive_url(url)
    archive_created = False
    try:
        with response:
            content_encoding = response.headers.get("Content-Encoding", "identity")
            if content_encoding.strip().lower() not in {"", "identity"}:
                raise ValueError("Soloist archive used unexpected HTTP compression")

            length_header = response.headers.get("Content-Length")
            expected_length: int | None = None
            if length_header is not None:
                try:
                    expected_length = int(length_header)
                except ValueError as exc:
                    raise ValueError("Soloist archive has an invalid Content-Length") from exc
                if expected_length < 0 or expected_length > MAX_ARCHIVE_BYTES:
                    raise ValueError("Soloist archive exceeds the size limit")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(archive_path, flags, 0o600)
            archive_created = True
            total = 0
            with os.fdopen(descriptor, "wb") as output:
                while True:
                    chunk = response.read(
                        min(DOWNLOAD_CHUNK_BYTES, MAX_ARCHIVE_BYTES - total + 1)
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ARCHIVE_BYTES:
                        raise ValueError("Soloist archive exceeds the size limit")
                    output.write(chunk)
            if total == 0:
                raise ValueError("Soloist archive is empty")
            if expected_length is not None and total != expected_length:
                raise ValueError("Soloist archive size did not match Content-Length")
    except BaseException:
        if archive_created:
            archive_path.unlink(missing_ok=True)
        raise


def download_archive(url: str, archive_path: Path) -> None:
    """Download one HTTPS archive with bounded streaming and transient retries."""

    for attempt in range(4):
        try:
            _download_archive_once(url, archive_path)
            return
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {408, 425, 429} or 500 <= exc.code < 600
            if not retryable or attempt == 3:
                raise
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            if attempt == 3:
                raise
        time.sleep(2)


def extract_soloist(archive_path: Path, destination: Path) -> None:
    """Write one bounded regular ``soloist`` member to a new file."""

    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("Soloist archive exceeds the size limit")

    with _open_tar_archive(archive_path) as archive:
        names: set[str] = set()
        executable: tarfile.TarInfo | None = None
        for member_count, entry in enumerate(archive, start=1):
            if member_count > 1 + len(ALLOWED_DOCUMENTATION_NAMES):
                raise ValueError("Soloist archive contains an invalid member count")
            if not entry.isfile() or entry.name not in (
                ALLOWED_NAMES | ALLOWED_DOCUMENTATION_NAMES
            ):
                raise ValueError("Soloist archive contains an unexpected member")
            if entry.name in names:
                raise ValueError("Soloist archive contains duplicate members")
            names.add(entry.name)
            if entry.name in ALLOWED_DOCUMENTATION_NAMES and (
                entry.size < 0 or entry.size > MAX_DOCUMENTATION_BYTES
            ):
                raise ValueError("Soloist documentation has an invalid size")
            if entry.name in ALLOWED_NAMES:
                if executable is not None:
                    raise ValueError(
                        "Soloist archive must contain one regular soloist file"
                    )
                if entry.size <= 0 or entry.size > MAX_SOLOIST_BYTES:
                    raise ValueError("Soloist executable has an invalid size")
                executable = entry

        if executable is None:
            raise ValueError("Soloist archive must contain one regular soloist file")
        source = archive.extractfile(executable)
        if source is None:
            raise ValueError("Soloist archive member could not be read")

        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(destination, flags, 0o700)
        with source, os.fdopen(descriptor, "wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def main(argv: list[str]) -> int:
    if len(argv) == 5 and argv[1] == "--download":
        archive_path = Path(argv[3])
        try:
            download_archive(argv[2], archive_path)
            extract_soloist(archive_path, Path(argv[4]))
        except (OSError, tarfile.TarError, ValueError) as exc:
            print(f"Could not safely download or extract Soloist: {exc}", file=sys.stderr)
            return 1
        return 0
    if len(argv) != 3:
        print(
            "usage: extract_soloist.py ARCHIVE DESTINATION | "
            "extract_soloist.py --download HTTPS_URL ARCHIVE DESTINATION",
            file=sys.stderr,
        )
        return 2
    try:
        extract_soloist(Path(argv[1]), Path(argv[2]))
    except (OSError, tarfile.TarError, ValueError) as exc:
        print(f"Could not safely extract Soloist: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
