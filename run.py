#!/usr/bin/env python3
"""
Tulipa Offers Scraper - Kompletní nástroj pro stahování dat z Tulipa a synchronizaci se Shopify

Tento skript obsahuje:
1. Připojení k Tulipa serveru a stahování produktů
2. Uložení dat do CSV souboru
3. Synchronizaci inventáře se Shopify
4. Kompletní workflow pro správu produktů

Použití:
    python tulipa_offers_scraper.py [možnosti]

Možnosti:
    --scrape-only          Pouze stáhni data a ulož do CSV
    --shopify-only         Pouze synchronizuj se Shopify (přeskoč stahování)
    --show-changes         Zobrazit pouze změny v inventáři bez úprav
    --dry-run              Zobrazit co by se změnilo bez provedení změn
    --limit N              Omezit počet produktů k zpracování
    --filter-group GROUP   Filtrovat podle hlavní skupiny (např. "Dekor", "Kveto")
    --output FILE          Výstupní CSV soubor (výchozí: produkty_komplet.csv)
    --debug                Zapnout debug režim
    --reset                Resetovat session a začít znovu
    --discover             Objevit dostupné kategorie
    --loop                 Spouštět v nekonečné smyčce každých 30 minut
"""

import requests
import json
import gzip
import io
import sys
import csv
import time
import os
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dotenv import load_dotenv

# Načtení environment proměnných
load_dotenv()

# --- KONFIGURACE ---
BASE_URL = "https://eserver.tulipapraha.com:4343"
ENDPOINT = "/datasnap/rest/THeliosMethods/%22Execute%22"
FULL_URL = f"{BASE_URL}{ENDPOINT}"
USERNAME = "eserver_mat"
PASSWORD = "Mat100953"
REQUEST_TIMEOUT = 30
SESSION_FILE = os.path.join("data", "tulipa_session.json")
DEBUG_MODE = False

# --- INVENTÁŘ KONFIGURACE ---
# Maximální počet dostupných rostlin (fyzické množství)
MAX_PHYSICAL_PLANTS = 16
# Rezerva pro jistotu (kusy, které nebudeme inzerovat) - pouze pokud je méně než 20 kusů
SAFETY_RESERVE = 5
# Prahová hodnota pro odečítání rezervy
RESERVE_THRESHOLD = 20

# --- HLAVNÍ SKUPINY K PROCESOVÁNÍ ---
HLAVNI_SKUPINY = [
    "Dekor", "Kveto", "Sezón", "Sukul", 
]

# --- ID AKCÍ ---
ACTION_ID_SUBGROUPS = "7C100193-68DF-4C59-8692-33E421EEBCD3"
ACTION_ID_PRODUCTS = "7DCCBAB9-35EA-4310-BC22-B7AC873F9398"

# --- SHOPIFY KONFIGURACE ---
SHOPIFY_LOCATION_ID = "108931842374"  # "Sklad Tulipa" location
SHOPIFY_API_VERSION = "2024-10"

# --- Globální session ---
session = requests.Session()
session.headers.update({
    "Content-Type": "application/json",
    "User-Agent": "Embarcadero RESTClient/1.0",
    "Accept-Encoding": "gzip, deflate, br"
})


# =============================================================================
# TULIPA FUNKCE
# =============================================================================

def save_session_token(token, expires_at=None, logger=None):
    """Uloží session token do souboru."""
    if expires_at is None:
        estimated_timeout = estimate_session_timeout()
        expires_at = datetime.now() + estimated_timeout
        if logger:
            logger.info(f"Session token bude platný přibližně do {expires_at.strftime('%H:%M:%S')} (odhad: {estimated_timeout})")
        else:
            print(f"[INFO] Session token bude platný přibližně do {expires_at.strftime('%H:%M:%S')} (odhad: {estimated_timeout})")
    
    session_data = {
        "token": token,
        "username": USERNAME,
        "created_at": datetime.now().isoformat(),
        "expires_at": expires_at.isoformat(),
        "estimated_timeout": str(expires_at - datetime.now())
    }
    
    try:
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(session_data, f, indent=2, ensure_ascii=False)
        if logger:
            logger.info(f"Session token uložen do {SESSION_FILE}")
        else:
            print(f"[INFO] Session token uložen do {SESSION_FILE}")
        return True
    except Exception as e:
        if logger:
            logger.warning(f"Nepodařilo se uložit session token: {e}")
        else:
            print(f"[VAROVÁNÍ] Nepodařilo se uložit session token: {e}")
        return False


def load_session_token():
    """Načte session token ze souboru, pokud je platný."""
    if not os.path.exists(SESSION_FILE):
        return None
    
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            session_data = json.load(f)
        
        expires_at = datetime.fromisoformat(session_data["expires_at"])
        if datetime.now() > expires_at:
            print(f"[INFO] Session token vypršel v {expires_at.strftime('%H:%M:%S')}")
            os.remove(SESSION_FILE)
            return None
        
        if session_data.get("username") != USERNAME:
            print("[INFO] Session token je pro jiného uživatele")
            os.remove(SESSION_FILE)
            return None
        
        print(f"[INFO] Načten platný session token (vyprší v {expires_at.strftime('%H:%M:%S')})")
        return session_data["token"]
        
    except Exception as e:
        print(f"[VAROVÁNÍ] Nepodařilo se načíst session token: {e}")
        if os.path.exists(SESSION_FILE):
            os.remove(SESSION_FILE)
        return None


def estimate_session_timeout():
    """Odhadne timeout session na základě pozorování."""
    # Tokeny obvykle vydrží déle než 30 minut, ale pro jistotu použijeme konzervativní odhad
    # Pokud token vydrží déle, bude automaticky obnoven při kontrole platnosti
    return timedelta(minutes=45)


def check_token_validity(session_token, logger=None):
    """Zkontroluje platnost tokenu - zjednodušená verze."""
    if not session_token:
        return False
    
    # Token je platný pokud existuje a není starší než 45 minut
    # Kontrola se provádí pouze na základě času, ne API volání
    return True


def is_token_error(response, logger=None):
    """Zkontroluje, zda je chyba v odpovědi způsobena neplatným tokenem."""
    if not response:
        return False
    
    try:
        # Kontrola chybové zprávy
        error_msg = response.get("result", [{}])[0].get("fields", {}).get("ErrorMessage", "").lower()
        if any(keyword in error_msg for keyword in ["session", "token", "login", "expired", "invalid"]):
            if logger:
                logger.debug(f"Detekována token chyba: {error_msg}")
            return True
        
        # Kontrola chybového kódu
        is_error = response.get("result", [{}])[0].get("fields", {}).get("IsError", False)
        if is_error and "session" in error_msg:
            if logger:
                logger.debug(f"Detekována session chyba: {error_msg}")
            return True
            
    except (KeyError, IndexError, AttributeError) as e:
        if logger:
            logger.debug(f"Chyba při parsování odpovědi pro token kontrolu: {e}")
        pass
    
    return False


def refresh_session_token(logger=None):
    """Obnoví session token při vypršení."""
    if logger:
        logger.info("Obnovuji session token...")
    else:
        print("[INFO] Obnovuji session token...")
    
    login_payload = {
        "_parameters": [
            "", "Login", 
            {
                "Version": "1.0", 
                "Username": USERNAME, 
                "Password": PASSWORD, 
                "PluginSysName": "eServerTulipaMAT", 
                "DatabaseName": "Helios001"
            }, 
            []
        ]
    }
    
    login_response = execute_helios_command(login_payload, logger=logger)
    if not login_response or login_response["result"][0]["fields"]["IsError"]:
        error_msg = login_response["result"][0]["fields"].get("ErrorMessage", "Neznámá chyba") if login_response else "Žádná odpověď"
        if logger:
            logger.error(f"Obnovení tokenu selhalo: {error_msg}")
        else:
            print(f"[CHYBA] Obnovení tokenu selhalo: {error_msg}")
        return None
    
    new_token = login_response["result"][0]["fields"]["Result"]
    if logger:
        logger.info("Token úspěšně obnoven!")
    else:
        print("[OK] Token úspěšně obnoven!")
    
    save_session_token(new_token, logger=logger)
    return new_token


def get_valid_session_token(logger=None):
    """Získá platný session token - buď načte existující nebo vytvoří nový."""
    # Zkusíme načíst existující token
    session_token = load_session_token()
    
    if session_token:
        # Zkontrolujeme jeho platnost
        if check_token_validity(session_token, logger):
            if logger:
                logger.debug("Existující token je platný")
            return session_token
        else:
            if logger:
                logger.info("Existující token není platný, obnovuji...")
            # Token není platný, zkusíme ho obnovit
            session_token = refresh_session_token(logger)
            if session_token:
                return session_token
    
    # Pokud nemáme platný token, vytvoříme nový
    if logger:
        logger.info("Vytvářím nový session token...")
    else:
        print("[INFO] Vytvářím nový session token...")
    
    session_token = refresh_session_token(logger)
    return session_token


