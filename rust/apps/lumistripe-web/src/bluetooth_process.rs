//! Bounded child-process runner shared by BlueZ and PipeWire providers.

use futures_util::future::BoxFuture;
use std::process::Stdio;
use std::time::Duration;
use thiserror::Error;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWriteExt};
use tokio::process::Command;
use tokio::time::timeout;

const MAX_COMMAND_OUTPUT: usize = 1024 * 1024;

#[derive(Clone, Debug, Error)]
#[error("{message}")]
pub(crate) struct BluetoothError {
    pub code: &'static str,
    pub message: String,
}

impl BluetoothError {
    pub(crate) fn new(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

pub(crate) trait ProcessRunner: Send + Sync {
    fn exists(&self, program: &'static str) -> bool;

    fn run(
        &self,
        program: &'static str,
        args: Vec<String>,
        stdin: Option<String>,
        timeout: Duration,
    ) -> BoxFuture<'static, Result<String, BluetoothError>>;
}

#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct SystemProcessRunner;

impl ProcessRunner for SystemProcessRunner {
    fn exists(&self, program: &'static str) -> bool {
        std::env::var_os("PATH").is_some_and(|path| {
            std::env::split_paths(&path).any(|directory| directory.join(program).is_file())
        })
    }

    fn run(
        &self,
        program: &'static str,
        args: Vec<String>,
        script: Option<String>,
        deadline: Duration,
    ) -> BoxFuture<'static, Result<String, BluetoothError>> {
        Box::pin(async move {
            let mut command = Command::new(program);
            command
                .args(args)
                .stdin(if script.is_some() {
                    Stdio::piped()
                } else {
                    Stdio::null()
                })
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .kill_on_drop(true);
            let mut child = command.spawn().map_err(|error| {
                BluetoothError::new("process", format!("could not run {program}: {error}"))
            })?;
            if let Some(script) = script {
                if let Some(mut stdin) = child.stdin.take() {
                    stdin.write_all(script.as_bytes()).await.map_err(|error| {
                        BluetoothError::new(
                            "process",
                            format!("could not send command input: {error}"),
                        )
                    })?;
                }
            }
            let mut stdout = child.stdout.take().ok_or_else(|| {
                BluetoothError::new("process", format!("could not read {program} output"))
            })?;
            let mut stderr = child.stderr.take().ok_or_else(|| {
                BluetoothError::new("process", format!("could not read {program} errors"))
            })?;
            let output = timeout(deadline, async {
                let (stdout, stderr, status) = tokio::join!(
                    read_capped(&mut stdout, MAX_COMMAND_OUTPUT),
                    read_capped(&mut stderr, MAX_COMMAND_OUTPUT),
                    child.wait(),
                );
                let stdout = stdout?;
                let stderr = stderr?;
                let status = status?;
                Ok::<_, std::io::Error>((stdout, stderr, status))
            })
            .await
            .map_err(|_| BluetoothError::new("timeout", format!("{program} timed out")))?
            .map_err(|error| {
                BluetoothError::new("process", format!("could not wait for {program}: {error}"))
            })?;
            if output.0.len() + output.1.len() > MAX_COMMAND_OUTPUT {
                return Err(BluetoothError::new(
                    "output_limit",
                    format!("{program} output exceeded the size limit"),
                ));
            }
            let text = String::from_utf8_lossy(&output.0).to_string();
            let errors = String::from_utf8_lossy(&output.1);
            let cleaned = clean_command_output(&format!("{text}\n{errors}"));
            if !output.2.success() {
                return Err(BluetoothError::new(
                    "command_failed",
                    if cleaned.is_empty() {
                        format!("{program} failed with exit code {}", output.2)
                    } else {
                        cleaned
                    },
                ));
            }
            Ok(cleaned)
        })
    }
}

async fn read_capped(
    reader: &mut (impl AsyncRead + Unpin),
    limit: usize,
) -> std::io::Result<Vec<u8>> {
    let mut output = Vec::with_capacity(limit.min(8192));
    let mut buffer = [0_u8; 8192];
    loop {
        let read = reader.read(&mut buffer).await?;
        if read == 0 {
            break;
        }
        let stored = (limit + 1).saturating_sub(output.len()).min(read);
        output.extend_from_slice(&buffer[..stored]);
    }
    Ok(output)
}

fn clean_command_output(output: &str) -> String {
    let mut cleaned = String::with_capacity(output.len());
    let mut escape = false;
    let mut csi = false;
    for character in output.chars() {
        if csi {
            if ('@'..='~').contains(&character) {
                csi = false;
            }
            continue;
        }
        if escape {
            escape = false;
            if character == '[' {
                csi = true;
            }
            continue;
        }
        match character {
            '\u{1b}' => escape = true,
            '\r' => {}
            other => cleaned.push(other),
        }
    }
    cleaned
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("\n")
}

#[cfg(test)]
mod tests {
    use super::{ProcessRunner, SystemProcessRunner, clean_command_output, read_capped};
    use std::time::Duration;
    use tokio::io::AsyncWriteExt;

    #[tokio::test]
    async fn capped_reader_stores_only_the_limit_plus_one_bytes() {
        let (mut writer, mut reader) = tokio::io::duplex(16);
        let send = tokio::spawn(async move {
            writer.write_all(b"bounded output").await.unwrap();
        });

        let output = read_capped(&mut reader, 5).await.unwrap();

        assert_eq!(output, b"bounde");
        send.await.unwrap();
    }

    #[test]
    fn command_output_removes_terminal_sequences_and_crlf() {
        assert_eq!(
            clean_command_output("\u{1b}[31mDevice AA:BB\u{1b}[0m\r\n"),
            "Device AA:BB"
        );
    }

    #[tokio::test]
    async fn process_timeout_drops_the_child_process() {
        let runner = SystemProcessRunner;
        let error = runner
            .run(
                "/bin/sleep",
                vec!["5".to_owned()],
                None,
                Duration::from_millis(20),
            )
            .await
            .unwrap_err();

        assert_eq!(error.code, "timeout");
    }
}
