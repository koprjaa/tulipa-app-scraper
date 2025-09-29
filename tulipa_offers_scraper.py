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
    """Odhadne timeout session."""
    return timedelta(minutes=30)


def execute_helios_command(payload, is_reset_call=False):
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
        print(f"\n[CHYBA] Problém s připojením k serveru: {e}")
        print("[INFO] Server může být nedostupný nebo restartuje.")
        return None
    except requests.exceptions.Timeout as e:
        print(f"\n[CHYBA] Timeout při připojení k serveru: {e}")
        print("[INFO] Server je pomalý nebo přetížený.")
        return None
    except Exception as e:
        print(f"\n[CHYBA] Neočekávaná chyba: {e}")
        if DEBUG_MODE:
            import traceback
            print(f"[DEBUG] Traceback: {traceback.format_exc()}")
        return None


def fetch_subgroups(session_token, group_name, logger=None):
    """Stáhne podskupiny pro hlavní skupinu."""
    if logger:
        logger.info(f"Stahuji podskupiny pro skupinu: '{group_name}'...")
    else:
        print(f"[*] Stahuji podskupiny pro skupinu: '{group_name}'...")
    
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {"Version": "1.0", "ActionID": ACTION_ID_SUBGROUPS, "Parameters": [group_name]},
            []
        ]
    }
    response = execute_helios_command(payload)
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


def fetch_products(session_token, group_name, subgroup_code, subgroup_name, logger=None):
    """Stáhne produkty pro danou kombinaci."""
    if logger:
        logger.info(f"  Stahuji produkty pro '{group_name} -> {subgroup_name} ({subgroup_code})'...")
    else:
        print(f"  [*] Stahuji produkty pro '{group_name} -> {subgroup_name} ({subgroup_code})'...")
    
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {"Version": "1.0", "ActionID": ACTION_ID_PRODUCTS, "Parameters": [group_name, subgroup_code]},
            []
        ]
    }
    response = execute_helios_command(payload)
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