def execute_helios_command(payload, is_reset_call=False, session_token=None, logger=None):
    """Odesílá příkaz na server a zpracovává odpověď."""
    try:
        if not is_reset_call:
            time.sleep(0.2)
        
        if DEBUG_MODE:
            print(f"[DEBUG] Odesílám požadavek na: {FULL_URL}")
            print(f"[DEBUG] Payload: {json.dumps(payload, indent=2)}")
        
        response = session.post(FULL_URL, data=json.dumps(payload), verify=False, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        
        if DEBUG_MODE and len(response.content) < 1000:
            print(f"[DEBUG] Response Content: {response.text}")
        elif DEBUG_MODE:
            print(f"[DEBUG] Response Content (prvních 500 znaků): {response.text[:500]}...")
        
        # Zkontrolujeme, zda je v odpovědi chyba a vypíšeme ji
        try:
            response_data = response.json()
            if response_data.get("result", [{}])[0].get("fields", {}).get("IsError", False):
                error_msg = response_data.get("result", [{}])[0].get("fields", {}).get("ErrorMessage", "Neznámá chyba")
                if logger:
                    logger.warning(f"Server vrátil chybu: {error_msg}")
                else:
                    print(f"[VAROVÁNÍ] Server vrátil chybu: {error_msg}")
        except (KeyError, IndexError, json.JSONDecodeError):
            pass
        
        if 'gzip' in response.headers.get('Content-Encoding', ''):
            try:
                buffer = io.BytesIO(response.content)
                with gzip.GzipFile(fileobj=buffer) as f:
                    decompressed_content = f.read()
                return json.loads(decompressed_content.decode('utf-8'))
            except (gzip.BadGzipFile, EOFError):
                return response.json()
        else:
            return response.json()
            
    except requests.exceptions.ConnectionError as e:
        if logger:
            logger.error(f"Problém s připojením k serveru: {e}")
        else:
            print(f"\n[CHYBA] Problém s připojením k serveru: {e}")
            print("[INFO] Server může být nedostupný nebo restartuje.")
        return None
    except requests.exceptions.Timeout as e:
        if logger:
            logger.error(f"Timeout při připojení k serveru: {e}")
        else:
            print(f"\n[CHYBA] Timeout při připojení k serveru: {e}")
            print("[INFO] Server je pomalý nebo přetížený.")
        return None
    except Exception as e:
        if logger:
            logger.error(f"Neočekávaná chyba: {e}")
        else:
            print(f"\n[CHYBA] Neočekávaná chyba: {e}")
            if DEBUG_MODE:
                import traceback
                print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return None


def fetch_subgroups(group_name, logger=None):
    """Stáhne podskupiny pro hlavní skupinu."""
    if logger:
        logger.info(f"Stahuji podskupiny pro skupinu: '{group_name}'...")
    else:
        print(f"[*] Stahuji podskupiny pro skupinu: '{group_name}'...")
    
    # Získáme platný session token
    session_token = get_valid_session_token(logger)
    if not session_token:
        if logger:
            logger.error("Nepodařilo se získat platný session token")
        else:
            print("[CHYBA] Nepodařilo se získat platný session token")
        return {}
    
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {"Version": "1.0", "ActionID": ACTION_ID_SUBGROUPS, "Parameters": [group_name]},
            []
        ]
    }
    response = execute_helios_command(payload, logger=logger)
    
    # Kontrola chyb souvisejících s tokenem
    if is_token_error(response, logger):
        if logger:
            logger.warning(f"Token chyba při stahování podskupin pro '{group_name}', zkusím obnovit token...")
        else:
            print(f"  -> Token chyba při stahování podskupin pro '{group_name}', zkusím obnovit token...")
        
        # Zkusíme obnovit token a opakovat požadavek
        new_token = refresh_session_token(logger)
        if new_token:
            payload["_parameters"][0] = new_token
            response = execute_helios_command(payload, logger=logger)
    
    if not response or response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.warning(f"Pro skupinu '{group_name}' se nepodařilo stáhnout podskupiny.")
        else:
            print(f"  -> Pro skupinu '{group_name}' se nepodařilo stáhnout podskupiny.")
        return {}
    
    try:
        rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
    except (KeyError, IndexError):
        return {}

    if not rows:
        return {}

    subgroups = {}
    for row in rows:
        try:
            subcode = row[1]['Value']
            subname = row[2]['Value']
            if subcode:
                subgroups[subcode] = subname
        except (IndexError, KeyError):
            continue
    
    if logger:
        logger.info(f"Nalezeno {len(subgroups)} podskupin pro skupinu '{group_name}'.")
    else:
        print(f"  -> Nalezeno {len(subgroups)} podskupin.")
    return subgroups


def fetch_products(group_name, subgroup_code, subgroup_name, logger=None):
    """Stáhne produkty pro danou kombinaci."""
    if logger:
        logger.info(f"  Stahuji produkty pro '{group_name} -> {subgroup_name} ({subgroup_code})'...")
    else:
        print(f"  [*] Stahuji produkty pro '{group_name} -> {subgroup_name} ({subgroup_code})'...")
    
    # Získáme platný session token
    session_token = get_valid_session_token(logger)
    if not session_token:
        if logger:
            logger.error("Nepodařilo se získat platný session token")
        else:
            print("[CHYBA] Nepodařilo se získat platný session token")
        return []
    
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {"Version": "1.0", "ActionID": ACTION_ID_PRODUCTS, "Parameters": [group_name, subgroup_code]},
            []
        ]
    }
    response = execute_helios_command(payload, logger=logger)
    
    # Kontrola chyb souvisejících s tokenem
    if is_token_error(response, logger):
        if logger:
            logger.warning(f"Token chyba při stahování produktů pro '{subgroup_name}', zkusím obnovit token...")
        else:
            print(f"    -> Token chyba při stahování produktů pro '{subgroup_name}', zkusím obnovit token...")
        
        # Zkusíme obnovit token a opakovat požadavek
        new_token = refresh_session_token(logger)
        if new_token:
            payload["_parameters"][0] = new_token
            response = execute_helios_command(payload, logger=logger)
    
    if not response or response["result"][0]["fields"].get("IsError", True):
        return []
    
    rows = None
    try:
        result_node = response["result"][0]["fields"]["Result"]
        if "fields" in result_node and "QueryBrowse" in result_node["fields"]:
            rows = result_node["fields"]["QueryBrowse"]["table"]["rows"]
        elif "table" in result_node:
            rows = result_node["table"]["rows"]
    except (KeyError, IndexError, TypeError):
        return []

    if not rows:
        return []

    processed_products = []
    for row in rows:
        item_dict = {field['FieldName']: field['Value'] for field in row}
        processed_products.append(item_dict)
        
    if logger:
        logger.info(f"    Staženo {len(processed_products)} produktů pro '{subgroup_name}'.")
    else:
        print(f"    -> Staženo {len(processed_products)} produktů.")
    return processed_products


def discover_categories(logger=None):
    """Objeví všechny dostupné kategorie."""
    if logger:
        logger.info("Objevuji dostupné kategorie...")
    else:
        print("[INFO] Objevuji dostupné kategorie...")
    
    # Získáme platný session token
    session_token = get_valid_session_token(logger)
    if not session_token:
        if logger:
            logger.error("Nepodařilo se získat platný session token")
        else:
            print("[CHYBA] Nepodařilo se získat platný session token")
        return {}
    
    possible_categories = [
        "Dnešní nabídka", "Dnes", "Nabídka", "Aktuální", "Speciální",
        "Akce", "Sleva", "Promo", "Týdenní", "Měsíční",
        "Novinky", "Trendy", "Sezónní", "Výprodej", "Doporučené"
    ]
    
    discovered_categories = {}
    
    for category in possible_categories:
        try:
            if logger:
                logger.info(f"Zkouším kategorii: '{category}'...")
            else:
                print(f"  [*] Zkouším kategorii: '{category}'...")
            payload = {
                "_parameters": [
                    session_token, "RunExternalAction",
                    {"Version": "1.0", "ActionID": ACTION_ID_SUBGROUPS, "Parameters": [category]},
                    []
                ]
            }
            response = execute_helios_command(payload, logger=logger)
            
            if response and not response["result"][0]["fields"].get("IsError", True):
                try:
                    rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
                    if rows:
                        discovered_categories[category] = {
                            "exists": True,
                            "subgroups_count": len(rows),
                            "sample_subgroups": [row[2]['Value'] for row in rows[:3]]
                        }
                        if logger:
                            logger.info(f"Nalezena! Má {len(rows)} podskupin")
                        else:
                         print(f"    ✅ Nalezena! Má {len(rows)} podskupin")
                    else:
                        discovered_categories[category] = {"exists": True, "subgroups_count": 0}
                        if logger:
                            logger.info("Nalezena! (bez podskupin)")
                        else:
                          print(f"    ✅ Nalezena! (bez podskupin)")
                except (KeyError, IndexError):
                    discovered_categories[category] = {"exists": True, "subgroups_count": "unknown"}
                    if logger:
                        logger.info("Nalezena! (struktura odpovědi se liší)")
                    else:
                       print(f"    ✅ Nalezena! (struktura odpovědi se liší)")
            else:
                discovered_categories[category] = {"exists": False}
                if logger:
                    logger.info("Nenalezena")
                else:
                    print(f"    ❌ Nenalezena")
                
        except Exception as e:
            discovered_categories[category] = {"exists": False, "error": str(e)}
            if logger:
                logger.warning(f"Chyba: {e}")
            else:
                print(f"    ❌ Chyba: {e}")
        
        time.sleep(0.1)
    
    return discovered_categories


def show_category_discovery_results(results):
    """Zobrazí výsledky objevování kategorií."""
    print(f"\n{'='*60}")
    print("           OBJEVENÉ KATEGORIE")
    print(f"{'='*60}")
    
    found_categories = []
    for category, info in results.items():
        if info.get("exists"):
            found_categories.append((category, info))
    
    if not found_categories:
        print("❌ Nebyla nalezena žádná nová kategorie.")
        return []
    
    print(f"✅ Nalezeno {len(found_categories)} kategorií:\n")
    
    for category, info in found_categories:
        print(f"📁 {category}")
        if "subgroups_count" in info:
            if info["subgroups_count"] == 0:
                print(f"   └── Bez podskupin")
            elif isinstance(info["subgroups_count"], int):
                print(f"   └── {info['subgroups_count']} podskupin")
            else:
                print(f"   └── Počet podskupin: {info['subgroups_count']}")
        print()
    
    return [cat for cat, _ in found_categories]


