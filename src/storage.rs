// Project: tulipa-app-scraper
// File: storage.rs
// Description: Dated cache and byte-compatible CSV storage.
// Author: Jan Alexandr Kopřiva
// Based on tulipa-app-scraper by Jan Alexandr Kopřiva
// License: MIT
use crate::{text, Logger, Product, Result};
use chrono::Local;
use std::{
    collections::BTreeSet,
    fs,
    io::Write,
    path::{Path, PathBuf},
    time::{Duration, SystemTime},
};
pub struct CacheStore {
    pub data_dir: PathBuf,
}
impl CacheStore {
    pub fn new_cache_path(&self, base: &str) -> Result<PathBuf> {
        let now = Local::now();
        let dir = self.data_dir.join(now.format("%Y-%m-%d").to_string());
        fs::create_dir_all(&dir)?;
        Ok(dir.join(format!("{}_{}.csv", base, now.format("%Y%m%d_%H%M%S"))))
    }
    pub fn files(&self, base: &str) -> Vec<PathBuf> {
        let mut out = vec![];
        if let Ok(dirs) = fs::read_dir(&self.data_dir) {
            for dir in dirs.flatten() {
                if dir.file_name().to_string_lossy().starts_with('.') {
                    continue;
                }
                if let Ok(files) = fs::read_dir(dir.path()) {
                    for file in files.flatten() {
                        let name = file.file_name().to_string_lossy().into_owned();
                        if name.starts_with(&format!("{base}_")) && name.ends_with(".csv") {
                            out.push(file.path());
                        }
                    }
                }
            }
        }
        out
    }
    pub fn find_latest(&self, base: &str) -> Option<PathBuf> {
        self.files(base)
            .into_iter()
            .max_by_key(|p| p.metadata().and_then(|m| m.modified()).ok())
    }
    pub fn is_fresh(path: &Path, hours: u64) -> bool {
        path.metadata()
            .and_then(|m| m.modified())
            .map(|t| {
                SystemTime::now().duration_since(t).unwrap_or_default()
                    < Duration::from_secs(hours * 3600)
            })
            .unwrap_or(false)
    }
    pub fn cleanup_old(&self, base: &str, hours: u64) -> usize {
        self.files(base)
            .iter()
            .filter(|p| {
                p.metadata()
                    .and_then(|m| m.modified())
                    .ok()
                    .and_then(|t| SystemTime::now().duration_since(t).ok())
                    .is_some_and(|age| age > Duration::from_secs(hours * 3600))
                    && fs::remove_file(p).is_ok()
            })
            .count()
    }
}
pub fn fieldnames(products: &[Product]) -> Vec<String> {
    let mut keys: BTreeSet<String> = products.iter().flat_map(|p| p.keys().cloned()).collect();
    let mut fields = vec![];
    for k in [
        "HlavniSkupina",
        "PodskupinaKod",
        "PodskupinaNazev",
        "Nazev1",
        "RegCis",
        "_cena_cu1",
        "Mnozstvi",
    ] {
        if keys.remove(k) {
            fields.push(k.into());
        }
    }
    fields.extend(keys);
    fields
}
pub fn write_csv(products: &[Product], path: &Path, log: &Logger) -> bool {
    if products.is_empty() {
        log.log("WARNING", "No products to write");
        return false;
    }
    let fields = fieldnames(products);
    let run = || -> Result<()> {
        if let Some(p) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(p)?;
        }
        let mut file = fs::File::create(path)?;
        file.write_all(b"\xef\xbb\xbf")?;
        let mut w = csv::WriterBuilder::new()
            .delimiter(b';')
            .terminator(csv::Terminator::CRLF)
            .from_writer(file);
        w.write_record(&fields)?;
        for p in products {
            w.write_record(
                fields
                    .iter()
                    .map(|k| p.get(k).map(text).unwrap_or_default()),
            )?;
        }
        w.flush()?;
        Ok(())
    };
    match run() {
        Ok(()) => {
            log.log(
                "INFO",
                format!(
                    "Wrote {} products to {} ({} cols)",
                    products.len(),
                    path.display(),
                    fields.len()
                ),
            );
            true
        }
        Err(e) => {
            log.log("ERROR", format!("Failed to write CSV: {e}"));
            false
        }
    }
}
pub fn read_csv(path: &Path) -> Result<Vec<Product>> {
    let bytes = fs::read(path)?;
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
    let mut reader = csv::ReaderBuilder::new().delimiter(b';').from_reader(bytes);
    let keys = reader.headers()?.clone();
    reader
        .records()
        .map(|r| {
            Ok(keys
                .iter()
                .zip(r?.iter())
                .map(|(k, v)| (k.to_owned(), v.into()))
                .collect())
        })
        .collect()
}
