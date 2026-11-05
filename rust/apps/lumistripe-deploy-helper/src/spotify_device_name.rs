use std::env;
use std::error::Error;
use std::fs::{self, File, OpenOptions, Permissions};
use std::io::{self, Write};
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};

const DEVICE_NAME_RELATIVE_PATH: &str = ".config/lumistripe/spotify-device-name.env";
const MAX_DEVICE_NAME_CHARS: usize = 64;

type Result<T> = std::result::Result<T, Box<dyn Error + Send + Sync>>;

pub fn run(name: &str) -> Result<()> {
    let name = name.trim();
    validate_name(name)?;
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|home| !home.as_os_str().is_empty())
        .ok_or_else(|| io::Error::other("service home directory is not configured"))?;
    write_name_file(&home.join(DEVICE_NAME_RELATIVE_PATH), name)?;
    Ok(())
}

fn validate_name(name: &str) -> io::Result<()> {
    let length = name.chars().count();
    if length == 0 || length > MAX_DEVICE_NAME_CHARS || name.chars().any(char::is_control) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Spotify player name must be 1 to 64 printable characters",
        ));
    }
    Ok(())
}

fn write_name_file(path: &Path, name: &str) -> io::Result<()> {
    validate_name(name)?;
    let parent = path
        .parent()
        .filter(|parent| !parent.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    fs::create_dir_all(parent)?;
    let filename = path
        .file_name()
        .ok_or_else(|| io::Error::other("Spotify player name path has no file name"))?
        .to_string_lossy();
    let encoded_name = name.replace('\\', "\\\\").replace('"', "\\\"");
    let contents = format!("LUMI_SPOTIFY_DEVICE_NAME=\"{encoded_name}\"\n");

    let (temporary_path, mut temporary_file) = (0..32_u32)
        .find_map(|attempt| {
            let candidate = parent.join(format!(
                ".{filename}.{}.{}.tmp",
                std::process::id(),
                attempt
            ));
            match OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(libc::O_CLOEXEC | libc::O_NOFOLLOW)
                .open(&candidate)
            {
                Ok(file) => Some(Ok((candidate, file))),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => None,
                Err(error) => Some(Err(error)),
            }
        })
        .transpose()?
        .ok_or_else(|| io::Error::other("could not create a temporary player name file"))?;

    let result = (|| -> io::Result<()> {
        temporary_file.write_all(contents.as_bytes())?;
        temporary_file.set_permissions(Permissions::from_mode(0o644))?;
        temporary_file.sync_all()?;
        drop(temporary_file);
        fs::rename(&temporary_path, path)?;
        File::open(parent)?.sync_all()?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temporary_path);
    }
    result
}

#[cfg(test)]
mod tests {
    use super::{validate_name, write_name_file};
    use std::fs;
    use std::os::unix::fs::PermissionsExt;
    use tempfile::tempdir;

    #[test]
    fn writes_escaped_device_name_with_systemd_environment_file_permissions() {
        let directory = tempdir().unwrap();
        let state_file = directory.path().join("nested/spotify-device-name.env");

        write_name_file(&state_file, "Studio \\\"player\\\\ one\\\"").unwrap();

        assert_eq!(
            fs::read_to_string(&state_file).unwrap(),
            "LUMI_SPOTIFY_DEVICE_NAME=\"Studio \\\\\\\"player\\\\\\\\ one\\\\\\\"\"\n"
        );
        assert_eq!(
            fs::metadata(&state_file).unwrap().permissions().mode() & 0o777,
            0o644
        );
    }

    #[test]
    fn atomically_replaces_a_symlink_without_touching_its_target() {
        let directory = tempdir().unwrap();
        let target = directory.path().join("target.env");
        let state_file = directory.path().join("spotify-device-name.env");
        fs::write(&target, "LUMI_SPOTIFY_DEVICE_NAME=\"other\"\n").unwrap();
        std::os::unix::fs::symlink(&target, &state_file).unwrap();

        write_name_file(&state_file, "Studio").unwrap();

        assert_eq!(
            fs::read_to_string(&state_file).unwrap(),
            "LUMI_SPOTIFY_DEVICE_NAME=\"Studio\"\n"
        );
        assert_eq!(
            fs::read_to_string(target).unwrap(),
            "LUMI_SPOTIFY_DEVICE_NAME=\"other\"\n"
        );
    }

    #[test]
    fn rejects_empty_long_and_control_character_names() {
        assert!(validate_name("").is_err());
        assert!(validate_name(&"a".repeat(65)).is_err());
        assert!(validate_name("Studio\nplayer").is_err());
        assert!(validate_name("Studio player").is_ok());
    }
}
