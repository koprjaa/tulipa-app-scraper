// Project: tulipa-app-scraper
// File: compat.rs
// Description: Original test equivalents and offline regression coverage.
// Author: Jan Alexandr Kopřiva
// License: MIT
use serde_json::{json, Value};
use std::{
    fs,
    path::Path,
    process::Command,
    time::{Duration, SystemTime},
};
use tulipa_app_scraper::{client::*, scraper::*, storage::*, *};
fn log() -> Logger {
    Logger { level: 3 }
}
fn p(v: Value) -> Product {
    v.as_object().unwrap().clone()
}
fn settings(dir: &Path) -> Settings {
    Settings {
        data_dir: dir.into(),
        session_file: dir.join("session.json"),
        ..Settings::default()
    }
}
fn old(path: &Path, hours: u64) {
    fs::OpenOptions::new()
        .write(true)
        .open(path)
        .unwrap()
        .set_times(
            fs::FileTimes::new()
                .set_modified(SystemTime::now() - Duration::from_secs(hours * 3600)),
        )
        .unwrap();
}
#[test]
fn defaults_have_required_fields() {
    let s = Settings::default();
    assert!(s.base_url.starts_with("https://"));
    assert!(s.endpoint.starts_with('/'));
    assert!(s.request_timeout > 0);
    assert!(!s.main_groups.is_empty());
    assert_eq!(
        s.category_series
            .iter()
            .map(|x| x.0.as_str())
            .collect::<Vec<_>>(),
        ["100", "300"]
    );
    assert_eq!(SUBGROUPS.len(), 36);
    assert!(s.session_timeout_minutes > 0);
    assert!(s.username.is_empty() && s.password.is_empty());
}
#[test]
fn full_url_composition() {
    let s = Settings {
        base_url: "https://host".into(),
        endpoint: "/api".into(),
        ..Settings::default()
    };
    assert_eq!(s.full_url(), "https://host/api");
}
#[test]
fn session_file_is_under_data_dir() {
    let s = Settings::default();
    assert!(s.session_file.starts_with(s.data_dir));
}
#[test]
fn from_env_uses_defaults_when_env_missing() {
    let dir = tempfile::tempdir().unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
        .current_dir(dir.path())
        .env_remove("HELIOS_URL")
        .env_remove("HELIOS_USERNAME")
        .env_remove("HELIOS_PASSWORD")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stdout)
        .contains("HELIOS_USERNAME and HELIOS_PASSWORD are required"));
}
#[test]
fn from_env_overrides() {
    let dir = tempfile::tempdir().unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
        .current_dir(dir.path())
        .env("HELIOS_URL", "http://127.0.0.1:0")
        .env("HELIOS_USERNAME", "test-user")
        .env("HELIOS_PASSWORD", "test-value")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&out.stdout).contains("Connection error"));
}
#[test]
fn new_cache_path_creates_date_folder() {
    let d = tempfile::tempdir().unwrap();
    let c = CacheStore {
        data_dir: d.path().into(),
    };
    let path = c.new_cache_path("produkty_komplet").unwrap();
    assert!(path.parent().unwrap().exists());
    assert_eq!(path.parent().unwrap().parent().unwrap(), d.path());
    assert!(path
        .file_name()
        .unwrap()
        .to_str()
        .unwrap()
        .starts_with("produkty_komplet_"));
    assert_eq!(path.extension().unwrap(), "csv");
}
#[test]
fn find_latest_returns_none_when_empty() {
    let d = tempfile::tempdir().unwrap();
    assert!(CacheStore {
        data_dir: d.path().into()
    }
    .find_latest("produkty_komplet")
    .is_none());
}
#[test]
fn find_latest_picks_newest() {
    let d = tempfile::tempdir().unwrap();
    let c = CacheStore {
        data_dir: d.path().into(),
    };
    fs::create_dir(d.path().join("day")).unwrap();
    let a = d.path().join("day/produkty_komplet_a.csv");
    let b = d.path().join("day/produkty_komplet_b.csv");
    fs::write(&a, "").unwrap();
    fs::write(&b, "").unwrap();
    old(&a, 48);
    assert_eq!(c.find_latest("produkty_komplet"), Some(b));
}
#[test]
fn is_fresh_true_for_new_file() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("fresh.csv");
    fs::write(&f, "").unwrap();
    assert!(CacheStore::is_fresh(&f, 1));
}
#[test]
fn is_fresh_false_for_old_file() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("old.csv");
    fs::write(&f, "").unwrap();
    old(&f, 3);
    assert!(!CacheStore::is_fresh(&f, 1));
}
#[test]
fn is_fresh_false_for_missing_file() {
    let d = tempfile::tempdir().unwrap();
    assert!(!CacheStore::is_fresh(&d.path().join("missing"), 1));
}
#[test]
fn cleanup_old_removes_stale() {
    let d = tempfile::tempdir().unwrap();
    let c = CacheStore {
        data_dir: d.path().into(),
    };
    let f = c.new_cache_path("produkty_komplet").unwrap();
    fs::write(&f, "").unwrap();
    let new = f.with_file_name("produkty_komplet_new.csv");
    fs::write(&new, "").unwrap();
    old(&f, 48);
    assert_eq!(c.cleanup_old("produkty_komplet", 24), 1);
    assert!(!f.exists());
    assert!(new.exists());
}
fn products() -> Vec<Product> {
    vec![
        p(json!({"HlavniSkupina":"Dekor","ID":"1"})),
        p(json!({"HlavniSkupina":"Kveto","ID":"2"})),
        p(json!({"HlavniSkupina":"Dekor","ID":"3"})),
    ]
}
#[test]
fn no_filter_returns_all() {
    assert_eq!(select_products(products(), None, None), products());
}
#[test]
fn filter_group_is_case_insensitive() {
    let v = select_products(products(), Some("dekor"), None);
    assert_eq!(
        v.iter().map(|p| text(&p["ID"])).collect::<Vec<_>>(),
        ["1", "3"]
    );
}
#[test]
fn limit_applies_after_filter() {
    assert_eq!(
        select_products(products(), Some("Dekor"), Some(1)),
        vec![products()[0].clone()]
    );
}
#[test]
fn prices_use_decimal_point() {
    assert_eq!(
        row_to_dict(
            &json!([{"FieldName":"_cena_cu1","Value":"7,5"},{"FieldName":"Nazev1","Value":"A, B"}])
        ),
        p(json!({"_cena_cu1":"7.5","Nazev1":"A, B"}))
    );
}
#[test]
fn write_empty_returns_false() {
    let d = tempfile::tempdir().unwrap();
    assert!(!write_csv(&[], &d.path().join("out.csv"), &log()));
}
#[test]
fn roundtrip() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("out.csv");
    let v = vec![
        p(json!({"RegCis":"A001","Nazev1":"Tulip","_cena_cu1":"42"})),
        p(json!({"RegCis":"A002","Nazev1":"Rose","_cena_cu1":"55"})),
    ];
    assert!(write_csv(&v, &f, &log()));
    assert_eq!(read_csv(&f).unwrap(), v);
}
#[test]
fn key_columns_are_promoted_to_front() {
    let v = vec![p(
        json!({"Zcolumn":"z","Acolumn":"a","Nazev1":"Tulip","RegCis":"X1","HlavniSkupina":"Dekor"}),
    )];
    assert_eq!(
        fieldnames(&v),
        ["HlavniSkupina", "Nazev1", "RegCis", "Acolumn", "Zcolumn"]
    );
}
#[test]
fn extras_action_ignore_skips_missing_fields() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("out.csv");
    assert!(write_csv(
        &[
            p(json!({"RegCis":"A1","Foo":"x"})),
            p(json!({"RegCis":"A2","Bar":"y"}))
        ],
        &f,
        &log()
    ));
    let v = read_csv(&f).unwrap();
    assert_eq!(v[0]["Foo"], "x");
    assert_eq!(v[0]["Bar"], "");
    assert_eq!(v[1]["Foo"], "");
    assert_eq!(v[1]["Bar"], "y");
}
#[test]
fn call_verifies_tls_against_pinned_ca_bundle() {
    assert_eq!(
        reqwest::Certificate::from_pem_bundle(CA_BUNDLE)
            .unwrap()
            .len(),
        2
    );
    let s = Settings {
        base_url: "https://127.0.0.1:0".into(),
        ..Settings::default()
    };
    let c = HeliosClient::new(s, log()).unwrap();
    assert!(c.call(json!({"_parameters":[]}), true).is_null());
    assert!(!String::from_utf8_lossy(include_bytes!("../src/client.rs"))
        .contains("danger_accept_invalid_certs"));
}
#[test]
fn session_token_file_is_owner_only() {
    let d = tempfile::tempdir().unwrap();
    let s = settings(d.path());
    fs::write(&s.session_file, "{}").unwrap();
    let mut c = HeliosClient::new(s.clone(), log()).unwrap();
    c.token = Some("test-session".into());
    c.expires = Some(chrono::Local::now().naive_local() + chrono::Duration::minutes(5));
    c.save_token().unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&s.session_file).unwrap().permissions().mode() & 0o777,
            0o600
        );
    }
}
fn ok(v: Value) -> Value {
    json!({"result":[{"fields":{"IsError":false,"Result":v}}]})
}
#[test]
fn every_action_is_preceded_by_change_database() {
    let server = httpmock::MockServer::start();
    let change = server.mock(|when, then| {
        when.method("POST")
            .body_includes("ChangeDatabase")
            .header("user-agent", "Embarcadero RESTClient/1.0")
            .header("accept-encoding", "gzip, deflate, br");
        then.json_body(ok(json!({})));
    });
    let action = server.mock(|when, then| {
        when.method("POST").body_includes("RunExternalAction");
        then.json_body(ok(json!({})));
    });
    let d = tempfile::tempdir().unwrap();
    let mut s = settings(d.path());
    s.base_url = server.base_url();
    s.endpoint = "/".into();
    let mut c = HeliosClient::new(s, log()).unwrap();
    c.token = Some("test-session".into());
    c.expires = Some(chrono::Local::now().naive_local() + chrono::Duration::minutes(5));
    c.action("A", json!([])).unwrap();
    c.action("B", json!([])).unwrap();
    change.assert_calls(2);
    action.assert_calls(2);
}
#[test]
fn csv_bytes_and_limit_edges() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("out.csv");
    write_csv(&[p(json!({"Nazev1":"Žluť; \"x\"","ID":null}))], &f, &log());
    assert_eq!(
        fs::read(f).unwrap(),
        "\u{feff}Nazev1;ID\r\n\"Žluť; \"\"x\"\"\";\r\n".as_bytes()
    );
    assert_eq!(select_products(products(), None, Some(0)).len(), 3);
    assert_eq!(select_products(products(), None, Some(-1)).len(), 2);
    assert_eq!(
        select_products(
            vec![p(json!({"HlavniSkupina":"Straße"}))],
            Some("STRASSE"),
            None
        )
        .len(),
        1
    );
}
#[test]
fn response_shapes_and_missing_fields() {
    let row = json!([[{"FieldName":"ID","Value":1}]]);
    for v in [
        row.clone(),
        json!({"table":{"rows":row}}),
        json!({"fields":{"QueryBrowse":{"table":{"rows":row}}}}),
    ] {
        assert_eq!(extract_rows(&v).len(), 1);
    }
    assert!(extract_rows(&Value::Null).is_empty());
    assert!(!success(&json!({})));
    assert!(token_error(
        &json!({"result":[{"fields":{"ErrorMessage":"Invalid SESSION"}}]})
    ));
}
#[test]
fn cli_help_errors_reset_and_fresh_custom_cache() {
    let d = tempfile::tempdir().unwrap();
    let run = |args: &[&str]| {
        Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
            .current_dir(d.path())
            .args(args)
            .output()
            .unwrap()
    };
    assert!(run(&["--help"]).status.success());
    assert_eq!(run(&["--unknown"]).status.code(), Some(2));
    assert_eq!(run(&["--limit", "bad"]).status.code(), Some(2));
    assert!(run(&["--reset"]).status.success());
    fs::write(d.path().join("custom.csv"), "cached").unwrap();
    let out = run(&["--output", "custom.csv"]);
    assert!(out.status.success());
    assert!(String::from_utf8_lossy(&out.stdout).contains("Fresh cache found"));
}
#[test]
fn invalid_disk_token_refreshes_instead_of_reusing() {
    let server = httpmock::MockServer::start();
    let login = server.mock(|w, t| {
        w.method("POST").body_includes("Login");
        t.json_body(ok(json!("fresh-session")));
    });
    server.mock(|w, t| {
        w.method("POST").body_includes("ChangeDatabase");
        t.json_body(ok(json!({})));
    });
    let bad = server.mock(|w, t| {
        w.method("POST")
            .body_includes("RunExternalAction")
            .body_includes("stale-session");
        t.json_body(
            json!({"result":[{"fields":{"IsError":true,"ErrorMessage":"invalid session"}}]}),
        );
    });
    let good = server.mock(|w, t| {
        w.method("POST")
            .body_includes("RunExternalAction")
            .body_includes("fresh-session");
        t.json_body(ok(json!({})));
    });
    let d = tempfile::tempdir().unwrap();
    let mut s = settings(d.path());
    s.base_url = server.base_url();
    s.endpoint = "/".into();
    s.username = "test-user".into();
    s.password = "test-value".into();
    let mut c = HeliosClient::new(s, log()).unwrap();
    c.token = Some("stale-session".into());
    c.expires = Some(chrono::Local::now().naive_local() + chrono::Duration::minutes(5));
    c.save_token().unwrap();
    assert!(success(&c.action("A", json!([])).unwrap()));
    login.assert_calls(1);
    bad.assert_calls(1);
    good.assert_calls(1);
}
#[test]
fn cli_browse_exports_fixture_without_live_network() {
    let server = httpmock::MockServer::start();
    server.mock(|w, t| {
        w.method("POST").body_includes("ChangeDatabase");
        t.json_body(ok(json!({})));
    });
    server.mock(|w,t|{w.method("POST").body_includes("GetBrowse");t.json_body(ok(json!({"table":{"rows":[[{"FieldName":"ID","Value":"1"},{"FieldName":"Nazev1","Value":"Tulip"},{"FieldName":"_cena_cu1","Value":"7,5"}]]}})));});
    let d = tempfile::tempdir().unwrap();
    fs::create_dir(d.path().join("data")).unwrap();
    fs::write(d.path().join("data/tulipa_session.json"),json!({"token":"test-session","username":"test-user","expires_at":(chrono::Local::now().naive_local()+chrono::Duration::minutes(5)).format("%Y-%m-%dT%H:%M:%S%.6f").to_string()}).to_string()).unwrap();
    let out = Command::new(env!("CARGO_BIN_EXE_tulipa-scraper"))
        .current_dir(d.path())
        .env("HELIOS_URL", server.base_url())
        .env("HELIOS_USERNAME", "test-user")
        .args(["--browse", "--output", "out.csv"])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stdout)
    );
    let v = read_csv(&d.path().join("out.csv")).unwrap();
    assert_eq!(v[0]["_cena_cu1"], "7.5");
    assert_eq!(v[0]["HlavniSkupina"], "Browse");
}
struct Script {
    calls: Vec<(String, Value)>,
    replies: std::collections::VecDeque<Value>,
}
impl Rpc for Script {
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
fn table(items: Vec<Value>) -> Value {
    ok(
        json!({"table":{"rows":items.into_iter().map(|v|v.as_object().unwrap().iter().map(|(k,v)|json!({"FieldName":k,"Value":v})).collect::<Vec<_>>()).collect::<Vec<_>>()}}),
    )
}
#[test]
fn complete_walk_fallback_dedup_and_category_annotations() {
    let mut rpc=Script{calls:vec![],replies:vec![table(vec![json!({"K1":"Dekor","Pocet":"1"})]),ok(json!({"table":{"rows":[[{"Value":"ignored"},{"Value":"sub"},{"Value":"Sub name"}]]}})),table(vec![json!({"ID":"1","_EAN":"same"})]),table(vec![json!({"ID":"2","_EAN":"same"}),json!({"ID":"3"})]),table(vec![json!({"K1":"cut","K1Name":"Cut flowers","Pocet":1})]),table(vec![json!({"K2":"rose","K2Name":"Roses"})]),table(vec![json!({"ID":"4"})]),table(vec![])].into()};
    let s = Settings::default();
    let l = log();
    let products = Scraper {
        rpc: &mut rpc,
        settings: &s,
        log: &l,
    }
    .browse()
    .unwrap();
    assert_eq!(products.len(), 3);
    assert_eq!(products[0]["HlavniSkupina"], "Dekor");
    assert_eq!(products[1]["HlavniSkupina"], "Extra");
    assert_eq!(products[2]["HlavniSkupina"], "Řezané");
    assert_eq!(products[2]["PodskupinaKod"], "cut");
    assert_eq!(rpc.calls[0], (CATEGORIES.into(), json!(["200"])));
    assert!(rpc.replies.is_empty());
}
#[test]
fn nested_field_values_and_browse_diagnostics_preserve_python_notation() {
    assert_eq!(
        python_repr(&json!({"items":["Tulip",null,true]})),
        "{'items': ['Tulip', None, True]}"
    );
    assert_eq!(python_repr(&json!("a'b")), "\"a'b\"");
    assert_eq!(text(&json!([1, "x"])), "[1, 'x']");
    assert!(select_products(vec![Product::new()], Some("missing"), None).is_empty());
}
#[test]
fn a_failed_csv_write_keeps_the_previous_file() {
    let d = tempfile::tempdir().unwrap();
    let f = d.path().join("out.csv");
    fs::write(&f, "old").unwrap();
    // The sibling the new file is built in cannot be created.
    fs::create_dir(d.path().join("out.csv.part")).unwrap();
    assert!(!write_csv(&[p(json!({"ID":"1"}))], &f, &log()));
    assert_eq!(fs::read_to_string(&f).unwrap(), "old");
    fs::remove_dir(d.path().join("out.csv.part")).unwrap();
    assert!(write_csv(&[p(json!({"ID":"1"}))], &f, &log()));
    assert!(!d.path().join("out.csv.part").exists());
}
#[test]
fn trailing_slash_in_base_url_and_error_retry_wait() {
    let s = Settings {
        base_url: "https://host/".into(),
        endpoint: "/api".into(),
        ..Settings::default()
    };
    assert_eq!(s.full_url(), "https://host/api");
    assert_eq!(s.loop_wait(false), 1800);
    assert_eq!(s.loop_wait(true), 300);
}