def scrape_tulipa_data(logger=None):
    """Stáhne všechna data z Tulipa."""
    if logger:
        logger.info("=== FÁZE 1: STAHOVÁNÍ DAT Z TULIPA ===")
    
    # Získáme platný session token
    session_token = get_valid_session_token(logger)
    if not session_token:
        if logger:
            logger.error("Nepodařilo se získat platný session token")
        else:
            print("[CHYBA] Nepodařilo se získat platný session token")
            return []
    
    # Aktivace databáze
    change_db_payload = {
        "_parameters": [
            session_token, "ChangeDatabase", 
            {"Version": "1.0", "DatabaseName": "Helios001"}, []
        ]
    }
    change_db_response = execute_helios_command(change_db_payload, is_reset_call=True, logger=logger)
    if not change_db_response or change_db_response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.error("Aktivace databáze selhala.")
        else:
            print("[CHYBA] Aktivace databáze selhala.")
        return []
    
    # Stahování všech produktů
    all_products = []
    
    for group in HLAVNI_SKUPINY:
        if logger:
            logger.info(f"Zpracovávám hlavní skupinu: {group}")
        else:
            print(f"\n{'='*15} Zpracovávám hlavní skupinu: {group.upper()} {'='*15}")
        
        # Před každou skupinou zkontrolujeme token a aktivujeme databázi
        current_token = get_valid_session_token(logger)
        if not current_token:
            if logger:
                logger.error("Token se stal neplatným během stahování")
            else:
                print("[CHYBA] Token se stal neplatným během stahování")
            return []
        
        change_db_payload = {
            "_parameters": [
                current_token, "ChangeDatabase", 
                {"Version": "1.0", "DatabaseName": "Helios001"}, []
            ]
        }
        execute_helios_command(change_db_payload, is_reset_call=True, logger=logger)
        
        subgroups_dict = fetch_subgroups(group, logger)
        
        if not subgroups_dict:
            continue
        
        for subgroup_code, subgroup_name in subgroups_dict.items():
            # Před každou podskupinou zkontrolujeme token
            current_token = get_valid_session_token(logger)
            if not current_token:
                if logger:
                    logger.error("Token se stal neplatným během stahování")
                else:
                    print("[CHYBA] Token se stal neplatným během stahování")
                return []
            
            change_db_payload = {
                "_parameters": [
                    current_token, "ChangeDatabase", 
                    {"Version": "1.0", "DatabaseName": "Helios001"}, []
                ]
            }
            execute_helios_command(change_db_payload, is_reset_call=True, logger=logger)
            
            products = fetch_products(group, subgroup_code, subgroup_name, logger)
            
            for product in products:
                product['HlavniSkupina'] = group
                product['PodskupinaKod'] = subgroup_code
                product['PodskupinaNazev'] = subgroup_name
                all_products.append(product)

    return all_products


# =============================================================================
# SHOPIFY FUNKCE
# =============================================================================

def get_shopify_session():
    """Vrátí konfiguraci Shopify session."""
    domain = os.getenv("SHOPIFY_STORE_DOMAIN")
    token = os.getenv("SHOPIFY_ADMIN_API_ACCESS_TOKEN")
    if not domain or not token:
        raise RuntimeError("Chybí SHOPIFY_STORE_DOMAIN nebo SHOPIFY_ADMIN_API_ACCESS_TOKEN v .env souboru")
    return {
        "domain": domain,
        "url": f"https://{domain}/admin/api/{SHOPIFY_API_VERSION}/graphql.json",
        "headers": {
            "X-Shopify-Access-Token": token,
            "Content-Type": "application/json",
        },
    }


def shopify_graphql(query, variables=None):
    """Provede GraphQL dotaz na Shopify API."""
    sess = get_shopify_session()
    resp = requests.post(
        sess["url"], 
        headers=sess["headers"], 
        json={"query": query, "variables": variables or {}}, 
        timeout=30
    )
    resp.raise_for_status()
    payload = resp.json()
    if "errors" in payload:
        raise RuntimeError(str(payload["errors"]))
    return payload["data"]


def load_shopify_products(limit=None, logger=None):
    """Načte produkty ze Shopify."""
    if logger:
        logger.info("=== FÁZE 3: NAČÍTÁNÍ ZE SHOPIFY ===")
    
    products = []
    count = 0
    cursor = None
    batch_size = min(20, limit) if limit else 20  # Zmenšená velikost pro nižší cost
    
    while True:
        # Zjednodušený dotaz pro nižší cost
        query = f"""
        query ProductsPage($cursor: String) {{
          products(first: {batch_size}, after: $cursor, sortKey: ID) {{
            edges {{
              cursor
              node {{
                id
                title
                handle
                metafield(namespace: "custom", key: "Reg_cis") {{ value }}
                variants(first: 10) {{
                  edges {{
                    node {{
                      id
                      sku
                      inventoryQuantity
                      inventoryItem {{
                        id
                        tracked
                        unitCost {{
                          amount
                        }}
                        inventoryLevels(first: 5) {{
                          edges {{
                            node {{
                              id
                              location {{
                                id
                              }}
                              quantities(names: ["available"]) {{
                                name
                                quantity
                              }}
                            }}
                          }}
                        }}
                      }}
                    }}
                  }}
                }}
              }}
            }}
            pageInfo {{ hasNextPage }}
          }}
        }}
        """
        
        try:
            data = shopify_graphql(query, {"cursor": cursor})
            edges = data["products"]["edges"]
            
            for edge in edges:
                products.append(edge["node"])
                count += 1
                
                if limit and count >= limit:
                    if logger:
                        logger.info(f"Dosažen limit {limit} produktů")
                    break
                    
                if count % 20 == 0 and logger:
                    logger.info(f"Načteno {count} produktů ze Shopify...")
            
            if not data["products"]["pageInfo"]["hasNextPage"] or (limit and count >= limit):
                break
                
            cursor = edges[-1]["cursor"]
            
        except Exception as e:
            if logger:
                logger.warning(f"Chyba v batch dotazu: {e}")
            break
    
    if logger:
        logger.info(f"Úspěšně načteno {len(products)} produktů ze Shopify")
    return products


def setup_inventory_tracking(inventory_item_id, location_id, dry_run=False):
    """Nastaví sledování inventáře pro produkt na lokaci."""
    if dry_run:
        return True
    
    query = """
    mutation inventoryActivate($inventoryItemId: ID!, $locationId: ID!) {
        inventoryActivate(inventoryItemId: $inventoryItemId, locationId: $locationId) {
            inventoryLevel {
                id
                quantities(names: ["available"]) {
                    name
                    quantity
                }
            }
            userErrors {
                field
                message
            }
        }
    }
    """
    
    variables = {
        "inventoryItemId": inventory_item_id,
        "locationId": location_id
    }
    
    try:
        payload = shopify_graphql(query, variables)
        if "errors" in payload:
            raise RuntimeError(str(payload["errors"]))
        
        if "data" not in payload or not payload["data"]:
            raise RuntimeError("Prázdná odpověď z Shopify API")
        
        result = payload["data"]["inventoryActivate"]
        if result["userErrors"]:
            errs = [err["message"] for err in result["userErrors"]]
            raise RuntimeError(f"inventoryActivate chyba: {errs}")
        
        return True
    except Exception as e:
        raise RuntimeError(f"Selhalo nastavení sledování inventáře {inventory_item_id}: {e}")


def update_shopify_inventory(inventory_level_id, quantity, dry_run=False):
    """Aktualizuje inventář v Shopify."""
    if dry_run:
        return
    
    mutation = """
    mutation inventorySetQuantities($input: InventorySetQuantitiesInput!) {
      inventorySetQuantities(input: $input) {
        userErrors {
          field
          message
        }
        inventoryAdjustmentGroup {
          createdAt
          reason
          referenceDocumentUri
        }
      }
    }
    """
    
    # Extrahování inventory item ID a location ID z inventory level ID
    if "?" in inventory_level_id:
        query_part = inventory_level_id.split("?")[1]
        if "inventory_item_id=" in query_part:
            inventory_item_id = query_part.split("inventory_item_id=")[1]
        else:
            raise RuntimeError(f"Neplatný formát inventory level GID: {inventory_level_id}")
    else:
        raise RuntimeError(f"Neplatný formát inventory level GID: {inventory_level_id}")
    
    variables = {
        "input": {
            "reason": "correction",
            "referenceDocumentUri": "tulipa-sync://inventory-update",
            "name": "available",
            "ignoreCompareQuantity": True,
            "quantities": [
                {
                    "inventoryItemId": f"gid://shopify/InventoryItem/{inventory_item_id}",
                    "locationId": f"gid://shopify/Location/{SHOPIFY_LOCATION_ID}",
                    "quantity": quantity
                }
            ]
        }
    }
    
    try:
        data = shopify_graphql(mutation, variables)
        errs = data["inventorySetQuantities"].get("userErrors")
        if errs:
            raise RuntimeError(f"inventorySetQuantities chyba: {errs}")
    except Exception as e:
        raise RuntimeError(f"Selhalo aktualizování inventáře {inventory_level_id}: {e}")


def update_shopify_variant_cost(inventory_item_id, cost, dry_run=False):
    """Aktualizuje náklady na položku varianty v Shopify pomocí REST API."""
    if dry_run:
        return True
    
    # Extrahujeme ID z GID
    if inventory_item_id.startswith("gid://shopify/InventoryItem/"):
        item_id = inventory_item_id.split("/")[-1]
    else:
        item_id = inventory_item_id
    
    # Získáme konfiguraci ze shopify_graphql funkce
    domain = os.getenv("SHOPIFY_STORE_DOMAIN")
    token = os.getenv("SHOPIFY_ADMIN_API_ACCESS_TOKEN")
    
    if not domain or not token:
        raise RuntimeError("Chybí SHOPIFY_STORE_DOMAIN nebo SHOPIFY_ADMIN_API_ACCESS_TOKEN v .env souboru")
    
    url = f"https://{domain}/admin/api/2024-01/inventory_items/{item_id}.json"
    
    headers = {
        "X-Shopify-Access-Token": token,
        "Content-Type": "application/json"
    }
    
    data = {
        "inventory_item": {
            "id": int(item_id),
            "cost": str(cost)
        }
    }
    
    try:
        response = requests.put(url, headers=headers, json=data)
        response.raise_for_status()
        return True
    except Exception as e:
        raise RuntimeError(f"Selhalo aktualizování nákladů inventory item {inventory_item_id}: {e}")


