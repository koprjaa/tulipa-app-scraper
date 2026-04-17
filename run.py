#!/usr/bin/env python3
"""
Tulipa Offers Scraper — downloads product and inventory data from the Tulipa
B2B portal (Helios backend) and saves it to CSV.

Typical usage:
    python run.py                       # full scrape + save CSV
    python run.py --loop                # rerun every 30 minutes
    python run.py --output my.csv       # custom output path
    python run.py --filter-group Dekor  # only one main group
    python run.py --reset               # force new Helios session
    python run.py --discover            # list available categories
    python run.py --browse              # use faster GetBrowse endpoint
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
ACTION_ID_SUBGROUPS = "7C100193-68DF-4C59-8692-33E421EEBCD3"  # podskupiny s parametrem skupiny
ACTION_ID_PRODUCTS = "7DCCBAB9-35EA-4310-BC22-B7AC873F9398"  # produkty s parametrem podskupiny

# --- SPECIÁLNÍ AKCE ---
ACTION_ID_KONTAKTY = "E0E9F6FC-D077-49D9-BB05-4C8056F669E8"  # s parametry ["12095", "15303"] - kontakty
ACTION_ID_KATEGORIE_100 = "44465692-619A-41AD-A578-ADB755659D0B"  # s parametrem ["100"] - kategorie řezaných květin
ACTION_ID_KATEGORIE_300 = "44465692-619A-41AD-A578-ADB755659D0B"  # s parametrem ["300"] - kategorie s počty
ACTION_ID_BROWSE_METADATA = "BC3642D5-D287-4CFE-A3CB-566DA8A126E0"  # bez parametrů - browse metadata
ACTION_ID_NOVY = "C0D85B0E-01D0-4832-8D56-BCC1C71317CF"  # bez parametrů - nový ActionID

# --- NOVÉ ACTIONID PRO PRODUKTY Z KATEGORIÍ ---
ACTION_ID_PRODUCT_DETAILS = "9752DF9E-E95E-46F2-97F8-11F96ABEB71C"  # produkty s EAN kódem - QueryBrowse struktura
ACTION_ID_PRODUCT_IMAGES = "982F0820-87A5-4F22-A2E9-724E14C208E1"  # obrázky produktů s EAN kódem - table struktura

# --- BROWSE NAME ---
BROWSE_NAME_PRODUCTS = "82"  # Hlavní seznam produktů

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


def get_browse_list(session_token, logger=None):
    """Získá seznam všech dostupných browse definic."""
    if logger:
        logger.info("Získávám seznam dostupných browse definic...")
    else:
        print("[*] Získávám seznam dostupných browse definic...")
    
    payload = {
        "_parameters": [
            session_token, "GetBrowse",
            {"Version": "1.0"},
            []
        ]
    }
    
    response = execute_helios_command(payload, logger=logger)
    
    if not response or response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.warning("Nepodařilo se získat seznam browse definic.")
        else:
            print("  -> Nepodařilo se získat seznam browse definic.")
        return []
    
    try:
        rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
        browse_list = []
        
        for row in rows:
            try:
                # Zkusíme extrahovat název a ID browse definice
                browse_info = {}
                for field in row:
                    field_name = field.get('FieldName', '')
                    field_value = field.get('Value', '')
                    browse_info[field_name] = field_value
                
                browse_list.append(browse_info)
            except (IndexError, KeyError):
                continue
        
        if logger:
            logger.info(f"Nalezeno {len(browse_list)} browse definic.")
        else:
            print(f"  -> Nalezeno {len(browse_list)} browse definic.")
        
        return browse_list
        
    except (KeyError, IndexError):
        if logger:
            logger.warning("Nepodařilo se parsovat browse definice.")
        else:
            print("  -> Nepodařilo se parsovat browse definice.")
        return []


def get_browse_data(session_token, browse_name, logger=None):
    """Získá data z konkrétní browse definice."""
    if logger:
        logger.info(f"Získávám data z browse '{browse_name}'...")
    else:
        print(f"[*] Získávám data z browse '{browse_name}'...")
    
    payload = {
        "_parameters": [
            session_token, "GetBrowse",
            {"Version": "1.1", "BrowseName": browse_name},
            []
        ]
    }
    
    response = execute_helios_command(payload, logger=logger)
    
    if not response or response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.warning(f"Nepodařilo se získat data z browse '{browse_name}'.")
        else:
            print(f"  -> Nepodařilo se získat data z browse '{browse_name}'.")
        return []
    
    try:
        result_data = response["result"][0]["fields"]["Result"]
        
        # Zkusíme různé struktury odpovědi
        rows = None
        if isinstance(result_data, dict) and "table" in result_data:
            rows = result_data["table"]["rows"]
        elif isinstance(result_data, list):
            rows = result_data
        
        if not rows:
            if logger:
                logger.warning(f"Žádné řádky v odpovědi z browse '{browse_name}'.")
            else:
                print(f"  -> Žádné řádky v odpovědi.")
            return []
        
        products = []
        
        for row in rows:
            try:
                # Převedeme row na dictionary
                product_dict = {}
                for field in row:
                    field_name = field.get('FieldName', '')
                    field_value = field.get('Value', '')
                    product_dict[field_name] = field_value
                
                products.append(product_dict)
            except (IndexError, KeyError, TypeError):
                continue
        
        if logger:
            logger.info(f"Získáno {len(products)} produktů z browse '{browse_name}'.")
        else:
            print(f"  -> Získáno {len(products)} produktů.")
        
        return products
        
    except (KeyError, IndexError, TypeError) as e:
        if logger:
            logger.warning(f"Nepodařilo se parsovat data z browse '{browse_name}': {e}")
        else:
            print(f"  -> Nepodařilo se parsovat data z browse '{browse_name}': {e}")
        return []


def get_all_ean_codes_from_categories(session_token, logger=None):
    """Získá všechny EAN kódy ze všech kategorií a podkategorií."""
    if logger:
        logger.info("Získávám všechny EAN kódy z kategorií...")
    else:
        print("  -> Získávám všechny EAN kódy z kategorií...")
    
    # Seznam všech kategorií, které jsme našli
    categories = [
        "Aranž", "Deko", "Dráty", "Fólie", "Funkč", "Hnoji", "Lesky", "Nářad", 
        "Obaly", "Osiva", "Ostat", "Papír", "Pásky", "Rafie", "Manip", "Sklo", 
        "Stuhy", "Subst", "Svíčk", "Špend", "Výživ"
    ]
    
    all_ean_codes = []
    
    for category in categories:
        if logger:
            logger.info(f"Získávám EAN kódy pro kategorii: {category}")
        else:
            print(f"    -> Získávám EAN kódy pro kategorii: {category}")
        
        # Získáme podskupiny pro kategorii
        subgroups_payload = {
            "_parameters": [
                session_token, "RunExternalAction",
                {
                    "Version": "1.0",
                    "ActionID": ACTION_ID_SUBGROUPS,
                    "SelectedRows": [],
                    "Parameters": [category]
                },
                []
            ]
        }
        
        subgroups_response = execute_helios_command(subgroups_payload, logger=logger)
        if subgroups_response and not subgroups_response["result"][0]["fields"].get("IsError", True):
            try:
                result = subgroups_response["result"][0]["fields"]["Result"]
                if isinstance(result, dict) and 'table' in result:
                    rows = result['table']['rows']
                    
                    # Pro každou podskupinu získáme produkty
                    for row in rows:
                        subgroup_code = None
                        subgroup_name = None
                        
                        for field in row:
                            field_name = field.get('FieldName', '')
                            field_value = field.get('Value', '')
                            if field_name == 'K2':
                                subgroup_code = field_value
                            elif field_name == 'K2Name':
                                subgroup_name = field_value
                        
                        if subgroup_code:
                            if logger:
                                logger.info(f"      Získávám produkty pro podskupinu: {subgroup_name} ({subgroup_code})")
                            else:
                                print(f"      -> Získávám produkty pro podskupinu: {subgroup_name} ({subgroup_code})")
                            
                            # Získáme produkty pro podskupinu
                            products_payload = {
                                "_parameters": [
                                    session_token, "RunExternalAction",
                                    {
                                        "Version": "1.0",
                                        "ActionID": ACTION_ID_PRODUCTS,
                                        "SelectedRows": [],
                                        "Parameters": [category, subgroup_code]
                                    },
                                    []
                                ]
                            }
                            
                            products_response = execute_helios_command(products_payload, logger=logger)
                            if products_response and not products_response["result"][0]["fields"].get("IsError", True):
                                try:
                                    products_result = products_response["result"][0]["fields"]["Result"]
                                    if isinstance(products_result, dict) and 'table' in products_result:
                                        product_rows = products_result['table']['rows']
                                        
                                        # Extrahujeme EAN kódy
                                        category_ean_count = 0
                                        for product_row in product_rows:
                                            for field in product_row:
                                                field_name = field.get('FieldName', '')
                                                field_value = field.get('Value', '')
                                                if field_name == '_EAN' and field_value and len(field_value) > 5:
                                                    all_ean_codes.append(field_value)
                                                    category_ean_count += 1
                                                    break
                                        
                                        if logger:
                                            logger.info(f"        Získáno {len(product_rows)} produktů, {category_ean_count} EAN kódů")
                                        else:
                                            print(f"        -> Získáno {len(product_rows)} produktů, {category_ean_count} EAN kódů")
                                            
                                except (KeyError, IndexError, TypeError):
                                    if logger:
                                        logger.warning(f"        Nepodařilo se parsovat produkty pro podskupinu {subgroup_code}")
                                    else:
                                        print(f"        -> Nepodařilo se parsovat produkty pro podskupinu {subgroup_code}")
                            else:
                                if logger:
                                    logger.warning(f"        Nepodařilo se stáhnout produkty pro podskupinu {subgroup_code}")
                                else:
                                    print(f"        -> Nepodařilo se stáhnout produkty pro podskupinu {subgroup_code}")
                                    
            except (KeyError, IndexError, TypeError):
                if logger:
                    logger.warning(f"Nepodařilo se parsovat podskupiny pro kategorii {category}")
                else:
                    print(f"    -> Nepodařilo se parsovat podskupiny pro kategorii {category}")
        else:
            if logger:
                logger.warning(f"Nepodařilo se stáhnout podskupiny pro kategorii {category}")
            else:
                print(f"    -> Nepodařilo se stáhnout podskupiny pro kategorii {category}")
    
    # Odstraníme duplikáty
    unique_ean_codes = list(set(all_ean_codes))
    
    if logger:
        logger.info(f"Celkem získáno {len(unique_ean_codes)} unikátních EAN kódů ze všech kategorií")
    else:
        print(f"  -> Celkem získáno {len(unique_ean_codes)} unikátních EAN kódů ze všech kategorií")
    
    return unique_ean_codes




def fetch_product_details(session_token, ean_code, logger=None):
    """Stáhne detaily produktu pomocí ActionID_PRODUCT_DETAILS."""
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {
                "Version": "1.0",
                "ActionID": ACTION_ID_PRODUCT_DETAILS,
                "SelectedRows": [],
                "Parameters": [ean_code]
            },
            []
        ]
    }
    
    response = execute_helios_command(payload, logger=logger)
    if response and not response["result"][0]["fields"].get("IsError", True):
        try:
            result = response["result"][0]["fields"]["Result"]
            if isinstance(result, dict) and 'fields' in result and 'QueryBrowse' in result['fields']:
                query_browse = result['fields']['QueryBrowse']
                if isinstance(query_browse, dict) and 'table' in query_browse:
                    rows = query_browse['table']['rows']
                    if rows:
                        # Vezmeme první produkt
                        product_dict = {}
                        for field in rows[0]:
                            field_name = field.get('FieldName', '')
                            field_value = field.get('Value', '')
                            product_dict[field_name] = field_value
                        
                        # Přidáme metadata pro kompatibilitu
                        product_dict['HlavniSkupina'] = 'Kategorie'
                        product_dict['PodskupinaKod'] = product_dict.get('K1', 'Unknown')
                        product_dict['PodskupinaNazev'] = product_dict.get('NazevK1', 'Unknown')
                        
                        if logger:
                            logger.info(f"    Získáno {len(rows)} produktů pro EAN {ean_code}")
                        else:
                            print(f"    -> Získáno {len(rows)} produktů pro EAN {ean_code}")
                        
                        return product_dict
        except (KeyError, IndexError, TypeError):
            if logger:
                logger.warning(f"Nepodařilo se parsovat detaily produktu pro EAN {ean_code}")
            else:
                print(f"    -> Nepodařilo se parsovat detaily produktu pro EAN {ean_code}")
    
    return None


def fetch_product_images(session_token, ean_code, logger=None):
    """Stáhne obrázky produktu pomocí ActionID_PRODUCT_IMAGES."""
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {
                "Version": "1.0",
                "ActionID": ACTION_ID_PRODUCT_IMAGES,
                "SelectedRows": [],
                "Parameters": [ean_code]
            },
            []
        ]
    }
    
    response = execute_helios_command(payload, logger=logger)
    if response and not response["result"][0]["fields"].get("IsError", True):
        try:
            result = response["result"][0]["fields"]["Result"]
            if isinstance(result, dict) and 'table' in result:
                rows = result['table']['rows']
                images = []
                for row in rows:
                    image_dict = {}
                    for field in row:
                        field_name = field.get('FieldName', '')
                        field_value = field.get('Value', '')
                        image_dict[field_name] = field_value
                    images.append(image_dict)
                
                if logger:
                    logger.info(f"    Získáno {len(images)} obrázků pro EAN {ean_code}")
                else:
                    print(f"    -> Získáno {len(images)} obrázků pro EAN {ean_code}")
                
                return images
        except (KeyError, IndexError, TypeError):
            if logger:
                logger.warning(f"Nepodařilo se parsovat obrázky produktu pro EAN {ean_code}")
            else:
                print(f"    -> Nepodařilo se parsovat obrázky produktu pro EAN {ean_code}")
    
    return []


def fetch_products_for_categories(session_token, logger=None):
    """Stáhne produkty pro všechny kategorie z ActionID_KATEGORIE_300."""
    if logger:
        logger.info("Stahuji produkty pro všechny kategorie...")
    else:
        print("[*] Stahuji produkty pro všechny kategorie...")
    
    # Seznam všech kategorií, které jsme našli
    categories = [
        "Aranž", "Deko", "Dráty", "Fólie", "Funkč", "Hnoji", "Lesky", "Nářad", 
        "Obaly", "Osiva", "Ostat", "Papír", "Pásky", "Rafie", "Manip", "Sklo", 
        "Stuhy", "Subst", "Svíčk", "Špend", "Výživ"
    ]
    
    all_products = []
    
    for category in categories:
        if logger:
            logger.info(f"Stahuji produkty pro kategorii: {category}")
        else:
            print(f"  -> Stahuji produkty pro kategorii: {category}")
        
        # Použijeme ACTION_ID_PRODUCTS s parametry [group_name, subgroup_code]
        # Kategorie fungují jako skupiny, takže použijeme kategorii jako oba parametry
        payload = {
            "_parameters": [
                session_token, "RunExternalAction",
                {
                    "Version": "1.0",
                    "ActionID": ACTION_ID_PRODUCTS,
                    "SelectedRows": [],
                    "Parameters": [category, category]
                },
                []
            ]
        }
        
        response = execute_helios_command(payload, logger=logger)
        if response and not response["result"][0]["fields"].get("IsError", True):
            try:
                rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
                category_products = []
                
                for row in rows:
                    try:
                        product_dict = {}
                        for field in row:
                            field_name = field.get('FieldName', '')
                            field_value = field.get('Value', '')
                            product_dict[field_name] = field_value
                        
                        # Přidáme metadata pro kompatibilitu
                        product_dict['HlavniSkupina'] = 'Kategorie'
                        product_dict['PodskupinaKod'] = category
                        product_dict['PodskupinaNazev'] = category
                        
                        category_products.append(product_dict)
                    except (IndexError, KeyError, TypeError):
                        continue
                
                all_products.extend(category_products)
                
                if logger:
                    logger.info(f"    Získáno {len(category_products)} produktů pro kategorii {category}")
                else:
                    print(f"    -> Získáno {len(category_products)} produktů pro kategorii {category}")
                    
            except (KeyError, IndexError):
                if logger:
                    logger.warning(f"Nepodařilo se parsovat produkty pro kategorii {category}")
                else:
                    print(f"    -> Nepodařilo se parsovat produkty pro kategorii {category}")
        else:
            if logger:
                logger.warning(f"Nepodařilo se stáhnout produkty pro kategorii {category}")
            else:
                print(f"    -> Nepodařilo se stáhnout produkty pro kategorii {category}")
    
    if logger:
        logger.info(f"Celkem získáno {len(all_products)} produktů ze všech kategorií")
    else:
        print(f"  -> Celkem získáno {len(all_products)} produktů ze všech kategorií")
    
    return all_products


def fetch_extra_products(session_token, logger=None):
    """Stáhne dodatečné produkty z ActionID browse metadata (jediné, které vrací skutečné produkty)."""
    if logger:
        logger.info("Stahuji dodatečné produkty z ActionID browse metadata...")
    else:
        print("[*] Stahuji dodatečné produkty z ActionID browse metadata...")
    
    extra_products = []
    
    # Test ActionID browse metadata bez parametrů (jediné, které vrací skutečné produkty)
    payload3 = {
        "_parameters": [
            session_token, "RunExternalAction",
            {
                "Version": "1.0",
                "ActionID": ACTION_ID_BROWSE_METADATA,
                "SelectedRows": [],
                "Parameters": []
            },
            []
        ]
    }
    
    response3 = execute_helios_command(payload3, logger=logger)
    if response3 and not response3["result"][0]["fields"].get("IsError", True):
        try:
            result3 = response3["result"][0]["fields"]["Result"]
            # ActionID 3 má jinou strukturu - Result je přímo dict s 'table'
            if isinstance(result3, dict) and 'table' in result3:
                rows3 = result3["table"]["rows"]
                for row in rows3:
                    try:
                        product_dict = {}
                        for field in row:
                            field_name = field.get('FieldName', '')
                            field_value = field.get('Value', '')
                            product_dict[field_name] = field_value
                        
                        # Přidáme metadata pro kompatibilitu
                        product_dict['HlavniSkupina'] = 'Extra'
                        product_dict['PodskupinaKod'] = 'Extra3'
                        product_dict['PodskupinaNazev'] = 'Extra produkty 3'
                        
                        extra_products.append(product_dict)
                    except (IndexError, KeyError, TypeError):
                        continue
                
                if logger:
                    logger.info(f"Získáno {len(rows3)} produktů z ActionID 3")
                else:
                    print(f"  -> Získáno {len(rows3)} produktů z ActionID 3")
            else:
                if logger:
                    logger.warning("ActionID 3 má neočekávanou strukturu výsledku")
                else:
                    print("  -> ActionID 3 má neočekávanou strukturu výsledku")
        except (KeyError, IndexError):
            if logger:
                logger.warning("Nepodařilo se parsovat produkty z ActionID 3")
            else:
                print("  -> Nepodařilo se parsovat produkty z ActionID 3")
    
    if logger:
        logger.info(f"Celkem získáno {len(extra_products)} dodatečných produktů")
    else:
        print(f"  -> Celkem získáno {len(extra_products)} dodatečných produktů")
    
    return extra_products


def fetch_additional_categories(session_token, logger=None):
    """Stáhne dodatečné kategorie s počty produktů."""
    if logger:
        logger.info("Stahuji dodatečné kategorie s počty...")
    else:
        print("[*] Stahuji dodatečné kategorie s počty...")
    
    payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {
                "Version": "1.0",
                "ActionID": ACTION_ID_KATEGORIE_300,
                "SelectedRows": [],
                "Parameters": ["300"]
            },
            []
        ]
    }
    
    response = execute_helios_command(payload, logger=logger)
    
    if not response or response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.warning("Nepodařilo se stáhnout dodatečné kategorie.")
        else:
            print("  -> Nepodařilo se stáhnout dodatečné kategorie.")
        return {}
    
    try:
        rows = response["result"][0]["fields"]["Result"]["table"]["rows"]
        categories = {}
        
        for row in rows:
            try:
                category_id = row[0].get('Value', '')
                category_code = row[1].get('Value', '')
                category_name = row[2].get('Value', '')
                count = row[3].get('Value', '0')
                
                categories[category_code] = {
                    'id': category_id,
                    'name': category_name,
                    'count': int(count) if count.isdigit() else 0
                }
            except (IndexError, KeyError, ValueError):
                continue
        
        if logger:
            logger.info(f"Získáno {len(categories)} dodatečných kategorií.")
        else:
            print(f"  -> Získáno {len(categories)} dodatečných kategorií.")
        
        return categories
        
    except (KeyError, IndexError):
        if logger:
            logger.warning("Nepodařilo se parsovat dodatečné kategorie.")
        else:
            print("  -> Nepodařilo se parsovat dodatečné kategorie.")
        return {}


def test_action_ids_with_parameters(session_token, logger=None):
    """Otestuje ActionID s různými parametry podle příkladů."""
    if logger:
        logger.info("=== TESTOVÁNÍ ACTIONID S PARAMETRY ===")
    else:
        print("\n=== TESTOVÁNÍ ACTIONID S PARAMETRY ===")

    action_tests = [
        (ACTION_ID_KONTAKTY, ["12095", "15303"], "Kontakty s parametry"),
        (ACTION_ID_KATEGORIE_100, ["100"], "Kategorie s parametrem 100"),
        (ACTION_ID_KATEGORIE_300, ["300"], "Kategorie s parametrem 300"),
        (ACTION_ID_BROWSE_METADATA, [], "Browse metadata bez parametrů"),
        (ACTION_ID_SUBGROUPS, ["Aranž"], "Podskupiny pro Aranž"),
        (ACTION_ID_SUBGROUPS, ["Sezón"], "Podskupiny pro Sezón")
    ]
    
    results = {}
    
    for action_id, params, description in action_tests:
        if logger:
            logger.info(f"Testuji {description}: {action_id}")
        else:
            print(f"  [*] Testuji {description}: {action_id}")
        
        payload = {
            "_parameters": [
                session_token, "RunExternalAction",
                {
                    "Version": "1.0",
                    "ActionID": action_id,
                    "SelectedRows": [],
                    "Parameters": params
                },
                []
            ]
        }
        
        response = execute_helios_command(payload, logger=logger)
        
        if response and not response["result"][0]["fields"].get("IsError", True):
            results[action_id] = {
                "success": True,
                "description": description,
                "params": params,
                "data": response["result"][0]["fields"].get("Result", {})
            }
            
            if logger:
                logger.info(f"    ✅ {description} - ÚSPĚCH")
            else:
                print(f"    ✅ {description} - ÚSPĚCH")
            
            # Zkusíme extrahovat počet řádků
            try:
                result_data = response["result"][0]["fields"].get("Result", {})
                if isinstance(result_data, dict) and "table" in result_data:
                    rows = result_data["table"]["rows"]
                    if logger:
                        logger.info(f"      └── Vrátil {len(rows)} řádků")
                    else:
                        print(f"      └── Vrátil {len(rows)} řádků")
            except Exception:
                pass
        else:
            results[action_id] = {
                "success": False,
                "description": description,
                "params": params,
                "error": response["result"][0]["fields"].get("ErrorMessage", "Neznámá chyba") if response else "Žádná odpověď"
            }
            
            if logger:
                logger.info(f"    ❌ {description} - SELHALO")
            else:
                print(f"    ❌ {description} - SELHALO")
    
    return results


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


def scrape_tulipa_data_browse(logger=None):
    """Stáhne všechna data z Tulipa pomocí GetBrowse metody (rychlejší)."""
    if logger:
        logger.info("=== FÁZE 1: STAHOVÁNÍ DAT Z TULIPA (GetBrowse) ===")
    
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
    
    # Stáhneme data pomocí GetBrowse
    products = get_browse_data(session_token, BROWSE_NAME_PRODUCTS, logger)
    
    if not products:
        if logger:
            logger.warning("Nepodařilo se stáhnout produkty pomocí GetBrowse, zkusím původní metodu...")
        else:
            print("[VAROVÁNÍ] Nepodařilo se stáhnout produkty pomocí GetBrowse, zkusím původní metodu...")
        return scrape_tulipa_data(logger)
    
    # Přidáme metadata pro kompatibilitu s původním systémem
    for product in products:
        # Pokud nemáme hlavní skupinu, zkusíme ji odvodit z názvu nebo jiných polí
        if 'HlavniSkupina' not in product:
            product['HlavniSkupina'] = 'Browse'  # Označíme jako Browse data
        if 'PodskupinaKod' not in product:
            product['PodskupinaKod'] = product.get('SkupZbo', '')
        if 'PodskupinaNazev' not in product:
            product['PodskupinaNazev'] = product.get('SkupZbo', '')
    
    if logger:
        logger.info(f"Úspěšně staženo {len(products)} produktů pomocí GetBrowse")
    else:
        print(f"[OK] Úspěšně staženo {len(products)} produktů pomocí GetBrowse")
    
    return products


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
    
    # Stahování dodatečných kategorií
    additional_categories = fetch_additional_categories(session_token, logger)
    
    # Stahování dodatečných produktů
    extra_products = fetch_extra_products(session_token, logger)
    
    # Stahování produktů pro všechny kategorie
    category_products = fetch_products_for_categories(session_token, logger)
    
    # Stahování všech produktů z kategorií pomocí kompletního workflow
    all_category_products = fetch_all_products_from_categories(session_token, logger)
    
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

    # Přidáme dodatečné produkty
    all_products.extend(extra_products)

    # Přidáme produkty z kategorií
    all_products.extend(category_products)
    
    # Přidáme všechny produkty z kategorií pomocí kompletního workflow
    all_products.extend(all_category_products)

    # Přidáme dodatečné kategorie jako speciální řádky
    for category_code, category_info in additional_categories.items():
        category_row = {
            'HlavniSkupina': 'Kategorie',
            'PodskupinaKod': category_code,
            'PodskupinaNazev': category_info['name'],
            'Nazev1': f"KATEGORIE: {category_info['name']}",
            'RegCis': f"CAT_{category_code}",
            '_cena_cu1': '0',
            'Mnozstvi': str(category_info['count']),
            'ID': category_info['id'],
            'JizNaSklade': str(category_info['count']),
            'K1': category_code,
            'K2': category_info['name'],
            'Nazev': f"KATEGORIE: {category_info['name']}",
            'Nazev2': '',
            'Nazev4': '',
            'NazevK1': category_code,
            'NazevK2': category_info['name'],
            'PrepMnozstvi': str(category_info['count']),
            'SKP': '',
            'SkupZbo': category_code,
            'Sluzba': '0',
            'Vykres': '',
            '_Barva': '',
            '_EAN': '',
            '_PocNaPlat': '',
            '_Tulipa_Baleni': '',
            '_Tulipa_DelkaVyska': '',
            '_Tulipa_ExistujeObrazek': '',
            '_Tulipa_KodObalu': '',
            '_Tulipa_NeaktualizovatCenu': '',
            '_Tulipa_PocetGramaz': '',
            '_Tulipa_PrumKvet': '',
            '_Tulipa_Rozmer': '',
            '_Tulipa_URL_Obrazku': '',
            '_Tulipa_Zkratka': category_code,
            '_cena_cu2': '0',
            '_cena_cu3': '0',
            '_cena_cu4': '0'
        }
        all_products.append(category_row)

    return all_products


# CACHE A SOUBOR FUNKCE
# =============================================================================

def get_cache_file_path(base_name="produkty_komplet"):
    """Vrátí cestu k cache souboru s datem v adresáři pro daný den."""
    # Vytvoříme adresář s datem (YYYY-MM-DD)
    date_str = datetime.now().strftime("%Y-%m-%d")
    date_dir = os.path.join("data", date_str)
    
    # Vytvoříme adresář pokud neexistuje
    os.makedirs(date_dir, exist_ok=True)
    
    # Vytvoříme název souboru s časem
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{base_name}_{timestamp}.csv"
    
    return os.path.join(date_dir, filename)


def is_cache_valid(file_path, max_age_hours=1):
    """Zkontroluje, zda je cache soubor stále platný."""
    if not os.path.exists(file_path):
        return False
    
    file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
    current_time = datetime.now()
    age = current_time - file_time
    
    return age.total_seconds() < (max_age_hours * 3600)


def find_latest_cache_file(base_name="produkty_komplet"):
    """Najde nejnovější cache soubor v adresářích s daty."""
    data_dir = "data"
    if not os.path.exists(data_dir):
        return None
    
    # Najdeme všechny soubory odpovídající vzoru v podadresářích
    import glob
    pattern = os.path.join(data_dir, "*", f"{base_name}_*.csv")
    files = glob.glob(pattern)
    
    if not files:
        return None
    
    # Vrátíme nejnovější soubor
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def cleanup_old_cache_files(base_name="produkty_komplet", keep_hours=24):
    """Smaže staré cache soubory v adresářích s daty."""
    data_dir = "data"
    if not os.path.exists(data_dir):
        return
    
    import glob
    pattern = os.path.join(data_dir, "*", f"{base_name}_*.csv")
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
        # Zkusíme načíst z cache (pouze pokud není zadán konkrétní soubor)
        if args.output == "produkty_komplet.csv":  # Výchozí hodnota
            csv_products, csv_file = load_csv_from_cache(None, logger)
        else:
            csv_products, csv_file = load_csv_from_cache(args.output, logger)

        # Pokud cache není platný, stáhneme nová data
        if csv_products is None:
            logger.info("Začínám fázi stahování...")

            # Vybereme metodu stahování
            if args.browse:
                logger.info("Používám GetBrowse metodu (rychlejší)")
                csv_products = scrape_tulipa_data_browse(logger)
            else:
                logger.info("Používám původní metodu (RunExternalAction)")
                csv_products = scrape_tulipa_data(logger)

            if not csv_products:
                logger.error("Žádné produkty staženy, ukončuji")
                return 1

            # Uložení do CSV s cache logikou
            success, csv_file = save_to_csv(csv_products, None, logger)  # Použijeme automatické pojmenování
            if not success:
                logger.error("Selhalo uložení CSV, ukončuji")
                return 1

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
    parser.add_argument("--browse", action="store_true", help="Použít GetBrowse metodu místo původní metody (rychlejší)")
    parser.add_argument("--test-actions", action="store_true", help="Otestovat ActionID s různými parametry")
    parser.add_argument("--list-browse", action="store_true", help="Vypsat dostupné browse definice")
    
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
    
    # Testování ActionID s parametry
    if args.test_actions:
        logger.info("=== TESTOVÁNÍ ACTIONID S PARAMETRY ===")
        session_token = get_valid_session_token(logger)
        if not session_token:
            logger.error("Nepodařilo se získat platný session token")
            return 1
        
        # Aktivace databáze
        change_db_payload = {
            "_parameters": [
                session_token, "ChangeDatabase", 
                {"Version": "1.0", "DatabaseName": "Helios001"}, []
            ]
        }
        execute_helios_command(change_db_payload, is_reset_call=True, logger=logger)
        
        results = test_action_ids_with_parameters(session_token, logger)
        logger.info("=== VÝSLEDKY TESTOVÁNÍ ===")
        for action_id, result in results.items():
            if result['success']:
                logger.info(f"✅ {result['description']}: ÚSPĚCH")
            else:
                logger.info(f"❌ {result['description']}: {result['error']}")
        return 0
    
    
    # Vypsání browse definic
    if args.list_browse:
        logger.info("=== DOSTUPNÉ BROWSE DEFINICE ===")
        session_token = get_valid_session_token(logger)
        if not session_token:
            logger.error("Nepodařilo se získat platný session token")
            return 1
        
        # Aktivace databáze
        change_db_payload = {
            "_parameters": [
                session_token, "ChangeDatabase", 
                {"Version": "1.0", "DatabaseName": "Helios001"}, []
            ]
        }
        execute_helios_command(change_db_payload, is_reset_call=True, logger=logger)
        
        browse_list = get_browse_list(session_token, logger)
        logger.info(f"Nalezeno {len(browse_list)} browse definic:")
        for i, browse in enumerate(browse_list[:10]):  # Zobrazíme prvních 10
            logger.info(f"  {i+1}. {browse}")
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


def fetch_all_products_from_categories(session_token, logger=None):
    """Stáhne všechny produkty z kategorií pomocí kompletního workflow."""
    if logger:
        logger.info("Stahuji všechny produkty z kategorií pomocí kompletního workflow...")
    else:
        print("[*] Stahuji všechny produkty z kategorií pomocí kompletního workflow...")
    
    all_products = []
    
    # 1. Získáme všechny kategorie
    categories_payload = {
        "_parameters": [
            session_token, "RunExternalAction",
            {
                "Version": "1.0",
                "ActionID": ACTION_ID_KATEGORIE_300,
                "SelectedRows": [],
                "Parameters": ["300"]
            },
            []
        ]
    }
    
    categories_response = execute_helios_command(categories_payload, logger=logger)
    if not categories_response or categories_response["result"][0]["fields"].get("IsError", True):
        if logger:
            logger.warning("Nepodařilo se stáhnout kategorie")
        else:
            print("  -> Nepodařilo se stáhnout kategorie")
        return []
    
    try:
        categories_rows = categories_response["result"][0]["fields"]["Result"]["table"]["rows"]
        categories = []
        
        for row in categories_rows:
            category_id = row[0].get('Value', '')
            category_code = row[1].get('Value', '')
            category_name = row[2].get('Value', '')
            count = row[3].get('Value', '0')
            
            categories.append({
                'id': category_id,
                'code': category_code,
                'name': category_name,
                'count': int(count) if count.isdigit() else 0
            })
        
        if logger:
            logger.info(f"Získáno {len(categories)} kategorií")
        else:
            print(f"  -> Získáno {len(categories)} kategorií")
        
        # 2. Pro každou kategorii získáme podskupiny a produkty
        for category in categories:
            category_code = category['code']
            category_name = category['name']
            category_count = category['count']
            
            if category_count == 0:
                if logger:
                    logger.info(f"Kategorie {category_code} má 0 produktů, přeskočeno")
                else:
                    print(f"  -> Kategorie {category_code} má 0 produktů, přeskočeno")
                continue
            
            if logger:
                logger.info(f"Zpracovávám kategorii: {category_code} - {category_name} ({category_count} produktů)")
            else:
                print(f"  -> Zpracovávám kategorii: {category_code} - {category_name} ({category_count} produktů)")
            
            # Získáme podskupiny pro kategorii
            subgroups_payload = {
                "_parameters": [
                    session_token, "RunExternalAction",
                    {
                        "Version": "1.0",
                        "ActionID": ACTION_ID_SUBGROUPS,
                        "SelectedRows": [],
                        "Parameters": [category_code]
                    },
                    []
                ]
            }
            
            subgroups_response = execute_helios_command(subgroups_payload, logger=logger)
            if not subgroups_response or subgroups_response["result"][0]["fields"].get("IsError", True):
                if logger:
                    logger.warning(f"Nepodařilo se stáhnout podskupiny pro kategorii {category_code}")
                else:
                    print(f"    -> Nepodařilo se stáhnout podskupiny pro kategorii {category_code}")
                continue
            
            try:
                subgroups_rows = subgroups_response["result"][0]["fields"]["Result"]["table"]["rows"]
                subgroups = []
                
                for row in subgroups_rows:
                    subgroup_code = None
                    subgroup_name = None
                    
                    for field in row:
                        field_name = field.get('FieldName', '')
                        field_value = field.get('Value', '')
                        if field_name == 'K2':
                            subgroup_code = field_value
                        elif field_name == 'K2Name':
                            subgroup_name = field_value
                    
                    if subgroup_code:
                        subgroups.append({
                            'code': subgroup_code,
                            'name': subgroup_name
                        })
                
                if logger:
                    logger.info(f"    Získáno {len(subgroups)} podskupin pro kategorii {category_code}")
                else:
                    print(f"    -> Získáno {len(subgroups)} podskupin pro kategorii {category_code}")
                
                # Pro každou podskupinu získáme produkty
                for subgroup in subgroups:
                    subgroup_code = subgroup['code']
                    subgroup_name = subgroup['name']
                    
                    products_payload = {
                        "_parameters": [
                            session_token, "RunExternalAction",
                            {
                                "Version": "1.0",
                                "ActionID": ACTION_ID_PRODUCTS,
                                "SelectedRows": [],
                                "Parameters": [category_code, subgroup_code]
                            },
                            []
                        ]
                    }
                    
                    products_response = execute_helios_command(products_payload, logger=logger)
                    if products_response and not products_response["result"][0]["fields"].get("IsError", True):
                        try:
                            products_result = products_response["result"][0]["fields"]["Result"]
                            
                            # Debug: vypíšeme strukturu Result
                            if logger:
                                logger.debug(f"      Struktura Result pro {subgroup_code}: {type(products_result)}")
                                if isinstance(products_result, dict):
                                    logger.debug(f"      Klíče Result: {list(products_result.keys())}")
                            
                            products_rows = []
                            
                            # Zkusíme různé struktury
                            if isinstance(products_result, dict):
                                # Struktura 1: QueryBrowse
                                if 'fields' in products_result and 'QueryBrowse' in products_result['fields']:
                                    query_browse = products_result['fields']['QueryBrowse']
                                    if isinstance(query_browse, dict) and 'table' in query_browse:
                                        products_rows = query_browse['table']['rows']
                                
                                # Struktura 2: Přímá table struktura
                                elif 'table' in products_result:
                                    products_rows = products_result['table']['rows']
                                
                                # Struktura 3: Přímý seznam řádků
                                elif isinstance(products_result, list):
                                    products_rows = products_result
                            
                            # Zpracujeme řádky
                            if products_rows:
                                for row in products_rows:
                                    try:
                                        product_dict = {}
                                        for field in row:
                                            field_name = field.get('FieldName', '')
                                            field_value = field.get('Value', '')
                                            product_dict[field_name] = field_value
                                        
                                        # Přidáme metadata pro kompatibilitu
                                        product_dict['HlavniSkupina'] = 'Kategorie'
                                        product_dict['PodskupinaKod'] = category_code
                                        product_dict['PodskupinaNazev'] = subgroup_name
                                        
                                        all_products.append(product_dict)
                                    except (IndexError, KeyError, TypeError):
                                        continue
                                
                                if logger:
                                    logger.info(f"      Získáno {len(products_rows)} produktů pro podskupinu {subgroup_code}")
                                else:
                                    print(f"      -> Získáno {len(products_rows)} produktů pro podskupinu {subgroup_code}")
                            else:
                                if logger:
                                    logger.warning(f"      Žádné produkty pro podskupinu {subgroup_code} (struktura: {type(products_result)})")
                                else:
                                    print(f"      -> Žádné produkty pro podskupinu {subgroup_code} (struktura: {type(products_result)})")
                                    
                        except (KeyError, IndexError, TypeError) as e:
                            if logger:
                                logger.warning(f"      Nepodařilo se parsovat produkty pro podskupinu {subgroup_code}: {e}")
                            else:
                                print(f"      -> Nepodařilo se parsovat produkty pro podskupinu {subgroup_code}: {e}")
                    else:
                        if logger:
                            logger.warning(f"      Nepodařilo se stáhnout produkty pro podskupinu {subgroup_code}")
                        else:
                            print(f"      -> Nepodařilo se stáhnout produkty pro podskupinu {subgroup_code}")
                            
            except (KeyError, IndexError, TypeError):
                if logger:
                    logger.warning(f"    Nepodařilo se parsovat podskupiny pro kategorii {category_code}")
                else:
                    print(f"    -> Nepodařilo se parsovat podskupiny pro kategorii {category_code}")
                    
    except (KeyError, IndexError, TypeError):
        if logger:
            logger.warning("Nepodařilo se parsovat kategorie")
        else:
            print("  -> Nepodařilo se parsovat kategorie")
        return []
    
    if logger:
        logger.info(f"Celkem získáno {len(all_products)} produktů ze všech kategorií")
    else:
        print(f"  -> Celkem získáno {len(all_products)} produktů ze všech kategorií")
    
    return all_products


if __name__ == "__main__":
    sys.exit(main())
