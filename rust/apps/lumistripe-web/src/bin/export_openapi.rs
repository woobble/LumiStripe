use utoipa::OpenApi;

fn main() {
    let document = lumistripe_web::ApiDoc::openapi();
    println!(
        "{}",
        document
            .to_pretty_json()
            .expect("Rust OpenAPI document should serialize")
    );
}