# =============================================================================
# INVENTÁŘ FUNKCE
# =============================================================================

def calculate_shared_inventory(csv_products, shopify_products, logger=None):
    """
    Vypočítá sdílený inventář pro produkty s obyčejnými a průhlednými květináči.
    
    Logika:
    - Pokud je v Tulipě více než 20 kusů, neodečítat rezervu
    - Pokud je méně než 20 kusů, odečíst 5 kusů pro rezervu
    - Rozdělit dostupný inventář na půl mezi obyčejné a průhledné
    - U lichých čísel přidat tu jednu vždy k obyčejnému plastovému
    
    Podporuje také produkty s více variantami v Shopify se stejným SKU.
    
    Args:
        csv_products: List CSV produktů z Tulipa
        shopify_products: List Shopify produktů z GraphQL API
        logger: Logger instance pro výpis informací
        
    Returns:
        dict: Mapování base_regcis na sdílený inventář
    """
    if logger:
        logger.info("Vypočítávám sdílený inventář pro produkty s květináči...")
    
    # Skupiny produktů podle RegCis (bez přípony květináče)
    product_groups = {}
    
    for product in csv_products:
        regcis = product.get('RegCis', '').strip()
        if not regcis:
            continue
            
        # Rozpoznání typu květináče podle názvu nebo SKU
        nazev = product.get('Nazev1', '').lower()
        is_transparent = any(keyword in nazev for keyword in ['průhledný', 'průhledny', 'transparent', 'skleněný', 'skleneny'])
        
        # Debug výpis pro průhledné produkty
        if logger and is_transparent:
            logger.info(f"Nalezen průhledný produkt: '{nazev}' (RegCis: {regcis})")
        
        # Základní RegCis (bez přípony květináče)
        base_regcis = regcis
        if is_transparent:
            # Pokud je průhledný, zkusíme najít základní RegCis
            base_regcis = regcis.replace('_pr', '').replace('_transparent', '').replace('_sklo', '')
        
        if base_regcis not in product_groups:
            product_groups[base_regcis] = {
                'regular': None,
                'transparent': None,
                'csv_quantity': 0
            }
        
        # Uložení produktu podle typu
        if is_transparent:
            product_groups[base_regcis]['transparent'] = product
        else:
            product_groups[base_regcis]['regular'] = product
        
        # Sečteme množství z Tulipa
        product_groups[base_regcis]['csv_quantity'] += int(product.get('Mnozstvi', 0))
    
    # Zkontrolujeme Shopify produkty a označíme, které mají obě varianty
    shopify_variant_counts = {}
    for shopify_product in shopify_products:
        variants = shopify_product.get("variants", {}).get("edges", [])
        if len(variants) >= 2:
            # Produkt má více variant, zkontrolujeme SKU
            for variant in variants:
                sku = variant["node"].get('sku', '')
                if sku:
                    base_sku = sku.replace('_pr', '').replace('_transparent', '').replace('_sklo', '')
                    if base_sku not in shopify_variant_counts:
                        shopify_variant_counts[base_sku] = 0
                    shopify_variant_counts[base_sku] += 1
    
    # Výpočet sdíleného inventáře
    shared_inventory_results = {}
    
    for base_regcis, group in product_groups.items():
        csv_total = group['csv_quantity']
        
        # Určení dostupného inventáře podle prahové hodnoty
        if csv_total > RESERVE_THRESHOLD:
            # Pokud je více než 20 kusů, neodečítat rezervu
            available_inventory = csv_total
            reserve_applied = False
        else:
            # Pokud je méně než 20 kusů, odečíst 5 kusů pro rezervu
            available_inventory = max(0, csv_total - SAFETY_RESERVE)
            reserve_applied = True
        
        # Rozdělení mezi obyčejné a průhledné
        # Zkontrolujeme, zda má produkt více variant v Shopify
        has_multiple_variants = base_regcis in shopify_variant_counts and shopify_variant_counts[base_regcis] >= 2
        
        if has_multiple_variants or (group['regular'] and group['transparent']):
            # Rozdělíme dostupný inventář na půl
            half_inventory = available_inventory // 2
            regular_qty = half_inventory
            transparent_qty = half_inventory
            
            # U lichých čísel přidat tu jednu vždy k obyčejnému plastovému
            if available_inventory % 2 == 1:
                regular_qty += 1
        elif group['regular']:
            # Pouze obyčejný květináč
            regular_qty = available_inventory
            transparent_qty = 0
        elif group['transparent']:
            # Pouze průhledný květináč
            regular_qty = 0
            transparent_qty = available_inventory
        else:
            continue
        
        shared_inventory_results[base_regcis] = {
            'regular_qty': regular_qty,
            'transparent_qty': transparent_qty,
            'total_available': available_inventory,
            'csv_total': csv_total,
            'reserve_applied': reserve_applied,
            'regular_product': group['regular'],
            'transparent_product': group['transparent']
        }
        
        if logger:
            reserve_info = " (rezerva odečtena)" if reserve_applied else " (bez rezervy)"
            logger.info(f"RegCis {base_regcis}: CSV={csv_total}, Dostupný={available_inventory}{reserve_info}, "
                       f"Obyčejný={regular_qty}, Průhledný={transparent_qty}")
            
            # Debug výpis pro skupiny s více RegCis
            all_regcis = group.get('all_regcis', [])
            if len(all_regcis) > 1:
                logger.info(f"  └── Skupina obsahuje RegCis: {all_regcis}")
                if group['regular']:
                    logger.info(f"  └── Obyčejný: {group['regular'].get('RegCis', '')} - {group['regular'].get('Nazev1', '')}")
                if group['transparent']:
                    logger.info(f"  └── Průhledný: {group['transparent'].get('RegCis', '')} - {group['transparent'].get('Nazev1', '')}")
    
    return shared_inventory_results


def get_product_inventory_limit(regcis, product_type='regular'):
    """
    Vrátí maximální dostupný inventář pro konkrétní produkt.
    
    Args:
        regcis: RegCis produktu
        product_type: 'regular' nebo 'transparent'
    """
    # Prozatím vrátíme základní limit
    # Tato funkce bude rozšířena později pro dynamické výpočty
    return 7 if product_type == 'regular' else 7


# =============================================================================
# CACHE A SOUBOR FUNKCE
# =============================================================================

