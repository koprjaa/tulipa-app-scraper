// Project: tulipa-app-scraper
// File: scraper.rs
// Description: Product traversal and portal discovery.
// Author: Jan Alexandr Kopřiva
// License: MIT
use crate::{
    client::{fields, success, Rpc},
    *,
};
use serde_json::json;
use std::{collections::HashSet, thread, time::Duration};
pub fn extract_rows(v: &Value) -> Vec<Value> {
    v.as_array()
        .or_else(|| v["table"]["rows"].as_array())
        .or_else(|| v["fields"]["QueryBrowse"]["table"]["rows"].as_array())
        .cloned()
        .unwrap_or_default()
}
pub fn row_to_dict(row: &Value) -> Product {
    let mut p = Product::new();
    if let Some(row) = row.as_array() {
        for f in row {
            let key = f["FieldName"].as_str().unwrap_or("");
            let mut value = f.get("Value").cloned().unwrap_or(json!(""));
            if key.starts_with("_cena_cu") {
                if let Some(s) = value.as_str() {
                    value = s.replace(',', ".").into();
                }
            }
            p.insert(key.into(), value);
        }
    }
    p
}
fn rows(v: &Value) -> Vec<Product> {
    if success(v) {
        extract_rows(&fields(v)["Result"])
            .iter()
            .map(row_to_dict)
            .collect()
    } else {
        vec![]
    }
}
fn annotate(p: &mut Product, group: &Value, code: &Value, name: &Value) {
    for (k, v) in [
        ("HlavniSkupina", group),
        ("PodskupinaKod", code),
        ("PodskupinaNazev", name),
    ] {
        p.insert(k.into(), v.clone());
    }
}
pub struct Scraper<'a, R: Rpc> {
    pub rpc: &'a mut R,
    pub settings: &'a Settings,
    pub log: &'a Logger,
}
impl<R: Rpc> Scraper<'_, R> {
    /// An action that got no answer at all (timeout, dropped connection, bad body)
    /// fails the export; carrying on would write a catalogue with holes in it.
    fn act(&mut self, id: &str, params: Value) -> Result<Value> {
        let v = self.rpc.action(id, params)?;
        if v.is_null() {
            return Err(format!(
                "No response from the server for action {id}; export is incomplete"
            )
            .into());
        }
        Ok(v)
    }
    fn categories(&mut self, series: &str) -> Result<Vec<Product>> {
        let v = self.act(CATEGORIES, json!([series]))?;
        if !success(&v) {
            self.log.log(
                "WARNING",
                format!("  Failed to fetch category series {series}"),
            );
        }
        Ok(rows(&v)
            .into_iter()
            .filter(|c| {
                truth(c.get("K1").unwrap_or(&Value::Null))
                    && category_count(c.get("Pocet").unwrap_or(&Value::Null)).is_some_and(|n| n > 0)
            })
            .collect())
    }
    fn products(&mut self, cat: &Value, sub: &Value) -> Result<Vec<Product>> {
        Ok(rows(&self.act(PRODUCTS, json!([cat, sub]))?))
    }
    pub fn browse(&mut self) -> Result<Vec<Product>> {
        self.log
            .log("INFO", "=== Phase 1: scrape via GetBrowse ===");
        self.rpc.activate()?;
        let v = self.rpc.browse(Some("82"))?;
        if !success(&v) {
            self.log.log("WARNING", "GetBrowse '82' failed");
        }
        let mut products = rows(&v);
        if products.is_empty() {
            self.log.log(
                "WARNING",
                "GetBrowse returned nothing; falling back to RunExternalAction",
            );
            return self.actions();
        }
        for p in &mut products {
            let sk = p.get("SkupZbo").cloned().unwrap_or(json!(""));
            p.entry("HlavniSkupina").or_insert(json!("Browse"));
            p.entry("PodskupinaKod").or_insert(sk.clone());
            p.entry("PodskupinaNazev").or_insert(sk);
        }
        self.log.log(
            "INFO",
            format!("Browse path collected {} products", products.len()),
        );
        Ok(products)
    }
    pub fn actions(&mut self) -> Result<Vec<Product>> {
        self.log
            .log("INFO", "=== Phase 1: scrape via RunExternalAction ===");
        let cats = self.categories("200")?;
        let groups: Vec<Value> = if cats.is_empty() {
            self.settings
                .main_groups
                .iter()
                .cloned()
                .map(Value::String)
                .collect()
        } else {
            cats.iter()
                .map(|c| c.get("K1").cloned().unwrap_or(Value::Null))
                .collect()
        };
        let mut combined = vec![];
        for group in groups {
            self.log
                .log("INFO", format!("Main group: {}", text(&group)));
            self.log.log(
                "INFO",
                format!("  Fetching subgroups for '{}'", text(&group)),
            );
            let v = self.act(SUBGROUPS, json!([group]))?;
            if !success(&v) {
                self.log.log(
                    "WARNING",
                    format!("  Failed to fetch subgroups for '{}'", text(&group)),
                );
                continue;
            }
            let mut subgroups: Vec<(Value, Value)> = vec![];
            for row in extract_rows(&fields(&v)["Result"]) {
                if let (Some(code), Some(name)) = (
                    row.get(1).and_then(|v| v.get("Value")),
                    row.get(2).and_then(|v| v.get("Value")),
                ) {
                    if truth(code) {
                        if let Some((_, old_name)) = subgroups
                            .iter_mut()
                            .find(|(old, _)| identity_key(old) == identity_key(code))
                        {
                            *old_name = name.clone();
                        } else {
                            subgroups.push((code.clone(), name.clone()));
                        }
                    }
                }
            }
            self.log.log(
                "INFO",
                format!(
                    "  Found {} subgroups for '{}'",
                    subgroups.len(),
                    text(&group)
                ),
            );
            for (code, name) in subgroups {
                self.log.log(
                    "INFO",
                    format!(
                        "    Fetching products for '{} / {} ({})'",
                        text(&group),
                        text(&name),
                        text(&code)
                    ),
                );
                let mut p = self.products(&group, &code)?;
                self.log
                    .log("INFO", format!("    Got {} products", p.len()));
                for p in &mut p {
                    annotate(p, &group, &code, &name);
                }
                combined.extend(p);
            }
        }
        self.log
            .log("INFO", "Fetching extra products from browse metadata...");
        let v = self.act(METADATA, json!([]))?;
        if success(&v) {
            let mut extra = rows(&v);
            for p in &mut extra {
                annotate(
                    p,
                    &json!("Extra"),
                    &json!("Extra3"),
                    &json!("Extra produkty 3"),
                );
            }
            self.log
                .log("INFO", format!("  Got {} extra products", extra.len()));
            combined.extend(extra);
        }
        for (series, label) in &self.settings.category_series {
            self.log.log(
                "INFO",
                format!("Fetching all products via category series {series} ({label})..."),
            );
            let mut total = 0;
            for cat in self.categories(series)? {
                let code = cat.get("K1").cloned().unwrap_or(Value::Null);
                let name = cat.get("K1Name").cloned().unwrap_or(json!(""));
                let expected_value = cat.get("Pocet").unwrap_or(&Value::Null);
                let expected = category_count(expected_value).unwrap_or(0);
                let expected_label = text(expected_value);
                self.log.log(
                    "INFO",
                    format!(
                        "  Category: {} — {} ({expected_label} products)",
                        text(&code),
                        text(&name)
                    ),
                );
                let v = self.act(SUBGROUPS, json!([code]))?;
                let mut got = vec![];
                if success(&v) {
                    for sub in rows(&v) {
                        if !truth(sub.get("K2").unwrap_or(&Value::Null)) {
                            continue;
                        }
                        let subcode = sub.get("K2").cloned().unwrap_or(Value::Null);
                        let subname = sub.get("K2Name").cloned().unwrap_or(json!(""));
                        for mut p in self.products(&code, &subcode)? {
                            annotate(
                                &mut p,
                                &json!(label),
                                &code,
                                if !truth(&subname) { &name } else { &subname },
                            );
                            got.push(p);
                        }
                    }
                }
                if got.len() < expected {
                    self.log.log(
                        "WARNING",
                        format!(
                            "  Category {}: server reports {expected_label}, collected {}",
                            text(&code),
                            got.len()
                        ),
                    );
                }
                total += got.len();
                combined.extend(got);
            }
            self.log.log(
                "INFO",
                format!("  Total {total} products in series {series}"),
            );
        }
        let count = combined.len();
        let mut seen = HashSet::new();
        let unique: Vec<_> = combined
            .into_iter()
            .filter(|p| {
                let key = p
                    .get("_EAN")
                    .filter(|v| truth(v))
                    .or_else(|| p.get("ID").filter(|v| truth(v)));
                key.is_none_or(|v| seen.insert(identity_key(v)))
            })
            .collect();
        self.log.log(
            "INFO",
            format!(
                "Collected {} unique products ({count} rows before dedup)",
                unique.len()
            ),
        );
        Ok(unique)
    }
    pub fn discover(&mut self) -> Result<()> {
        let mut found = vec![];
        for candidate in [
            "Dnešní nabídka",
            "Dnes",
            "Nabídka",
            "Aktuální",
            "Speciální",
            "Akce",
            "Sleva",
            "Promo",
            "Týdenní",
            "Měsíční",
            "Novinky",
            "Trendy",
            "Sezónní",
            "Výprodej",
            "Doporučené",
        ] {
            self.log
                .log("INFO", format!("Probing category: '{candidate}'"));
            let v = self.rpc.action(SUBGROUPS, json!([candidate]))?;
            if success(&v) {
                if !extract_rows(&fields(&v)["Result"]).is_empty() {
                    found.push(candidate);
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
        let names = found
            .iter()
            .map(|n| format!("'{n}'"))
            .collect::<Vec<_>>()
            .join(", ");
        self.log.log(
            "INFO",
            format!("Found {} categories: [{names}]", found.len()),
        );
        Ok(())
    }
    pub fn test_actions(&mut self) -> Result<()> {
        let mut results = Product::new();
        for (id, params, description) in [
            (CONTACTS, json!(["12095", "15303"]), "Kontakty with params"),
            (CATEGORIES, json!(["100"]), "Kategorie with param 100"),
            (CATEGORIES, json!(["300"]), "Kategorie with param 300"),
            (METADATA, json!([]), "Browse metadata, no params"),
            (SUBGROUPS, json!(["Aranž"]), "Subgroups for Aranž"),
            (SUBGROUPS, json!(["Sezón"]), "Subgroups for Sezón"),
        ] {
            self.log.log("INFO", format!("Testing {description}: {id}"));
            let v = self.rpc.action(id, params)?;
            let ok = success(&v);
            results.insert(id.into(), ok.into());
            if ok {
                let count = fields(&v)["Result"]["table"]["rows"]
                    .as_array()
                    .map(|a| a.len().to_string())
                    .unwrap_or("?".into());
                self.log
                    .log("INFO", format!("  ✓ {description} — OK ({count} rows)"));
            } else {
                self.log.log(
                    "INFO",
                    format!(
                        "  ✗ {description} — FAILED: {}",
                        fields(&v)["ErrorMessage"]
                            .as_str()
                            .unwrap_or(if v.is_null() {
                                "no response"
                            } else {
                                "unknown"
                            })
                    ),
                );
            }
        }
        self.log.log(
            "INFO",
            format!(
                "Test summary: {}/{} passed",
                results.values().filter(|v| truth(v)).count(),
                results.len()
            ),
        );
        Ok(())
    }
}

// Python dictionary keys equate booleans and numerically equal integers/floats.
fn identity_key(value: &Value) -> String {
    match value {
        Value::Bool(v) => format!("number:{}", u8::from(*v)),
        Value::Number(v) => {
            let numeric = if v.is_i64() || v.is_u64() {
                v.to_string()
            } else if let Some(n) = v.as_f64().filter(|n| n.fract() == 0.0) {
                format!("{n:.0}")
            } else {
                v.to_string()
            };
            format!("number:{numeric}")
        }
        _ => value.to_string(),
    }
}

// Saturation preserves category traversal even when the server reports an enormous count.
fn category_count(value: &Value) -> Option<usize> {
    let s = text(value);
    if s.is_empty() {
        return None;
    }
    s.chars().try_fold(0usize, |count, c| {
        let digit = decimal_digit(c)?;
        Some(count.saturating_mul(10).saturating_add(digit as usize))
    })
}
