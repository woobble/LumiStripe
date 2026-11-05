//! Bounded downloader and archive reader for the optional Spotify Soloist binary.

use flate2::read::MultiGzDecoder;
use futures_util::StreamExt;
use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;
use std::time::Duration;
use tokio::time::{sleep, timeout};
use url::Url;

const MAX_SOLOIST_BYTES: u64 = 512 * 1024 * 1024;
const MAX_ARCHIVE_BYTES: u64 = 512 * 1024 * 1024;
const MAX_DOCUMENTATION_BYTES: u64 = 1024 * 1024;
const MAX_UNCOMPRESSED_ARCHIVE_BYTES: u64 =
    MAX_SOLOIST_BYTES + 2 * MAX_DOCUMENTATION_BYTES + 64 * 1024;
const DOWNLOAD_CHUNK_BYTES: usize = 1024 * 1024;
const MAX_REDIRECTS: usize = 5;
const OFFICIAL_HOST: &str = "soloist-builds.spotifycdn.com";
const ALLOWED_DOCUMENTATION: [&[u8]; 2] = [b"CHANGELOG.md", b"THIRD_PARTY_LICENSES.txt"];

type Result<T> = std::result::Result<T, String>;

fn official_archive_url(raw: &str) -> Result<Url> {
    let url = Url::parse(raw).map_err(|_| "Soloist archive URL is invalid.".to_owned())?;
    if !is_official_url(&url) {
        return Err("Soloist archive URL must use the official Spotify HTTPS host.".to_owned());
    }
    Ok(url)
}

fn is_official_url(url: &Url) -> bool {
    url.scheme() == "https"
        && url
            .host_str()
            .is_some_and(|host| host.eq_ignore_ascii_case(OFFICIAL_HOST))
        && url.port_or_known_default() == Some(443)
        && url.username().is_empty()
        && url.password().is_none()
}

fn download_client() -> Result<reqwest::Client> {
    reqwest::Client::builder()
        .connect_timeout(Duration::from_secs(10))
        .redirect(reqwest::redirect::Policy::custom(|attempt| {
            if attempt.previous().len() < MAX_REDIRECTS && is_official_url(attempt.url()) {
                attempt.follow()
            } else {
                attempt.stop()
            }
        }))
        .build()
        .map_err(|_| "Could not initialize the Soloist download client.".to_owned())
}

async fn download_once(client: &reqwest::Client, url: &Url, archive_path: &Path) -> Result<()> {
    let request = client
        .get(url.clone())
        .header(reqwest::header::ACCEPT_ENCODING, "identity");
    let response = timeout(Duration::from_secs(60), request.send())
        .await
        .map_err(|_| "Soloist archive download timed out.".to_owned())?
        .map_err(|_| "Soloist archive download failed.".to_owned())?;
    if !is_official_url(response.url()) {
        return Err("Soloist archive download left the official Spotify HTTPS origin.".to_owned());
    }
    let status = response.status();
    if !status.is_success() {
        return Err(format!(
            "Soloist archive server returned HTTP {}.",
            status.as_u16()
        ));
    }
    if response
        .headers()
        .get(reqwest::header::CONTENT_ENCODING)
        .is_some_and(|value| !value.as_bytes().eq_ignore_ascii_case(b"identity"))
    {
        return Err("Soloist archive used unexpected HTTP compression.".to_owned());
    }
    if response
        .content_length()
        .is_some_and(|length| length == 0 || length > MAX_ARCHIVE_BYTES)
    {
        return Err("Soloist archive exceeds the size limit.".to_owned());
    }
    let expected_length = response.content_length();
    let mut output = create_new_file(archive_path, 0o600)?;
    let mut stream = response.bytes_stream();
    let mut total = 0_u64;
    let result = async {
        loop {
            let next_chunk = timeout(Duration::from_secs(60), stream.next())
                .await
                .map_err(|_| "Soloist archive download timed out.".to_owned())?;
            let Some(chunk) = next_chunk else { break };
            let chunk = chunk.map_err(|_| "Soloist archive download failed.".to_owned())?;
            total = total.saturating_add(chunk.len() as u64);
            if total > MAX_ARCHIVE_BYTES {
                return Err("Soloist archive exceeds the size limit.".to_owned());
            }
            output
                .write_all(&chunk)
                .map_err(|_| "Could not write the Soloist archive.".to_owned())?;
        }
        if total == 0 || expected_length.is_some_and(|length| length != total) {
            return Err("Soloist archive size did not match its HTTP response.".to_owned());
        }
        output
            .sync_all()
            .map_err(|_| "Could not sync the downloaded Soloist archive.".to_owned())
    }
    .await;
    if result.is_err() {
        drop(output);
        let _ = fs::remove_file(archive_path);
    }
    result
}

