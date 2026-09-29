use serde::{Deserialize, Serialize};
use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{LogicalSize, Manager, PhysicalPosition, WebviewWindow};

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq)]
struct SavedGeometry {
    monitor: Option<String>,
    // Logical offsets within the monitor work area survive DPI changes.
    x: f64,
    y: f64,
    width: f64,
    height: f64,
    maximized: bool,
}

pub struct GeometryState {
    path: PathBuf,
    windows: Mutex<HashMap<String, SavedGeometry>>,
    pending: AtomicBool,
    pub ready: AtomicBool,
}

pub fn profile_dir(app: &tauri::AppHandle) -> Result<PathBuf, String> {
    if let Some(path) = std::env::var_os("BILIBILI_RADIO_PROFILE_DIR") {
        return Ok(PathBuf::from(path));
    }
    app.path().app_config_dir().map_err(|e| e.to_string())
}

pub fn initialize(app: &tauri::AppHandle) -> Result<(), String> {
    let path = profile_dir(app)?.join("window-geometry.json");
    let windows = std::fs::read(&path)
        .ok()
        .and_then(|data| serde_json::from_slice(&data).ok())
        .unwrap_or_default();
    app.manage(GeometryState {
        path,
        windows: Mutex::new(windows),
        pending: AtomicBool::new(false),
        ready: AtomicBool::new(false),
    });
    Ok(())
}

fn fitted(saved: &SavedGeometry, width: f64, height: f64) -> (f64, f64, f64, f64) {
    let w = saved.width.clamp(160.0, width.max(160.0));
    let h = saved.height.clamp(80.0, height.max(80.0));
    (
        saved.x.clamp(0.0, (width - w).max(0.0)),
        saved.y.clamp(0.0, (height - h).max(0.0)),
        w,
        h,
    )
}

#[cfg(windows)]
fn normal_placement(window: &WebviewWindow, monitor: &tauri::Monitor) -> Option<SavedGeometry> {
    use windows_sys::Win32::UI::{HiDpi::AdjustWindowRectExForDpi, WindowsAndMessaging::*};
    unsafe {
        let hwnd = window.hwnd().ok()?.0;
        let mut placement: WINDOWPLACEMENT = std::mem::zeroed();
        placement.length = std::mem::size_of_val(&placement) as u32;
        if GetWindowPlacement(hwnd, &mut placement) == 0 {
            return None;
        }
        let scale = monitor.scale_factor();
        let mut frame = windows_sys::Win32::Foundation::RECT::default();
        if AdjustWindowRectExForDpi(
            &mut frame,
            (GetWindowLongW(hwnd, GWL_STYLE) as u32) & !WS_MAXIMIZE,
            0,
            GetWindowLongW(hwnd, GWL_EXSTYLE) as u32,
            (scale * 96.0).round() as u32,
        ) == 0
        {
            return None;
        }
        let rect = placement.rcNormalPosition;
        // WINDOWPLACEMENT uses workspace coordinates for a normal top-level window.
        let origin = monitor.position();
        Some(SavedGeometry {
            monitor: monitor.name().cloned(),
            x: (rect.left - origin.x) as f64 / scale,
            y: (rect.top - origin.y) as f64 / scale,
            width: ((rect.right - rect.left) - (frame.right - frame.left)) as f64 / scale,
            height: ((rect.bottom - rect.top) - (frame.bottom - frame.top)) as f64 / scale,
            maximized: placement.showCmd == SW_SHOWMAXIMIZED as u32,
        })
    }
}

