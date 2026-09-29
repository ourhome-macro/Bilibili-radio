#[cfg(windows)]
pub struct BackendJob {
    _handle: std::os::windows::io::OwnedHandle,
}

#[cfg(windows)]
impl BackendJob {
    pub fn attach(child: &std::process::Child) -> std::io::Result<Self> {
        use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
        use windows_sys::Win32::System::JobObjects::*;
        unsafe {
            let raw = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if raw.is_null() {
                return Err(std::io::Error::last_os_error());
            }
            let handle = OwnedHandle::from_raw_handle(raw);
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            if SetInformationJobObject(
                raw,
                JobObjectExtendedLimitInformation,
                &limits as *const _ as *const _,
                std::mem::size_of_val(&limits) as u32,
            ) == 0
                || AssignProcessToJobObject(raw, child.as_raw_handle()) == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            Ok(Self { _handle: handle })
        }
    }
}

#[cfg(not(windows))]
pub struct BackendJob;
#[cfg(not(windows))]
impl BackendJob {
    pub fn attach(_: &std::process::Child) -> std::io::Result<Self> {
        Ok(Self)
    }
}
