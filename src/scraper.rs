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
fn annotate(p: &mut Product, group: &str, code: &str, name: &str) {
    for (k, v) in [
        ("HlavniSkupina", group),
        ("PodskupinaKod", code),
        ("PodskupinaNazev", name),
    ] {
        p.insert(k.into(), v.into());
    }
}
pub struct Scraper<'a, R: Rpc> {
    pub rpc: &'a mut R,
    pub settings: &'a Settings,
    pub log: &'a Logger,
}
impl<R: Rpc> Scraper<'_, R> {
    fn categories(&mut self, series: &str) -> Result<Vec<Product>> {
        let v = self.rpc.action(CATEGORIES, json!([series]))?;
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
                    && text(c.get("Pocet").unwrap_or(&Value::Null))
                        .chars()
                        .all(|c| c.is_ascii_digit())
                    && text(c.get("Pocet").unwrap_or(&Value::Null))
                        .parse::<u64>()
                        .unwrap_or(0)
                        > 0
            })
            .collect())
    }
    fn products(&mut self, cat: &str, sub: &str) -> Result<Vec<Product>> {
        Ok(rows(&self.rpc.action(PRODUCTS, json!([cat, sub]))?))
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
        let groups = if cats.is_empty() {
            self.settings.main_groups.clone()
        } else {
            cats.iter()
                .map(|c| text(c.get("K1").unwrap_or(&Value::Null)))
                .collect()
        };
        let mut combined = vec![];
        for group in groups {
            self.log.log("INFO", format!("Main group: {group}"));
            self.log
                .log("INFO", format!("  Fetching subgroups for '{group}'"));
            let v = self.rpc.action(SUBGROUPS, json!([group]))?;
            if !success(&v) {
                self.log.log(
                    "WARNING",
                    format!("  Failed to fetch subgroups for '{group}'"),
                );
                continue;
            }
            let mut subgroups = Product::new();
            for row in extract_rows(&fields(&v)["Result"]) {
                if let (Some(code), Some(name)) = (
                    row.get(1).and_then(|v| v.get("Value")),
                    row.get(2).and_then(|v| v.get("Value")),
                ) {
                    if truth(code) {
                        subgroups.insert(text(code), name.clone());
                    }
                }
            }
            self.log.log(
                "INFO",
                format!("  Found {} subgroups for '{group}'", subgroups.len()),
            );
            for (code, name) in subgroups {
                let name = text(&name);
                self.log.log(
                    "INFO",
                    format!("    Fetching products for '{group} / {name} ({code})'"),
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
        let v = self.rpc.action(METADATA, json!([]))?;
        if success(&v) {
            let mut extra = rows(&v);
            for p in &mut extra {
                annotate(p, "Extra", "Extra3", "Extra produkty 3");
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
                let code = text(cat.get("K1").unwrap_or(&Value::Null));
                let name = text(cat.get("K1Name").unwrap_or(&Value::Null));
                let expected = text(cat.get("Pocet").unwrap_or(&Value::Null)).parse::<usize>()?;
                self.log.log(
                    "INFO",
                    format!("  Category: {code} — {name} ({expected} products)"),
                );
                let v = self.rpc.action(SUBGROUPS, json!([code]))?;
                let mut got = vec![];
                if success(&v) {
                    for sub in rows(&v) {
                        if !truth(sub.get("K2").unwrap_or(&Value::Null)) {
                            continue;
                        }
                        let subcode = text(sub.get("K2").unwrap_or(&Value::Null));
                        let subname = text(sub.get("K2Name").unwrap_or(&Value::Null));
                        for mut p in self.products(&code, &subcode)? {
                            annotate(
                                &mut p,
                                label,
                                &code,
                                if subname.is_empty() { &name } else { &subname },
                            );
                            got.push(p);
                        }
                    }
                }
                if got.len() < expected {
                    self.log.log(
                        "WARNING",
                        format!(
                            "  Category {code}: server reports {expected}, collected {}",
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
                key.is_none_or(|v| seen.insert(v.to_string()))
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
