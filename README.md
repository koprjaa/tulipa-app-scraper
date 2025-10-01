# Tulipa Offers Scraper

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)

Automatizovaný nástroj pro synchronizaci inventáře mezi Tulipa serverem a Shopify obchodem. Inteligentní cache systém, automatické nastavování sledování inventáře, pokročilé error handling a podpora pro produkty s více variantami.

## Klíčové funkce

- **Automatická synchronizace** - Inventář se synchronizuje mezi Tulipa a Shopify
- **Inteligentní cache** - Rychlé načítání dat s automatickým stárnutím
- **Shopify integrace** - Plná podpora GraphQL API s optimalizovanými dotazy
- **Detailní reporting** - Kompletní zprávy o provedených změnách
- **Error handling** - Robustní zpracování chyb s automatickým opakováním
- **Flexibilní konfigurace** - Různé režimy spuštění a filtrování
- **🆕 Podpora více variant** - Automatické zpracování produktů s více SKU variantami
- **🆕 Sdílený inventář** - Inteligentní rozdělení inventáře mezi obyčejné a průhledné květináče
- **🆕 Skupinování produktů** - Automatické seskupování produktů podle názvu a hlavní skupiny

## Rychlý start

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
python run.py
```

#### Pouze stahování (bez Shopify)
```bash
python run.py --scrape-only
```

#### Synchronizace se Shopify (z cache)
```bash
python run.py --shopify-only
```

#### Testovací režim (bez změn)
```bash
python run.py --modify --dry-run
```

#### Reset session
```bash
python run.py --reset
```

## Dostupné možnosti

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

## Pokročilé funkce

### Podpora více variant

Skript automaticky zpracovává produkty s více variantami:

- **Více SKU v jednom poli**: `"895999, 000410"` - automaticky rozdělí a zpracuje každé SKU zvlášť
- **Produkty s více variantami**: Např. Monstera s obyčejným a průhledným květináčem se stejným SKU
- **Inteligentní rozdělení**: Automaticky rozdělí inventář mezi varianty podle typu

### Sdílený inventář

Pro produkty s obyčejnými a průhlednými květináči:

- **Automatické rozpoznání**: Identifikuje produkty s více variantami podle názvu
- **Inteligentní rozdělení**: 
  - Pokud je více než 20 kusů: neodečítá rezervu
  - Pokud je méně než 20 kusů: odečte 5 kusů pro rezervu
  - Rozdělí dostupný inventář na půl mezi obyčejné a průhledné
  - U lichých čísel přidá tu jednu vždy k obyčejnému plastovému

### Skupinování produktů

- **Automatické seskupování**: Produkty se seskupují podle názvu a hlavní skupiny
- **Součet množství**: Množství ze všech RegCis ve skupině se sečte
- **Správné přiřazení**: Každé SKU se zpracuje s správným množstvím ze skupiny

## Co skript dělá

1. **Inteligentní cache** - Automaticky ukládá data s časovým razítkem
2. **Stahování dat** - Připojí se k Tulipa serveru a stáhne produktová data
3. **Uložení do CSV** - Uloží data do CSV souboru s českým kódováním
4. **Načtení ze Shopify** - Načte existující produkty z Shopify obchodu
5. **Analýza rozdílů** - Porovná data z Tulipa s inventářem v Shopify
6. **Synchronizace** - Aktualizuje inventář v Shopify podle dat z Tulipa

### Cache systém
- **Automatické pojmenování**: Soubory se ukládají s datem (`produkty_komplet_20250911_175247.csv`)
- **Inteligentní cache**: Pokud je soubor novější než 1 hodina, použije se cache
- **Automatické čištění**: Staré soubory (starší než 24 hodin) se automaticky mažou
- **Rychlé spuštění**: Při opakovaném spuštění se data načtou z cache místo stahování

## Konfigurace

Hlavní skupiny k zpracování můžete upravit přímo v souboru `run.py`:

```python
HLAVNI_SKUPINY = [
    "Dekor", "Kveto", "Sezón", "Sukul", 
]
```

## Výstupní soubory

Všechny výstupní soubory se ukládají do složky `data/`:

- `data/produkty_komplet_YYYYMMDD_HHMMSS.csv` - Stáhnutá data z Tulipa s časovým razítkem
- `data/tulipa_session.json` - Session token pro připojení k Tulipa serveru
- `data/modification_report_YYYYMMDD_HHMMSS.json` - Detailní zprávy o provedených změnách v Shopify

## Řešení problémů

### Chyby připojení
```bash
# Resetujte session
python run.py --reset

# Testujte připojení
python run.py --discover
```

### Shopify chyby
- Zkontrolujte `.env` soubor
- Ověřte platnost API tokenu
- Zkontrolujte oprávnění tokenu

### Debug režim
```bash
python run.py --debug
```

## Cache systém

### Automatické řízení cache
- **Časové razítko**: Soubory se pojmenovávají podle data vytvoření
- **1-hodinová platnost**: Cache se použije, pokud je soubor novější než 1 hodina
- **Automatické čištění**: Soubory starší než 24 hodin se automaticky mažou
- **Inteligentní načítání**: Systém automaticky najde nejnovější platný cache soubor

### Manuální správa cache
```bash
# Vymazat všechny cache soubory
rm -rf data/produkty_komplet_*.csv

# Zobrazit velikost cache
du -sh data/
```

## Technické detaily

### Optimalizace výkonu
- **GraphQL dotazy**: Optimalizované pro minimální náklady na API
- **Batch processing**: Zpracování po dávkách pro lepší výkon
- **Retry mechanismus**: Automatické opakování při selhání
- **Rate limiting**: Respektování limitů Shopify API

### Bezpečnost
- **Environment variables**: Citlivé údaje v `.env` souboru
- **Session management**: Bezpečné ukládání a obnovování session tokenů
- **Error logging**: Detailní logování bez vystavování citlivých dat

## Poznámky

- Skript automaticky odečítá 5 kusů od množství z Tulipa pro jistotu
- Session token se ukládá a automaticky obnovuje
- Všechny změny se logují do konzole s časovým razítkem
- V dry-run režimu se žádné změny neprovedou
- Automatické nastavování sledování inventáře v Shopify
- **🆕 Automatické zpracování více variant** - Produkty s více SKU se zpracují automaticky
- **🆕 Inteligentní rozdělení inventáře** - Automatické rozdělení mezi obyčejné a průhledné květináče
- **🆕 Skupinování produktů** - Automatické seskupování a součet množství ze skupin

## Licence

Tento projekt je licencován pod MIT licencí - viz [LICENSE](LICENSE) soubor pro detaily.

---

**Bezpečnostní upozornění**: Tento nástroj pracuje s citlivými daty a API přihlašovacími údaji. Ujistěte se, že dodržujete správné bezpečnostní postupy a nikdy necommitnete `.env` soubor do Git repository.