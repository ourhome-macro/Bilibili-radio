#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend_job;
mod desktop_controls;
mod startup_gate;
mod window_geometry;

use std::{
    env,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

use serde::Serialize;
use tauri::{
    Emitter, Manager, PhysicalPosition, PhysicalSize, Position, Size, State, WebviewWindow,
    WindowEvent,
};

const DEFAULT_DESKTOP_PORT: u16 = 41517;
const LYRICS_WINDOW_LABEL: &str = "desktop-lyrics";
const LYRICS_UPDATE_EVENT: &str = "desktop-lyrics:update";
const LYRICS_WINDOW_WIDTH: f64 = 920.0;
const LYRICS_WINDOW_HEIGHT: f64 = 112.0;
const LYRICS_WINDOW_BOTTOM_MARGIN: i32 = 96;
const DEFAULT_LYRICS_FONT_SIZE: u32 = 30;
const MIN_LYRICS_FONT_SIZE: u32 = 22;
const MAX_LYRICS_FONT_SIZE: u32 = 48;
const MIN_LYRICS_WINDOW_WIDTH: f64 = 760.0;
const MAX_LYRICS_WINDOW_WIDTH: f64 = 1240.0;
const MIN_LYRICS_WINDOW_HEIGHT: f64 = 92.0;
const MAX_LYRICS_WINDOW_HEIGHT: f64 = 168.0;

struct BackendState {
    endpoint: Mutex<Option<String>>,
    child: Mutex<Option<Child>>,
    job: Mutex<Option<backend_job::BackendJob>>,
    lyrics_payload: Mutex<LyricsPayload>,
}

#[derive(Clone, Serialize)]
struct LyricsPayload {
    enabled: bool,
    text: String,
    color: String,
    #[serde(rename = "fontSize")]
    font_size: u32,
    title: String,
    #[serde(rename = "isPlaying")]
    is_playing: bool,
}

#[derive(Clone, Serialize)]
struct LyricsWindowPoint {
    x: i32,
    y: i32,
}

#[derive(Clone, Serialize)]
struct LyricsWindowSize {
    width: u32,
    height: u32,
}

#[derive(Clone, Serialize)]
struct LyricsWindowSnapshot {
    exists: bool,
    visible: Option<bool>,
    minimized: Option<bool>,
    position: Option<LyricsWindowPoint>,
    size: Option<LyricsWindowSize>,
}

#[derive(Clone, Serialize)]
struct LyricsWindowStep {
    name: String,
    ok: bool,
    message: String,
}

#[derive(Clone, Serialize)]
struct LyricsWindowDebug {
    action: String,
    requested_enabled: Option<bool>,
    existed_before: bool,
    created: bool,
    status_before: LyricsWindowSnapshot,
    status_after_show: LyricsWindowSnapshot,
    status_after: LyricsWindowSnapshot,
    steps: Vec<LyricsWindowStep>,
}

impl LyricsWindowDebug {
    fn new(action: &str, requested_enabled: Option<bool>, app: &tauri::AppHandle) -> Self {
        let status_before = lyrics_window_snapshot(app);
        let existed_before = status_before.exists;
        Self {
            action: action.to_string(),
            requested_enabled,
            existed_before,
            created: false,
            status_after_show: empty_lyrics_window_snapshot(),
            status_after: status_before.clone(),
            status_before,
            steps: Vec::new(),
        }
    }
}

impl BackendState {
    fn new() -> Self {
        Self {
            endpoint: Mutex::new(None),
            child: Mutex::new(None),
            job: Mutex::new(None),
            lyrics_payload: Mutex::new(default_lyrics_payload()),
        }
    }
}

impl Drop for BackendState {
    fn drop(&mut self) {
        if let Ok(job) = self.job.get_mut() {
            job.take();
        }
        if let Ok(mut child_guard) = self.child.lock() {
            if let Some(mut child) = child_guard.take() {
                let _ = child.kill();
                let _ = child.wait();
            }
        }
    }
}

#[tauri::command]
fn desktop_backend_endpoint(state: State<'_, BackendState>) -> Result<String, String> {
    state
        .endpoint
        .lock()
        .map_err(|_| "Backend state lock is poisoned".to_string())?
        .clone()
        .ok_or_else(|| "Desktop backend is not ready".to_string())
}

#[tauri::command]
fn show_lyrics_window(
    app: tauri::AppHandle,
    state: State<'_, BackendState>,
) -> Result<LyricsWindowDebug, String> {
    let mut debug = LyricsWindowDebug::new("show_lyrics_window", Some(true), &app);
    log_desktop_lyrics_debug("received show_lyrics_window command");

    let payload = state
        .lyrics_payload
        .lock()
        .map_err(|_| "Lyrics payload lock is poisoned".to_string())?
        .clone();
    let window = ensure_lyrics_window(&app, &mut debug)?;
    resize_lyrics_window_for_font(&app, &window, payload.font_size, &mut debug);
    reveal_lyrics_window(&app, &window, &mut debug)?;
    record_step(
        &mut debug,
        "emit_payload",
        emit_lyrics_payload(&app, payload).map_err(|error| error.to_string()),
    );
    debug.status_after = lyrics_window_snapshot(&app);
    log_lyrics_window_debug(&debug);
    Ok(debug)
}

#[tauri::command]
fn hide_lyrics_window(
    app: tauri::AppHandle,
    state: State<'_, BackendState>,
) -> Result<LyricsWindowDebug, String> {
    let mut debug = LyricsWindowDebug::new("hide_lyrics_window", Some(false), &app);
    log_desktop_lyrics_debug("received hide_lyrics_window command");

    if let Some(window) = app.get_webview_window(LYRICS_WINDOW_LABEL) {
        let payload = default_lyrics_payload();
        window_geometry::capture(&app, &window);
        window_geometry::save(&app);
        *state
            .lyrics_payload
            .lock()
            .map_err(|_| "Lyrics payload lock is poisoned".to_string())? = payload.clone();
        record_step(
            &mut debug,
            "emit_disabled_payload",
            emit_lyrics_payload(&app, payload).map_err(|error| error.to_string()),
        );
        record_step(
            &mut debug,
            "hide",
            window.hide().map_err(|error| error.to_string()),
        );
    } else {
        record_text_step(&mut debug, "get_existing_window", true, "window not found");
    }
    debug.status_after = lyrics_window_snapshot(&app);
    log_lyrics_window_debug(&debug);
    Ok(debug)
}

#[tauri::command]
fn set_lyrics_window_payload(
    app: tauri::AppHandle,
    state: State<'_, BackendState>,
    enabled: bool,
    text: String,
    color: String,
    font_size: u32,
    title: String,
    is_playing: bool,
) -> Result<LyricsWindowDebug, String> {
    let mut debug = LyricsWindowDebug::new("set_lyrics_window_payload", Some(enabled), &app);
    log_desktop_lyrics_debug(&format!(
        "received set_lyrics_window_payload command enabled={enabled}"
    ));

    let payload = LyricsPayload {
        enabled,
        text: normalized_lyrics_text(text),
        color: normalized_lyrics_color(color),
        font_size: normalized_lyrics_font_size(font_size),
        title,
        is_playing,
    };
    *state
        .lyrics_payload
        .lock()
        .map_err(|_| "Lyrics payload lock is poisoned".to_string())? = payload.clone();

    if enabled {
        let window = ensure_lyrics_window(&app, &mut debug)?;
        resize_lyrics_window_for_font(&app, &window, payload.font_size, &mut debug);
        reveal_lyrics_window(&app, &window, &mut debug)?;
    } else {
        record_text_step(&mut debug, "skip_reveal", true, "enabled=false");
    }

    record_step(
        &mut debug,
        "emit_payload",
        emit_lyrics_payload(&app, payload).map_err(|error| error.to_string()),
    );
    debug.status_after = lyrics_window_snapshot(&app);
    log_lyrics_window_debug(&debug);
    Ok(debug)
}

#[tauri::command]
fn current_lyrics_window_payload(state: State<'_, BackendState>) -> Result<LyricsPayload, String> {
    state
        .lyrics_payload
        .lock()
        .map_err(|_| "Lyrics payload lock is poisoned".to_string())
        .map(|payload| payload.clone())
}

#[tauri::command]
fn lyrics_window_debug_status(app: tauri::AppHandle) -> LyricsWindowDebug {
    let mut debug = LyricsWindowDebug::new("lyrics_window_debug_status", None, &app);
    record_text_step(&mut debug, "snapshot", true, "status only");
    debug.status_after = lyrics_window_snapshot(&app);
    log_lyrics_window_debug(&debug);
    debug
}

fn main() {
    let gate = match startup_gate::acquire() {
        Ok(Some(gate)) => gate,
        Ok(None) => return,
        Err(error) => {
            write_startup_error(&format!("Failed to acquire desktop startup gate: {error}"));
            return;
        }
    };
    if let Err(error) = run_app(gate) {
        write_startup_error(&format!("{error:#}"));
        panic!("failed to run Bilibili Radio desktop client: {error:#}");
    }
}

fn run_app(gate: startup_gate::StartupGate) -> tauri::Result<()> {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            desktop_controls::show_main(app)
        }))
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .manage(gate)
        .manage(desktop_controls::ControlState::default())
        .manage(BackendState::new())
        .setup(|app| {
            window_geometry::initialize(app.handle())?;
            let (endpoint, child, job) = start_backend(app)?;
            let state = app.state::<BackendState>();
            *state
                .endpoint
                .lock()
                .map_err(|_| "Backend endpoint lock is poisoned".to_string())? = Some(endpoint);
            *state
                .child
                .lock()
                .map_err(|_| "Backend child lock is poisoned".to_string())? = Some(child);
            *state
                .job
                .lock()
                .map_err(|_| "Backend job lock is poisoned".to_string())? = Some(job);
            desktop_controls::initialize(app.handle())?;
            if let Some(window) = app.get_webview_window("main") {
                window_geometry::restore(app.handle(), &window);
            }
            initialize_lyrics_window(app.handle());
            app.state::<window_geometry::GeometryState>()
                .ready
                .store(true, std::sync::atomic::Ordering::SeqCst);
            desktop_controls::show_main(app.handle());
            startup_gate::listen(app.handle());
            if let Some(window) = app.get_webview_window("main") {
                window_geometry::capture(app.handle(), &window);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            let app = window.app_handle();
            let Some(webview) = app.get_webview_window(window.label()) else {
                return;
            };
            let window = &webview;
            if app
                .state::<desktop_controls::ControlState>()
                .quitting
                .load(std::sync::atomic::Ordering::SeqCst)
            {
                return;
            }
            match event {
                WindowEvent::CloseRequested { api, .. } if window.label() == "main" => {
                    desktop_controls::trace(app, "main close requested");
                    api.prevent_close();
                    desktop_controls::hide_main(app, window);
                }
                WindowEvent::CloseRequested { api, .. }
                    if window.label() == LYRICS_WINDOW_LABEL =>
                {
                    api.prevent_close();
                    window_geometry::capture(app, window);
                    window_geometry::save(app);
                    let _ = window.hide();
                    let _ = app.emit_to("main", "desktop:control", "lyrics-close");
                }
                WindowEvent::Resized(_)
                    if window.label() == "main" && window.is_minimized().unwrap_or(false) =>
                {
                    desktop_controls::hide_main(app, window);
                }
                WindowEvent::Moved(_)
                | WindowEvent::Resized(_)
                | WindowEvent::ScaleFactorChanged { .. } => {
                    window_geometry::capture(app, window);
                }
                _ => {}
            }
        })
        .invoke_handler(tauri::generate_handler![
            desktop_backend_endpoint,
            show_lyrics_window,
            hide_lyrics_window,
            set_lyrics_window_payload,
            current_lyrics_window_payload,
            lyrics_window_debug_status,
            desktop_controls::finish_desktop_exit,
            desktop_controls::desktop_control_status
        ])
        .build(tauri::generate_context!())?;
    app.run(|app, event| {
        if matches!(event, tauri::RunEvent::Exit) {
            startup_gate::stop(app);
            window_geometry::save(app);
            shutdown_backend(app);
        }
    });
    Ok(())
}