def discover_categories(session_token):
    """Objeví všechny dostupné kategorie."""
    print("[INFO] Objevuji dostupné kategorie...")
    
    possible_categories = [
        "Dnešní nabídka", "Dnes", "Nabídka", "Aktuální", "Speciální",
        "Akce", "Sleva", "Promo", "Týdenní", "Měsíční",
        "Novinky", "Trendy", "Sezónní", "Výprodej", "Doporučené"
    ]
    
    discovered_categories = {}
    
    for category in possible_categories:
        try:
            print(f"  [*] Zkouším kategorii: '{category}'...")
            payload = {
                "_parameters": [
                    session_token, "RunExternalAction",
                    {"Version": "1.0", "ActionID": ACTION_ID_SUBGROUPS, "Parameters": [category]},
                    []
                ]
            }
            response = execute_helios_command(payload)
            
            if response and not response["result"][0]["fields"].get("IsError", True):
                try:
                    rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
                    if rows:
                        discovered_categories[category] = {
                            "exists": True,
                            "subgroups_count": len(rows),
                            "sample_subgroups": [row[2]['Value'] for row in rows[:3]]
                        }
                        print(f"    ✅ Nalezena! Má {len(rows)} podskupin")
                    else:
                        discovered_categories[category] = {"exists": True, "subgroups_count": 0}
                        print(f"    ✅ Nalezena! (bez podskupin)")
                except (KeyError, IndexError):
                    discovered_categories[category] = {"exists": True, "subgroups_count": "unknown"}
                    print(f"    ✅ Nalezena! (struktura odpovědi se liší)")
            else:
                discovered_categories[category] = {"exists": False}
                print(f"    ❌ Nenalezena")
                
        except Exception as e:
            discovered_categories[category] = {"exists": False, "error": str(e)}
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
    
    # Načtení nebo vytvoření session
    session_token = load_session_token()
    
    if not session_token:
        if logger:
            logger.info("Přihlašuji se na server...")
        else:
            print("[INFO] Přihlašuji se na server...")
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
        
        login_response = execute_helios_command(login_payload)
        if not login_response or login_response["result"][0]["fields"]["IsError"]:
            error_msg = login_response["result"][0]["fields"].get("ErrorMessage", "Neznámá chyba") if login_response else "Žádná odpověď"
            if logger:
                logger.error(f"Přihlášení selhalo: {error_msg}")
            else:
                print(f"[CHYBA] Přihlášení selhalo: {error_msg}")
            return []
        
        session_token = login_response["result"][0]["fields"]["Result"]
        if logger:
            logger.info("Přihlášení úspěšné!")
        else:
            print("[OK] Přihlášení úspěšné!")
        save_session_token(session_token, logger=logger)
    
    # Aktivace databáze
    change_db_payload = {
        "_parameters": [
            session_token, "ChangeDatabase", 
            {"Version": "1.0", "DatabaseName": "Helios001"}, []
        ]
    }
    change_db_response = execute_helios_command(change_db_payload, is_reset_call=True)
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
        
        execute_helios_command(change_db_payload, is_reset_call=True)
        subgroups_dict = fetch_subgroups(session_token, group, logger)
        
        if not subgroups_dict:
            continue
        
        for subgroup_code, subgroup_name in subgroups_dict.items():
            execute_helios_command(change_db_payload, is_reset_call=True)
            products = fetch_products(session_token, group, subgroup_code, subgroup_name, logger)
            
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
                        inventoryLevels(first: 5) {{
                          edges {{
                            node {{
                              id
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
    """Analyzuje rozdíly mezi CSV a Shopify produkty."""
    if logger:
        logger.info("=== FÁZE 4: ANALÝZA ROZDÍLŮ ===")
    
    # Vytvoření CSV mapování podle RegCis
    csv_mapping = {}
    for product in csv_products:
        regcis = product.get('RegCis', '').strip()
        if regcis:
            csv_mapping[regcis] = product
    
    analysis = {
        'total_shopify': len(shopify_products),
        'total_csv': len(csv_products),
        'matched': 0,
        'unmatched_shopify': 0,
        'unmatched_csv': 0,
        'inventory_differences': 0,
        'details': []
    }
    
    for shopify_product in shopify_products:
        try:
            # Získání SKU z prvního variantu
            variants = shopify_product.get("variants", {}).get("edges", [])
            sku = ""
            if variants:
                sku = variants[0]["node"].get('sku', '')
            
            if sku in csv_mapping:
                analysis['matched'] += 1
                csv_product = csv_mapping[sku]
                
                # Kontrola rozdílů v inventáři - čteme z inventory levels
                shopify_inventory = 0
                for variant in variants:
                    variant_node = variant["node"]
                    inventory_item = variant_node.get("inventoryItem")
                    if inventory_item and inventory_item.get("tracked"):
                        inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])
                        for level_edge in inventory_levels:
                            level = level_edge["node"]
                            # Nový formát Shopify API - quantities array
                            quantities = level.get("quantities", [])
                            for qty in quantities:
                                if qty.get("name") == "available":
                                    shopify_inventory += qty.get("quantity", 0)
                                    break
                            # Fallback pro starý formát
                            if not quantities:
                                shopify_inventory += level.get("available", 0)
                csv_inventory = int(csv_product.get('Mnozstvi', 0))
                
                # Odečíst 5 kusů od Tulipa množství pro jistotu
                adjusted_csv_inventory = max(0, csv_inventory - 5)
                
                if shopify_inventory != adjusted_csv_inventory:
                    analysis['inventory_differences'] += 1
                    analysis['details'].append({
                        'sku': sku,
                        'regcis': csv_product.get('RegCis', ''),
                        'title': shopify_product.get('title', ''),
                        'shopify': shopify_inventory,
                        'csv': csv_inventory,
                        'adjusted_csv': adjusted_csv_inventory,
                        'difference': adjusted_csv_inventory - shopify_inventory
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
            if variants:
                sku = variants[0]["node"].get('sku', '')
                if sku:
                    matched_skus.add(sku)
        except Exception:
            continue
    
    for csv_product in csv_products:
        regcis = csv_product.get('RegCis', '').strip()
        if regcis and regcis not in matched_skus:
            analysis['unmatched_csv'] += 1
    
    if logger:
        logger.info(f"Analýza dokončena:")
        logger.info(f"Celkem Shopify produktů: {analysis['total_shopify']}")
        logger.info(f"Celkem CSV produktů: {analysis['total_csv']}")
        logger.info(f"Spárované produkty: {analysis['matched']}")
        logger.info(f"Nespárované Shopify: {analysis['unmatched_shopify']}")
        logger.info(f"Nespárované CSV: {analysis['unmatched_csv']}")
        logger.info(f"Rozdíly v inventáři: {analysis['inventory_differences']}")
    
    return analysis


def display_inventory_changes(analysis, logger=None):
    """Zobrazí jednotlivé změny v inventáři rostlin."""
    if not analysis.get('details'):
        if logger:
            logger.info("Žádné rozdíly v inventáři nebyly nalezeny.")
        return
    
    if logger:
        logger.info("=== ZMĚNĚNÉ ROSTLINY ===")
        logger.info(f"{'SKU':<10} {'RegCis':<10} {'Název':<40} {'Shopify':<8} {'CSV':<8} {'Upravené':<10} {'Rozdíl':<8}")
        logger.info("-" * 100)
        
        for item in analysis['details']:
            sku = item['sku']
            regcis = item['regcis']
            title = item['title'][:37] + "..." if len(item['title']) > 40 else item['title']
            shopify_qty = item['shopify']
            csv_qty = item['csv']
            adjusted_csv = item['adjusted_csv']
            difference = item['difference']
            
            logger.info(f"{sku:<10} {regcis:<10} {title:<40} {shopify_qty:<8} {csv_qty:<8} {adjusted_csv:<10} {difference:<8}")
    else:
        print("=== ZMĚNĚNÉ ROSTLINY ===")
        print(f"{'SKU':<10} {'RegCis':<10} {'Název':<40} {'Shopify':<8} {'CSV':<8} {'Upravené':<10} {'Rozdíl':<8}")
        print("-" * 100)
        
        for item in analysis['details']:
            sku = item['sku']
            regcis = item['regcis']
            title = item['title'][:37] + "..." if len(item['title']) > 40 else item['title']
            shopify_qty = item['shopify']
            csv_qty = item['csv']
            adjusted_csv = item['adjusted_csv']
            difference = item['difference']
            
            print(f"{sku:<10} {regcis:<10} {title:<40} {shopify_qty:<8} {csv_qty:<8} {adjusted_csv:<10} {difference:<8}")


def modify_products(analysis, shopify_products, dry_run, logger=None):
    """Upraví produkty na základě analýzy."""
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
        'errors': 0
    }
    
    for detail in analysis['details']:
        # Všechny detaily v analysis['details'] jsou rozdíly v inventáři
        if 'difference' in detail:
            results['total_processed'] += 1
            
            # Najdeme odpovídající Shopify produkt
            shopify_product = None
            for sp in shopify_products:
                variants = sp.get("variants", {}).get("edges", [])
                if variants and variants[0]["node"].get('sku') == detail['sku']:
                    shopify_product = sp
                    break
            
            if not shopify_product:
                if logger:
                    logger.warning(f"Shopify produkt nenalezen pro SKU: {detail['sku']}")
                results['errors'] += 1
                continue
            
            try:
                desired_qty = detail.get('adjusted_csv', detail['csv'])
                
                # Aktualizace inventáře pro všechny varianty
                variants = shopify_product.get("variants", {}).get("edges", [])
                updated_variants = 0
                
                for variant_edge in variants:
                    variant = variant_edge["node"]
                    inventory_item = variant.get("inventoryItem")
                    if not inventory_item or not inventory_item.get("tracked"):
                        continue
                    
                    inventory_levels = inventory_item.get("inventoryLevels", {}).get("edges", [])
                    if not inventory_levels:
                        continue
                    
                    for level_edge in inventory_levels:
                        level = level_edge["node"]
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
                # Pokud updated_variants == 0, znamená to, že inventář už byl na správné úrovni
                # To není chyba, takže nepřidáváme results['errors'] += 1
                    
            except Exception as e:
                if logger:
                    logger.error(f"Chyba při aktualizaci produktu {detail['regcis']}: {e}")
                else:
                    print(f"Chyba při aktualizaci produktu {detail['regcis']}: {e}")
                results['errors'] += 1
    
    if logger:
        logger.info(f"Úprava dokončena:")
        logger.info(f"Celkem zpracováno: {results['total_processed']}")
        logger.info(f"Inventář aktualizován: {results['inventory_updated']}")
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
                execute_helios_command(logout_payload, is_reset_call=True)
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
            analysis = analyze_differences(csv_products, shopify_products, logger)
            
            # Zobrazení jednotlivých změněných rostlin
            display_inventory_changes(analysis, logger)
            
            # Pokud je požadováno pouze zobrazení změn, ukončit zde
            if args.show_changes:
                logger.info("Zobrazení změn dokončeno.")
                return 0
            
            # Fáze 5: Úprava produktů (vždy, pokud nejsou použity speciální režimy)
            if not args.show_changes:
                modification_results = modify_products(analysis, shopify_products, args.dry_run, logger)
                
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
    
    args = parser.parse_args()
    
    # Nastavení debug režimu
    global DEBUG_MODE
    if args.debug:
        DEBUG_MODE = True
    
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