fn create_new_file(path: &Path, mode: u32) -> Result<File> {
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(mode)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(path)
        .map_err(|_| "Could not create a private Soloist temporary file.".to_owned())
}

pub async fn download_and_extract(
    url: &str,
    archive_path: &Path,
    destination: &Path,
) -> Result<()> {
    let url = official_archive_url(url)?;
    let client = download_client()?;
    let mut last_error = "Soloist archive download failed.".to_owned();
    for attempt in 0..4 {
        match download_once(&client, &url, archive_path).await {
            Ok(()) => {
                let result = extract(archive_path, destination);
                let _ = fs::remove_file(archive_path);
                return result;
            }
            Err(error) => {
                let retryable = retryable_download_error(&error);
                last_error = error;
                if !retryable || attempt == 3 {
                    break;
                }
                sleep(Duration::from_secs(2)).await;
            }
        }
    }
    Err(last_error)
}

fn retryable_download_error(error: &str) -> bool {
    if error == "Soloist archive download timed out." || error == "Soloist archive download failed."
    {
        return true;
    }
    error
        .strip_prefix("Soloist archive server returned HTTP ")
        .and_then(|status| status.strip_suffix('.'))
        .and_then(|status| status.parse::<u16>().ok())
        .is_some_and(|status| matches!(status, 408 | 425 | 429) || (500..600).contains(&status))
}

pub fn extract(archive_path: &Path, destination: &Path) -> Result<()> {
    extract_with_limit(archive_path, destination, MAX_UNCOMPRESSED_ARCHIVE_BYTES)
}

fn extract_with_limit(
    archive_path: &Path,
    destination: &Path,
    maximum_decompressed: u64,
) -> Result<()> {
    let compressed = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
        .open(archive_path)
        .map_err(|_| "Could not open the Soloist archive.".to_owned())?;
    let metadata = compressed
        .metadata()
        .map_err(|_| "Could not inspect the Soloist archive.".to_owned())?;
    if !metadata.is_file() || metadata.len() > MAX_ARCHIVE_BYTES {
        return Err("Soloist archive exceeds the size limit or is not a regular file.".to_owned());
    }

    let decoder = MultiGzDecoder::new(compressed);
    let mut reader = BoundedReader::new(decoder, maximum_decompressed + 1);
    let mut destination_created = false;
    let result = (|| -> Result<()> {
        extract_tar(&mut reader, destination, &mut destination_created)?;
        drain_archive(&mut reader, maximum_decompressed)
    })();
    if result.is_err() && destination_created {
        let _ = fs::remove_file(destination);
    }
    result
}

struct BoundedReader<R> {
    inner: R,
    bytes_read: u64,
    maximum_bytes: u64,
}

impl<R: Read> BoundedReader<R> {
    fn new(inner: R, maximum_bytes: u64) -> Self {
        Self {
            inner,
            bytes_read: 0,
            maximum_bytes,
        }
    }

    fn read_exact(&mut self, buffer: &mut [u8]) -> Result<()> {
        if self.bytes_read + buffer.len() as u64 > self.maximum_bytes {
            return Err("Soloist archive exceeds the decompressed size limit.".to_owned());
        }
        self.inner
            .read_exact(buffer)
            .map_err(|_| "Soloist archive is truncated or decompression failed.".to_owned())?;
        self.bytes_read += buffer.len() as u64;
        Ok(())
    }

