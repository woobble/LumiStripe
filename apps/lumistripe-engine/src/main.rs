use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    match parse_server_args(env::args().skip(1)) {
        Ok(Some((socket_path, owner_pid))) => match owner_pid.map_or_else(
            || lumistripe_engine_runtime::serve(&socket_path),
            |owner_pid| lumistripe_engine_runtime::serve_with_owner(&socket_path, owner_pid),
        ) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("lumistripe-engine: {error}");
                ExitCode::FAILURE
            }
        },
        Ok(None) => {
            println!(
                "Usage: lumistripe-engine --socket PATH [--owner-pid PID]\nRuns the versioned local engine IPC server."
            );
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("{message}");
            ExitCode::from(2)
        }
    }
}

fn parse_server_args(
    args: impl IntoIterator<Item = String>,
) -> Result<Option<(PathBuf, Option<libc::pid_t>)>, String> {
    let args: Vec<String> = args.into_iter().collect();
    match args.as_slice() {
        [flag, path] if flag == "--socket" && !path.is_empty() => {
            Ok(Some((PathBuf::from(path), None)))
        }
        [socket_flag, path, owner_flag, owner_pid]
            if socket_flag == "--socket" && !path.is_empty() && owner_flag == "--owner-pid" =>
        {
            let owner_pid = owner_pid
                .parse::<libc::pid_t>()
                .map_err(|_| "--owner-pid must be a positive process ID".to_string())?;
            if owner_pid <= 0 {
                return Err("--owner-pid must be a positive process ID".into());
            }
            Ok(Some((PathBuf::from(path), Some(owner_pid))))
        }
        [flag] if flag == "--help" || flag == "-h" => Ok(None),
        _ => Err("Usage: lumistripe-engine --socket PATH [--owner-pid PID]".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::parse_server_args;
    use std::path::PathBuf;

    #[test]
    fn requires_one_socket_path() {
        assert!(parse_server_args(Vec::<String>::new()).is_err());
    }

    #[test]
    fn supports_help_without_starting_the_server() {
        assert_eq!(parse_server_args(["--help".to_string()]), Ok(None));
    }

    #[test]
    fn accepts_one_socket_path() {
        assert_eq!(
            parse_server_args(["--socket".to_string(), "/tmp/engine.sock".to_string()]),
            Ok(Some((PathBuf::from("/tmp/engine.sock"), None)))
        );
    }

    #[test]
    fn accepts_an_explicit_owner_process() {
        assert_eq!(
            parse_server_args([
                "--socket".to_string(),
                "/tmp/engine.sock".to_string(),
                "--owner-pid".to_string(),
                "1234".to_string(),
            ]),
            Ok(Some((PathBuf::from("/tmp/engine.sock"), Some(1234))))
        );
    }
}
