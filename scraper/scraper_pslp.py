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
    id_raw = raw.get("idAnnuncio") or raw.get("id")
    try:
        # Teniamo l'ID come numero se possibile per coerenza, ma lo normalizziamo in stringa per la webapp se necessario
        id_annuncio = int(id_raw) if id_raw else ""
    except:
        id_annuncio = str(id_raw) if id_raw else ""

    # 1. Recupero Azienda con logica raffinata
    # Priorità: campo azienda esplicito -> denominazione in idAziAnagrafica -> null
    azienda = raw.get("azienda")
    if not azienda:
        anag = raw.get("idAziAnagrafica")
        if isinstance(anag, dict):
            azienda = anag.get("denominazione")

    # Se ancora null, fallback su intermediario (CPI)
    if not azienda:
        intermed = raw.get("idIntermediario")
        if isinstance(intermed, dict):
            azienda = intermed.get("dsIntermediario")
        else:
            azienda = raw.get("dsIntermediario") or ""

    comune = raw.get("descrComuneSede")
    provincia = raw.get("descrProvinciaSede")
    contratto = raw.get("contratto")
    qualifica = raw.get("qualifica")
    profilo = raw.get("dsProfiloIstat")

    # Se mancano, prova a cercarli nelle liste del dettaglio (arricchimento)
    cond_list = raw.get("condLavorativaOffertaList", [])
    if cond_list and isinstance(cond_list, list) and len(cond_list) > 0:
        cond = cond_list[0]
        if not comune:
            sede = cond.get("idComuneSedeLavoro", {})
            comune = sede.get("dsComune")
            if not provincia:
                prov = sede.get("idProvincia", {})
                provincia = prov.get("dsSiglaProvincia") or prov.get("dsProvincia")
        if not contratto or contratto == "–":
            rapp = cond.get("idTipoRapportoLavoro", {})
            contratto = rapp.get("descrTipoRapportoLavoro")

    prof_list = raw.get("profiloRicercatoList", [])
    if prof_list and isinstance(prof_list, list) and len(prof_list) > 0:
        prof = prof_list[0]
        if not qualifica:
            qualifica = prof.get("dsQualifica")
        if not profilo:
            q_istat = prof.get("blpDQualifica", {})
            profilo = q_istat.get("descrQualifica")

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
        "numAnnuncio":     raw.get("numAnnuncio") or "",
        "titolo":          (raw.get("titoloVacancy") or "").strip().title(),
        "azienda":         (azienda or "").strip().title(),
        "comune":          (comune or "").strip().title(),
        "provincia":       (provincia or "").strip().title(),
        "cpi":             (raw.get("descrCpi") or raw.get("dsIntermediario") or "").strip().title(),
        "contratto":       (contratto or "–").strip().title(),
        "profilo":         (profilo or "").strip().title(),
        "qualifica":       (qualifica or "").strip(),
        "categoria":       categoria,
        "stato":           (raw.get("stato") or "").strip(),
        "dataScadenza":    data_scad_fmt,
        "dataPubblicazione": data_stato_fmt,
        "mapsUrl":         raw.get("mapsUrls") or "",
        "urlFonte":        f"{DETAIL_URL}?id={id_annuncio}",
    }


