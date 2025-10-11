# Tulipa Offers Scraper

Nástroj pro stahování produktových dat z Tulipa serveru a synchronizaci s Shopify. Umožňuje automatizaci správy inventáře a produktů mezi systémy.

## Instalace

```bash
git clone https://github.com/koprjaa/tulipa-offers-scraper.git
cd tulipa-offers-scraper
pip install -r requirements.txt
```

## Použití

Stažení dat z Tulipa:
```bash
python run.py --scrape-only
```

Synchronizace se Shopify:
```bash
python run.py --shopify-only
```

Kompletní workflow:
```bash
python run.py
```

## Licence

MIT License - viz [LICENSE](LICENSE) soubor pro detaily.
