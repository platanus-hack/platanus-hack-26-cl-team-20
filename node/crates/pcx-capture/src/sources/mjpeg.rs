//! `mjpeg`: HTTP `multipart/x-mixed-replace` (Quest `:8787/cam.mjpg`, camserve, cámaras IP).
//! Lee cada parte por `Content-Length`; si no viene, corta por marcadores JPEG (FFD8…FFD9). Reconexión 1→5 s.

use crate::contracts::now_ns;
use crate::frame::{decode, find, fit, Frame, LatestTx};
use bytes::{Buf, BytesMut};
use futures_util::StreamExt;
use std::time::Duration;

/// Parser incremental de partes JPEG de un cuerpo multipart.
#[derive(Default)]
pub struct MultipartParser {
    buf: BytesMut,
    body_len: Option<Option<usize>>, // None = leyendo cabeceras; Some(Some(n)) = n bytes; Some(None) = por marcadores
}

impl MultipartParser {
    pub fn push(&mut self, chunk: &[u8], out: &mut Vec<Vec<u8>>) {
        self.buf.extend_from_slice(chunk);
        loop {
            match self.body_len {
                None => {
                    let soi = find(&self.buf, &[0xFF, 0xD8]);
                    let hdr_end = find(&self.buf, b"\r\n\r\n");
                    match (hdr_end, soi) {
                        (Some(e), s) if s.is_none_or(|s| e < s) => {
                            let headers = String::from_utf8_lossy(&self.buf[..e]).to_ascii_lowercase();
                            let len = headers
                                .lines()
                                .find_map(|l| l.trim().strip_prefix("content-length:").map(|v| v.trim().parse().ok()))
                                .flatten();
                            self.buf.advance(e + 4);
                            self.body_len = Some(len);
                        }
                        (_, Some(s)) => {
                            // sin cabeceras de parte: directo a marcadores
                            self.buf.advance(s);
                            self.body_len = Some(None);
                        }
                        _ => return,
                    }
                }
                Some(Some(n)) => {
                    if self.buf.len() < n {
                        return;
                    }
                    out.push(self.buf.split_to(n).to_vec());
                    self.body_len = None;
                }
                Some(None) => {
                    let Some(s) = find(&self.buf, &[0xFF, 0xD8]) else { return };
                    let Some(e) = find(&self.buf[s + 2..], &[0xFF, 0xD9]) else { return };
                    let end = s + 2 + e + 2;
                    out.push(self.buf[s..end].to_vec());
                    self.buf.advance(end);
                    self.body_len = None;
                }
            }
        }
    }
}

pub fn spawn(url: String, size: Option<(u32, u32)>, tx: LatestTx) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let client = reqwest::Client::builder().connect_timeout(Duration::from_secs(4)).build().expect("cliente http");
        let mut wait = Duration::from_secs(1);
        loop {
            let res: Result<(), String> = async {
                let resp = client.get(&url).send().await.map_err(|e| e.to_string())?;
                if !resp.status().is_success() {
                    return Err(format!("HTTP {}", resp.status()));
                }
                wait = Duration::from_secs(1);
                let mut stream = resp.bytes_stream();
                let mut parser = MultipartParser::default();
                let mut parts = Vec::new();
                loop {
                    let chunk = tokio::select! {
                        c = stream.next() => c,
                        _ = tx.closed() => return Ok(()),
                    };
                    let chunk = chunk.ok_or("stream cerrado")?.map_err(|e| e.to_string())?;
                    parser.push(&chunk, &mut parts);
                    // último gana: solo se decodifica la parte más nueva del lote
                    if let Some(jpeg) = parts.pop() {
                        let t = now_ns();
                        if let Ok(img) = decode(&jpeg) {
                            tx.put(Frame { img: fit(&img, size).into_owned(), t_capture_ns: t });
                        }
                    }
                    parts.clear();
                }
            }
            .await;
            if tx.is_closed() {
                return;
            }
            if let Err(e) = res {
                eprintln!("[pcx-capture] mjpeg {url}: {e}; reintento en {:.1} s", wait.as_secs_f32());
            }
            tokio::select! {
                _ = tokio::time::sleep(wait) => {},
                _ = tx.closed() => return,
            }
            wait = (wait.mul_f32(1.6)).min(Duration::from_secs(5));
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_with_and_without_content_length_in_any_chunking() {
        let j1 = [0xFF, 0xD8, 1, 2, 3, 0xFF, 0xD9];
        let j2 = [0xFF, 0xD8, 9, 0xFF, 0xD9];
        let mut stream = Vec::new();
        stream.extend_from_slice(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: 7\r\n\r\n");
        stream.extend_from_slice(&j1);
        stream.extend_from_slice(b"\r\n--frame\r\nContent-Type: image/jpeg\r\n\r\n");
        stream.extend_from_slice(&j2);
        stream.extend_from_slice(b"\r\n--frame\r\n");
        for chunk in [1, 3, 7, 1000] {
            let mut p = MultipartParser::default();
            let mut out = Vec::new();
            for c in stream.chunks(chunk) {
                p.push(c, &mut out);
            }
            assert_eq!(out, vec![j1.to_vec(), j2.to_vec()], "chunk={chunk}");
        }
    }
}