fn shutdown_backend(app: &tauri::AppHandle) {
    let state = app.state::<BackendState>();
    if let Ok(mut job) = state.job.lock() {
        job.take();
    }
    if let Ok(mut child) = state.child.lock() {
        if let Some(mut process) = child.take() {
            let _ = process.kill();
            let _ = process.wait();
        }
    };
}

fn ensure_lyrics_window(
    app: &tauri::AppHandle,
    debug: &mut LyricsWindowDebug,
) -> Result<WebviewWindow, String> {
    if let Some(window) = app.get_webview_window(LYRICS_WINDOW_LABEL) {
        debug.existed_before = true;
        record_text_step(debug, "get_existing_window", true, "found existing window");
        return Ok(window);
    }

    record_text_step(
        debug,
        "get_existing_window",
        false,
        "configured desktop-lyrics window not found",
    );
    let message = "desktop-lyrics window is not available from Tauri config".to_string();
    log_desktop_lyrics_debug(&message);
    Err(message)
}

fn initialize_lyrics_window(app: &tauri::AppHandle) {
    let mut debug = LyricsWindowDebug::new("initialize_lyrics_window", Some(false), app);
    if let Some(window) = app.get_webview_window(LYRICS_WINDOW_LABEL) {
        record_text_step(
            &mut debug,
            "get_existing_window",
            true,
            "found configured window",
        );
        configure_lyrics_window(&window, &mut debug);
        if window_geometry::restore(app, &window) {
            record_text_step(
                &mut debug,
                "restore_position",
                true,
                "saved geometry restored",
            );
        } else if let Err(error) = position_lyrics_window(app, &window) {
            record_text_step(&mut debug, "set_position", false, &error);
        } else {
            record_text_step(&mut debug, "set_position", true, "ok");
        }
        record_step(
            &mut debug,
            "set_ignore_cursor_events_false",
            window
                .set_ignore_cursor_events(false)
                .map_err(|error| error.to_string()),
        );
        record_step(
            &mut debug,
            "hide_initial",
            window.hide().map_err(|error| error.to_string()),
        );
    } else {
        record_text_step(
            &mut debug,
            "get_existing_window",
            false,
            "configured window missing during setup",
        );
    }
    debug.status_after = lyrics_window_snapshot(app);
    log_lyrics_window_debug(&debug);
}

