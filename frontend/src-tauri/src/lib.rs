use tauri::Manager;

#[tauri::command]
fn app_shell_status() -> &'static str {
    "frontend-fixture"
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![app_shell_status])
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                window.set_title("莲心 AI")?;
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Lianxin UI");
}
