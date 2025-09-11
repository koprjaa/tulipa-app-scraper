# Tulipa Offers Scraper

Jednoduchý nástroj pro stahování dat z Tulipa serveru a synchronizaci inventáře se Shopify.

## 🚀 Rychlý start

### 1. Instalace

```bash
# Vytvoření virtuálního prostředí
python -m venv venv
source venv/bin/activate  # Na Windows: venv\Scripts\activate

# Instalace závislostí
pip install -r requirements.txt
```

### 2. Konfigurace

Vytvořte `.env` soubor v hlavní složce:

```env
SHOPIFY_STORE_DOMAIN=vas-obchod.myshopify.com
SHOPIFY_ADMIN_API_ACCESS_TOKEN=vase-token
```

### 3. Použití

#### Základní stahování dat (s automatickým cache)
```bash
python tulipa_offers_scraper.py
```

#### Pouze stahování (bez Shopify)
```bash
python tulipa_offers_scraper.py --scrape-only
```

#### Synchronizace se Shopify (z cache)
```bash
python tulipa_offers_scraper.py --shopify-only
```

#### Testovací režim (bez změn)
```bash
python tulipa_offers_scraper.py --modify --dry-run
```

#### Reset session
```bash
python tulipa_offers_scraper.py --reset
```

## 📋 Dostupné možnosti

- `--scrape-only` - Pouze stáhni data a ulož do CSV
- `--shopify-only` - Pouze synchronizuj se Shopify (přeskoč stahování)
- `--modify` - Povolit úpravu produktů
- `--dry-run` - Zobrazit co by se změnilo bez provedení změn
- `--limit N` - Omezit počet produktů k zpracování
- `--filter-group GROUP` - Filtrovat podle hlavní skupiny
- `--output FILE` - Výstupní CSV soubor
- `--debug` - Zapnout debug režim
- `--reset` - Resetovat session a začít znovu
- `--discover` - Objevit dostupné kategorie

## 📊 Co skript dělá

1. **Inteligentní cache** - Automaticky ukládá data s časovým razítkem
2. **Stahování dat** - Připojí se k Tulipa serveru a stáhne produktová data
3. **Uložení do CSV** - Uloží data do CSV souboru s českým kódováním
4. **Načtení ze Shopify** - Načte existující produkty z Shopify obchodu
5. **Analýza rozdílů** - Porovná data z Tulipa s inventářem v Shopify
6. **Synchronizace** - Aktualizuje inventář v Shopify podle dat z Tulipa

### 🗂️ Cache systém
- **Automatické pojmenování**: Soubory se ukládají s datem (`produkty_komplet_20250911_175247.csv`)
- **Inteligentní cache**: Pokud je soubor novější než 1 hodina, použije se cache
- **Automatické čištění**: Staré soubory (starší než 24 hodin) se automaticky mažou
- **Rychlé spuštění**: Při opakovaném spuštění se data načtou z cache místo stahování

## ⚙️ Konfigurace

Hlavní skupiny k zpracování můžete upravit přímo v souboru `tulipa_offers_scraper.py`:

```python
HLAVNI_SKUPINY = [
    "Dekor", "Kveto", "Sezón", "Sukul", 
]
```

## 📁 Výstupní soubory

- `data/produkty_komplet_YYYYMMDD_HHMMSS.csv` - Stáhnutá data z Tulipa s časovým razítkem
- `tulipa_session.json` - Session token pro připojení
- `modification_report_*.json` - Zprávy o provedených změnách

## 🔧 Řešení problémů

### Chyby připojení
```bash
# Resetujte session
python tulipa_offers_scraper.py --reset

# Testujte připojení
python tulipa_offers_scraper.py --discover
```

### Shopify chyby
- Zkontrolujte `.env` soubor
- Ověřte platnost API tokenu
- Zkontrolujte oprávnění tokenu

### Debug režim
```bash
python tulipa_offers_scraper.py --debug
```

## 📝 Poznámky

- Skript automaticky odečítá 5 kusů od množství z Tulipa pro jistotu
- Session token se ukládá a automaticky obnovuje
- Všechny změny se logují do konzole
- V dry-run režimu se žádné změny neprovedou

---

**Poznámka**: Tento nástroj pracuje s citlivými daty a API přihlašovacími údaji. Ujistěte se, že dodržujete správné bezpečnostní postupy.