fn configure_lyrics_window(window: &WebviewWindow, debug: &mut LyricsWindowDebug) {
    record_step(
        debug,
        "set_always_on_top",
        window
            .set_always_on_top(true)
            .map_err(|error| error.to_string()),
    );
    record_step(
        debug,
        "set_skip_taskbar",
        window
            .set_skip_taskbar(true)
            .map_err(|error| error.to_string()),
    );
    record_step(
        debug,
        "set_visible_on_all_workspaces",
        window
            .set_visible_on_all_workspaces(true)
            .map_err(|error| error.to_string()),
    );
}

fn position_lyrics_window(app: &tauri::AppHandle, window: &WebviewWindow) -> Result<(), String> {
    let size = window
        .outer_size()
        .ok()
        .map(|size| (size.width as f64, size.height as f64))
        .unwrap_or((LYRICS_WINDOW_WIDTH, LYRICS_WINDOW_HEIGHT));
    let Some((x, y)) = lyrics_window_initial_position(app, size.0, size.1) else {
        return Ok(());
    };
    window
        .set_position(Position::Physical(PhysicalPosition::new(
            x.round() as i32,
            y.round() as i32,
        )))
        .map_err(|error| error.to_string())
}

fn lyrics_window_initial_position(
    app: &tauri::AppHandle,
    width: f64,
    height: f64,
) -> Option<(f64, f64)> {
    let monitor = app.primary_monitor().ok()??;
    let work_area = monitor.work_area();
    let width = width.round() as i32;
    let height = height.round() as i32;
    let x = work_area.position.x + ((work_area.size.width as i32 - width) / 2).max(0);
    let y = work_area.position.y
        + (work_area.size.height as i32 - height - LYRICS_WINDOW_BOTTOM_MARGIN).max(0);
    Some((x as f64, y as f64))
}

