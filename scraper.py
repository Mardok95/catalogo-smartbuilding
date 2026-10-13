#!/usr/bin/env python3
"""Produce una pagina HTML statica dal catalogo pubblico degli espositori."""
import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

CATALOG_URL = 'https://www.smartbuildinglevante.it/it/catalogo-mibalevante/'
# Ripiego: l'endpoint reale viene letto dalla configurazione feAjax della pagina.
AJAX_URL = 'https://www.smartbuildinglevante.it/wp-admin/admin-ajax.php'
PER_PAGE = 200
MIN_EXPECTED = int(os.getenv('MIN_EXPECTED_EXHIBITORS', '100'))
TIMEOUT = 25
HEADERS = {'User-Agent': 'CatalogoEspositoriSync/1.0 (knowledge-base mirror; public exhibition data)', 'Accept-Language': 'it-IT,it;q=0.9'}


class AccessBlocked(Exception):
    """La richiesta è stata bloccata prima di arrivare a WordPress (WAF, filtro IP)."""


def page_ajax_config(text):
    """Legge endpoint e nonce dall'oggetto feAjax pubblicato nella pagina."""
    m = re.search(r'\bfeAjax\s*=\s*(\{.*?\})\s*;', text, re.S)
    if not m:
        return None, None
    try:
        cfg = json.loads(m.group(1))
    except ValueError:
        return None, None
    url = cfg.get('url') if isinstance(cfg.get('url'), str) else None
    nonce = cfg.get('nonce') if isinstance(cfg.get('nonce'), str) else None
    if url:
        url = urljoin(CATALOG_URL, url)
        if urlparse(url).hostname != urlparse(CATALOG_URL).hostname:
            url = None
    return url, nonce


def nonce_candidates(text):
    """Cerca nonce in vari formati di configurazione JS di WordPress."""
    patterns = [
        r'''["'](?:nonce|fe_nonce|ajax_nonce|security)["']\s*:\s*["']([a-f0-9]{8,16})["']''',
        r'''\b(?:nonce|fe_nonce|ajax_nonce|security)\s*[:=]\s*["']([a-f0-9]{8,16})["']''',
        r'''["'](?:nonce|fe_nonce|ajax_nonce|security)["']\s*,\s*["']([a-f0-9]{8,16})["']''',
        r'''\bnonce\b[^\n]{0,90}?["']([a-f0-9]{8,16})["']'''
    ]
    matches = []
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            context = text[max(0, m.start() - 1000):m.end() + 1000].lower()
            score = (10 if 'fe_filter' in context else 0) + (5 if 'fe_' in context else 0)
            matches.append((score, m.group(1)))
    seen = set()
    return [nonce for _, nonce in sorted(matches, key=lambda x: -x[0]) if not (nonce in seen or seen.add(nonce))]


def locate_nonces(session):
    r = session.get(CATALOG_URL, timeout=TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, 'html.parser')
    ajax_url, page_nonce = page_ajax_config(r.text)
    candidates = ([page_nonce] if page_nonce else []) + nonce_candidates(r.text)
    scripts = []
    for s in soup.select('script[src]'):
        url = urljoin(CATALOG_URL, s.get('src', ''))
        if urlparse(url).hostname != urlparse(CATALOG_URL).hostname:
            continue
        label = url.lower()
        if any(x in label for x in ('fe-', 'filter', 'catalog', 'exhib', 'frontend', 'main', 'custom', 'ajax')):
            scripts.append(url)
    # I nonce spesso sono esposti in un oggetto localizzato nell'HTML;
    # come ripiego controlliamo gli script JS dello stesso dominio.
    if not candidates:
        scripts = list(dict.fromkeys(scripts + [
            urljoin(CATALOG_URL, s.get('src', '')) for s in soup.select('script[src]')
            if urlparse(urljoin(CATALOG_URL, s.get('src', ''))).hostname == urlparse(CATALOG_URL).hostname
        ]))
    for url in dict.fromkeys(scripts[:35]):
        try:
            response = session.get(url, timeout=TIMEOUT)
            response.raise_for_status()
            if len(response.content) > 2_000_000:
                continue
            candidates.extend(nonce_candidates(response.text))
        except requests.RequestException as exc:
            print('Avviso: script non accessibile:', url, str(exc), file=sys.stderr)
    return ajax_url or AJAX_URL, list(dict.fromkeys(candidates))


def ajax_page(session, ajax_url, nonce, page):
    params = {
        'action': 'fe_filter', 'nonce': nonce, 'lang': 'it',
        'paged': str(page), 'per_page': str(PER_PAGE),
        'orderby': 'title', 'order': 'ASC', 'search': ''
    }
    r = session.post(ajax_url, data=params, timeout=TIMEOUT,
                     headers={'Referer': CATALOG_URL, 'Origin': 'https://www.smartbuildinglevante.it'})
    if r.status_code in (400, 403):
        body = r.text.strip()
        # WordPress risponde "-1" (nonce respinto) o "0" (azione non registrata):
        # in questi casi la richiesta è arrivata a destinazione.
        if body == '-1':
            raise ValueError(f'HTTP {r.status_code} da WordPress: nonce respinto per {ajax_url}.')
        if body == '0':
            raise ValueError(f'HTTP {r.status_code} da WordPress: azione fe_filter non registrata su {ajax_url}.')
        if r.status_code == 403:
            snippet = ' '.join(body[:200].split())
            raise AccessBlocked(f'HTTP 403 non generato da WordPress per {ajax_url}: probabile blocco delle richieste '
                                f'(Server: {r.headers.get("Server", "?")}; risposta: {snippet!r}).')
    r.raise_for_status()
    payload = r.json()
    if not isinstance(payload, dict) or payload.get('success') is not True:
        raise ValueError('Il server AJAX ha rifiutato la richiesta (nonce non valido o cambiamento del sito).')
    data = payload.get('data')
    if not isinstance(data, dict) or not isinstance(data.get('html'), str):
        raise ValueError('Risposta JSON senza data.html.')
    return data


