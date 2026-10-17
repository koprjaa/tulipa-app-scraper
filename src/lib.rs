// Project: tulipa-app-scraper
// File: lib.rs
// Description: Configuration, storage and Helios service interfaces.
// Author: Jan Alexandr Kopřiva
// License: MIT
pub mod client;
pub mod scraper;
pub mod storage;
use chrono::Local;
use serde_json::{Map, Value};
use std::path::PathBuf;
use unicode_casefold::UnicodeCaseFold;
pub type Product = Map<String, Value>;
pub type Result<T> = std::result::Result<T, Box<dyn std::error::Error + Send + Sync>>;
pub const SUBGROUPS: &str = "7C100193-68DF-4C59-8692-33E421EEBCD3";
pub const PRODUCTS: &str = "7DCCBAB9-35EA-4310-BC22-B7AC873F9398";
pub const CATEGORIES: &str = "44465692-619A-41AD-A578-ADB755659D0B";
pub const METADATA: &str = "BC3642D5-D287-4CFE-A3CB-566DA8A126E0";
pub const CONTACTS: &str = "E0E9F6FC-D077-49D9-BB05-4C8056F669E8";
pub const DETAILS: &str = "9752DF9E-E95E-46F2-97F8-11F96ABEB71C";
pub const IMAGES: &str = "982F0820-87A5-4F22-A2E9-724E14C208E1";
#[derive(Clone, Debug)]
pub struct Settings {
    pub base_url: String,
    pub endpoint: String,
    pub username: String,
    pub password: String,
    pub data_dir: PathBuf,
    pub session_file: PathBuf,
    pub request_timeout: u64,
    pub debug: bool,
    pub session_timeout_minutes: i64,
    pub main_groups: Vec<String>,
    pub category_series: Vec<(String, String)>,
    pub safety_reserve: i64,
    pub reserve_threshold: i64,
    pub loop_interval_seconds: u64,
    pub loop_error_wait_seconds: u64,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            base_url: "https://eserver.tulipapraha.com:4343".into(),
            endpoint: "/datasnap/rest/THeliosMethods/%22Execute%22".into(),
            username: String::new(),
            password: String::new(),
            data_dir: "data".into(),
            session_file: "data/tulipa_session.json".into(),
            request_timeout: 30,
            debug: false,
            session_timeout_minutes: 45,
            main_groups: ["Dekor", "Kveto", "Sezón", "Sukul"]
                .map(String::from)
                .to_vec(),
            category_series: vec![
                ("100".into(), "Řezané".into()),
                ("300".into(), "Kategorie".into()),
            ],
            safety_reserve: 5,
            reserve_threshold: 20,
            loop_interval_seconds: 1800,
            loop_error_wait_seconds: 300,
        }
    }
}
impl Settings {
    pub fn full_url(&self) -> String {
        format!("{}{}", self.base_url, self.endpoint)
    }
    pub fn from_env() -> Self {
        let _ = dotenvy::dotenv();
        let mut s = Self::default();
        for (name, target) in [
            ("HELIOS_URL", &mut s.base_url),
            ("HELIOS_USERNAME", &mut s.username),
            ("HELIOS_PASSWORD", &mut s.password),
        ] {
            if let Ok(v) = std::env::var(name) {
                if !v.is_empty() {
                    *target = v;
                }
            }
        }
        s
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Category {
    pub id: String,
    pub code: String,
    pub name: String,
    pub count: i64,
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Subgroup {
    pub code: String,
    pub name: String,
}
#[derive(Clone)]
pub struct Logger {
    pub level: u8,
}
impl Logger {
    pub fn log(&self, level: &str, msg: impl AsRef<str>) {
        let n = match level {
            "DEBUG" => 0,
            "INFO" => 1,
            "WARNING" => 2,
            _ => 3,
        };
        if n >= self.level {
            println!(
                "{} [{}] tulipa_scraper: {}",
                Local::now().format("%H:%M:%S"),
                level,
                msg.as_ref()
            );
        }
    }
}
pub fn text(v: &Value) -> String {
    match v {
        Value::Null => String::new(),
        Value::String(s) => s.clone(),
        Value::Bool(b) => {
            if *b {
                "True".into()
            } else {
                "False".into()
            }
        }
        Value::Array(_) | Value::Object(_) => python_repr(v),
        _ => v.to_string(),
    }
}
pub fn truth(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(s) => !s.is_empty(),
        Value::Number(n) => n.as_f64() != Some(0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}
pub fn select_products(
    mut p: Vec<Product>,
    group: Option<&str>,
    limit: Option<i64>,
) -> Vec<Product> {
    if let Some(g) = group.filter(|s| !s.is_empty()) {
        let g: String = g.case_fold().collect();
        p.retain(|p| {
            text(p.get("HlavniSkupina").unwrap_or(&Value::Null))
                .case_fold()
                .collect::<String>()
                == g
        });
    }
    if let Some(n) = limit.filter(|n| *n != 0) {
        let len = if n < 0 {
            p.len().saturating_sub(n.unsigned_abs() as usize)
        } else {
            n as usize
        };
        p.truncate(len);
    }
    p
}

/// Match the original diagnostics and string conversion of nested field values.
pub fn python_repr(v: &Value) -> String {
    match v {
        Value::Null => "None".into(),
        Value::String(s) => {
            let quote = if s.contains('\'') && !s.contains('"') {
                '"'
            } else {
                '\''
            };
            let mut result = String::new();
            result.push(quote);
            for c in s.chars() {
                match c {
                    '\\' => result.push_str("\\\\"),
                    '\n' => result.push_str("\\n"),
                    '\r' => result.push_str("\\r"),
                    '\t' => result.push_str("\\t"),
                    c if c == quote => {
                        result.push('\\');
                        result.push(c);
                    }
                    c if c.is_control() => {
                        let n = c as u32;
                        result.push_str(&if n < 256 {
                            format!("\\x{n:02x}")
                        } else {
                            format!("\\u{n:04x}")
                        });
                    }
                    c => result.push(c),
                }
            }
            result.push(quote);
            result
        }
        Value::Array(a) => format!(
            "[{}]",
            a.iter().map(python_repr).collect::<Vec<_>>().join(", ")
        ),
        Value::Object(o) => format!(
            "{{{}}}",
            o.iter()
                .map(|(k, v)| format!(
                    "{}: {}",
                    python_repr(&Value::String(k.clone())),
                    python_repr(v)
                ))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        _ => text(v),
    }
}
