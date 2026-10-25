// Project: tulipa-app-scraper
// File: main.rs
// Description: Command line modes and repeating export workflow.
// Author: Jan Alexandr Kopřiva
// License: AGPL-3.0-or-later
use clap::Parser;
use std::{
    path::PathBuf,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tulipa_app_scraper::{
    client::{fields, success, HeliosClient, Rpc},
    scraper::{extract_rows, Scraper},
    storage::{write_csv, CacheStore},
    *,
};
#[derive(Parser)]
#[command(
    name = "tulipa-scraper",
    about = "Tulipa B2B Helios scraper — export product data to CSV.",
    disable_version_flag = true,
    infer_long_args = true,
    args_override_self = true
)]
struct Args {
    #[arg(
        long,
        default_value = "produkty_komplet.csv",
        help = "Output CSV file name"
    )]
    output: String,
    #[arg(long, help = "Filter by main group (e.g. Dekor, Kveto)")]
    filter_group: Option<String>,
    #[arg(long, allow_hyphen_values = true, value_parser = parse_limit, help = "Cap number of products")]
    limit: Option<i64>,
    #[arg(long, help = "Use fast GetBrowse endpoint")]
    browse: bool,
    #[arg(long = "loop", help = "Rerun every 30 minutes; Ctrl+C stops")]
    repeat: bool,
    #[arg(long, help = "Wipe cached session token and exit")]
    reset: bool,
    #[arg(long, help = "Probe candidate categories and exit")]
    discover: bool,
    #[arg(long, help = "List available GetBrowse definitions and exit")]
    list_browse: bool,
    #[arg(long, help = "Probe each known ActionID with expected params and exit")]
    test_actions: bool,
    #[arg(long, help = "Enable debug-level logging")]
    debug: bool,
    #[arg(long,default_value="INFO",value_parser=["DEBUG","INFO","WARNING","ERROR"])]
    log_level: String,
    #[arg(long, default_value_t = 5, allow_hyphen_values = true, value_parser = parse_limit)]
    safety_reserve: i64,
    #[arg(long, default_value_t = 20, allow_hyphen_values = true, value_parser = parse_limit)]
    reserve_threshold: i64,
}
fn parse_limit(value: &str) -> std::result::Result<i64, String> {
    let value = value.trim();
    let (negative, digits) = if let Some(s) = value.strip_prefix('-') {
        (true, s)
    } else {
        (false, value.strip_prefix('+').unwrap_or(value))
    };
    let mut normalized = String::new();
    let mut previous_digit = false;
    for c in digits.chars() {
        if c == '_' && previous_digit {
            previous_digit = false;
        } else if let Some(n) = decimal_digit(c) {
            normalized.push(char::from(b'0' + n as u8));
            previous_digit = true;
        } else {
            return Err("expected an integer".into());
        }
    }
    if !previous_digit {
        return Err("expected an integer".into());
    }
    if negative {
        normalized.insert(0, '-');
    }
    Ok(normalized
        .parse::<i64>()
        .unwrap_or(if negative { i64::MIN } else { i64::MAX }))
}
fn run_once(args: &Args, client: &mut HeliosClient, s: &Settings, log: &Logger) -> Result<i32> {
    let cache = CacheStore {
        data_dir: s.data_dir.clone(),
    };
    let partial = args.filter_group.as_ref().is_some_and(|s| !s.is_empty())
        || args.limit.is_some_and(|n| n != 0);
    let base = if partial {
        "produkty_vyber"
    } else {
        "produkty_komplet"
    };
    let custom = (args.output != "produkty_komplet.csv").then(|| PathBuf::from(&args.output));
    let latest = if partial {
        None
    } else if let Some(p) = &custom {
        p.exists().then(|| p.clone())
    } else {
        cache.find_latest(base)
    };
    if let Some(p) = latest {
        if CacheStore::is_fresh(&p, 1) {
            log.log(
                "INFO",
                format!("Fresh cache found: {} — skipping scrape", p.display()),
            );
            return Ok(0);
        }
    }
    log.log("INFO", "Cache stale or missing — starting scrape");
    let mut scraper = Scraper {
        rpc: client,
        settings: s,
        log,
    };
    let products = if args.browse {
        scraper.browse()?
    } else {
        scraper.actions()?
    };
    let products = select_products(products, args.filter_group.as_deref(), args.limit);
    if products.is_empty() {
        log.log("ERROR", "No products scraped; aborting");
        return Ok(1);
    }
    let out = match custom {
        Some(p) => p,
        None => cache.new_cache_path(base)?,
    };
    if !write_csv(&products, &out, log) {
        log.log("ERROR", "CSV write failed");
        return Ok(1);
    }
    cache.cleanup_old(base, 24);
    log.log("INFO", "Workflow finished");
    Ok(0)
}
fn execute(args: &Args, s: &Settings, log: &Logger) -> Result<i32> {
    let mut client = HeliosClient::new(s.clone(), log.clone())?;
    if args.reset {
        log.log("INFO", "Reset mode — wiping session");
        client.logout()?;
        return Ok(0);
    }
    if args.discover {
        log.log("INFO", "Discovery mode");
        Scraper {
            rpc: &mut client,
            settings: s,
            log,
        }
        .discover()?;
        return Ok(0);
    }
    if args.test_actions {
        log.log("INFO", "Test-actions mode");
        Scraper {
            rpc: &mut client,
            settings: s,
            log,
        }
        .test_actions()?;
        return Ok(0);
    }
    if args.list_browse {
        log.log("INFO", "List-browse mode");
        client.activate()?;
        let v = client.browse(None)?;
        let rows = if success(&v) {
            extract_rows(&fields(&v)["Result"])
        } else {
            vec![]
        };
        log.log("INFO", format!("Found {} browse definitions:", rows.len()));
        for (i, row) in rows.iter().take(10).enumerate() {
            log.log(
                "INFO",
                format!(
                    "  {}. {}",
                    i + 1,
                    python_repr(&serde_json::Value::Object(
                        row.as_array()
                            .into_iter()
                            .flatten()
                            .map(|field| (
                                field["FieldName"].as_str().unwrap_or("").to_owned(),
                                field
                                    .get("Value")
                                    .cloned()
                                    .unwrap_or_else(|| serde_json::json!(""))
                            ))
                            .collect()
                    ))
                ),
            );
        }
        return Ok(0);
    }
    std::fs::create_dir_all(&s.data_dir)?;
    let stopped = Arc::new(AtomicBool::new(false));
    let signal = stopped.clone();
    let signal_log = log.clone();
    let repeating = args.repeat;
    ctrlc::set_handler(move || {
        signal.store(true, Ordering::SeqCst);
        signal_log.log(
            "INFO",
            if repeating {
                "Loop stopped by user"
            } else {
                "Interrupted by user"
            },
        );
        std::process::exit(if repeating { 0 } else { 1 });
    })?;
    // The flag says the workflow raised an error (as opposed to finishing with a failing code).
    let run = |client: &mut HeliosClient| match run_once(args, client, s, log) {
        Ok(n) => (n, false),
        Err(e) => {
            log.log("ERROR", format!("Workflow failed: {e}"));
            (1, true)
        }
    };
    if !args.repeat {
        return Ok(run(&mut client).0);
    }
    log.log(
        "INFO",
        format!(
            "Loop mode — every {} minutes. Ctrl+C to stop.",
            s.loop_interval_seconds / 60
        ),
    );
    let mut iteration = 1u64;
    while !stopped.load(Ordering::SeqCst) {
        log.log(
            "INFO",
            format!(
                "Iteration #{iteration} — {}",
                chrono::Local::now().format("%Y-%m-%d %H:%M:%S")
            ),
        );
        let (rc, errored) = run(&mut client);
        let wait = s.loop_wait(errored);
        log.log(
            if rc == 0 { "INFO" } else { "WARNING" },
            format!("Iteration #{iteration} finished (exit {rc})"),
        );
        iteration = iteration.saturating_add(1);
        log.log(
            "INFO",
            format!("Sleeping {} min before next iteration", wait / 60),
        );
        for _ in 0..wait {
            for _ in 0..10 {
                if stopped.load(Ordering::SeqCst) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(100));
            }
        }
    }
    log.log("INFO", "Loop stopped by user");
    Ok(0)
}
fn main() {
    eprintln!(
        "{}\n  tulipa-app-scraper  v0.2.0\n",
        include_str!("../assets/banner.txt")
    );
    let args = Args::parse();
    let log = Logger {
        level: match args.log_level.as_str() {
            "DEBUG" => 0,
            "INFO" => 1,
            "WARNING" => 2,
            _ => 3,
        },
    };
    let mut s = Settings::from_env();
    s.debug = args.debug;
    s.safety_reserve = args.safety_reserve;
    s.reserve_threshold = args.reserve_threshold;
    let code = match execute(&args, &s, &log) {
        Ok(c) => c,
        Err(e) => {
            log.log("ERROR", format!("Workflow failed: {e}"));
            1
        }
    };
    std::process::exit(code);
}
