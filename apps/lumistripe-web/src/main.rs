use clap::Parser;
use lumistripe_web::{CliOptions, WebApplication};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(false)
        .init();

    let options = CliOptions::parse();
    let simulate = options.simulate;
    let pairing_code = options.pairing_code.clone();
    let (config, host, port) = options.into_config()?;
    let app = WebApplication::start(config, pairing_code).await?;
    if simulate {
        println!("LumiStripe simulator: http://{host}:{port}/?simulator=1");
    }
    app.serve(&host, port).await?;
    Ok(())
}
