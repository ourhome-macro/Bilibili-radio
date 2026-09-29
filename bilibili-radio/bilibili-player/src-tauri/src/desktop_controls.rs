use std::{
    collections::HashSet,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, TrayIcon, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

#[derive(Default)]
pub struct ControlState {
    pub quitting: AtomicBool,
    finished: AtomicBool,
    held: Mutex<HashSet<u32>>,
    warnings: Mutex<Vec<String>>,
    tray: Mutex<Option<TrayIcon>>,
}

// Separate profiles are also used for isolated desktop diagnostics.
pub fn trace(app: &tauri::AppHandle, message: &str) {
    if std::env::var_os("BILIBILI_RADIO_PROFILE_DIR").is_none() {
        return;
    }
    use std::io::Write;
    if let Ok(directory) = super::window_geometry::profile_dir(app) {
        if let Ok(mut file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join("desktop-lifecycle.log"))
        {
            let _ = writeln!(file, "{} {message}", std::process::id());
        }
    }
}

pub fn show_main(app: &tauri::AppHandle) {
    trace(app, "show main");
    if let Some(window) = app.get_webview_window("main") {
        if window.is_minimized().unwrap_or(false) {
            let _ = window.unminimize();
        }
        super::window_geometry::keep_on_screen(&window);
        let _ = window.show();
        let _ = window.set_focus();
    }
}

pub fn hide_main(app: &tauri::AppHandle, window: &WebviewWindow) {
    trace(app, "hide main requested");
    super::window_geometry::capture(app, window);
    super::window_geometry::save(app);
    let _ = app.emit_to("main", "desktop:control", "checkpoint");
    let result = window.hide();
    trace(app, &format!("hide main result: {result:?}"));
}

pub fn request_quit(app: &tauri::AppHandle) {
    trace(app, "quit requested");
    if app
        .state::<ControlState>()
        .quitting
        .swap(true, Ordering::SeqCst)
    {
        return;
    }
    for window in app.webview_windows().values() {
        super::window_geometry::capture(app, window);
    }
    super::window_geometry::save(app);
    let _ = app.emit_to("main", "desktop:control", "quit");
    let app = app.clone();
    std::thread::spawn(move || {
        std::thread::sleep(std::time::Duration::from_secs(5));
        // Also exit when the renderer is unresponsive; checkpoints are periodic.
        finish_quit(&app);
    });
}

fn finish_quit(app: &tauri::AppHandle) {
    trace(app, "quit checkpoint completed or deadline reached");
    if app
        .state::<ControlState>()
        .finished
        .swap(true, Ordering::SeqCst)
    {
        return;
    }
    let _ = app.global_shortcut().unregister_all();
    super::shutdown_backend(app);
    app.exit(0);
}

#[tauri::command]
pub fn finish_desktop_exit(window: WebviewWindow) -> Result<(), String> {
    let app = window.app_handle();
    if window.label() != "main" || !app.state::<ControlState>().quitting.load(Ordering::SeqCst) {
        return Err("No desktop exit is pending".to_string());
    }
    finish_quit(app);
    Ok(())
}

#[tauri::command]
pub fn desktop_control_status(window: WebviewWindow) -> Vec<String> {
    window
        .app_handle()
        .state::<ControlState>()
        .warnings
        .lock()
        .map(|v| v.clone())
        .unwrap_or_default()
}

pub fn initialize(app: &tauri::AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "打开播放器", true, None::<&str>)?;
    let play = MenuItem::with_id(
        app,
        "toggle-play",
        "播放 / 暂停    Ctrl+Alt+Space",
        true,
        None::<&str>,
    )?;
    let previous = MenuItem::with_id(app, "prev", "上一曲    Ctrl+Alt+Left", true, None::<&str>)?;
    let next = MenuItem::with_id(app, "next", "下一曲    Ctrl+Alt+Right", true, None::<&str>)?;
    let locate = MenuItem::with_id(app, "locate", "定位当前播放", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
    let menu = Menu::with_items(
        app,
        &[&open, &play, &previous, &next, &locate, &separator, &quit],
    )?;
    let mut builder = TrayIconBuilder::with_id("bilibili-radio")
        .tooltip("Bilibili Radio · 双击打开播放器")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_main(app),
            "quit" => request_quit(app),
            "locate" => {
                show_main(app);
                let _ = app.emit_to("main", "desktop:control", "locate");
            }
            "toggle-play" | "prev" | "next" => {
                let _ = app.emit_to("main", "desktop:control", event.id.as_ref());
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                }
            ) {
                show_main(tray.app_handle());
            }
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    let tray = builder.build(app)?;
    *app.state::<ControlState>().tray.lock().unwrap() = Some(tray);
    for (shortcut, action) in [
        ("Ctrl+Alt+Space", "toggle-play"),
        ("Ctrl+Alt+ArrowLeft", "prev"),
        ("Ctrl+Alt+ArrowRight", "next"),
    ] {
        if let Err(error) =
            app.global_shortcut()
                .on_shortcut(shortcut, move |app, shortcut, event| {
                    let state = app.state::<ControlState>();
                    if state.quitting.load(Ordering::Relaxed) {
                        return;
                    }
                    let Ok(mut held) = state.held.lock() else {
                        return;
                    };
                    if event.state == ShortcutState::Pressed {
                        if held.insert(shortcut.id()) {
                            trace(app, action);
                            let _ = app.emit_to("main", "desktop:control", action);
                        }
                    } else {
                        held.remove(&shortcut.id());
                    }
                })
        {
            app.state::<ControlState>()
                .warnings
                .lock()
                .unwrap()
                .push(format!("快捷键 {shortcut} 无法注册，可能已被占用：{error}"));
        }
    }
    Ok(())
}