#[cfg(windows)]
fn restore_normal_placement(
    window: &WebviewWindow,
    monitor: &tauri::Monitor,
    saved: &SavedGeometry,
) -> bool {
    use windows_sys::Win32::UI::{HiDpi::AdjustWindowRectExForDpi, WindowsAndMessaging::*};
    unsafe {
        let Ok(handle) = window.hwnd() else {
            return false;
        };
        let hwnd = handle.0;
        let scale = monitor.scale_factor();
        let mut placement: WINDOWPLACEMENT = std::mem::zeroed();
        placement.length = std::mem::size_of_val(&placement) as u32;
        if GetWindowPlacement(hwnd, &mut placement) == 0 {
            return false;
        }
        let mut frame = windows_sys::Win32::Foundation::RECT::default();
        if AdjustWindowRectExForDpi(
            &mut frame,
            (GetWindowLongW(hwnd, GWL_STYLE) as u32) & !WS_MAXIMIZE,
            0,
            GetWindowLongW(hwnd, GWL_EXSTYLE) as u32,
            (scale * 96.0).round() as u32,
        ) == 0
        {
            return false;
        }
        let area = monitor.work_area();
        let frame_width = frame.right - frame.left;
        let frame_height = frame.bottom - frame.top;
        let (x, y, w, h) = fitted(
            saved,
            (area.size.width as f64 - frame_width as f64) / scale,
            (area.size.height as f64 - frame_height as f64) / scale,
        );
        let left = monitor.position().x + (x * scale).round() as i32;
        let top = monitor.position().y + (y * scale).round() as i32;
        placement.rcNormalPosition = windows_sys::Win32::Foundation::RECT {
            left,
            top,
            right: left + (w * scale).round() as i32 + frame_width,
            bottom: top + (h * scale).round() as i32 + frame_height,
        };
        placement.showCmd = if saved.maximized {
            SW_SHOWMAXIMIZED
        } else {
            SW_SHOWNOACTIVATE
        } as u32;
        placement.flags = 0;
        SetWindowPlacement(hwnd, &placement) != 0
    }
}

pub fn restore(app: &tauri::AppHandle, window: &WebviewWindow) -> bool {
    let state = app.state::<GeometryState>();
    let saved = state
        .windows
        .lock()
        .ok()
        .and_then(|map| map.get(window.label()).cloned());
    let Some(saved) = saved else {
        return false;
    };
    if [saved.x, saved.y, saved.width, saved.height]
        .iter()
        .any(|v| !v.is_finite())
    {
        return false;
    }
    let monitors = window.available_monitors().unwrap_or_default();
    let monitor = monitors
        .iter()
        .find(|m| m.name().cloned() == saved.monitor)
        .cloned()
        .or_else(|| window.primary_monitor().ok().flatten());
    let Some(monitor) = monitor else {
        return false;
    };
    #[cfg(windows)]
    if window.label() == "main" {
        return restore_normal_placement(window, &monitor, &saved);
    }
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let (x, y, w, h) = fitted(
        &saved,
        area.size.width as f64 / scale,
        area.size.height as f64 / scale,
    );
    let _ = window.set_position(PhysicalPosition::new(
        area.position.x + (x * scale).round() as i32,
        area.position.y + (y * scale).round() as i32,
    ));
    let _ = window.set_size(LogicalSize::new(w, h));
    keep_on_screen(window);
    if saved.maximized && window.label() == "main" {
        let _ = window.maximize();
    }
    true
}

pub fn keep_on_screen(window: &WebviewWindow) {
    #[cfg(windows)]
    if let Ok(handle) = window.hwnd() {
        use windows_sys::Win32::UI::WindowsAndMessaging::{IsIconic, IsZoomed};
        if unsafe { IsIconic(handle.0) != 0 || IsZoomed(handle.0) != 0 } {
            return;
        }
    }
    if window.is_minimized().unwrap_or(false) || window.is_maximized().unwrap_or(false) {
        return;
    }
    let Ok(position) = window.outer_position() else {
        return;
    };
    let Ok(size) = window.outer_size() else {
        return;
    };
    let monitors = window.available_monitors().unwrap_or_default();
    let monitor = monitors
        .iter()
        .find(|m| {
            let a = m.work_area();
            position.x >= a.position.x
                && position.x < a.position.x + a.size.width as i32
                && position.y >= a.position.y
                && position.y < a.position.y + a.size.height as i32
        })
        .cloned()
        .or_else(|| window.primary_monitor().ok().flatten());
    if let Some(monitor) = monitor {
        let area = monitor.work_area();
        let x = position.x.clamp(
            area.position.x,
            area.position.x + area.size.width.saturating_sub(size.width) as i32,
        );
        let y = position.y.clamp(
            area.position.y,
            area.position.y + area.size.height.saturating_sub(size.height) as i32,
        );
        if x != position.x || y != position.y {
            let _ = window.set_position(PhysicalPosition::new(x, y));
        }
    }
}

