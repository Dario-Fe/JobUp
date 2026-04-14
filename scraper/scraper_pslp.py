"""
JobUp - Scraper offerte di lavoro
pslp.regione.piemonte.it → annunci.json

Uso locale:
    py -3 scraper_pslp.py --comune "Verbania" --distanza 50
    py -3 scraper_pslp.py --comune "Verbania" --distanza 50 --visible

Uso da GitHub Actions (headless, variabili da env):
    python scraper_pslp.py
"""

import asyncio
import json
import argparse
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from playwright.async_api import async_playwright

BASE_URL   = "https://pslp.regione.piemonte.it"
LIST_URL   = f"{BASE_URL}/pslpwcl/pslpfcweb/consulta-annunci/profili-ricercati"
DETAIL_URL = f"{BASE_URL}/pslpwcl/pslpfcweb/consulta-annunci/visualizza-annuncio-profili-ric"
API_URL    = f"{BASE_URL}/pslpbff/api-public/v1/annunci-pslp/consulta-annunci"
OUTPUT     = os.path.join(os.path.dirname(__file__), "..", "annunci.json")


def costruisci_annuncio(raw: dict) -> dict:
    """Normalizza un annuncio grezzo in un oggetto pulito per la webapp."""
    id_annuncio = raw.get("idAnnuncio", "")

    # Badge categoria (L68 art.1 = disabilità, art.18 = altre categorie)
    if raw.get("flgL68Art1") == "S":
        categoria = "Collocamento mirato – Art. 1 L.68/99"
    elif raw.get("flgL68Art18") == "S":
        categoria = "Collocamento mirato – Art. 18 L.68/99"
    else:
        categoria = "Offerta ordinaria"

    # Data scadenza leggibile
    data_scad = raw.get("dataScadenza", "")
    try:
        dt = datetime.fromisoformat(data_scad.replace("Z", "+00:00"))
        data_scad_fmt = dt.strftime("%d/%m/%Y")
    except Exception:
        data_scad_fmt = data_scad[:10] if data_scad else "–"

    # Data pubblicazione/stato
    data_stato = raw.get("dataStato", "")
    try:
        dt2 = datetime.fromisoformat(data_stato.replace("Z", "+00:00"))
        data_stato_fmt = dt2.strftime("%d/%m/%Y")
    except Exception:
        data_stato_fmt = data_stato[:10] if data_stato else "–"

    return {
        "id":              id_annuncio,
        "numAnnuncio":     raw.get("numAnnuncio", ""),
        "titolo":          raw.get("titoloVacancy", "").strip().title(),
        "azienda":         raw.get("azienda", "").strip().title(),
        "comune":          raw.get("descrComuneSede", "").strip().title(),
        "provincia":       raw.get("descrProvinciaSede", "").strip().title(),
        "cpi":             raw.get("descrCpi", raw.get("dsIntermediario", "")).strip().title(),
        "contratto":       raw.get("contratto", "–").strip().title(),
        "profilo":         raw.get("dsProfiloIstat", "").strip().title(),
        "qualifica":       raw.get("qualifica", "").strip(),
        "categoria":       categoria,
        "stato":           raw.get("stato", "").strip(),
        "dataScadenza":    data_scad_fmt,
        "dataPubblicazione": data_stato_fmt,
        "mapsUrl":         raw.get("mapsUrls", ""),
        "urlFonte":        f"{DETAIL_URL}?id={id_annuncio}",
    }