fn resize_lyrics_window_for_font(
    app: &tauri::AppHandle,
    window: &WebviewWindow,
    font_size: u32,
    debug: &mut LyricsWindowDebug,
) {
    let target = lyrics_window_size_for_font(font_size);
    let current_size = window.outer_size().ok();
    if current_size
        .as_ref()
        .is_some_and(|size| size.width == target.width && size.height == target.height)
    {
        record_text_step(debug, "resize_for_font", true, "skip; unchanged");
        return;
    }

    let previous_position = window.outer_position().ok();
    let previous_size = current_size;
    let was_visible = window.is_visible().ok() == Some(true);
    record_step(
        debug,
        "resize_for_font",
        window
            .set_size(Size::Physical(target))
            .map_err(|error| error.to_string()),
    );

    let Some(position) = lyrics_window_position_after_resize(
        app,
        previous_position,
        previous_size,
        target,
        was_visible,
    ) else {
        return;
    };
    record_step(
        debug,
        "reposition_after_resize",
        window
            .set_position(Position::Physical(position))
            .map_err(|error| error.to_string()),
    );
}

fn lyrics_window_size_for_font(font_size: u32) -> PhysicalSize<u32> {
    let scale = normalized_lyrics_font_size(font_size) as f64 / DEFAULT_LYRICS_FONT_SIZE as f64;
    PhysicalSize::new(
        (LYRICS_WINDOW_WIDTH * scale)
            .clamp(MIN_LYRICS_WINDOW_WIDTH, MAX_LYRICS_WINDOW_WIDTH)
            .round() as u32,
        (LYRICS_WINDOW_HEIGHT * scale)
            .clamp(MIN_LYRICS_WINDOW_HEIGHT, MAX_LYRICS_WINDOW_HEIGHT)
            .round() as u32,
    )
}

