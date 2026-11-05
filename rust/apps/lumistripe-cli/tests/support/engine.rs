use std::env;
use std::path::PathBuf;

fn main() {
    let args = env::args().skip(1).collect::<Vec<_>>();
    let parsed = match args.as_slice() {
        [socket_flag, socket, owner_flag, owner]
            if socket_flag == "--socket" && owner_flag == "--owner-pid" =>
        {
            owner
                .parse::<libc::pid_t>()
                .map(|pid| (PathBuf::from(socket), pid))
                .map_err(|_| "invalid owner pid")
        }
        _ => Err("expected --socket PATH --owner-pid PID"),
    };
    let (socket, owner_pid) = parsed.unwrap_or_else(|error| {
        eprintln!("test engine: {error}");
        std::process::exit(2);
    });
    if let Err(error) = lumistripe_engine_runtime::serve_with_owner(&socket, owner_pid) {
        eprintln!("test engine: {error}");
        std::process::exit(1);
    }
}