async def scrapa(comune: str, distanza: int, headless: bool) -> list:
    tutti = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = await context.new_page()

        # Intercetta richiesta E risposta per catturare headers di sessione
        api_params = {
            "trovato":  False,
            "url_base": None,
            "headers":  {},
        }

        def on_request(request):
            if "annunci-pslp/consulta-annunci" in request.url:
                # Salva gli headers della prima richiesta (contengono cookie/auth)
                api_params["url_base"] = request.url.split("?")[0]
                api_params["headers"]  = dict(request.headers)

        async def on_response(response):
            url = response.url
            ct  = response.headers.get("content-type", "")
            if "json" not in ct or "annunci-pslp/consulta-annunci" not in url:
                return
            try:
                data = await response.json()
                items = data.get("list") or data.get("content") or []
                if items and isinstance(items[0], dict):
                    if not api_params["trovato"]:
                        print(f"[+] API trovata: {url}")
                        api_params["trovato"] = True
                    tutti.extend(items)
                    print(f"    +{len(items)} annunci (tot: {len(tutti)})")
            except Exception:
                pass

        page.on("request",  on_request)
        page.on("response", on_response)

        # ── Carica pagina ────────────────────────────────────────────
        print(f"[*] Carico {LIST_URL}")
        await page.goto(LIST_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(2)

        # ── Campo COMUNE (PrimeNG p-dropdown) ────────────────────────
        print(f"[*] Seleziono comune: {comune}")
        await page.click("p-dropdown", timeout=10000)
        await asyncio.sleep(0.8)
        await page.keyboard.type(comune, delay=100)
        await asyncio.sleep(2)

        opzioni = page.locator("ul.p-dropdown-items li.p-dropdown-item, .p-dropdown-item")
        n = await opzioni.count()
        if n > 0:
            testo = (await opzioni.first.inner_text()).strip()
            await opzioni.first.click()
            print(f"[+] Comune selezionato: {testo}")
        else:
            await page.keyboard.press("Enter")
            print("[!] Autocomplete vuoto, premuto Enter")
        await asyncio.sleep(1)

        # ── Campo DISTANZA ───────────────────────────────────────────
        print(f"[*] Inserisco distanza: {distanza} km")
        await page.wait_for_selector("#rangeKM:not([disabled])", timeout=10000)
        await page.click("#rangeKM")
        await page.keyboard.press("Control+a")
        await page.keyboard.type(str(distanza))
        await asyncio.sleep(0.3)

        # ── CERCA ────────────────────────────────────────────────────
        print("[*] Clicco CERCA...")
        await page.wait_for_selector('button:has-text("CERCA"):not([disabled])', timeout=10000)
        await page.click('button:has-text("CERCA")')

        # Aspetta prima pagina (100 risultati)
        await page.wait_for_load_state("networkidle", timeout=30000)
        await asyncio.sleep(4)

        # ── Paginazione: usa page.request con headers di sessione ────────
        if api_params["trovato"] and len(tutti) == 100:
            print("[*] Possibili altre pagine, scarico via API diretta...")
            url_base = api_params.get("url_base") or API_URL
            hdrs = dict(api_params.get("headers", {}))
            hdrs["accept"] = "application/json"

            pagina = 1
            while True:
                url_pag = f"{url_base}?page={pagina}&recForPage=100"
                print(f"    Richiedo pagina {pagina}...")
                try:
                    resp  = await page.request.get(url_pag, headers=hdrs, timeout=20000)
                    testo = await resp.text()
                    if resp.status != 200 or not testo.strip():
                        print(f"    HTTP {resp.status} o risposta vuota, fine.")
                        break
                    dati  = json.loads(testo)
                    items = dati.get("list") or dati.get("content") or []
                    if not items:
                        print(f"    Pagina {pagina}: nessun risultato, fine.")
                        break
                    tutti.extend(items)
                    print(f"    Pagina {pagina}: +{len(items)} (tot: {len(tutti)})")
                    if len(items) < 100:
                        break
                    pagina += 1
                    await asyncio.sleep(0.5)
                except Exception as e:
                    print(f"    Errore pagina {pagina}: {e}")
                    break

        await browser.close()

    return tutti


def salva_json(annunci_raw: list, comune: str, distanza: int):
    # Ordina per dataStato (decrescente) e idAnnuncio (decrescente)
    # In questo modo i più recenti appaiono per primi.
    annunci_raw.sort(
        key=lambda x: (x.get("dataStato") or "", x.get("idAnnuncio", 0)),
        reverse=True
    )

    annunci = [costruisci_annuncio(a) for a in annunci_raw]

    # Rimuovi eventuali duplicati per id (mantenendo l'ordine)
    visti = set()
    unici = []
    for a in annunci:
        if a["id"] not in visti:
            visti.add(a["id"])
            unici.append(a)

    now_italy = datetime.now(ZoneInfo("Europe/Rome"))
    output = {
        "aggiornato":   now_italy.strftime("%d/%m/%Y %H:%M"),
        "comune":       comune,
        "distanzaKm":   distanza,
        "totale":       len(unici),
        "annunci":      unici,
    }

    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n[+] Salvati {len(unici)} annunci in {OUTPUT}")
    print(f"    Aggiornato: {output['aggiornato']}")


async def main(comune: str, distanza: int, headless: bool):
    print(f"=== JobUp Scraper ===")
    print(f"Comune: {comune} | Distanza: {distanza} km | Headless: {headless}")
    print("=" * 40)

    annunci_raw = await scrapa(comune, distanza, headless)

    if not annunci_raw:
        print("[!] Nessun annuncio trovato.")
        return

    salva_json(annunci_raw, comune, distanza)


if __name__ == "__main__":
    # Parametri: da argomenti CLI oppure da variabili d'ambiente (GitHub Actions)
    parser = argparse.ArgumentParser()
    parser.add_argument("--comune",   default=os.environ.get("JOBUP_COMUNE", "Verbania"))
    parser.add_argument("--distanza", type=int, default=int(os.environ.get("JOBUP_DISTANZA", "50")))
    parser.add_argument("--visible",  action="store_true")
    args = parser.parse_args()

    asyncio.run(main(
        comune   = args.comune,
        distanza = args.distanza,
        headless = not args.visible,
    ))