fn lyrics_window_position_after_resize(
    app: &tauri::AppHandle,
    previous_position: Option<PhysicalPosition<i32>>,
    previous_size: Option<PhysicalSize<u32>>,
    target_size: PhysicalSize<u32>,
    _was_visible: bool,
) -> Option<PhysicalPosition<i32>> {
    if previous_position.is_none() || previous_size.is_none() {
        let (x, y) = lyrics_window_initial_position(
            app,
            target_size.width as f64,
            target_size.height as f64,
        )?;
        return Some(PhysicalPosition::new(x.round() as i32, y.round() as i32));
    }

    let previous_position = previous_position?;
    let previous_size = previous_size?;
    let center_x = previous_position.x as f64 + previous_size.width as f64 / 2.0;
    let bottom_y = previous_position.y as f64 + previous_size.height as f64;
    Some(PhysicalPosition::new(
        (center_x - target_size.width as f64 / 2.0).round() as i32,
        (bottom_y - target_size.height as f64).round() as i32,
    ))
}

fn reveal_lyrics_window(
    app: &tauri::AppHandle,
    window: &WebviewWindow,
    debug: &mut LyricsWindowDebug,
) -> Result<(), String> {
    debug.status_before = lyrics_window_snapshot(app);
    configure_lyrics_window(window, debug);
    record_step(
        debug,
        "unminimize",
        window.unminimize().map_err(|error| error.to_string()),
    );
    let show_result = window.show().map_err(|error| error.to_string());
    let show_error = show_result.as_ref().err().cloned();
    record_step(debug, "show", show_result);
    debug.status_after_show = lyrics_window_snapshot(app);
    if let Some(error) = show_error {
        debug.status_after = lyrics_window_snapshot(app);
        log_lyrics_window_debug(debug);
        return Err(error);
    }
    window_geometry::keep_on_screen(window);
    window_geometry::capture(app, window);
    record_text_step(debug, "set_position", true, "preserve user position");
    configure_lyrics_window(window, debug);
    record_step(
        debug,
        "set_ignore_cursor_events_false",
        window
            .set_ignore_cursor_events(false)
            .map_err(|error| error.to_string()),
    );
    debug.status_after = lyrics_window_snapshot(app);
    Ok(())
}

fn emit_lyrics_payload(app: &tauri::AppHandle, payload: LyricsPayload) -> Result<(), String> {
    app.emit_to(LYRICS_WINDOW_LABEL, LYRICS_UPDATE_EVENT, payload)
        .map_err(|error| error.to_string())
}

fn empty_lyrics_window_snapshot() -> LyricsWindowSnapshot {
    LyricsWindowSnapshot {
        exists: false,
        visible: None,
        minimized: None,
        position: None,
        size: None,
    }
}

fn lyrics_window_snapshot(app: &tauri::AppHandle) -> LyricsWindowSnapshot {
    app.get_webview_window(LYRICS_WINDOW_LABEL)
        .map(|window| lyrics_window_snapshot_from_window(&window))
        .unwrap_or_else(empty_lyrics_window_snapshot)
}

