# Catalogo espositori Smart Building Levante — mirror per ElevenLabs

Genera una pagina HTML statica con **tutti gli espositori** pubblicati sul catalogo ufficiale, poi la ospita gratuitamente su GitHub Pages. ElevenLabs può acquisire l'URL come normale documento web, con auto-sync giornaliero. **Non servono chiavi API di ElevenLabs.**

## Attivazione

1. Crea un repository GitHub **pubblico** (es. `catalogo-smartbuilding`). Carica **tutti** i file di questa cartella, compresa `.github/workflows/aggiorna-catalogo.yml`. Con Git CLI puoi usare `git init`, `git add .`, `git commit` e `git push`.
2. Su GitHub apri **Settings → Pages → Build and deployment → Source** e seleziona **GitHub Actions**.
3. In **Settings → Actions → General**, assicurati che le Actions siano abilitate e che **Workflow permissions** consenta **Read and write permissions** (il workflow include anche `permissions: contents: write`).
4. Apri **Actions → Aggiorna catalogo espositori → Run workflow**. Controlla che tutti i passaggi siano verdi. Se fallisce il recupero nonce, consulta la sezione 'Problemi noti'.
5. Visita `https://TUO-UTENTE.github.io/catalogo-smartbuilding/` (sostituisci utente/repository); verifica che il numero di espositori sia corretto e che ci siano le ultime aziende nell'HTML.
6. In ElevenLabs, nella **Knowledge Base** dell'agente, aggiungi un documento **URL** con l'indirizzo GitHub Pages. Abilita **Auto-sync ogni giorno** se disponibile nel tuo piano/interfaccia. Verifica che il documento sia associato all'agente e che il recupero delle schede funzioni.
7. Prova domande su aziende che si trovavano nell'ultima pagina del catalogo, non soltanto sulle prime 12.

## Pianificazione

Il workflow gira **ogni giorno alle 04:17 UTC** (06:17 in Italia con ora legale; 05:17 con ora solare). Può essere avviato anche manualmente. Se ElevenLabs consente di scegliere un orario, imposta il crawling dopo la pubblicazione GitHub Pages, ad esempio in tarda mattinata italiana. La frequenza giornaliera rimane attiva anche dopo l'evento finché non cambi il cron. Se il catalogo non è cambiato, `index.html` rimane identico; `last-check.txt` documenta il controllo riuscito e genera attività sul repository. Nei repository pubblici GitHub può disattivare i cron dopo 60 giorni senza attività: controlla periodicamente la scheda Actions.

## Protezioni

- Il nonce WordPress è cercato automaticamente nell'HTML e nei file JavaScript dello stesso dominio, non è codificato nello script.
- Il server è interrogato con `per_page=200` e sono seguite tutte le pagine se necessarie.
- Il totale delle schede deve coincidere con `found`, senza ID duplicati.
- Per impostazione predefinita sono necessarie almeno **100 aziende** (`MIN_EXPECTED_EXHIBITORS`). Se il catalogo diminuisce legittimamente sotto 100, modifica la variabile.
- Se la sincronizzazione fallisce, lo script restituisce un errore: GitHub **non pubblica** un nuovo catalogo e mantiene l'ultima versione riuscita.
- Pubblica solo dati resi pubblici dalla pagina ufficiale; cita e collega sempre la fonte. Verifica di avere il permesso di ripubblicare integralmente il catalogo.

## Problemi noti e risoluzione

- **Nonce non trovato/accettato:** lo script tenta di individuarlo in JS/HTML, ma la generazione effettiva del sito non è stata verificata da questo ambiente. Apri **Actions → ultimo run → Scarica e valida il catalogo**. Se non funziona, ispeziona la pagina originale per vedere dove è definito `fe_filter` / `nonce` e adatta `nonce_candidates` o `locate_nonces`. Non sostituire il nonce con un valore fisso destinato a scadere.
- **403/429 dal sito:** possibili protezioni anti-bot, limiti o blocco degli IP GitHub. In tal caso servirà un'alternativa (ad esempio recupero da un ambiente autorizzato).
- **Pagina di placeholder:** non collegare ElevenLabs finché il primo workflow non ha pubblicato il catalogo reale.
- **Conflitto tra aggiornamenti:** il workflow imposta una concurrency group per impedire aggiornamenti paralleli.

## Avvio locale

```bash
python -m pip install -r requirements.txt
python scraper.py --output index.html
```

L'endpoint di origine è `https://www.smartbuildinglevante.it/mibalevante/wp-admin/admin-ajax.php` con POST `action=fe_filter`, `paged=1`, `per_page=200`, `orderby=title`, `order=ASC`, `lang=it` e nonce temporaneo.
