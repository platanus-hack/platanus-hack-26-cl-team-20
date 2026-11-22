//! Escritor en este proceso, dos lectores en otros procesos (el mismo binario de test re-ejecutado).
//! Cada cuadro es constante con el valor `seq % 251` y lleva su `seq` en los primeros 8 bytes: una lectura
//! partida se detecta.

use pcx_shm::{PixelFormat, RingReader, RingWriter};
use std::process::{Command, Stdio};
use std::time::Duration;

const W: u32 = 320;
const H: u32 = 240;
const N: u64 = 3000;

fn pattern(seq: u64, dst: &mut [u8]) {
    dst.fill((seq % 251) as u8);
    dst[..8].copy_from_slice(&seq.to_le_bytes());
}

fn check(seq: u64, data: &[u8]) -> bool {
    u64::from_le_bytes(data[..8].try_into().unwrap()) == seq && data[8..].iter().all(|&b| b == (seq % 251) as u8)
}

/// Cuerpo del lector hijo; sin `PCX_SHM_CHILD` no hace nada.
#[test]
fn child_reader() {
    let Ok(id) = std::env::var("PCX_SHM_CHILD") else { return };
    let mut r = RingReader::open(&id, Duration::from_secs(5)).unwrap();
    let (mut last, mut got, mut bad) = (0u64, 0u64, 0u64);
    let mut buf = Vec::new();
    loop {
        match r.wait_newer(last, Duration::from_secs(5), &mut buf) {
            Ok(Some(m)) => {
                assert!(m.seq > last);
                if !check(m.seq, &buf) {
                    bad += 1;
                }
                last = m.seq;
                got += 1;
            }
            Ok(None) => break,
            Err(pcx_shm::ShmError::Closed(_)) => break,
            Err(e) => panic!("{e}"),
        }
    }
    println!("RESULT got={got} bad={bad} last={last} retries={}", r.retries());
}

#[test]
fn readers_in_other_processes_never_see_torn_frames() {
    let id = format!("xp{}", std::process::id() % 100_000);
    let mut w = RingWriter::create(&id, W as usize, H as usize, 3, 4).unwrap();
    let exe = std::env::current_exe().unwrap();
    let kids: Vec<_> = (0..2)
        .map(|_| {
            Command::new(&exe)
                .args(["child_reader", "--exact", "--nocapture", "--test-threads=1"])
                .env("PCX_SHM_CHILD", &id)
                .stdout(Stdio::piped())
                .spawn()
                .unwrap()
        })
        .collect();
    std::thread::sleep(Duration::from_millis(400));
    for _ in 0..N {
        let seq = w.seq() + 1;
        w.write_with(W, H, PixelFormat::Bgr8, None, |dst| pattern(seq, dst)).unwrap();
        std::thread::sleep(Duration::from_micros(50));
    }
    std::thread::sleep(Duration::from_millis(50));
    w.close();
    for k in kids {
        let out = String::from_utf8(k.wait_with_output().unwrap().stdout).unwrap();
        let line =
            out.lines().find_map(|l| l.find("RESULT").map(|i| &l[i..])).unwrap_or_else(|| panic!("sin RESULT: {out}"));
        let field = |name: &str| -> u64 {
            line.split_whitespace().find_map(|kv| kv.strip_prefix(&format!("{name}="))).unwrap().parse().unwrap()
        };
        eprintln!("{line}");
        assert_eq!(field("bad"), 0, "lectura partida: {line}");
        assert_eq!(field("last"), N, "el lector no vio el último cuadro: {line}");
        assert!(field("got") > 50, "{line}");
    }
}

#[cfg(unix)]
#[test]
fn child_crash_writer() {
    let Ok(id) = std::env::var("PCX_SHM_CRASH_WRITER") else {
        return;
    };
    let mut writer = RingWriter::create(&id, 8, 8, 1, 2).unwrap();
    writer.write(&[1; 64], 8, 8, pcx_shm::PixelFormat::Gray8, None).unwrap();
    println!("READY");
    use std::io::Write;
    std::io::stdout().flush().unwrap();
    let mut line = String::new();
    std::io::stdin().read_line(&mut line).unwrap();
    std::process::exit(0); // sin Drop: simula la pérdida del escritor
}

#[cfg(unix)]
#[test]
fn dead_writer_can_be_recovered_with_old_reader_still_open() {
    use std::io::{BufRead, BufReader, Write};
    let id = format!("cr{}", std::process::id());
    let mut child = Command::new(std::env::current_exe().unwrap())
        .args(["child_crash_writer", "--exact", "--nocapture"])
        .env("PCX_SHM_CRASH_WRITER", &id)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    let mut output = BufReader::new(child.stdout.take().unwrap());
    let mut line = String::new();
    loop {
        line.clear();
        assert!(output.read_line(&mut line).unwrap() > 0);
        if line.contains("READY") {
            break;
        }
    }
    let old = RingReader::open(&id, Duration::ZERO).unwrap();
    child.stdin.take().unwrap().write_all(b"exit\n").unwrap();
    assert!(child.wait().unwrap().success());
    let mut new = RingWriter::create(&id, 8, 8, 1, 2).unwrap();
    assert!(old.closed());
    new.write(&[2; 64], 8, 8, pcx_shm::PixelFormat::Gray8, None).unwrap();
    let mut reader = RingReader::open(&id, Duration::ZERO).unwrap();
    assert_eq!(reader.latest().unwrap().unwrap().1, vec![2; 64]);
}
