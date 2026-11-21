//! Segmento con nombre, compatible con `multiprocessing.shared_memory` de Python.
//!
//! - POSIX (macOS/Linux): `shm_open("/" + name)`; el escritor hace `shm_unlink` al cerrar.
//! - Windows: *file mapping* con nombre sobre el archivo de paginación (`CreateFileMappingW(INVALID_HANDLE_VALUE)`),
//!   igual que `SharedMemory(create=True)`. No hay `unlink`: el SO lo libera con el último handle, por eso el
//!   escritor mantiene el suyo abierto toda la vida del anillo.

pub struct Segment {
    pub ptr: *mut u8,
    pub len: usize,
    #[cfg(windows)]
    handle: windows_sys::Win32::Foundation::HANDLE,
}

// El puntero apunta a memoria compartida que vive mientras viva el `Segment`.
unsafe impl Send for Segment {}

#[cfg(unix)]
mod imp {
    use super::Segment;
    use std::ffi::CString;
    use std::io;

    fn cname(name: &str) -> io::Result<CString> {
        CString::new(format!("/{name}")).map_err(|e| io::Error::new(io::ErrorKind::InvalidInput, e))
    }

    unsafe fn open_fd(c: &CString, flags: libc::c_int) -> libc::c_int {
        #[cfg(target_os = "macos")]
        {
            libc::shm_open(c.as_ptr(), flags, 0o600 as libc::c_uint)
        }
        #[cfg(not(target_os = "macos"))]
        {
            libc::shm_open(c.as_ptr(), flags, 0o600 as libc::mode_t)
        }
    }

    unsafe fn map(fd: libc::c_int, len: usize, write: bool) -> io::Result<*mut u8> {
        let prot = if write { libc::PROT_READ | libc::PROT_WRITE } else { libc::PROT_READ };
        let p = libc::mmap(std::ptr::null_mut(), len, prot, libc::MAP_SHARED, fd, 0);
        if p == libc::MAP_FAILED {
            return Err(io::Error::last_os_error());
        }
        Ok(p as *mut u8)
    }

    /// Crea el segmento (falla con `AlreadyExists` si ya existe) y lo mapea en lectura/escritura.
    pub fn create(name: &str, size: usize) -> io::Result<Segment> {
        let c = cname(name)?;
        unsafe {
            let fd = open_fd(&c, libc::O_CREAT | libc::O_EXCL | libc::O_RDWR);
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let res = (|| {
                if libc::ftruncate(fd, size as libc::off_t) != 0 {
                    return Err(io::Error::last_os_error());
                }
                map(fd, size, true)
            })();
            libc::close(fd);
            match res {
                Ok(ptr) => Ok(Segment { ptr, len: size }),
                Err(e) => {
                    libc::shm_unlink(c.as_ptr());
                    Err(e)
                }
            }
        }
    }

    /// Abre un segmento existente. `WouldBlock` si aún tiene tamaño 0 (el escritor está a medio crear).
    pub fn open(name: &str, write: bool) -> io::Result<Segment> {
        let c = cname(name)?;
        unsafe {
            let fd = open_fd(&c, if write { libc::O_RDWR } else { libc::O_RDONLY });
            if fd < 0 {
                return Err(io::Error::last_os_error());
            }
            let mut st: libc::stat = std::mem::zeroed();
            let res = if libc::fstat(fd, &mut st) != 0 {
                Err(io::Error::last_os_error())
            } else if (st.st_size as usize) < crate::HDR {
                Err(io::Error::new(io::ErrorKind::WouldBlock, "segmento aún se está creando"))
            } else {
                map(fd, st.st_size as usize, write).map(|ptr| Segment { ptr, len: st.st_size as usize })
            };
            libc::close(fd);
            res
        }
    }

    pub fn unlink(name: &str) -> io::Result<()> {
        let c = cname(name)?;
        if unsafe { libc::shm_unlink(c.as_ptr()) } != 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(())
    }

    pub fn pid_alive(pid: u32) -> bool {
        if unsafe { libc::kill(pid as libc::pid_t, 0) } == 0 {
            return true;
        }
        io::Error::last_os_error().raw_os_error() == Some(libc::EPERM)
    }

    impl Drop for Segment {
        fn drop(&mut self) {
            unsafe { libc::munmap(self.ptr as *mut libc::c_void, self.len) };
        }
    }
}

#[cfg(windows)]
mod imp {
    use super::Segment;
    use std::io;
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, ERROR_ALREADY_EXISTS, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::System::Memory::{
        CreateFileMappingW, MapViewOfFile, OpenFileMappingW, UnmapViewOfFile, VirtualQuery, FILE_MAP_ALL_ACCESS,
        FILE_MAP_READ, MEMORY_BASIC_INFORMATION, MEMORY_MAPPED_VIEW_ADDRESS, PAGE_READWRITE,
    };

    fn wide(name: &str) -> Vec<u16> {
        name.encode_utf16().chain(std::iter::once(0)).collect()
    }

    pub fn create(name: &str, size: usize) -> io::Result<Segment> {
        let w = wide(name);
        unsafe {
            let h = CreateFileMappingW(
                INVALID_HANDLE_VALUE,
                std::ptr::null(),
                PAGE_READWRITE,
                ((size as u64) >> 32) as u32,
                size as u32,
                w.as_ptr(),
            );
            if h.is_null() {
                return Err(io::Error::last_os_error());
            }
            if GetLastError() == ERROR_ALREADY_EXISTS {
                CloseHandle(h);
                return Err(io::Error::new(io::ErrorKind::AlreadyExists, format!("{name} ya existe")));
            }
            let v = MapViewOfFile(h, FILE_MAP_ALL_ACCESS, 0, 0, size);
            if v.Value.is_null() {
                let e = io::Error::last_os_error();
                CloseHandle(h);
                return Err(e);
            }
            Ok(Segment { ptr: v.Value as *mut u8, len: size, handle: h })
        }
    }

    pub fn open(name: &str, write: bool) -> io::Result<Segment> {
        let w = wide(name);
        let access = if write { FILE_MAP_ALL_ACCESS } else { FILE_MAP_READ };
        unsafe {
            let h = OpenFileMappingW(access, 0, w.as_ptr());
            if h.is_null() {
                return Err(io::Error::last_os_error());
            }
            let v = MapViewOfFile(h, access, 0, 0, 0);
            if v.Value.is_null() {
                let e = io::Error::last_os_error();
                CloseHandle(h);
                return Err(e);
            }
            let mut mbi: MEMORY_BASIC_INFORMATION = std::mem::zeroed();
            VirtualQuery(v.Value, &mut mbi, std::mem::size_of::<MEMORY_BASIC_INFORMATION>());
            Ok(Segment { ptr: v.Value as *mut u8, len: mbi.RegionSize, handle: h })
        }
    }

    pub fn unlink(_name: &str) -> io::Result<()> {
        Ok(()) // el SO libera el mapping con el último handle
    }

    pub fn pid_alive(_pid: u32) -> bool {
        true // en Windows el nombre solo existe si alguien tiene un handle abierto
    }

    impl Drop for Segment {
        fn drop(&mut self) {
            unsafe {
                UnmapViewOfFile(MEMORY_MAPPED_VIEW_ADDRESS { Value: self.ptr as *mut _ });
                CloseHandle(self.handle);
            }
        }
    }
}

pub use imp::{create, open, pid_alive, unlink};