pub fn capture(app: &tauri::AppHandle, window: &WebviewWindow) {
    let Some(state) = app.try_state::<GeometryState>() else {
        return;
    };
    #[cfg(windows)]
    if let Ok(handle) = window.hwnd() {
        if unsafe { windows_sys::Win32::UI::WindowsAndMessaging::IsIconic(handle.0) != 0 } {
            return;
        }
    }
    if !state.ready.load(Ordering::Relaxed)
        || window.is_minimized().unwrap_or(true)
        || !window.is_visible().unwrap_or(false)
    {
        return;
    }
    let maximized = window.is_maximized().unwrap_or(false);
    let Ok(mut cache) = state.windows.lock() else {
        return;
    };
    #[cfg(windows)]
    let native = if window.label() == "main" {
        window
            .current_monitor()
            .ok()
            .flatten()
            .and_then(|m| normal_placement(window, &m))
    } else {
        None
    };
    #[cfg(not(windows))]
    let native: Option<SavedGeometry> = None;
    if let Some(next) = native {
        if cache.get(window.label()) == Some(&next) {
            return;
        }
        cache.insert(window.label().to_string(), next);
    } else if maximized {
        if let Some(saved) = cache.get_mut(window.label()) {
            saved.maximized = true;
        }
    } else {
        let (Ok(position), Ok(size), Ok(Some(monitor))) = (
            window.outer_position(),
            window.inner_size(),
            window.current_monitor(),
        ) else {
            return;
        };
        let scale = monitor.scale_factor();
        let origin = monitor.work_area().position;
        let next = SavedGeometry {
            monitor: monitor.name().cloned(),
            x: (position.x - origin.x) as f64 / scale,
            y: (position.y - origin.y) as f64 / scale,
            width: size.width as f64 / scale,
            height: size.height as f64 / scale,
            maximized: false,
        };
        if cache.get(window.label()) == Some(&next) {
            return;
        }
        cache.insert(window.label().to_string(), next);
    }
    drop(cache);
    if !state.pending.swap(true, Ordering::SeqCst) {
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(std::time::Duration::from_millis(500));
            save(&app);
            if let Some(state) = app.try_state::<GeometryState>() {
                state.pending.store(false, Ordering::SeqCst);
            }
        });
    }
}

pub fn save(app: &tauri::AppHandle) {
    let Some(state) = app.try_state::<GeometryState>() else {
        return;
    };
    let Ok(cache) = state.windows.lock() else {
        return;
    };
    let Some(parent) = state.path.parent() else {
        return;
    };
    if let Ok(data) = serde_json::to_vec_pretty(&*cache) {
        let temporary = state.path.with_extension("tmp");
        let result = std::fs::create_dir_all(parent)
            .and_then(|_| std::fs::write(&temporary, data))
            .and_then(|_| std::fs::rename(&temporary, &state.path));
        if let Err(error) = result {
            eprintln!("Failed to save window geometry: {error}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn saved() -> SavedGeometry {
        SavedGeometry {
            monitor: None,
            x: 120.0,
            y: 150.0,
            width: 1000.0,
            height: 700.0,
            maximized: false,
        }
    }
    #[test]
    fn retains_logical_geometry_across_dpi() {
        let (x, y, w, h) = fitted(&saved(), 1920.0, 1080.0);
        assert_eq!(
            (x * 1.5, y * 1.5, w * 1.5, h * 1.5),
            (180.0, 225.0, 1500.0, 1050.0)
        );
    }
    #[test]
    fn disconnected_monitor_coordinates_are_clamped() {
        let mut s = saved();
        s.x = 3000.0;
        s.y = -900.0;
        assert_eq!(fitted(&s, 1280.0, 900.0), (280.0, 0.0, 1000.0, 700.0));
    }
    #[test]
    fn oversized_window_fits_smaller_work_area() {
        assert_eq!(fitted(&saved(), 800.0, 600.0), (0.0, 0.0, 800.0, 600.0));
    }
}