def extract_cards(fragment):
    soup = BeautifulSoup(fragment, 'html.parser')
    cards = {}
    def field(card, selector):
        el = card.select_one(selector)
        return el.get_text(' ', strip=True) if el else ''
    for card in soup.select('article.fe-card'):
        key = card.get('data-id', '').strip()
        name = field(card, '.fe-card-title')
        if not key or not name:
            raise ValueError('Scheda azienda priva di ID o nome.')
        sectors = [e.get_text(' ', strip=True) for e in card.select('.fe-card-tags .fe-tag:not(.fe-tag-zone)')]
        record = {
            'name': name, 'position': field(card, '.fe-meta-stand'),
            'location': field(card, '.fe-meta-location'),
            'country': field(card, '.fe-meta-country'),
            'sectors': '; '.join(filter(None, sectors)),
            'area': field(card, '.fe-tag-zone'),
            'phone': field(card, '.fe-contact-phone'),
            'email': field(card, '.fe-contact-email'),
            'website': field(card, '.fe-contact-web'),
            'notes': field(card, '.fe-marchi-full')
        }
        if key in cards:
            raise ValueError('ID azienda duplicato all\'interno della pagina: ' + key)
        cards[key] = record
    return cards


def fetch_all(session):
    ajax_url, nonces = locate_nonces(session)
    print('Endpoint AJAX:', ajax_url)
    if not nonces:
        raise RuntimeError('Nonce non trovato nell\'HTML o negli script WordPress. Controlla la pagina e aggiorna i pattern di locate_nonces().')
    first = None
    for candidate in nonces:
        try:
            attempt = ajax_page(session, ajax_url, candidate, 1)
            if extract_cards(attempt['html']):
                first = attempt
                break
        except (ValueError, requests.RequestException) as exc:
            print('Nonce candidato respinto:', str(exc), file=sys.stderr)
    if first is None:
        raise RuntimeError('Nessun nonce trovato è stato accettato dall\'endpoint AJAX.')
    found = int(first['found'])
    pages = int(first['max_pages'])
    if found < MIN_EXPECTED or pages < 1 or pages > 100:
        raise ValueError(f'Conteggi sospetti: found={found}, max_pages={pages}. Pubblicazione annullata.')
    records = {}
    for p in range(1, pages + 1):
        data = first if p == 1 else ajax_page(session, ajax_url, candidate, p)
        extracted = extract_cards(data['html'])
        overlap = records.keys() & extracted.keys()
        if overlap:
            raise ValueError(f'Duplicati tra pagine, ad es. {next(iter(overlap))}')
        records.update(extracted)
        print(f'Pagina {p}/{pages}: {len(extracted)} aziende, totale {len(records)}')
        if p < pages:
            time.sleep(0.4)
    if len(records) != found:
        raise ValueError(f'Risultato incompleto: {len(records)} aziende ottenute, {found} attese. Nessun aggiornamento.')
    return sorted(records.values(), key=lambda r: r['name'].casefold())


def build_html(records):
    e = lambda x: html.escape(str(x), quote=True)
    lines = [
        '<!doctype html>', '<html lang="it">', '<head>',
        '<meta charset="utf-8">', '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex,nofollow">',
        '<title>Catalogo espositori Smart Building Levante</title>',
        '<style>body{font:17px/1.6 system-ui,sans-serif;max-width:900px;margin:auto;padding:30px 18px;color:#172334}article{padding:1.2rem 0;border-top:1px solid #ddd}h1,h2{line-height:1.25}h2{margin-bottom:.4rem}article p{margin:0 0 .35rem}</style>',
        '</head>', '<body>', '<main>',
        '<h1>Catalogo espositori Smart Building Levante</h1>',
        '<p>Trascrizione testuale del catalogo pubblico per consultazione e indicizzazione. '
        'I dati possono variare: verificare sempre sulla fonte ufficiale: ' + e(CATALOG_URL) + '</p>',
        f'<p><strong>Espositori:</strong> {len(records)}.</p>',
    ]
    fields = [('position', 'Padiglione e stand'), ('location', 'Indirizzo'),
              ('country', 'Paese'), ('sectors', 'Settori merceologici'),
              ('area', 'Area espositiva'), ('phone', 'Telefono'),
              ('email', 'Email'), ('website', 'Sito web'), ('notes', 'Note')]
    for rec in records:
        lines.append('<article>')
        lines.append(f'<h2>{e(rec["name"])}</h2>')
        # Solo titoli e paragrafi: gli estrattori di testo per RAG scartano
        # spesso le liste di definizione (dl/dt/dd) e il testo dei link.
        for key, label in fields:
            if rec[key]:
                lines.append(f'<p><strong>{e(label)}:</strong> {e(rec[key])}</p>')
        lines.append('</article>')
    lines.extend(['</main>', '</body>', '</html>'])
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', default='index.html')
    args = parser.parse_args()
    session = requests.Session()
    session.headers.update(HEADERS)
    records = fetch_all(session)
    result = build_html(records)
    target = Path(args.output)
    if target.exists() and target.read_text(encoding='utf-8') == result:
        print('Catalogo invariato, nessuna modifica a index.html.')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(result, encoding='utf-8')
        print(f'Creato {target}: {len(records)} aziende.')
    Path('last-check.txt').write_text('Ultimo controllo riuscito (UTC): ' + datetime.now(timezone.utc).isoformat() + '\n', encoding='utf-8')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('ERRORE: ' + str(exc), file=sys.stderr)
        sys.exit(1)
