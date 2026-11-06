use std::io::{BufRead, BufReader, Write};
use std::fs;
use std::net::{SocketAddr, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;
use tauri::Manager;

static SHUTTING_DOWN: AtomicBool = AtomicBool::new(false);

fn shutdown_backend() -> std::io::Result<()> {
    let address: SocketAddr = "127.0.0.1:8766".parse().unwrap();
    let mut connection = TcpStream::connect_timeout(&address, Duration::from_secs(2))?;
    connection.set_read_timeout(Some(Duration::from_secs(3)))?;
    connection.set_write_timeout(Some(Duration::from_secs(2)))?;
    connection.write_all(b"POST /api/app/shutdown HTTP/1.1\r\nHost: 127.0.0.1:8766\r\nX-Lianxin-Shutdown: 1\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")?;
    let mut response = String::new();
    BufReader::new(connection).read_line(&mut response)?;
    if !response.starts_with("HTTP/1.0 200 ") && !response.starts_with("HTTP/1.1 200 ") {
        return Err(std::io::Error::other("backend rejected shutdown"));
    }
    Ok(())
}

#[tauri::command]
fn app_shell_status() -> &'static str {
    "frontend-fixture"
}

#[tauri::command]
fn read_image_preview(path: String) -> Result<String, String> {
    let file_path = std::path::PathBuf::from(path);
    let extension = file_path
        .extension()
        .and_then(|value| value.to_str())
        .unwrap_or("")
        .to_ascii_lowercase();
    let mime = match extension.as_str() {
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "bmp" => "image/bmp",
        "gif" => "image/gif",
        _ => return Err("不支持的图片格式".into()),
    };
    let metadata = fs::metadata(&file_path).map_err(|error| error.to_string())?;
    if !metadata.is_file() || metadata.len() > 12 * 1024 * 1024 {
        return Err("图片不存在或超过 12MB".into());
    }
    let bytes = fs::read(file_path).map_err(|error| error.to_string())?;
    use base64::Engine;
    let encoded = base64::engine::general_purpose::STANDARD.encode(bytes);
    Ok(format!("data:{mime};base64,{encoded}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![app_shell_status, read_image_preview])
        .on_window_event(|window, event| {
            if window.label() != "main" {
                return;
            }
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                if SHUTTING_DOWN.swap(true, Ordering::SeqCst) {
                    return;
                }
                let app = window.app_handle().clone();
                std::thread::spawn(move || {
                    if let Err(error) = shutdown_backend() {
                        eprintln!("Backend shutdown: {error}");
                    }
                    app.exit(0);
                });
            }
        })
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                window.set_title("莲心 AI")?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Lianxin UI");
}