fn lyrics_window_snapshot_from_window(window: &WebviewWindow) -> LyricsWindowSnapshot {
    let position = window
        .outer_position()
        .ok()
        .map(|position| LyricsWindowPoint {
            x: position.x,
            y: position.y,
        });
    let size = window.outer_size().ok().map(|size| LyricsWindowSize {
        width: size.width,
        height: size.height,
    });

    LyricsWindowSnapshot {
        exists: true,
        visible: window.is_visible().ok(),
        minimized: window.is_minimized().ok(),
        position,
        size,
    }
}

fn record_step(debug: &mut LyricsWindowDebug, name: &str, result: Result<(), String>) -> bool {
    match result {
        Ok(()) => {
            record_text_step(debug, name, true, "ok");
            true
        }
        Err(error) => {
            record_text_step(debug, name, false, &error);
            false
        }
    }
}

fn record_text_step(debug: &mut LyricsWindowDebug, name: &str, ok: bool, message: &str) {
    debug.steps.push(LyricsWindowStep {
        name: name.to_string(),
        ok,
        message: message.to_string(),
    });
    log_desktop_lyrics_debug(&format!(
        "{} step={} ok={} message={}",
        debug.action, name, ok, message
    ));
}

fn log_lyrics_window_debug(debug: &LyricsWindowDebug) {
    log_desktop_lyrics_debug(&format!(
        "{} requested_enabled={:?} existed_before={} created={} before={} after_show={} after={}",
        debug.action,
        debug.requested_enabled,
        debug.existed_before,
        debug.created,
        format_lyrics_snapshot(&debug.status_before),
        format_lyrics_snapshot(&debug.status_after_show),
        format_lyrics_snapshot(&debug.status_after)
    ));
}

fn format_lyrics_snapshot(snapshot: &LyricsWindowSnapshot) -> String {
    let position = snapshot
        .position
        .as_ref()
        .map(|position| format!("{},{}", position.x, position.y))
        .unwrap_or_else(|| "none".to_string());
    let size = snapshot
        .size
        .as_ref()
        .map(|size| format!("{}x{}", size.width, size.height))
        .unwrap_or_else(|| "none".to_string());
    format!(
        "exists={} visible={:?} minimized={:?} position={} size={}",
        snapshot.exists, snapshot.visible, snapshot.minimized, position, size
    )
}

fn normalized_lyrics_text(text: String) -> String {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        "-".to_string()
    } else {
        trimmed.to_string()
    }
}

fn normalized_lyrics_color(color: String) -> String {
    let trimmed = color.trim();
    if trimmed.is_empty() {
        "#fb7299".to_string()
    } else {
        trimmed.to_string()
    }
}

fn normalized_lyrics_font_size(font_size: u32) -> u32 {
    font_size.clamp(MIN_LYRICS_FONT_SIZE, MAX_LYRICS_FONT_SIZE)
}

fn default_lyrics_payload() -> LyricsPayload {
    LyricsPayload {
        enabled: false,
        text: "-".to_string(),
        color: "#fb7299".to_string(),
        font_size: DEFAULT_LYRICS_FONT_SIZE,
        title: String::new(),
        is_playing: false,
    }
}

fn write_startup_error(message: &str) {
    let log_path = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(env::temp_dir)
        .join("Bilibili Radio")
        .join("startup-error.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = std::fs::write(log_path, message);
}

fn log_desktop_lyrics_debug(message: &str) {
    let log_path = env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(env::temp_dir)
        .join("Bilibili Radio")
        .join("desktop-lyrics.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let timestamp_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis())
        .unwrap_or_default();
    let line = format!("{timestamp_ms} {message}\n");
    let _ = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)
        .and_then(|mut file| file.write_all(line.as_bytes()));
}

fn start_backend(
    app: &tauri::App,
) -> Result<(String, Child, backend_job::BackendJob), Box<dyn std::error::Error>> {
    let port = choose_backend_port()?;
    let endpoint = format!("http://127.0.0.1:{port}");
    let data_dir = if env::var_os("BILIBILI_RADIO_PROFILE_DIR").is_some() {
        window_geometry::profile_dir(app.handle())?.join("data")
    } else {
        app.path().app_data_dir()?.join("data")
    };
    std::fs::create_dir_all(&data_dir)?;

    let mut command = backend_command(app)?;
    command
        .env("APP_RUNTIME", "desktop")
        .env("AUTH_MODE", "disabled")
        .env("SESSION_COOKIE_SECURE", "false")
        .env("APP_BIND_HOST", "127.0.0.1")
        .env("APP_BIND_PORT", port.to_string())
        .env("APP_DATA_DIR", data_dir)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null());

    let mut child = command.spawn()?;
    let job = match backend_job::BackendJob::attach(&child) {
        Ok(job) => job,
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(error.into());
        }
    };
    if let Err(error) = wait_for_ready(port, Duration::from_secs(25)) {
        let _ = child.kill();
        let _ = child.wait();
        return Err(error.into());
    }

    Ok((endpoint, child, job))
}