    fn read_header(&mut self) -> Result<Option<[u8; 512]>> {
        if self.bytes_read >= self.maximum_bytes {
            return Err("Soloist archive exceeds the decompressed size limit.".to_owned());
        }
        let mut header = [0_u8; 512];
        let first = self
            .inner
            .read(&mut header[..1])
            .map_err(|_| "Soloist archive decompression failed.".to_owned())?;
        if first == 0 {
            return Ok(None);
        }
        self.bytes_read += 1;
        self.read_exact(&mut header[1..])?;
        Ok(Some(header))
    }

    fn copy_exact(&mut self, mut size: u64, output: &mut dyn Write) -> Result<()> {
        let mut buffer = [0_u8; 64 * 1024];
        while size > 0 {
            let count = size.min(buffer.len() as u64) as usize;
            self.read_exact(&mut buffer[..count])?;
            output
                .write_all(&buffer[..count])
                .map_err(|_| "Could not write the extracted Soloist file.".to_owned())?;
            size -= count as u64;
        }
        Ok(())
    }

    fn skip_exact(&mut self, mut size: u64) -> Result<()> {
        let mut buffer = [0_u8; 64 * 1024];
        while size > 0 {
            let count = size.min(buffer.len() as u64) as usize;
            self.read_exact(&mut buffer[..count])?;
            size -= count as u64;
        }
        Ok(())
    }
}

fn extract_tar<R: Read>(
    reader: &mut BoundedReader<R>,
    destination: &Path,
    destination_created: &mut bool,
) -> Result<()> {
    let mut names = HashSet::<Vec<u8>>::new();
    let mut members = 0_usize;
    let mut extracted = false;
    let mut pending_pax = None;
    loop {
        let Some(header) = reader.read_header()? else {
            return Err("Soloist archive has no tar end marker.".to_owned());
        };
        if header.iter().all(|byte| *byte == 0) {
            if pending_pax.is_some() {
                return Err("Soloist archive has orphaned extended metadata.".to_owned());
            }
            break;
        }
        validate_tar_checksum(&header)?;
        let raw_size = parse_octal(&header[124..136])?;
        match header[156] {
            b'x' => {
                if pending_pax.is_some() || raw_size > 1024 * 1024 {
                    return Err("Soloist archive has oversized or repeated metadata.".to_owned());
                }
                let mut payload = vec![0_u8; raw_size as usize];
                reader.read_exact(&mut payload)?;
                skip_padding(reader, raw_size)?;
                pending_pax = Some(parse_pax(&payload)?);
                continue;
            }
            b'g' => return Err("Soloist archive contains global metadata.".to_owned()),
            b'0' | 0 => {}
            _ => return Err("Soloist archive contains an unexpected member.".to_owned()),
        }

        members += 1;
        if members > 1 + ALLOWED_DOCUMENTATION.len() {
            return Err("Soloist archive contains an invalid member count.".to_owned());
        }
        let mut name = tar_path(&header)?;
        let mut size = raw_size;
        if let Some(extensions) = pending_pax.take() {
            if let Some(path) = extensions.get("path") {
                name = path.as_bytes().to_vec();
            }
            if let Some(pax_size) = extensions.get("size") {
                size = pax_size
                    .parse::<u64>()
                    .map_err(|_| "Soloist archive has an invalid extended size.".to_owned())?;
            }
        }
        let is_executable = name == b"soloist" || name == b"./soloist";
        let is_documentation = ALLOWED_DOCUMENTATION.contains(&name.as_slice());
        if (!is_executable && !is_documentation) || !names.insert(name) {
            return Err("Soloist archive contains an unexpected or duplicate member.".to_owned());
        }
        let maximum = if is_executable {
            MAX_SOLOIST_BYTES
        } else {
            MAX_DOCUMENTATION_BYTES
        };
        if (is_executable && size == 0) || size > maximum {
            return Err("Soloist archive member has an invalid size.".to_owned());
        }
        if is_executable {
            if extracted {
                return Err("Soloist archive must contain one regular soloist file.".to_owned());
            }
            let mut output = create_new_file(destination, 0o700)?;
            *destination_created = true;
            reader.copy_exact(size, &mut output)?;
            output
                .sync_all()
                .map_err(|_| "Could not sync the extracted Soloist executable.".to_owned())?;
            extracted = true;
        } else {
            reader.skip_exact(size)?;
        }
        skip_padding(reader, size)?;
    }
    if !extracted {
        return Err("Soloist archive must contain one regular soloist file.".to_owned());
    }
    Ok(())
}

