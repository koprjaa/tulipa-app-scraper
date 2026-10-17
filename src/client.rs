// Project: tulipa-app-scraper
// File: client.rs
// Description: Sequential verified HTTP transport and session lifecycle.
// Author: Jan Alexandr Kopřiva
// License: MIT
use crate::{truth, Logger, Result, Settings};
use chrono::{Duration, Local, NaiveDateTime};
use serde_json::{json, Value};
use std::{fs, io::Write, thread, time};
pub const CA_BUNDLE: &[u8] = include_bytes!("../assets/tulipa_ca.pem");
pub fn fields(v: &Value) -> &Value {
    &v["result"][0]["fields"]
}
pub fn success(v: &Value) -> bool {
    fields(v).get("IsError").is_some_and(|v| !truth(v))
}
pub fn token_error(v: &Value) -> bool {
    let msg = fields(v)["ErrorMessage"]
        .as_str()
        .unwrap_or_default()
        .to_lowercase();
    ["session", "token", "login", "expired", "invalid"]
        .iter()
        .any(|s| msg.contains(s))
}
pub trait Rpc {
    fn action(&mut self, id: &str, params: Value) -> Result<Value>;
    fn browse(&mut self, name: Option<&str>) -> Result<Value>;
    fn activate(&mut self) -> Result<()>;
}
pub struct HeliosClient {
    pub settings: Settings,
    pub log: Logger,
    http: reqwest::blocking::Client,
    pub token: Option<String>,
    pub expires: Option<NaiveDateTime>,
}
impl HeliosClient {
    pub fn new(settings: Settings, log: Logger) -> Result<Self> {
        let mut headers = reqwest::header::HeaderMap::new();
        headers.insert("Content-Type", "application/json".parse()?);
        headers.insert("Accept", "*/*".parse()?);
        headers.insert("Connection", "keep-alive".parse()?);
        headers.insert("User-Agent", "Embarcadero RESTClient/1.0".parse()?);
        headers.insert("Accept-Encoding", "gzip, deflate, br".parse()?);
        let certs = reqwest::Certificate::from_pem_bundle(CA_BUNDLE)?;
        let mut builder = reqwest::blocking::Client::builder()
            .tls_built_in_root_certs(false)
            .redirect(reqwest::redirect::Policy::limited(30))
            .cookie_store(true)
            .default_headers(headers)
            .timeout(time::Duration::from_secs(settings.request_timeout));
        for cert in certs {
            builder = builder.add_root_certificate(cert);
        }
        Ok(Self {
            settings,
            log,
            http: builder.build()?,
            token: None,
            expires: None,
        })
    }
    pub fn call(&self, payload: Value, reset: bool) -> Value {
        if !reset {
            thread::sleep(time::Duration::from_millis(200));
        }
        if self.settings.debug {
            self.log
                .log("DEBUG", format!("POST {}", self.settings.full_url())); // Credentials and tokens must never enter diagnostic output.
        }
        let result = self
            .http
            .post(self.settings.full_url())
            .json(&payload)
            .send()
            .and_then(|r| r.error_for_status())
            .and_then(|r| r.json::<Value>());
        match result {
            Ok(v) => {
                if truth(&fields(&v)["IsError"]) {
                    self.log.log(
                        "WARNING",
                        format!(
                            "Server returned error: {}",
                            fields(&v)["ErrorMessage"].as_str().unwrap_or("unknown")
                        ),
                    );
                }
                v
            }
            Err(e) => {
                let label = if e.is_timeout() {
                    "Timeout"
                } else if e.is_connect() {
                    "Connection error"
                } else {
                    "Unexpected RPC error"
                };
                self.log.log("ERROR", format!("{label}: {e}"));
                Value::Null
            }
        }
    }
    pub fn get_token(&mut self) -> Result<String> {
        let now = Local::now().naive_local();
        if self.token.is_some() && self.expires.is_some_and(|e| now < e) {
            return Ok(self.token.clone().unwrap());
        }
        if self.settings.session_file.exists() {
            let loaded = || -> Result<(String, NaiveDateTime)> {
                let v: Value = serde_json::from_slice(&fs::read(&self.settings.session_file)?)?;
                let expiry = NaiveDateTime::parse_from_str(
                    v["expires_at"].as_str().ok_or("missing expiry")?,
                    "%Y-%m-%dT%H:%M:%S%.f",
                )?;
                if now > expiry || v["username"].as_str() != Some(&self.settings.username) {
                    return Err("expired or different username".into());
                }
                Ok((v["token"].as_str().ok_or("missing token")?.into(), expiry))
            };
            match loaded() {
                Ok((t, e)) => {
                    self.log.log(
                        "INFO",
                        format!(
                            "Loaded valid session token (expires {})",
                            e.format("%H:%M:%S")
                        ),
                    );
                    self.token = Some(t.clone());
                    self.expires = Some(e);
                    return Ok(t);
                }
                Err(_) => {
                    let _ = fs::remove_file(&self.settings.session_file);
                }
            }
        }
        if self.settings.username.is_empty() || self.settings.password.is_empty() {
            return Err("HELIOS_USERNAME and HELIOS_PASSWORD are required".into());
        }
        self.log
            .log("INFO", "Acquiring new session token via login");
        let v=self.call(json!({"_parameters":["","Login",{"Version":"1.0","Username":self.settings.username,"Password":self.settings.password,"PluginSysName":"eServerTulipaMAT","DatabaseName":"Helios001"},[]]}),true);
        if !success(&v) {
            return Err(format!(
                "Login failed: {}",
                fields(&v)["ErrorMessage"].as_str().unwrap_or("no response")
            )
            .into());
        }
        let token = fields(&v)["Result"]
            .as_str()
            .ok_or("Login failed: invalid token")?
            .to_string();
        self.token = Some(token.clone());
        self.expires = Some(
            Local::now().naive_local() + Duration::minutes(self.settings.session_timeout_minutes),
        );
        self.save_token()?;
        Ok(token)
    }
    pub fn save_token(&self) -> Result<()> {
        let path = &self.settings.session_file;
        if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent)?;
        }
        let mut options = fs::OpenOptions::new();
        options.write(true).create(true).truncate(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let mut file = options.open(path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            file.set_permissions(fs::Permissions::from_mode(0o600))?;
        }
        let now = Local::now().naive_local();
        let expires = self.expires.ok_or("missing expiry")?;
        let remaining = expires - now;
        let seconds = remaining.num_seconds();
        let timeout = format!(
            "{}:{:02}:{:02}.{:06}",
            seconds / 3600,
            (seconds / 60) % 60,
            seconds % 60,
            remaining.num_microseconds().unwrap_or(0) % 1_000_000
        );
        let v = json!({"token":self.token,"username":self.settings.username,"created_at":now.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),"expires_at":expires.format("%Y-%m-%dT%H:%M:%S%.6f").to_string(),"estimated_timeout":timeout});
        file.write_all(serde_json::to_string_pretty(&v)?.as_bytes())?;
        self.log
            .log("INFO", format!("Session token saved to {}", path.display()));
        Ok(())
    }
    pub fn logout(&mut self) -> Result<()> {
        let path = &self.settings.session_file;
        if path.exists() {
            if let Ok(data) = fs::read(path) {
                if let Ok(v) = serde_json::from_slice::<Value>(&data) {
                    if truth(&v["token"]) {
                        self.call(
                            json!({"_parameters":[v["token"],"Logout",{"Version":"1.0"},[]]}),
                            true,
                        );
                    }
                }
            }
            fs::remove_file(path)?;
        }
        self.token = None;
        self.expires = None;
        self.log.log("INFO", "Session invalidated");
        Ok(())
    }
}
impl Rpc for HeliosClient {
    fn activate(&mut self) -> Result<()> {
        let token = self.get_token()?;
        self.call(json!({"_parameters":[token,"ChangeDatabase",{"Version":"1.0","DatabaseName":"Helios001"},[]]}),true);
        Ok(())
    }
    fn browse(&mut self, name: Option<&str>) -> Result<Value> {
        let token = self.get_token()?;
        let params = match name {
            None => json!({"Version":"1.0"}),
            Some(n) => json!({"Version":"1.1","BrowseName":n}),
        };
        Ok(self.call(json!({"_parameters":[token,"GetBrowse",params,[]]}), false))
    }
    fn action(&mut self, id: &str, params: Value) -> Result<Value> {
        self.activate()?;
        let token = self.get_token()?;
        let mut payload = json!({"_parameters":[token,"RunExternalAction",{"Version":"1.0","ActionID":id,"SelectedRows":[],"Parameters":params},[]]});
        let mut response = self.call(payload.clone(), false);
        if token_error(&response) {
            self.log
                .log("WARNING", "Token error — refreshing and retrying once");
            self.token = None;
            self.expires = None;
            let _ = fs::remove_file(&self.settings.session_file);
            self.activate()?;
            payload["_parameters"][0] = self.get_token()?.into();
            response = self.call(payload, false);
        }
        Ok(response)
    }
}