fn backend_command(app: &tauri::App) -> Result<Command, Box<dyn std::error::Error>> {
    if let Ok(exe_path) = env::var("BILIBILI_RADIO_BACKEND_EXE") {
        return Ok(Command::new(exe_path));
    }

    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let dev_entry = manifest_dir
        .parent()
        .and_then(|path| path.parent())
        .map(|path| path.join("py-radio").join("desktop_entry.py"));
    if cfg!(debug_assertions) {
        if let Some(entry) = dev_entry {
            if entry.exists() {
                let python =
                    env::var("BILIBILI_RADIO_PYTHON").unwrap_or_else(|_| "python".to_string());
                let mut command = Command::new(python);
                command.arg(entry);
                return Ok(command);
            }
        }
    }

    let packaged_backend = locate_packaged_backend(app)?;
    Ok(Command::new(packaged_backend))
}

fn locate_packaged_backend(app: &tauri::App) -> Result<PathBuf, Box<dyn std::error::Error>> {
    let resource_dir = app.path().resource_dir()?;
    let direct_backend = resource_dir.join("bilibili-radio-backend.exe");
    if direct_backend.exists() {
        return Ok(direct_backend);
    }

    find_backend_under(&resource_dir).ok_or_else(|| {
        format!(
            "bilibili-radio-backend.exe not found under {}",
            resource_dir.display()
        )
        .into()
    })
}

fn find_backend_under(directory: &std::path::Path) -> Option<PathBuf> {
    let entries = std::fs::read_dir(directory).ok()?;
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_file()
            && path
                .file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.eq_ignore_ascii_case("bilibili-radio-backend.exe"))
        {
            return Some(path);
        }
        if path.is_dir() {
            if let Some(found) = find_backend_under(&path) {
                return Some(found);
            }
        }
    }
    None
}

fn reserve_loopback_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", 0))?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn choose_backend_port() -> std::io::Result<u16> {
    if let Ok(value) = env::var("BILIBILI_RADIO_DESKTOP_PORT") {
        if let Ok(port) = value.parse::<u16>() {
            return reserve_specific_loopback_port(port).or_else(|_| reserve_loopback_port());
        }
    }

    reserve_specific_loopback_port(DEFAULT_DESKTOP_PORT).or_else(|_| reserve_loopback_port())
}

fn reserve_specific_loopback_port(port: u16) -> std::io::Result<u16> {
    let listener = TcpListener::bind(("127.0.0.1", port))?;
    drop(listener);
    Ok(port)
}

fn wait_for_ready(port: u16, timeout: Duration) -> Result<(), String> {
    let started_at = Instant::now();
    while started_at.elapsed() < timeout {
        if ready_probe(port) {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(250));
    }
    Err(format!("Backend did not become ready on 127.0.0.1:{port}"))
}

fn ready_probe(port: u16) -> bool {
    let address = format!("127.0.0.1:{port}");
    let Ok(mut stream) = TcpStream::connect(address) else {
        return false;
    };
    let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
    let request = b"GET /health/ready HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n";
    if stream.write_all(request).is_err() {
        return false;
    }
    let mut response = [0; 64];
    let Ok(bytes_read) = stream.read(&mut response) else {
        return false;
    };
    let head = String::from_utf8_lossy(&response[..bytes_read]);
    head.starts_with("HTTP/1.1 200") || head.starts_with("HTTP/1.0 200")
}
