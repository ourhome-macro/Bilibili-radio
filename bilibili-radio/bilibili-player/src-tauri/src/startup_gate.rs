// Own the application for its entire lifetime, before any window/backend exists.
// A named auto-reset event also remembers reopen requests during cold startup.
#[cfg(windows)]
pub struct StartupGate {
    mutex: std::os::windows::io::OwnedHandle,
    event: std::os::windows::io::OwnedHandle,
    stopped: std::sync::atomic::AtomicBool,
}

#[cfg(windows)]
pub fn acquire() -> std::io::Result<Option<StartupGate>> {
    use std::os::windows::io::{FromRawHandle, OwnedHandle};
    use windows_sys::Win32::{
        Foundation::{WAIT_ABANDONED, WAIT_OBJECT_0, WAIT_TIMEOUT},
        System::Threading::{CreateEventW, CreateMutexW, SetEvent, WaitForSingleObject},
    };
    let name: Vec<u16> = "Local\\com.ourhome.bilibiliradio-startup-gate\0"
        .encode_utf16()
        .collect();
    unsafe {
        let event_name: Vec<u16> = "Local\\com.ourhome.bilibiliradio-reopen\0"
            .encode_utf16()
            .collect();
        // Create the event before acquiring ownership, so no early signal is lost.
        let event_raw = CreateEventW(std::ptr::null(), 0, 0, event_name.as_ptr());
        if event_raw.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let event = OwnedHandle::from_raw_handle(event_raw);
        let raw = CreateMutexW(std::ptr::null(), 0, name.as_ptr());
        if raw.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let handle = OwnedHandle::from_raw_handle(raw);
        match WaitForSingleObject(raw, 0) {
            WAIT_OBJECT_0 | WAIT_ABANDONED => Ok(Some(StartupGate {
                mutex: handle,
                event,
                stopped: std::sync::atomic::AtomicBool::new(false),
            })),
            WAIT_TIMEOUT => {
                use windows_sys::Win32::UI::WindowsAndMessaging::{
                    AllowSetForegroundWindow, FindWindowW, GetWindowThreadProcessId,
                };
                let class: Vec<u16> = "com.ourhome.bilibiliradio-sic\0".encode_utf16().collect();
                let title: Vec<u16> = "com.ourhome.bilibiliradio-siw\0".encode_utf16().collect();
                let hwnd = FindWindowW(class.as_ptr(), title.as_ptr());
                if !hwnd.is_null() {
                    let mut pid = 0;
                    GetWindowThreadProcessId(hwnd, &mut pid);
                    if pid != 0 {
                        AllowSetForegroundWindow(pid);
                    }
                }
                SetEvent(event_raw);
                Ok(None)
            }
            _ => Err(std::io::Error::last_os_error()),
        }
    }
}

#[cfg(windows)]
impl Drop for StartupGate {
    fn drop(&mut self) {
        use std::os::windows::io::AsRawHandle;
        unsafe {
            windows_sys::Win32::System::Threading::ReleaseMutex(self.mutex.as_raw_handle());
        }
    }
}

#[cfg(windows)]
pub fn listen(app: &tauri::AppHandle) {
    use std::os::windows::io::AsRawHandle;
    use std::sync::atomic::Ordering;
    use tauri::Manager;
    let app = app.clone();
    std::thread::spawn(move || {
        let state = app.state::<StartupGate>();
        while !state.stopped.load(Ordering::SeqCst) {
            let result = unsafe {
                windows_sys::Win32::System::Threading::WaitForSingleObject(
                    state.event.as_raw_handle(),
                    200,
                )
            };
            if result == windows_sys::Win32::Foundation::WAIT_OBJECT_0
                && !state.stopped.load(Ordering::SeqCst)
            {
                super::desktop_controls::show_main(&app);
            }
        }
    });
}

#[cfg(windows)]
pub fn stop(app: &tauri::AppHandle) {
    use tauri::Manager;
    if let Some(state) = app.try_state::<StartupGate>() {
        state
            .stopped
            .store(true, std::sync::atomic::Ordering::SeqCst);
    }
}

#[cfg(not(windows))]
pub struct StartupGate;
#[cfg(not(windows))]
pub fn acquire() -> std::io::Result<Option<StartupGate>> {
    Ok(Some(StartupGate))
}
#[cfg(not(windows))]
pub fn listen(_: &tauri::AppHandle) {}
#[cfg(not(windows))]
pub fn stop(_: &tauri::AppHandle) {}