def get_cache_file_path(base_name="produkty_komplet"):
    """Vrátí cestu k cache souboru s datem."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{base_name}_{timestamp}.csv"
    return os.path.join("data", filename)


def is_cache_valid(file_path, max_age_hours=1):
    """Zkontroluje, zda je cache soubor stále platný."""
    if not os.path.exists(file_path):
        return False
    
    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
    current_time = datetime.now()
    age = current_time - file_time
    
    return age.total_seconds() < (max_age_hours * 3600)


def find_latest_cache_file(base_name="produkty_komplet"):
    """Najde nejnovější cache soubor."""
    data_dir = "data"
    if not os.path.exists(data_dir):
        return None
    
    # Najdeme všechny soubory odpovídající vzoru
    import glob
    pattern = os.path.join(data_dir, f"{base_name}_*.csv")
    files = glob.glob(pattern)
    
    if not files:
        return None
    
    # Vrátíme nejnovější soubor
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def cleanup_old_cache_files(base_name="produkty_komplet", keep_hours=24):
    """Smaže staré cache soubory."""
    data_dir = "data"
    if not os.path.exists(data_dir):
        return
    
    import glob
    pattern = os.path.join(data_dir, f"{base_name}_*.csv")
    files = glob.glob(pattern)
    
    current_time = datetime.now()
    
    for file_path in files:
        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
        age = current_time - file_time
        
        if age.total_seconds() > (keep_hours * 3600):
            try:
                os.remove(file_path)
                print(f"[INFO] Smazán starý cache soubor: {file_path}")
            except Exception as e:
                print(f"[VAROVÁNÍ] Nepodařilo se smazat {file_path}: {e}")


# =============================================================================
# HLAVNÍ FUNKCE
# =============================================================================

def save_to_csv(products, output_file=None, logger=None):
    """Uloží produkty do CSV souboru s cache logikou."""
    if logger:
        logger.info("=== FÁZE 2: UKLÁDÁNÍ DO CSV ===")
    
    if not products:
        if logger:
            logger.warning("Žádné produkty k uložení")
        else:
            print("[INFO] Nebyla stažena žádná data k uložení.")
        return False, None
    
    # Vytvoříme data složku pokud neexistuje
    os.makedirs("data", exist_ok=True)
    
    # Pokud není zadán output_file, použijeme cache s datem
    if output_file is None:
        output_file = get_cache_file_path()
    
    try:
        fieldnames = sorted(list(set(key for product in products for key in product.keys())))
        
        # Přesuneme klíčové sloupce na začátek
        key_columns = ['HlavniSkupina', 'PodskupinaKod', 'PodskupinaNazev', 
                      'Nazev1', 'RegCis', '_cena_cu1', 'Mnozstvi']
        for col in reversed(key_columns):
            if col in fieldnames:
                fieldnames.insert(0, fieldnames.pop(fieldnames.index(col)))
        
        with open(output_file, 'w', newline='', encoding='utf-8-sig') as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, delimiter=';', extrasaction='ignore')
            writer.writeheader()
            writer.writerows(products)
        
        if logger:
            logger.info(f"Úspěšně uloženo {len(products)} produktů do {output_file}")
        else:
            print(f"\n{'='*50}")
            print("         ÚSPĚŠNĚ DOKONČENO")
            print("="*50)
            print(f"Data byla uložena do souboru: {output_file}")
            print(f"Celkem bylo uloženo {len(products)} produktů.")
            print("="*50)
        
        # Vyčistíme staré cache soubory
        cleanup_old_cache_files()
        
        return True, output_file
        
    except Exception as e:
        if logger:
            logger.error(f"Selhalo uložení CSV: {e}")
        else:
            print(f"[CHYBA] Selhalo uložení CSV: {e}")
        return False, None


def load_csv_from_cache(csv_file=None, logger=None):
    """Načte CSV soubor z cache nebo vytvoří nový."""
    # Pokud není zadán soubor, najdeme nejnovější cache
    if csv_file is None:
        csv_file = find_latest_cache_file()
    
    # Pokud máme cache soubor a je platný, použijeme ho
    if csv_file and os.path.exists(csv_file) and is_cache_valid(csv_file, max_age_hours=1):
        if logger:
            logger.info(f"Načítám data z cache: {csv_file}")
        else:
            print(f"[INFO] Načítám data z cache: {csv_file}")
        
        try:
            csv_products = []
            with open(csv_file, 'r', newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f, delimiter=';')
                for row in reader:
                    csv_products.append(dict(row))
            
            if logger:
                logger.info(f"Načteno {len(csv_products)} produktů z cache")
            else:
                print(f"[INFO] Načteno {len(csv_products)} produktů z cache")
            
            return csv_products, csv_file
            
        except Exception as e:
            if logger:
                logger.warning(f"Selhalo načtení cache: {e}")
            else:
                print(f"[VAROVÁNÍ] Selhalo načtení cache: {e}")
    
    # Cache není platný nebo neexistuje, musíme stáhnout nová data
    if logger:
        logger.info("Cache není platný nebo neexistuje, stahuji nová data...")
    else:
        print("[INFO] Cache není platný nebo neexistuje, stahuji nová data...")
    
    return None, None


def analyze_differences(csv_products, shopify_products, logger=None):
    """
    Analyzuje rozdíly mezi CSV produkty a Shopify produkty s podporou:
    - Sdíleného inventáře pro produkty s obyčejnými a průhlednými květináči
    - Více SKU variant pro jeden produkt (např. "895999, 000410")
    - Produktů s více variantami se stejným SKU (např. Monstera s obyčejným a průhledným květináčem)
    - Skupinování produktů podle názvu a hlavní skupiny
    
    Args:
        csv_products: List CSV produktů z Tulipa
        shopify_products: List Shopify produktů z GraphQL API
        logger: Logger instance pro výpis informací
        
    Returns:
        tuple: (analysis_dict, csv_mapping_dict)
    """
    if logger:
        logger.info("=== FÁZE 4: ANALÝZA ROZDÍLŮ ===")
    
    # Vypočítáme sdílený inventář
    shared_inventory = calculate_shared_inventory(csv_products, shopify_products, logger)
    
    # Vytvoření CSV mapování podle RegCis
    # Pokud má jeden produkt více RegCis, použijeme první jako hlavní a ostatní jako aliasy
    csv_mapping = {}
    product_groups = {}  # Skupiny produktů podle názvu a dalších charakteristik
    
    for product in csv_products:
        regcis = product.get('RegCis', '').strip()
        nazev = product.get('Nazev1', '').strip()
        
        if not regcis:
            continue
            
        # Vytvoříme klíč pro skupinu produktů (název + hlavní skupina)
        group_key = f"{nazev}_{product.get('HlavniSkupina', '')}"
        
        if group_key not in product_groups:
            product_groups[group_key] = {
                'main_product': product,
                'total_quantity': 0,
                'all_regcis': []
            }
        
        # Přidáme RegCis do skupiny
        product_groups[group_key]['all_regcis'].append(regcis)
        product_groups[group_key]['total_quantity'] += int(product.get('Mnozstvi', 0))
        
        # Mapujeme každý RegCis na hlavní produkt ze skupiny
        csv_mapping[regcis] = product_groups[group_key]['main_product']
    
    analysis = {
        'total_shopify': len(shopify_products),
        'total_csv': len(csv_products),
        'matched': 0,
        'unmatched_shopify': 0,
        'unmatched_csv': 0,
        'inventory_differences': 0,
        'shared_inventory_groups': len(shared_inventory),
        'details': []
    }
    
    for shopify_product in shopify_products:
        try:
            # Zpracujeme všechny varianty, ne jen první
            variants = shopify_product.get("variants", {}).get("edges", [])
            
            # Debug výpis pro zobrazení variant
            if logger and len(variants) > 1:
                logger.info(f"Produkt '{shopify_product.get('title', '')}' má {len(variants)} variant:")
                for i, variant in enumerate(variants):
                    sku_field = variant["node"].get('sku', '')
                    inventory_item = variant["node"].get("inventoryItem")
                    inventory_qty = 0
                    if inventory_item and inventory_item.get("tracked"):
                        inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])
                        for level_edge in inventory_levels:
                            level = level_edge["node"]
                            # Zkontrolujeme, zda je to správná lokace
                            location_id = level.get("location", {}).get("id", "")
                            if location_id == f"gid://shopify/Location/{SHOPIFY_LOCATION_ID}":
                                quantities = level.get("quantities", [])
                                for qty in quantities:
                                    if qty.get("name") == "available":
                                        inventory_qty += qty.get("quantity", 0)
                                        break
                    logger.info(f"  Varianta {i+1}: SKU='{sku_field}', Inventář={inventory_qty} (lokace {SHOPIFY_LOCATION_ID})")
            
            for variant in variants:
                variant_node = variant["node"]
                sku_field = variant_node.get('sku', '')
                
                if not sku_field:
                    continue
                
                # Zpracujeme každou variantu zvlášť
                # Pokud má SKU čárku, rozdělíme ho (např. "895999, 000410")
                if ',' in sku_field:
                    sku_list = [sku.strip() for sku in sku_field.split(',')]
                else:
                    sku_list = [sku_field]
                
                # Zpracujeme každé SKU zvlášť
                for sku in sku_list:
                    if sku not in csv_mapping:
                        continue
                    
                    analysis['matched'] += 1
                    csv_product = csv_mapping[sku]
                    
                    # Pro produkty s více variantami potřebujeme speciální logiku
                    if len(variants) > 1:
                        # Rozdělíme inventář mezi varianty
                        # První varianta = obyčejný květináč, druhá = průhledný
                        variant_index = variants.index(variant)
                        is_transparent = variant_index == 1  # Druhá varianta je průhledná
                    else:
                        # Pro produkty s jednou variantou použijeme původní logiku
                        nazev = csv_product.get('Nazev1', '').lower()
                        is_transparent = any(keyword in nazev for keyword in ['průhledný', 'průhledny', 'transparent', 'skleněný', 'skleneny'])
                    
                    # Najdeme základní RegCis pro sdílený inventář
                    # Použijeme RegCis z CSV, ne SKU ze Shopify
                    csv_regcis = csv_product.get('RegCis', '').strip()
                    base_regcis = csv_regcis
                    if is_transparent:
                        base_regcis = csv_regcis.replace('_pr', '').replace('_transparent', '').replace('_sklo', '')
                    
                    # Kontrola rozdílů v inventáři - čteme z inventory levels pro tuto konkrétní variantu
                    shopify_inventory = 0
                    inventory_item = variant_node.get("inventoryItem")
                    if inventory_item and inventory_item.get("tracked"):
                        inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])
                        for level_edge in inventory_levels:
                            level = level_edge["node"]
                            # Zkontrolujeme, zda je to správná lokace
                            location_id = level.get("location", {}).get("id", "")
                            if location_id == f"gid://shopify/Location/{SHOPIFY_LOCATION_ID}":
                                # Nový formát Shopify API - quantities array
                                quantities = level.get("quantities", [])
                                for qty in quantities:
                                    if qty.get("name") == "available":
                                        shopify_inventory += qty.get("quantity", 0)
                                        break
                                # Fallback pro starý formát
                                if not quantities:
                                    shopify_inventory += level.get("available", 0)
                    
                    # Získáme cílové množství ze sdíleného inventáře
                    if base_regcis in shared_inventory:
                        shared_data = shared_inventory[base_regcis]
                        # Pro produkty s více variantami v Shopify použijeme speciální logiku
                        if len(variants) > 1:
                            # Pro produkty s více variantami použijeme množství ze sdíleného inventáře
                            if is_transparent:
                                # Pro průhledné květináče použijeme množství ze sdíleného inventáře
                                desired_qty = shared_data['transparent_qty']
                            else:
                                # Pro obyčejné květináče použijeme množství ze sdíleného inventáře
                                desired_qty = shared_data['regular_qty']
                        else:
                            # Pro produkty s jednou variantou použijeme sdílený inventář
                            if is_transparent:
                                desired_qty = shared_data['transparent_qty']
                            else:
                                desired_qty = shared_data['regular_qty']
                    else:
                        # Pro produkty bez sdíleného inventáře rozdělíme inventář podle poměru
                        if len(variants) > 1:
                            total_csv_qty = int(csv_product.get('Mnozstvi', 0))
                            if is_transparent:
                                # Pro průhledné květináče použijeme druhou polovinu

                                desired_qty = total_csv_qty // 2
                            else:
                                # Pro obyčejné květináče použijeme první polovinu + zbytek
                                desired_qty = (total_csv_qty // 2) + (total_csv_qty % 2)
                        else:
                            # Pro produkty s jednou variantou použijeme celé množství
                            desired_qty = int(csv_product.get('Mnozstvi', 0))
                    
                        # Pro produkty bez sdíleného inventáře použijeme sečtené množství ze skupiny
                        group_key = f"{csv_product.get('Nazev1', '')}_{csv_product.get('HlavniSkupina', '')}"
                        if group_key in product_groups:
                            total_group_quantity = product_groups[group_key]['total_quantity']
                            desired_qty = max(0, total_group_quantity - 5)
                        else:
                            # Fallback na původní logiku
                            csv_inventory = int(csv_product.get('Mnozstvi', 0))
                            desired_qty = max(0, csv_inventory - 5)
                    
                    # Debug logging pro detekci rozdílů
                    product_title = shopify_product.get('title', 'Neznámý produkt')
                    logger.info(f" Porovnání pro {product_title} (SKU={sku}):")
                    logger.info(f"   Shopify={shopify_inventory}, Očekáváno={desired_qty}, Rozdíl={desired_qty - shopify_inventory}, is_transparent={is_transparent}")
                    
                    if shopify_inventory != desired_qty:
                        analysis['inventory_differences'] += 1
                        # Získáme informace o skupině produktů
                        group_key = f"{csv_product.get('Nazev1', '')}_{csv_product.get('HlavniSkupina', '')}"
                        group_info = product_groups.get(group_key, {})
                        all_regcis_in_group = group_info.get('all_regcis', [csv_product.get('RegCis', '')])
                        total_group_quantity = group_info.get('total_quantity', int(csv_product.get('Mnozstvi', 0)))
                        
                        analysis['details'].append({
                            'sku': sku,
                            'regcis': csv_product.get('RegCis', ''),
                            'title': shopify_product.get('title', ''),
                            'shopify': shopify_inventory,
                            'csv': int(csv_product.get('Mnozstvi', 0)),
                            'adjusted_csv': desired_qty,
                            'difference': desired_qty - shopify_inventory,
                            'is_transparent': is_transparent,
                            'base_regcis': base_regcis,
                            'shared_inventory': base_regcis in shared_inventory,
                            'variant_count': 1,  # Každé SKU je zpracováno samostatně
                            'desired_per_variant': desired_qty,
                            'variant_node': variant_node,  # Uložíme referenci na variantu pro pozdější použití
                            'csv_product': csv_product,  # Uložíme referenci na CSV produkt
                            'all_regcis_in_group': all_regcis_in_group,  # Všechny RegCis ve skupině
                            'total_group_quantity': total_group_quantity  # Celkové množství ve skupině
                        })
                    
            else:
                analysis['unmatched_shopify'] += 1
                
        except Exception as e:
            if logger:
                logger.error(f"Chyba při zpracování Shopify produktu: {e}")
            continue
    
    # Najdeme nespárované CSV produkty
    matched_skus = set()
    for shopify_product in shopify_products:
        try:
            variants = shopify_product.get("variants", {}).get("edges", [])
            for variant in variants:
                variant_node = variant["node"]
                sku_field = variant_node.get('sku', '')
                if sku_field:
                    # Rozdělíme SKU pole podle čárky pouze pokud obsahuje čárku
                    if ',' in sku_field:
                        sku_list = [sku.strip() for sku in sku_field.split(',')]
                    else:
                        sku_list = [sku_field]
                    for sku in sku_list:
                        matched_skus.add(sku)
        except Exception:
            continue
    
    for csv_product in csv_products:
        regcis = csv_product.get('RegCis', '').strip()
        nazev = csv_product.get('Nazev1', '').strip()
        group_key = f"{nazev}_{csv_product.get('HlavniSkupina', '')}"
        
        # Produkt je nespárovaný pouze pokud žádný z jeho RegCis není v matched_skus
        if regcis and regcis not in matched_skus:
            # Zkontrolujeme, zda má tento produkt více RegCis ve skupině
            if group_key in product_groups:
                all_regcis_in_group = product_groups[group_key]['all_regcis']
                # Pokud žádný RegCis ze skupiny není spárovaný, označíme jako nespárovaný
                if not any(r in matched_skus for r in all_regcis_in_group):
                    analysis['unmatched_csv'] += 1
            else:
                analysis['unmatched_csv'] += 1
    
    if logger:
        logger.info(f"Analýza dokončena:")
        logger.info(f"Celkem Shopify produktů: {analysis['total_shopify']}")
        logger.info(f"Celkem CSV produktů: {analysis['total_csv']}")
        logger.info(f"Spárované produkty: {analysis['matched']}")
        logger.info(f"Nespárované Shopify: {analysis['unmatched_shopify']}")
        logger.info(f"Nespárované CSV: {analysis['unmatched_csv']}")
        logger.info(f"Rozdíly v inventáři: {analysis['inventory_differences']}")
        logger.info(f"Skupiny se sdíleným inventářem: {analysis['shared_inventory_groups']}")
    
    return analysis, csv_mapping


def display_inventory_changes(analysis, logger=None):
    """Zobrazí jednotlivé změny v inventáři rostlin s podporou sdíleného inventáře."""
    if not analysis.get('details'):
        if logger:
            logger.info("Žádné rozdíly v inventáři nebyly nalezeny.")
        return
    
    if logger:
        logger.info("=== ZMĚNĚNÉ ROSTLINY ===")
        logger.info(f"{'SKU':<12} {'Typ':<3} {'Název':<25} {'Shopify':<8} {'CSV':<8} {'Cílové':<8} {'Rozdíl':<8} {'Var':<4} {'NaVar':<6} {'Sdílený':<8}")
        logger.info("-" * 120)
        
        for item in analysis['details']:
            sku = item['sku']
            nazev = item['title'][:22] + "..." if len(item['title']) > 25 else item['title']
            shopify_qty = item['shopify']
            csv_qty = item['csv']
            adjusted_csv = item['adjusted_csv']
            difference = item['difference']
            is_transparent = item.get('is_transparent', False)
            shared_inventory = item.get('shared_inventory', False)
            variant_count = item.get('variant_count', 1)
            desired_per_variant = item.get('desired_per_variant', 0)
            
            typ = "PR" if is_transparent else "OB"
            sdileny = "ANO" if shared_inventory else "NE"
            
            logger.info(f"{sku:<12} {typ:<3} {nazev:<25} {shopify_qty:<8} {csv_qty:<8} {adjusted_csv:<8} {difference:<8} {variant_count:<4} {desired_per_variant:<6} {sdileny:<8}")
    else:
        print("=== ZMĚNĚNÉ ROSTLINY ===")
        print(f"{'SKU':<12} {'Typ':<3} {'Název':<25} {'Shopify':<8} {'CSV':<8} {'Cílové':<8} {'Rozdíl':<8} {'Var':<4} {'NaVar':<6} {'Sdílený':<8}")
        print("-" * 120)
        
        for item in analysis['details']:
            sku = item['sku']
            nazev = item['title'][:22] + "..." if len(item['title']) > 25 else item['title']
            shopify_qty = item['shopify']
            csv_qty = item['csv']
            adjusted_csv = item['adjusted_csv']
            difference = item['difference']
            is_transparent = item.get('is_transparent', False)
            shared_inventory = item.get('shared_inventory', False)
            variant_count = item.get('variant_count', 1)
            desired_per_variant = item.get('desired_per_variant', 0)
            
            typ = "PR" if is_transparent else "OB"
            sdileny = "ANO" if shared_inventory else "NE"
            
            print(f"{sku:<12} {typ:<3} {nazev:<25} {shopify_qty:<8} {csv_qty:<8} {adjusted_csv:<8} {difference:<8} {variant_count:<4} {desired_per_variant:<6} {sdileny:<8}")
    
    # Zobrazení souhrnu sdíleného inventáře
    if analysis.get('shared_inventory_groups', 0) > 0:
        if logger:
            logger.info(f"\n📊 SOUHRN SDÍLENÉHO INVENTÁŘE:")
            logger.info(f"Celkem skupin se sdíleným inventářem: {analysis['shared_inventory_groups']}")
            logger.info(f"Rezerva: {SAFETY_RESERVE} kusů (pouze pokud je méně než {RESERVE_THRESHOLD} kusů)")
            logger.info(f"Rozdělení: na půl mezi obyčejné a průhledné, lichá čísla k obyčejným")
        else:
            print(f"\n📊 SOUHRN SDÍLENÉHO INVENTÁŘE:")
            print(f"Celkem skupin se sdíleným inventářem: {analysis['shared_inventory_groups']}")
            print(f"Rezerva: {SAFETY_RESERVE} kusů (pouze pokud je méně než {RESERVE_THRESHOLD} kusů)")
            print(f"Rozdělení: na půl mezi obyčejné a průhledné, lichá čísla k obyčejným")


def modify_products(analysis, shopify_products, csv_products, csv_mapping, dry_run, logger=None):
    """
    Upraví produkty na základě analýzy s podporou:
    - Aktualizace inventáře pro produkty s více variantami
    - Správné přiřazení variant podle typu (obyčejný/průhledný)
    - Aktualizace nákladů pro všechny produkty
    - Zpracování SKU polí s čárkami
    
    Args:
        analysis: Výsledek analýzy z analyze_differences
        shopify_products: List Shopify produktů z GraphQL API
        csv_products: List CSV produktů z Tulipa
        csv_mapping: Mapování RegCis na CSV produkty
        dry_run: Pokud True, pouze simuluje změny
        logger: Logger instance pro výpis informací
        
    Returns:
        dict: Výsledky úprav s počty aktualizovaných položek
    """
    if logger:
        logger.info("=== FÁZE 5: ÚPRAVA PRODUKTŮ ===")
    
    if dry_run:
        if logger:
            logger.info("DRY RUN REŽIM - Žádné změny nebudou provedeny")
        else:
            print("DRY RUN REŽIM - Žádné změny nebudou provedeny")
    
    results = {
        'total_processed': 0,
        'inventory_updated': 0,
        'metafields_updated': 0,
        'errors': 0
    }
    
    for detail in analysis['details']:
        # Všechny detaily v analysis['details'] jsou rozdíly v inventáři
        if 'difference' in detail:
            results['total_processed'] += 1
            
            # Najdeme odpovídající Shopify produkt a variantu
            shopify_product = None
            target_variant_node = None
            for sp in shopify_products:
                variants = sp.get("variants", {}).get("edges", [])
                for variant in variants:
                    variant_node = variant["node"]
                    sku_field = variant_node.get('sku', '')
                    if sku_field:
                        # Rozdělíme SKU pole podle čárky pouze pokud obsahuje čárku
                        if ',' in sku_field:
                            sku_list = [sku.strip() for sku in sku_field.split(',')]
                        else:
                            sku_list = [sku_field]
                        
                        if detail['sku'] in sku_list:
                            # Pro produkty s více variantami musíme najít správnou variantu
                            if len(variants) > 1:
                                variant_index = variants.index(variant)
                                is_transparent = variant_index == 1  # Druhá varianta je průhledná
                                detail_is_transparent = detail.get('is_transparent', False)
                                
                                # Porovnáme typ varianty s typem v detailu
                                if is_transparent == detail_is_transparent:
                                    shopify_product = sp
                                    target_variant_node = variant_node
                                    break
                            else:
                                # Pro produkty s jednou variantou použijeme první shodu
                                shopify_product = sp
                                target_variant_node = variant_node
                                break
                if shopify_product:
                    break
            
            if not shopify_product:
                if logger:
                    logger.warning(f"Shopify produkt nenalezen pro SKU: {detail['sku']}")
                results['errors'] += 1
                continue
            
            try:
                # Pro sdílený inventář použijeme správné množství
                if detail.get('shared_inventory', False):
                    desired_qty = detail['adjusted_csv']  # Už obsahuje správné množství ze sdíleného inventáře
                else:
                    desired_qty = detail.get('adjusted_csv', detail['csv'])
                
                # Aktualizace inventáře pro konkrétní variantu
                updated_variants = 0
                
                if target_variant_node:
                    inventory_item = target_variant_node.get("inventoryItem")
                    if inventory_item and inventory_item.get("tracked"):
                        inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])
                        if inventory_levels:
                            for level_edge in inventory_levels:
                                level = level_edge["node"]
                                # Zkontrolujeme, zda je to správná lokace
                                location_id = level.get("location", {}).get("id", "")
                                if location_id == f"gid://shopify/Location/{SHOPIFY_LOCATION_ID}":
                                    # Nový formát Shopify API - quantities array
                                    quantities = level.get("quantities", [])
                                    current_qty = 0
                                    for qty in quantities:
                                        if qty.get("name") == "available":
                                            current_qty = qty.get("quantity", 0)
                                            break
                                    # Fallback pro starý formát
                                    if not quantities:
                                        current_qty = level.get("available", 0)
                                    
                                    if current_qty == desired_qty:
                                        continue
                                    
                                    if dry_run:
                                        if logger:
                                            logger.info(f"[dry-run] Nastavil by {level['id']} na {desired_qty} (aktuální: {current_qty})")
                                        else:
                                            print(f"[dry-run] Nastavil by {level['id']} na {desired_qty} (aktuální: {current_qty})")
                                    else:
                                        try:
                                            update_shopify_inventory(level["id"], desired_qty)
                                            if logger:
                                                logger.info(f"Aktualizován inventář {level['id']} na {desired_qty}")
                                            else:
                                                print(f"Aktualizován inventář {level['id']} na {desired_qty}")
                                            updated_variants += 1
                                        except Exception as e:
                                            # Zkontrolujeme, zda je problém s nesledovaným inventářem
                                            if "not stocked at the location" in str(e):
                                                if logger:
                                                    logger.warning(f"Produkt není nastaven pro sledování inventáře na lokaci. Musíte ručně nastavit 'Track quantity' v Shopify Admin pro tento produkt.")
                                                else:
                                                    print(f"[VAROVÁNÍ] Produkt není nastaven pro sledování inventáře na lokaci. Musíte ručně nastavit 'Track quantity' v Shopify Admin pro tento produkt.")
                                                results['errors'] += 1
                                            else:
                                                if logger:
                                                    logger.error(f"Selhalo aktualizování inventáře {level['id']}: {e}")
                                                else:
                                                    print(f"Selhalo aktualizování inventáře {level['id']}: {e}")
                                                results['errors'] += 1
                
                if updated_variants > 0:
                    results['inventory_updated'] += 1
                    if logger:
                        logger.info(f"Aktualizován inventář pro {detail['regcis']}: {detail['shopify']} -> {desired_qty} (originální: {detail['csv']})")
                    else:
                        print(f"Aktualizován inventář pro {detail['regcis']}: {detail['shopify']} -> {desired_qty} (originální: {detail['csv']})")
                
                # Aktualizace nákladů na položku u variant (cena_cu1 * 1.12)
                # Aktualizujeme vždy, i když se inventář nemění
                try:
                    # Najdeme odpovídající CSV produkt pro získání ceny podle konkrétního RegCis
                    csv_product = None
                    for csv_prod in csv_products:
                        if csv_prod.get('RegCis', '').strip() == detail['regcis']:
                            csv_product = csv_prod
                            break
                    
                    # Pokud nenajdeme podle RegCis, použijeme produkt z analýzy
                    if not csv_product:
                        csv_product = detail.get('csv_product')
                    
                    if csv_product:
                        csv_price = float(csv_product.get('_cena_cu1', 0))
                        cost_with_vat = round(csv_price * 1.12, 2)
                    else:
                        cost_with_vat = 0
                    
                    # Aktualizace nákladů pro konkrétní variantu
                    if target_variant_node:
                        inventory_item = target_variant_node.get("inventoryItem")
                        if inventory_item:
                            inventory_item_id = inventory_item.get("id")
                            
                            # Získáme aktuální náklady
                            current_cost = 0
                            unit_cost = inventory_item.get("unitCost")
                            if unit_cost and unit_cost.get("amount"):
                                current_cost = round(float(unit_cost.get("amount")), 2)
                            
                            # Aktualizujeme pouze pokud se náklady liší
                            if current_cost != cost_with_vat:
                                if dry_run:
                                    if logger:
                                        logger.info(f"[dry-run] Změnil by náklady na položku z {current_cost} na {cost_with_vat} pro SKU {detail['sku']}")
                                    else:
                                        print(f"[dry-run] Změnil by náklady na položku z {current_cost} na {cost_with_vat} pro SKU {detail['sku']}")
                                else:
                                    update_shopify_variant_cost(
                                        inventory_item_id,
                                        cost_with_vat, 
                                        dry_run=False
                                    )
                                    results['metafields_updated'] += 1
                                    if logger:
                                        logger.info(f"Aktualizovány náklady na položku z {current_cost} na {cost_with_vat} pro SKU {detail['sku']}")
                                    else:
                                        print(f"Aktualizovány náklady na položku z {current_cost} na {cost_with_vat} pro SKU {detail['sku']}")
                            
                except Exception as e:
                    if logger:
                        logger.warning(f"Selhalo aktualizování nákladů pro {detail['regcis']}: {e}")
                    else:
                        print(f"[VAROVÁNÍ] Selhalo aktualizování nákladů pro {detail['regcis']}: {e}")
                
                # Pokud updated_variants == 0, znamená to, že inventář už byl na správné úrovni
                # To není chyba, takže nepřidáváme results['errors'] += 1
                    
            except Exception as e:
                if logger:
                    logger.error(f"Chyba při aktualizaci produktu {detail['regcis']}: {e}")
                else:
                    print(f"Chyba při aktualizaci produktu {detail['regcis']}: {e}")
                results['errors'] += 1
    
    # Aktualizace nákladů i pro produkty bez změny inventáře
    processed_skus = {detail['sku'] for detail in analysis['details']}
    
    for shopify_product in shopify_products:
        variants = shopify_product.get("variants", {}).get("edges", [])
        if not variants:
            continue
            
        for variant_edge in variants:
            variant = variant_edge["node"]
            sku_field = variant.get('sku', '')
            
            if not sku_field:
                continue
            
            # Rozdělíme SKU pole podle čárky pouze pokud obsahuje čárku
            if ',' in sku_field:
                sku_list = [sku.strip() for sku in sku_field.split(',')]
            else:
                sku_list = [sku_field]
            
            for sku in sku_list:
                # Přeskočíme produkty, které už byly zpracovány
                if sku in processed_skus:
                    continue
                
                # Najdeme odpovídající CSV produkt podle konkrétního SKU
                csv_product = None
                for csv_prod in csv_products:
                    if csv_prod.get('RegCis', '').strip() == sku:
                        csv_product = csv_prod
                        break
                
                # Pokud nenajdeme podle RegCis, použijeme mapování
                if not csv_product:
                    csv_product = csv_mapping.get(sku)
                
                if not csv_product:
                    continue
                
                try:
                    csv_price = float(csv_product.get('_cena_cu1', 0))
                    cost_with_vat = round(csv_price * 1.12, 2)
                    
                    # Získáme inventory_item z varianty
                    inventory_item = variant.get("inventoryItem")
                    if inventory_item:
                        inventory_item_id = inventory_item.get("id")
                        
                        # Získáme aktuální náklady
                        current_cost = 0
                        unit_cost = inventory_item.get("unitCost")
                        if unit_cost and unit_cost.get("amount"):
                            current_cost = round(float(unit_cost.get("amount")), 2)
                        
                        # Aktualizujeme pouze pokud se náklady liší
                        if current_cost != cost_with_vat:
                            if dry_run:
                                if logger:
                                    logger.info(f"[dry-run] Změnil by náklady na položku z {current_cost} na {cost_with_vat} pro SKU {sku} (bez změny inventáře)")
                            else:
                                update_shopify_variant_cost(
                                    inventory_item_id,
                                    cost_with_vat, 
                                    dry_run=False
                                )
                                results['metafields_updated'] += 1
                                if logger:
                                    logger.info(f"Aktualizovány náklady na položku z {current_cost} na {cost_with_vat} pro SKU {sku} (bez změny inventáře)")
                            
                except Exception as e:
                    if logger:
                        logger.warning(f"Selhalo aktualizování nákladů pro {sku}: {e}")
    
    if logger:
        logger.info(f"Úprava dokončena:")
        logger.info(f"Celkem zpracováno: {results['total_processed']}")
        logger.info(f"Inventář aktualizován: {results['inventory_updated']}")
        logger.info(f"Náklady na položku aktualizovány: {results['metafields_updated']}")
        logger.info(f"Chyby: {results['errors']}")
    
    return results


def force_logout_all(logger=None):
    """Pokusí se odhlásit všechny možné session."""
    if logger:
        logger.info("Pokus o odhlášení všech session...")
    else:
        print("[INFO] Pokus o odhlášení všech session...")
    
    # Zkusíme odhlásit uloženou session
    if os.path.exists(SESSION_FILE):
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                session_data = json.load(f)
            token = session_data.get('token')
            if token:
                logout_payload = {"_parameters": [token, "Logout", {"Version": "1.0"}, []]}
                execute_helios_command(logout_payload, is_reset_call=True, logger=logger)
        except:
            pass
    
    # Smažeme lokální session soubor
    if os.path.exists(SESSION_FILE):
        os.remove(SESSION_FILE)
        if logger:
            logger.info("Lokální session soubor smazán.")
        else:
            print("[INFO] Lokální session soubor smazán.")


def setup_logging(level="INFO"):
    """Nastaví logging."""
    logger = logging.getLogger("tulipa_scraper")
    logger.setLevel(getattr(logging, level.upper()))
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger


def run_main_workflow(args, logger):
    """Spustí hlavní workflow bez loop logiky."""
    try:
        # Fáze 1: Stahování nebo načtení z cache
        csv_products = []
        csv_file = None
        
        if not args.shopify_only:
            # Zkusíme načíst z cache (pouze pokud není zadán konkrétní soubor)
            if args.output == "produkty_komplet.csv":  # Výchozí hodnota
                csv_products, csv_file = load_csv_from_cache(None, logger)
            else:
                csv_products, csv_file = load_csv_from_cache(args.output, logger)
            
            # Pokud cache není platný, stáhneme nová data
            if csv_products is None:
                logger.info("Začínám fázi stahování...")
                csv_products = scrape_tulipa_data(logger)
                
                if not csv_products:
                    logger.error("Žádné produkty staženy, ukončuji")
                    return 1
                
                # Fáze 2: Uložení do CSV s cache logikou
                success, csv_file = save_to_csv(csv_products, None, logger)  # Použijeme automatické pojmenování
                if not success:
                    logger.error("Selhalo uložení CSV, ukončuji")
                    return 1
        else:
            logger.info("Přeskakuji fázi stahování (--shopify-only)")
            # Načtení existujícího CSV nebo cache
            if args.output == "produkty_komplet.csv":  # Výchozí hodnota
                csv_products, csv_file = load_csv_from_cache(None, logger)
            else:
                csv_products, csv_file = load_csv_from_cache(args.output, logger)
            if csv_products is None:
                logger.error("Selhalo načtení CSV dat")
                return 1
        
        # Fáze 3: Načtení ze Shopify (pokud není pouze stahování)
        if not args.scrape_only:
            logger.info("Začínám fázi načítání ze Shopify...")
            shopify_products = load_shopify_products(args.limit, logger)
            
            if not shopify_products:
                logger.error("Žádné produkty načteny ze Shopify, ukončuji")
                return 1
            
            # Fáze 4: Analýza rozdílů
            analysis, csv_mapping = analyze_differences(csv_products, shopify_products, logger)
            
            # Zobrazení jednotlivých změněných rostlin
            display_inventory_changes(analysis, logger)
            
            # Pokud je požadováno pouze zobrazení změn, ukončit zde
            if args.show_changes:
                logger.info("Zobrazení změn dokončeno.")
                return 0
            
            # Fáze 5: Úprava produktů (vždy, pokud nejsou použity speciální režimy)
            if not args.show_changes:
                modification_results = modify_products(analysis, shopify_products, csv_products, csv_mapping, args.dry_run, logger)
                
                # Uložení zprávy o úpravách
                report_file = os.path.join("data", f"modification_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
                
                # Smazání starých zpráv
                try:
                    import glob
                    old_reports = glob.glob(os.path.join("data", "modification_report_*.json"))
                    for old_report in old_reports:
                        if old_report != report_file:
                            os.remove(old_report)
                            logger.debug(f"Smazána stará zpráva: {old_report}")
                except Exception as e:
                    logger.warning(f"Selhalo mazání starých zpráv: {e}")
                
                with open(report_file, 'w', encoding='utf-8') as f:
                    json.dump({
                        'timestamp': datetime.now().isoformat(),
                        'analysis': analysis,
                        'modification_results': modification_results,
                        'dry_run': args.dry_run
                    }, f, indent=2, ensure_ascii=False)
                logger.info(f"Zpráva o úpravách uložena do: {report_file}")
        else:
            logger.info("Přeskakuji fáze Shopify (--scrape-only)")
        
        logger.info("Workflow úspěšně dokončen!")
        return 0
        
    except KeyboardInterrupt:
        logger.info("Workflow přerušen uživatelem")
        return 1
    except Exception as e:
        logger.error(f"Workflow selhal s chybou: {e}")
        return 1


def main():
    """Hlavní funkce s podporou loop režimu."""
    # Vypnutí SSL varování pro Tulipa server
    requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
    
    parser = argparse.ArgumentParser(description="Tulipa Offers Scraper")
    parser.add_argument("--scrape-only", action="store_true", help="Pouze stáhni data a ulož do CSV")
    parser.add_argument("--shopify-only", action="store_true", help="Pouze synchronizuj se Shopify (přeskoč stahování)")
    parser.add_argument("--show-changes", action="store_true", help="Zobrazit pouze změny v inventáři bez úprav")
    parser.add_argument("--dry-run", action="store_true", help="Zobrazit co by se změnilo bez provedení změn")
    parser.add_argument("--limit", type=int, help="Omezit počet produktů k zpracování")
    parser.add_argument("--filter-group", help="Filtrovat podle hlavní skupiny (např. 'Dekor', 'Kveto')")
    parser.add_argument("--output", default="produkty_komplet.csv", help="Výstupní CSV soubor")
    parser.add_argument("--debug", action="store_true", help="Zapnout debug režim")
    parser.add_argument("--reset", action="store_true", help="Resetovat session a začít znovu")
    parser.add_argument("--discover", action="store_true", help="Objevit dostupné kategorie")
    parser.add_argument("--loop", action="store_true", help="Spouštět v nekonečné smyčce každých 30 minut")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"], help="Úroveň logování")
    parser.add_argument("--safety-reserve", type=int, default=5, help="Rezerva pro jistotu (výchozí: 5)")
    parser.add_argument("--reserve-threshold", type=int, default=20, help="Prahová hodnota pro odečítání rezervy (výchozí: 20)")
    
    args = parser.parse_args()
    
    # Nastavení globálních konstant podle argumentů
    global DEBUG_MODE, SAFETY_RESERVE, RESERVE_THRESHOLD
    if args.debug:
        DEBUG_MODE = True
    
    SAFETY_RESERVE = args.safety_reserve
    RESERVE_THRESHOLD = args.reserve_threshold
    
    # Nastavení logování
    logger = setup_logging(args.log_level)
    
    # Zpracování speciálních režimů
    if args.reset:
        print("🔄 RESET SESSION REŽIM")
        print("=" * 50)
        force_logout_all(logger)
        print("\nNyní spusťte skript bez argumentů pro normální běh.")
        return 0
    
    if args.discover:
        print("🔍 OBJEVOVÁNÍ KATEGORIÍ REŽIM")
        print("=" * 50)
        print("Tento režim objeví dostupné kategorie pro pozdější použití.")
        print("Spusťte skript bez argumentů pro normální běh.")
        return 0
    
    logger.info("Spouštím Tulipa Offers Scraper")
    
    # Vytvoříme data složku pokud neexistuje
    os.makedirs("data", exist_ok=True)
    
    # Loop režim - spouštět každých 30 minut
    if args.loop:
        logger.info("🔄 LOOP REŽIM - Spouštím každých 30 minut")
        logger.info("Pro ukončení stiskněte Ctrl+C")
        print("=" * 60)
        print("🔄 LOOP REŽIM AKTIVNÍ")
        print("Skript se bude spouštět každých 30 minut")
        print("Pro ukončení stiskněte Ctrl+C")
        print("=" * 60)
        
        iteration = 1
        while True:
            try:
                logger.info(f"🔄 ITERACE #{iteration} - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"\n{'='*20} ITERACE #{iteration} {'='*20}")
                print(f"Čas spuštění: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"{'='*50}")
                
                # Spustíme hlavní workflow
                result = run_main_workflow(args, logger)
                
                if result == 0:
                    logger.info(f"✅ Iterace #{iteration} dokončena úspěšně")
                else:
                    logger.warning(f"⚠️ Iterace #{iteration} dokončena s chybami (kód: {result})")
                
                iteration += 1
                
                # Počkáme 30 minut před další iterací
                logger.info("⏰ Čekám 30 minut před další iterací...")
                print(f"\n⏰ Čekám 30 minut před další iterací...")
                print("Pro ukončení stiskněte Ctrl+C")
                
                # Čekání 30 minut (1800 sekund)
                time.sleep(1800)
                
            except KeyboardInterrupt:
                logger.info("🛑 Loop režim ukončen uživatelem")
                print("\n🛑 Loop režim ukončen uživatelem")
                return 0
            except Exception as e:
                logger.error(f"❌ Chyba v iteraci #{iteration}: {e}")
                print(f"\n❌ Chyba v iteraci #{iteration}: {e}")
                logger.info("⏰ Čekám 5 minut před opakováním...")
                print("⏰ Čekám 5 minut před opakováním...")
                time.sleep(300)  # Čekání 5 minut při chybě
                continue
    
    # Normální běh (bez loop)
    return run_main_workflow(args, logger)


if __name__ == "__main__":
    sys.exit(main())
