use std::env;
use std::path::PathBuf;
use std::process::ExitCode;

fn main() -> ExitCode {
    match parse_socket_path(env::args().skip(1)) {
        Ok(Some(socket_path)) => match lumistripe_engine_runtime::serve(&socket_path) {
            Ok(()) => ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("lumistripe-engine: {error}");
                ExitCode::FAILURE
            }
        },
        Ok(None) => {
            println!(
                "Usage: lumistripe-engine --socket PATH\nRuns the versioned local engine IPC server."
            );
            ExitCode::SUCCESS
        }
        Err(message) => {
            eprintln!("{message}");
            ExitCode::from(2)
        }
    }
}

fn parse_socket_path(args: impl IntoIterator<Item = String>) -> Result<Option<PathBuf>, String> {
    let args: Vec<String> = args.into_iter().collect();
    match args.as_slice() {
        [flag, path] if flag == "--socket" && !path.is_empty() => Ok(Some(PathBuf::from(path))),
        [flag] if flag == "--help" || flag == "-h" => Ok(None),
        _ => Err("Usage: lumistripe-engine --socket PATH".into()),
    }
}

#[cfg(test)]
mod tests {
    use super::parse_socket_path;
    use std::path::PathBuf;

    #[test]
    fn requires_one_socket_path() {
        assert!(parse_socket_path(Vec::<String>::new()).is_err());
    }

    #[test]
    fn supports_help_without_starting_the_server() {
        assert_eq!(parse_socket_path(["--help".to_string()]), Ok(None));
    }

    #[test]
    fn accepts_one_socket_path() {
        assert_eq!(
            parse_socket_path(["--socket".to_string(), "/tmp/engine.sock".to_string()]),
            Ok(Some(PathBuf::from("/tmp/engine.sock")))
        );
    }
}
