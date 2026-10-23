use std::io::{BufRead, BufReader, Write};
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

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![app_shell_status])
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