async def scrapa(comune_search: str, distanza: int, headless: bool) -> list:
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

        # Intercetta richiesta E risposta per catturare headers e payload di sessione
        api_params = {
            "trovato":  False,
            "url_base": None,
            "headers":  {},
            "payload":  None,
        }

        def on_request(request):
            if "annunci-pslp/consulta-annunci" in request.url and request.method == "POST":
                # Salva gli headers e il payload della prima richiesta
                api_params["url_base"] = request.url.split("?")[0]
                api_params["headers"]  = dict(request.headers)
                api_params["payload"]  = request.post_data_json
                if not api_params["trovato"]:
                    print(f"[+] Payload intercettato: {api_params['payload']}")

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
        print(f"[*] Seleziono comune: {comune_search}")
        await page.click("p-dropdown", timeout=10000)
        await asyncio.sleep(1)
        await page.keyboard.type(comune_search, delay=100)
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
        await asyncio.sleep(1.5)

        # ── Campo DISTANZA ───────────────────────────────────────────
        print(f"[*] Inserisco distanza: {distanza} km")
        await page.wait_for_selector("#rangeKM:not([disabled])", timeout=15000)
        await page.click("#rangeKM")
        await page.keyboard.press("Control+a")
        await page.keyboard.type(str(distanza))
        await asyncio.sleep(0.5)

        # ── CERCA ────────────────────────────────────────────────────
        print("[*] Clicco CERCA...")
        await page.wait_for_selector('button:has-text("CERCA"):not([disabled])', timeout=10000)
        await page.click('button:has-text("CERCA")')

        # Aspetta prima pagina (100 risultati)
        await page.wait_for_load_state("networkidle", timeout=30000)
        await asyncio.sleep(4)

        # ── Funzione di arricchimento (usata per lista iniziale e paginazione) ──
        async def arricchisci_batch(items_list: list, headers: dict):
            # Identifica gli incompleti (manca azienda o comune o provincia)
            mancanti = [i for i in items_list if not i.get("azienda") or not i.get("descrComuneSede")]
            if not mancanti:
                return

            print(f"[*] Arricchimento di {len(mancanti)} annunci...")
            sem = asyncio.Semaphore(5) # Concorrenza limitata

            async def fetch_detail(item):
                id_an = item.get("idAnnuncio")
                if not id_an: return
                async with sem:
                    try:
                        det_url = f"{BASE_URL}/pslpbff/api-public/v1/annunci-pslp/get-dettaglio/{id_an}"
                        # Il dettaglio richiede POST con body vuoto o {}
                        resp = await page.request.post(det_url, headers=headers, data={}, timeout=15000)
                        if resp.status == 200:
                            res_json = await resp.json()
                            det_data = res_json.get("annuncio", {})
                            item.update(det_data)
                        await asyncio.sleep(0.1)
                    except Exception as e:
                        print(f"    [!] Errore arricchimento {id_an}: {e}")

            await asyncio.gather(*(fetch_detail(i) for i in mancanti))

        # ── Arricchimento dati lista iniziale ──────────────────────────
        if tutti:
            hdrs = dict(api_params.get("headers", {}))
            hdrs["accept"] = "application/json"
            await arricchisci_batch(tutti, hdrs)

        # ── Paginazione: usa page.request con payload intercettato ──────
        if api_params["trovato"] and len(tutti) == 100:
            print("[*] Possibili altre pagine, scarico via API diretta...")
            url_base = api_params.get("url_base") or API_URL
            hdrs = dict(api_params.get("headers", {}))
            hdrs["accept"] = "application/json"
            payload = api_params.get("payload") or {}

            pagina = 1
            while True:
                url_pag = f"{url_base}?page={pagina}&recForPage=100"
                print(f"    Richiedo pagina {pagina}...")
                try:
                    # Usa il payload per mantenere i filtri geografici
                    resp  = await page.request.post(url_pag, headers=hdrs, data=payload, timeout=20000)
                    testo = await resp.text()
                    if resp.status != 200 or not testo.strip():
                        print(f"    HTTP {resp.status} o risposta vuota, fine.")
                        break
                    dati  = json.loads(testo)
                    items = dati.get("list") or dati.get("content") or []
                    if not items:
                        print(f"    Pagina {pagina}: nessun risultato, fine.")
                        break

                    await arricchisci_batch(items, hdrs)
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
    def sort_key(x):
        d_stato = x.get("dataStato") or ""
        # Normalizziamo l'ID come stringa per il confronto sicuro
        id_an = str(x.get("idAnnuncio") or x.get("id") or "0")
        return (d_stato, id_an)

    annunci_raw.sort(key=sort_key, reverse=True)

    annunci = [costruisci_annuncio(a) for a in annunci_raw]

    # Rimuovi eventuali duplicati per id (mantenendo l'ordine)
    visti = set()
    unici = []
    for a in annunci:
        aid = str(a["id"])
        if aid not in visti:
            visti.add(aid)
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
