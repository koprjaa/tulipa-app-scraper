// Project: tulipa-app-scraper
// File: audit.rs
// Description: Differential fixtures captured from the original Python implementation.
// Author: Jan Alexandr Kopřiva
// License: AGPL-3.0-or-later
use serde_json::{json, Value};
use std::fs;
use tulipa_app_scraper::{scraper::row_to_dict, storage::*, *};
#[test]
fn python_golden_cases() {
    let cases: Value = serde_json::from_str(include_str!("fixtures/python_golden.json")).unwrap();
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("out.csv");
    let mut failures = vec![];
    for case in cases.as_array().unwrap() {
        let input = &case["input"];
        let actual = match case["kind"].as_str().unwrap() {
            "read" => {
                fs::write(&path, input.as_str().unwrap()).unwrap();
                match read_csv(&path) {
                    Ok(v) => json!(v),
                    Err(e) => json!({"error":e.to_string()}),
                }
            }
            "write" => {
                let products: Vec<Product> = serde_json::from_value(input.clone()).unwrap();
                assert!(write_csv(&products, &path, &Logger { level: 3 }));
                json!(fs::read_to_string(&path).unwrap())
            }
            "walk" => {
                let mut rpc = WalkRpc {
                    replies: serde_json::from_value(input.clone()).unwrap(),
                    calls: vec![],
                };
                let settings = Settings {
                    main_groups: vec![],
                    category_series: vec![("100".into(), "Cut".into())],
                    ..Settings::default()
                };
                let products = tulipa_app_scraper::scraper::Scraper {
                    rpc: &mut rpc,
                    settings: &settings,
                    log: &Logger { level: 3 },
                }
                .actions()
                .unwrap();
                assert_eq!(json!(rpc.calls), case["calls"], "{}", case["name"]);
                json!(products)
            }
            "row" => json!(row_to_dict(input)),
            "select" => json!(select_products(
                serde_json::from_value(input.clone()).unwrap(),
                Some("STRASSE"),
                case["limit"].as_i64()
            )),
            _ => unreachable!(),
        };
        if actual != case["expected"] {
            failures.push(format!(
                "{} {}: expected {}, actual {}",
                case["kind"], case["name"], case["expected"], actual
            ));
        }
    }
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}

struct ExtraRpc {
    products: Value,
}
impl tulipa_app_scraper::client::Rpc for ExtraRpc {
    fn activate(&mut self) -> Result<()> {
        Ok(())
    }
    fn browse(&mut self, _: Option<&str>) -> Result<Value> {
        Ok(Value::Null)
    }
    fn action(&mut self, id: &str, _: Value) -> Result<Value> {
        let rows = if id == METADATA {
            self.products.clone()
        } else {
            json!([])
        };
        Ok(json!({"result":[{"fields":{"IsError":false,"Result":{"table":{"rows":rows}}}}]}))
    }
}
#[test]
fn numeric_identifiers_use_python_dictionary_equality() {
    let mut rpc = ExtraRpc {
        products: json!([
            [{"FieldName":"ID","Value":1}], [{"FieldName":"ID","Value":1.0}],
            [{"FieldName":"ID","Value":true}], [{"FieldName":"ID","Value":"1"}],
            [{"FieldName":"ID","Value":2}]
        ]),
    };
    let settings = Settings {
        main_groups: vec![],
        category_series: vec![],
        ..Settings::default()
    };
    let products = tulipa_app_scraper::scraper::Scraper {
        rpc: &mut rpc,
        settings: &settings,
        log: &Logger { level: 3 },
    }
    .actions()
    .unwrap();
    assert_eq!(
        products.iter().map(|p| p["ID"].clone()).collect::<Vec<_>>(),
        vec![json!(1), json!("1"), json!(2)]
    );
}
#[test]
fn cache_age_does_not_overflow() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("x");
    fs::write(&p, "").unwrap();
    assert!(CacheStore::is_fresh(&p, u64::MAX));
    assert_eq!(
        CacheStore {
            data_dir: dir.path().into()
        }
        .cleanup_old("x", u64::MAX),
        0
    );
}
#[test]
fn excess_csv_fields_are_rejected_explicitly() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("x");
    fs::write(&p, "a\n1;2\n").unwrap();
    assert!(read_csv(&p)
        .unwrap_err()
        .to_string()
        .contains("more fields"));
}

