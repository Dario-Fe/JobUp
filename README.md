# <img src="icon.svg" width="32" height="32" align="center" /> JobUp VCO

Webapp per consultare le offerte di lavoro del Verbano Cusio Ossola, tratte dal portale ufficiale [pslp.regione.piemonte.it](https://pslp.regione.piemonte.it).

## Come funziona

1. **GitHub Actions** esegue lo scraper Python **due volte al giorno** (08:00 e 17:00 ora locale italiana)
2. Lo scraper genera `annunci.json` con tutte le offerte nella zona configurata (filtrando per comune e raggio in km)
3. Il commit del JSON triggera un **redeploy automatico su Netlify**
4. La webapp legge il JSON e mostra le offerte con ricerca live e scheda dettaglio

## Struttura

```
JobUp/
├── index.html              ← webapp (tutto in un file, zero dipendenze)
├── infoutili.html          ← pagina informazioni e trasparenza
├── annunci.json            ← generato automaticamente dallo scraper
├── manifest.json           ← PWA manifest
├── icon.svg
├── netlify.toml
├── scraper/
│   └── scraper_pslp.py     ← scraper Playwright
└── .github/
    └── workflows/
        └── aggiorna.yml    ← cron 08:00 e 17:00
```

## Configurazione

Il comune e il raggio di ricerca si impostano nel workflow `.github/workflows/aggiorna.yml`:

```yaml
env:
  JOBUP_COMUNE: "Verbania"
  JOBUP_DISTANZA: "50"
```

## Esecuzione locale dello scraper

```bash
pip install playwright
playwright install chromium

# Con browser visibile (debug)
py -3 scraper/scraper_pslp.py --comune "Verbania" --distanza 50 --visible

# Headless
py -3 scraper/scraper_pslp.py --comune "Verbania" --distanza 50
```

## Deploy

1. Crea una repo GitHub e carica tutti i file
2. Collega la repo a [Netlify](https://netlify.com) (publish directory: `.`)
3. I dati si aggiorneranno automaticamente due volte al giorno

## Crediti

Progetto ispirato a [BenzApp](https://github.com/Dario-Fe/BenzApp).  
Dati: [Agenzia Piemonte Lavoro – pslp.regione.piemonte.it](https://pslp.regione.piemonte.it)