fn validate_tar_checksum(header: &[u8; 512]) -> Result<()> {
    let expected = parse_octal(&header[148..156])?;
    let actual = header
        .iter()
        .enumerate()
        .map(|(index, byte)| {
            if (148..156).contains(&index) {
                b' ' as u64
            } else {
                *byte as u64
            }
        })
        .sum::<u64>();
    if actual != expected {
        return Err("Soloist archive contains an invalid tar header.".to_owned());
    }
    Ok(())
}

fn parse_octal(bytes: &[u8]) -> Result<u64> {
    if bytes.first().is_some_and(|byte| byte & 0x80 != 0) {
        return Err("Soloist archive uses an unsupported numeric field.".to_owned());
    }
    let digits = bytes
        .iter()
        .copied()
        .skip_while(|byte| *byte == 0 || *byte == b' ')
        .take_while(|byte| *byte != 0 && *byte != b' ')
        .collect::<Vec<_>>();
    if digits.is_empty() {
        return Ok(0);
    }
    digits.iter().try_fold(0_u64, |value, digit| {
        if !(b'0'..=b'7').contains(digit) {
            return Err("Soloist archive contains an invalid numeric field.".to_owned());
        }
        value
            .checked_mul(8)
            .and_then(|value| value.checked_add((digit - b'0') as u64))
            .ok_or_else(|| "Soloist archive numeric field overflowed.".to_owned())
    })
}

fn tar_path(header: &[u8; 512]) -> Result<Vec<u8>> {
    fn field(bytes: &[u8]) -> &[u8] {
        &bytes[..bytes
            .iter()
            .position(|byte| *byte == 0)
            .unwrap_or(bytes.len())]
    }
    let name = field(&header[..100]);
    let prefix = field(&header[345..500]);
    if name.is_empty() {
        return Err("Soloist archive contains an empty member path.".to_owned());
    }
    if prefix.is_empty() {
        Ok(name.to_vec())
    } else {
        let mut path = prefix.to_vec();
        path.push(b'/');
        path.extend_from_slice(name);
        Ok(path)
    }
}

fn parse_pax(payload: &[u8]) -> Result<HashMap<String, String>> {
    let mut extensions = HashMap::new();
    let mut offset = 0_usize;
    while offset < payload.len() {
        let space = payload[offset..]
            .iter()
            .position(|byte| *byte == b' ')
            .map(|index| offset + index)
            .ok_or_else(|| "Soloist archive has malformed extended metadata.".to_owned())?;
        let record_size = std::str::from_utf8(&payload[offset..space])
            .ok()
            .and_then(|size| size.parse::<usize>().ok())
            .ok_or_else(|| "Soloist archive has malformed extended metadata.".to_owned())?;
        let end = offset
            .checked_add(record_size)
            .filter(|end| *end <= payload.len())
            .ok_or_else(|| "Soloist archive has malformed extended metadata.".to_owned())?;
        if record_size <= space - offset + 2 || payload[end - 1] != b'\n' {
            return Err("Soloist archive has malformed extended metadata.".to_owned());
        }
        let record = &payload[space + 1..end - 1];
        let equals = record
            .iter()
            .position(|byte| *byte == b'=')
            .ok_or_else(|| "Soloist archive has malformed extended metadata.".to_owned())?;
        let key = &record[..equals];
        let value = &record[equals + 1..];
        let key = std::str::from_utf8(key)
            .map_err(|_| "Soloist archive has invalid extended metadata.".to_owned())?;
        let value = std::str::from_utf8(value)
            .map_err(|_| "Soloist archive has invalid extended metadata.".to_owned())?;
        if !matches!(key, "path" | "size" | "mtime" | "atime" | "ctime")
            || extensions
                .insert(key.to_owned(), value.to_owned())
                .is_some()
        {
            return Err("Soloist archive has unsupported extended metadata.".to_owned());
        }
        offset = end;
    }
    Ok(extensions)
}