struct WalkRpc {
    replies: std::collections::VecDeque<Value>,
    calls: Vec<(String, Value)>,
}
impl tulipa_app_scraper::client::Rpc for WalkRpc {
    fn activate(&mut self) -> Result<()> {
        Ok(())
    }
    fn browse(&mut self, _: Option<&str>) -> Result<Value> {
        Ok(Value::Null)
    }
    fn action(&mut self, id: &str, params: Value) -> Result<Value> {
        self.calls.push((id.into(), params));
        Ok(self.replies.pop_front().expect("unexpected action"))
    }
}

#[test]
fn transport_handles_nested_gzip_bad_json_status_and_timeout() {
    use std::io::Write;
    use tulipa_app_scraper::client::HeliosClient;
    let server = httpmock::MockServer::start();
    let compress = |data: &[u8]| {
        let mut encoder = flate2::write::GzEncoder::new(Vec::new(), flate2::Compression::default());
        encoder.write_all(data).unwrap();
        encoder.finish().unwrap()
    };
    let raw = json!({"result":[{"fields":{"IsError":false}}]}).to_string();
    let nested = server.mock(|w, t| {
        w.path("/gzip");
        t.header("Content-Encoding", "gzip")
            .body(compress(&compress(raw.as_bytes())));
    });
    let dir = tempfile::tempdir().unwrap();
    let make = |endpoint: &str| {
        HeliosClient::new(
            Settings {
                base_url: server.base_url(),
                endpoint: endpoint.into(),
                session_file: dir.path().join("session.json"),
                request_timeout: 1,
                ..Settings::default()
            },
            Logger { level: 3 },
        )
        .unwrap()
    };
    assert!(tulipa_app_scraper::client::success(
        &make("/gzip").call(json!({}), true)
    ));
    nested.assert_calls(1);
    for (path, status, body) in [
        ("/bad", 200, "not json"),
        ("/status", 503, "{}"),
        ("/empty", 200, ""),
    ] {
        server.mock(|w, t| {
            w.path(path);
            t.status(status).body(body);
        });
        assert!(make(path).call(json!({}), true).is_null());
    }
    server.mock(|w, t| {
        w.path("/slow");
        t.delay(std::time::Duration::from_millis(1500))
            .json_body(json!({}));
    });
    let start = std::time::Instant::now();
    assert!(make("/slow").call(json!({}), true).is_null());
    assert!(start.elapsed() < std::time::Duration::from_secs(3));
}
#[test]
fn session_timeout_overflow_returns_error() {
    let server = httpmock::MockServer::start();
    server.mock(|w, t| {
        w.method("POST");
        t.json_body(json!({"result":[{"fields":{"IsError":false,"Result":"token"}}]}));
    });
    let dir = tempfile::tempdir().unwrap();
    let mut client = tulipa_app_scraper::client::HeliosClient::new(
        Settings {
            base_url: server.base_url(),
            endpoint: "/".into(),
            username: "test".into(),
            password: "test".into(),
            session_file: dir.path().join("session"),
            session_timeout_minutes: i64::MAX,
            ..Settings::default()
        },
        Logger { level: 3 },
    )
    .unwrap();
    assert!(client
        .get_token()
        .unwrap_err()
        .to_string()
        .contains("out of range"));
}
#[test]
fn list_browse_preserves_raw_price_fields() {
    use std::process::Command;
    let server = httpmock::MockServer::start();
    server.mock(|w, t| {
        w.body_includes("Login");
        t.json_body(json!({"result":[{"fields":{"IsError":false,"Result":"session"}}]}));
    });
    server.mock(|w, t| {
        w.body_includes("ChangeDatabase");
        t.json_body(json!({}));
    });
    server.mock(|w,t| {w.body_includes("GetBrowse");t.json_body(json!({"result":[{"fields":{"IsError":false,"Result":{"table":{"rows":[[{"FieldName":"_cena_cu1","Value":"7,5"}]]}}}}]}));});
    let dir = tempfile::tempdir().unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
        .current_dir(dir.path())
        .env("HELIOS_URL", server.base_url())
        .env("HELIOS_USERNAME", "test")
        .env("HELIOS_PASSWORD", "test")
        .arg("--list-browse")
        .output()
        .unwrap();
    assert!(out.status.success());
    assert!(String::from_utf8_lossy(&out.stdout).contains("{'_cena_cu1': '7,5'}"));
}
#[test]
fn cli_accepts_limits_larger_than_machine_integers() {
    for limit in [
        "999999999999999999999999999999",
        "-999999999999999999999999999999",
        "1_0",
        "١٠",
    ] {
        let dir = tempfile::tempdir().unwrap();
        let out = std::process::Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
            .current_dir(dir.path())
            .args(["--limit", limit, "--reset"])
            .output()
            .unwrap();
        assert!(
            out.status.success(),
            "{}",
            String::from_utf8_lossy(&out.stderr)
        );
    }
}
#[test]
fn future_cache_is_fresh_even_with_zero_ttl() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("future");
    fs::write(&p, "").unwrap();
    fs::OpenOptions::new()
        .write(true)
        .open(&p)
        .unwrap()
        .set_times(
            std::fs::FileTimes::new()
                .set_modified(std::time::SystemTime::now() + std::time::Duration::from_secs(3600)),
        )
        .unwrap();
    assert!(CacheStore::is_fresh(&p, 0));
}
#[test]
fn large_and_invalid_utf8_csv_inputs() {
    let dir = tempfile::tempdir().unwrap();
    let p = dir.path().join("data.csv");
    let data = "ž".repeat(150_000);
    fs::write(&p, format!("x\r\n{data}\r\n")).unwrap();
    assert_eq!(read_csv(&p).unwrap()[0]["x"], data);
    fs::write(&p, b"x\n\xff\n").unwrap();
    assert!(read_csv(&p).is_err());
    assert!(read_csv(&dir.path().join("missing")).is_err());
}
#[test]
fn malformed_envelopes_do_not_panic() {
    use tulipa_app_scraper::client::{fields, success, token_error};
    for value in [
        Value::Null,
        json!(true),
        json!(1),
        json!("text"),
        json!([]),
        json!({"result":true}),
        json!({"result":[null]}),
        json!({"result":[{"fields":[]}]}),
    ] {
        let _ = fields(&value);
        assert!(!success(&value));
        assert!(!token_error(&value));
        let _ = tulipa_app_scraper::scraper::extract_rows(&value);
        let _ = row_to_dict(&value);
    }
}

#[test]
fn an_unanswered_action_fails_the_export() {
    use tulipa_app_scraper::client::Rpc;
    struct Silent(usize);
    impl Rpc for Silent {
        fn activate(&mut self) -> Result<()> {
            Ok(())
        }
        fn browse(&mut self, _: Option<&str>) -> Result<Value> {
            Ok(Value::Null)
        }
        fn action(&mut self, _: &str, _: Value) -> Result<Value> {
            self.0 += 1;
            // The category list answers; every later action gets no response.
            Ok(if self.0 == 1 {
                json!({"result":[{"fields":{"IsError":false,"Result":{"table":{"rows":[[
                    {"FieldName":"K1","Value":"cut"},{"FieldName":"Pocet","Value":1}]]}}}}]})
            } else {
                Value::Null
            })
        }
    }
    let settings = Settings {
        main_groups: vec![],
        category_series: vec![],
        ..Settings::default()
    };
    let error = tulipa_app_scraper::scraper::Scraper {
        rpc: &mut Silent(0),
        settings: &settings,
        log: &Logger { level: 3 },
    }
    .actions()
    .unwrap_err();
    assert!(error.to_string().contains("No response"), "{error}");
}
