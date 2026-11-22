//! Selección y validación puras; la enumeración nativa nunca crea ventanas.
use winit::monitor::MonitorHandle;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Bounds {
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
}

impl Bounds {
    pub fn contains(self, other: Self) -> bool {
        other.width > 0
            && other.height > 0
            && other.x >= self.x
            && other.y >= self.y
            && i64::from(other.x) + i64::from(other.width) <= i64::from(self.x) + i64::from(self.width)
            && i64::from(other.y) + i64::from(other.height) <= i64::from(self.y) + i64::from(self.height)
    }
}

#[derive(Debug, Clone)]
pub struct DisplayInfo {
    pub name: String,
    pub bounds: Bounds,
    pub scale: f64,
    pub primary: bool,
}

pub fn select(displays: &[DisplayInfo], selector: &str, allow_main: bool) -> Result<usize, String> {
    let index = if let Ok(i) = selector.parse::<usize>() {
        if i >= displays.len() {
            return Err(format!("display {i} inexistente; usa --list-displays"));
        }
        i
    } else {
        if selector.trim().is_empty() {
            return Err("nombre de display vacío".into());
        }
        let needle = selector.to_lowercase();
        let matches: Vec<_> = displays
            .iter()
            .enumerate()
            .filter(|(_, d)| d.name.to_lowercase().contains(&needle))
            .map(|(i, _)| i)
            .collect();
        match matches.as_slice() {
            [i] => *i,
            [] => {
                return Err(format!("display '{selector}' inexistente; usa --list-displays"));
            }
            _ => return Err(format!("display '{selector}' ambiguo; elige un índice")),
        }
    };
    if displays[index].primary && !allow_main {
        return Err("pantalla principal protegida; requiere --allow-main".into());
    }
    Ok(index)
}

pub fn describe(mon: &MonitorHandle, primary: Option<&MonitorHandle>) -> DisplayInfo {
    let p = mon.position();
    let s = mon.size();
    DisplayInfo {
        name: real_name(mon).unwrap_or_else(|| mon.name().unwrap_or_else(|| "desconocido".into())),
        bounds: Bounds { x: p.x, y: p.y, width: s.width, height: s.height },
        scale: mon.scale_factor(),
        // Si el compositor no identifica la principal, se exige opt-in (fail closed).
        primary: primary.is_none_or(|p| p == mon),
    }
}

#[cfg(target_os = "macos")]
fn real_name(mon: &MonitorHandle) -> Option<String> {
    use objc2::{class, msg_send, runtime::AnyObject};
    use winit::platform::macos::MonitorHandleExtMacOS;
    // NSScreenNumber es el CGDirectDisplayID de CoreGraphics, igual a native_id() de winit.
    unsafe {
        let screens: *mut AnyObject = msg_send![class!(NSScreen), screens];
        let count: usize = msg_send![screens, count];
        for i in 0..count {
            let screen: *mut AnyObject = msg_send![screens, objectAtIndex: i];
            let desc: *mut AnyObject = msg_send![screen, deviceDescription];
            let key: *mut AnyObject = msg_send![class!(NSString), stringWithUTF8String: c"NSScreenNumber".as_ptr()];
            let number: *mut AnyObject = msg_send![desc, objectForKey: key];
            let id: u32 = msg_send![number, unsignedIntValue];
            if id == mon.native_id() {
                let name: *mut AnyObject = msg_send![screen, localizedName];
                let utf8: *const std::ffi::c_char = msg_send![name, UTF8String];
                if !utf8.is_null() {
                    return Some(std::ffi::CStr::from_ptr(utf8).to_string_lossy().into_owned());
                }
            }
        }
    }
    None
}

#[cfg(target_os = "windows")]
fn real_name(mon: &MonitorHandle) -> Option<String> {
    use windows_sys::Win32::Graphics::Gdi::{DISPLAY_DEVICEW, EnumDisplayDevicesW};
    use winit::platform::windows::MonitorHandleExtWindows;
    let device: Vec<u16> = mon.native_id().encode_utf16().chain(Some(0)).collect();
    let mut display: DISPLAY_DEVICEW = unsafe { std::mem::zeroed() };
    display.cb = std::mem::size_of::<DISPLAY_DEVICEW>() as u32;
    // Consulta el monitor conectado a \\.\DISPLAYn, no el nombre de su adaptador.
    if unsafe { EnumDisplayDevicesW(device.as_ptr(), 0, &mut display, 0) } != 0 {
        let n = display.DeviceString.iter().position(|c| *c == 0).unwrap_or(display.DeviceString.len());
        if n > 0 {
            return Some(String::from_utf16_lossy(&display.DeviceString[..n]));
        }
    }
    None
}

#[cfg(not(any(target_os = "macos", target_os = "windows")))]
fn real_name(mon: &MonitorHandle) -> Option<String> {
    mon.name()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn displays() -> Vec<DisplayInfo> {
        vec![
            DisplayInfo {
                name: "Built-in Retina".into(),
                bounds: Bounds { x: 0, y: 0, width: 3024, height: 1964 },
                scale: 2.0,
                primary: true,
            },
            DisplayInfo {
                name: "T749 Projector".into(),
                bounds: Bounds { x: -1920, y: 0, width: 1920, height: 1080 },
                scale: 1.0,
                primary: false,
            },
        ]
    }
    #[test]
    fn selection_fails_closed() {
        let mut d = displays();
        assert_eq!(select(&d, "t749", false).unwrap(), 1);
        assert_eq!(select(&d, "1", false).unwrap(), 1);
        assert!(select(&d, "0", false).is_err());
        assert!(select(&d, "Retina", false).is_err());
        assert_eq!(select(&d, "0", true).unwrap(), 0);
        assert!(select(&d, "20", true).is_err());
        assert!(select(&d, "missing", true).is_err());
        assert!(select(&d, "", true).is_err());
        assert!(select(&[], "0", true).is_err());
        d.push(d[1].clone());
        assert!(select(&d, "T749", false).is_err());
    }
    #[test]
    fn bounds_on_negative_origin_and_hidpi() {
        let d = displays();
        assert!(d[1].bounds.contains(d[1].bounds));
        assert!(!d[1].bounds.contains(d[0].bounds));
        assert!(d[1].bounds.contains(Bounds { x: -1920, y: 0, width: 960, height: 540 }));
        assert!(!d[1].bounds.contains(Bounds { x: -10, y: 0, width: 1920, height: 1080 }));
        assert!(!d[1].bounds.contains(Bounds { x: -1920, y: 0, width: 0, height: 0 }));
        assert!(!d[1].bounds.contains(Bounds { x: -1921, y: 0, width: 1920, height: 1080 }));
    }
}