fn skip_padding<R: Read>(reader: &mut BoundedReader<R>, size: u64) -> Result<()> {
    reader.skip_exact((512 - size % 512) % 512)
}

fn drain_archive<R: Read>(reader: &mut BoundedReader<R>, maximum_decompressed: u64) -> Result<()> {
    let mut buffer = [0_u8; DOWNLOAD_CHUNK_BYTES];
    loop {
        let remaining = maximum_decompressed + 1 - reader.bytes_read;
        let chunk_size = remaining.min(buffer.len() as u64) as usize;
        match reader.inner.read(&mut buffer[..chunk_size]) {
            Ok(0) => break,
            Ok(count) => {
                reader.bytes_read += count as u64;
                if reader.bytes_read > maximum_decompressed {
                    return Err("Soloist archive exceeds the decompressed size limit.".to_owned());
                }
            }
            Err(_) => return Err("Soloist archive decompression failed.".to_owned()),
        }
    }
    if reader.bytes_read > maximum_decompressed {
        return Err("Soloist archive exceeds the decompressed size limit.".to_owned());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use flate2::Compression;
    use flate2::write::GzEncoder;
    use std::os::unix::fs::PermissionsExt;
    use tempfile::TempDir;

    fn add_member(archive: &mut Vec<u8>, name: &str, kind: u8, payload: &[u8]) {
        let mut header = [0_u8; 512];
        header[..name.len()].copy_from_slice(name.as_bytes());
        header[100..108].copy_from_slice(b"0000700\0");
        header[108..116].copy_from_slice(b"0000000\0");
        header[116..124].copy_from_slice(b"0000000\0");
        let size = format!("{:011o}\0", payload.len());
        header[124..136].copy_from_slice(size.as_bytes());
        header[136..148].copy_from_slice(b"00000000000\0");
        header[148..156].fill(b' ');
        header[156] = kind;
        header[257..263].copy_from_slice(b"ustar\0");
        header[263..265].copy_from_slice(b"00");
        let checksum = header.iter().map(|byte| *byte as u64).sum::<u64>();
        let checksum = format!("{checksum:06o}\0 ");
        header[148..156].copy_from_slice(checksum.as_bytes());
        archive.extend_from_slice(&header);
        archive.extend_from_slice(payload);
        let padding = (512 - payload.len() % 512) % 512;
        archive.resize(archive.len() + padding, 0);
    }

    fn write_archive(path: &Path, members: &[(&str, u8, &[u8])]) {
        let mut tar = Vec::new();
        for (name, kind, payload) in members {
            add_member(&mut tar, name, *kind, payload);
        }
        tar.resize(tar.len() + 1024, 0);
        let mut gzip = GzEncoder::new(Vec::new(), Compression::default());
        gzip.write_all(&tar).expect("write test tar");
        fs::write(path, gzip.finish().expect("finish test gzip")).expect("write test archive");
    }

    fn pax_record(key: &str, value: &str) -> Vec<u8> {
        let mut length = key.len() + value.len() + 4;
        loop {
            let record = format!("{length} {key}={value}\n");
            if record.len() == length {
                return record.into_bytes();
            }
            length = record.len();
        }
    }

    #[test]
    fn extracts_one_regular_executable_and_ignores_allowed_documentation() {
        let temp = TempDir::new().expect("create temp directory");
        let archive = temp.path().join("soloist.tar.gz");
        let destination = temp.path().join("bin/soloist");
        fs::create_dir(destination.parent().expect("destination parent"))
            .expect("create destination parent");
        write_archive(
            &archive,
            &[
                ("CHANGELOG.md", b'0', b"release notes"),
                ("./soloist", b'0', b"trusted executable"),
            ],
        );

        extract(&archive, &destination).expect("extract valid archive");

        assert_eq!(
            fs::read(&destination).expect("read binary"),
            b"trusted executable"
        );
        assert_eq!(
            fs::metadata(&destination)
                .expect("binary metadata")
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
    }

    #[test]
    fn rejects_links_duplicate_names_and_unexpected_paths_without_output() {
        let temp = TempDir::new().expect("create temp directory");
        for (name, members) in [
            ("link", vec![("soloist", b'2', b"target".as_slice())]),
            (
                "duplicate",
                vec![
                    ("soloist", b'0', b"one".as_slice()),
                    ("soloist", b'0', b"two".as_slice()),
                ],
            ),
            ("path", vec![("../../escape", b'0', b"bad".as_slice())]),
        ] {
            let archive = temp.path().join(format!("{name}.tar.gz"));
            let destination = temp.path().join(format!("{name}-soloist"));
            write_archive(&archive, &members);
            assert!(extract(&archive, &destination).is_err());
            assert!(!destination.exists());
        }
    }

    #[test]
    fn accepts_bounded_pax_path_and_rejects_oversized_pax_before_reading_it() {
        let temp = TempDir::new().expect("create temp directory");
        let archive = temp.path().join("pax.tar.gz");
        let destination = temp.path().join("pax-soloist");
        let mut tar = Vec::new();
        let mut pax = pax_record("path", "soloist");
        pax.extend_from_slice(&pax_record("mtime", "1793799586"));
        add_member(&mut tar, "PaxHeader/soloist", b'x', &pax);
        add_member(&mut tar, "placeholder", b'0', b"pax executable");
        tar.resize(tar.len() + 1024, 0);
        let mut gzip = GzEncoder::new(Vec::new(), Compression::default());
        gzip.write_all(&tar).expect("write test tar");
        fs::write(&archive, gzip.finish().expect("finish test gzip")).expect("write test archive");

        extract(&archive, &destination).expect("extract bounded PAX archive");
        assert_eq!(
            fs::read(&destination).expect("read executable"),
            b"pax executable"
        );

        let oversized_archive = temp.path().join("oversized-pax.tar.gz");
        let oversized_destination = temp.path().join("oversized-pax-soloist");
        let oversized_pax = vec![b'x'; 1024 * 1024 + 1];
        write_archive(
            &oversized_archive,
            &[("PaxHeader/soloist", b'x', &oversized_pax)],
        );
        assert!(extract(&oversized_archive, &oversized_destination).is_err());
        assert!(!oversized_destination.exists());
    }

    #[test]
    fn rejects_non_official_or_plain_http_download_urls() {
        for url in [
            "http://soloist-builds.spotifycdn.com/archive.tar.gz",
            "https://attacker.example/archive.tar.gz",
            "https://user@soloist-builds.spotifycdn.com/archive.tar.gz",
            "https://soloist-builds.spotifycdn.com:444/archive.tar.gz",
        ] {
            assert!(official_archive_url(url).is_err(), "accepted {url}");
        }
        assert!(
            official_archive_url("https://soloist-builds.spotifycdn.com/archive.tar.gz").is_ok()
        );
    }

    #[test]
    fn retries_only_transient_download_errors() {
        for status in [408, 425, 429, 500, 599] {
            assert!(retryable_download_error(&format!(
                "Soloist archive server returned HTTP {status}."
            )));
        }
        assert!(!retryable_download_error(
            "Soloist archive server returned HTTP 404."
        ));
        assert!(!retryable_download_error(
            "Soloist archive exceeds the size limit."
        ));
    }

    #[test]
    fn decompressed_bytes_are_limited_even_after_tar_end_markers() {
        let temp = TempDir::new().expect("create temp directory");
        let archive = temp.path().join("soloist.tar.gz");
        let destination = temp.path().join("soloist");
        let mut tar = Vec::new();
        add_member(&mut tar, "soloist", b'0', b"binary");
        tar.resize(tar.len() + 1024, 0);
        tar.resize(2048, 0);
        let mut gzip = GzEncoder::new(Vec::new(), Compression::default());
        gzip.write_all(&tar).expect("write test tar");
        fs::write(&archive, gzip.finish().expect("finish test gzip")).expect("write archive");

        assert!(extract_with_limit(&archive, &destination, 1024).is_err());
        assert!(!destination.exists());
    }
}
