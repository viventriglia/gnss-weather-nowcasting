# GNSS Weather

Prototipo per ricavare vapore acqueo atmosferico e indicatori di possibile
precipitazione dalle osservazioni GNSS. Il progetto separa volutamente due
problemi diversi:

1. stimare con precisione il ritardo troposferico zenitale (ZTD);
2. trasformare ZTD in variabili meteorologiche e, dopo una calibrazione locale,
   in probabilità di pioggia.

## Installazione

Il progetto usa l'ambiente Poetry locale già presente:

```powershell
poetry install
```

## Esempio completo

Il campione iniziale usa la stazione EUREF `MATE00ITA` (Matera) dal 1 al 3
giugno 2025. Il download di OBS e NAV broadcast è delegato a PyTECGg:

```powershell
poetry run python -m gnss_weather.download_rinex `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-03
```

Per scaricare tutti i prodotti ausiliari:

```powershell
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-03 --complete
```

Il dataset `--complete` comprende:

- orbite SP3 e clock IGS finali GPS a 30 secondi;
- orbite, clock, bias OSB, ERP e attitude ORBEX CODE MGEX multi-GNSS;
- ERP combinato IGS settimanale e modello di antenna IGS20 ANTEX;
- griglie VMF3 1x1 gradi ogni 6 ore e orografia ellissoidica ufficiale;
- ZTD finale IGS a 5 minuti, quando pubblicato, come riferimento indipendente;
- metadati BKG della stazione, inclusi coordinate e storico apparati.

Per controllare gli URL senza scrivere dati, aggiungere `--dry-run`:

```powershell
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-03 `
  --complete --dry-run
```

I download sono atomici e idempotenti: i file parziali hanno suffisso `.part` e
i prodotti già presenti vengono saltati.

## Dallo ZTD al contenuto di vapore

La pipeline atmosferica attualmente eseguibile usa i SINEX TRO IGS scaricati
come sorgente di ZTD. Questo permette di verificare subito tutta la parte meteo,
senza confondere eventuali errori del futuro solver PPP con quelli della
conversione atmosferica:

```powershell
poetry run python -m gnss_weather.analyze_troposphere `
  --surface-temperature-c 20
```

Il valore di 20 °C è soltanto dimostrativo. Il risultato è
`data/derived/atmosphere.csv`; per i due giorni in cui IGS ha pubblicato il TRO
di MATE si ottengono 576 campioni a cinque minuti. Le colonne principali sono:

- `ztd_m`: ritardo totale osservato;
- `ztd_source`: provenienza dello ZTD (`igs_final_tro` oppure
  `rtklib_ppp_bias_corrected`);
- `zhd_m`: parte idrostatica, ricavata da VMF3 o dalla pressione locale;
- `zwd_m = ztd_m - zhd_m`: parte dovuta soprattutto al vapore acqueo;
- `pwv_mm`: acqua precipitabile nella colonna atmosferica;
- `rain_potential`: indice esplorativo fra 0 e 1;
- `rain_category`: classificazione qualitativa dell'indice.

Per generare il grafico temporale di ZTD/ZHD, ZWD/PWV e indice:

```powershell
poetry run python -m gnss_weather.plot_atmosphere
```

La figura viene salvata in `data/derived/atmosphere.png`. Percorsi, titolo e
risoluzione possono essere cambiati con `--input`, `--output`, `--title` e
`--dpi`.

### Meteo ERA5 tramite Open-Meteo

Per scaricare una serie ERA5 oraria alle coordinate della stazione senza una
chiave API:

```powershell
poetry run python -m gnss_weather.download_open_meteo `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-12
```

Le coordinate vengono ricavate dai metadati della stazione. Il modello e'
fissato esplicitamente a `era5`, evitando che il servizio scelga un modello
diverso in base alla data. Vengono salvati sia il JSON originale di Open-Meteo
sia un CSV normalizzato in `data/meteo/`, contenente temperatura, pressione
superficiale, umidita', dew point, precipitazione, pioggia e acqua colonnare.
Il CSV e' direttamente utilizzabile dalla pipeline:

Su Windows il modulo usa il trust store di Python e, se questo non riconosce il
certificato del proxy locale, ripiega su `curl` mantenendo attiva la verifica
TLS.

```powershell
poetry run python -m gnss_weather.analyze_troposphere `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-12 `
  --meteo-csv data/meteo/MATE00ITA_era5_2025-06-01_2025-06-12.csv `
  --output data/derived/atmosphere_era5_2025-06-01_2025-06-12.csv
```

Open-Meteo restituisce il punto della griglia ERA5 e la sua quota, registrati
in ogni riga. La pressione e la pioggia sono quindi stime di reanalisi alla
scala della griglia, non misure in situ. L'opzione `--elevation-m` consente di
richiedere il downscaling a una quota nota; non va usata direttamente con una
quota GNSS ellissoidica senza convertirne il datum verticale.

La relazione fisica implementata è:

```text
ZTD = ZHD + ZWD
PWV = Pi(Tm) * ZWD
```

Lo ZHD usa la formula di Saastamoinen. Per la PWV, la temperatura media pesata
dell'atmosfera `Tm` viene approssimata dalla temperatura superficiale con la
relazione di Bevis. Se temperatura e pressione misurate sono disponibili è
preferibile fornire un CSV:

```csv
epoch_utc,pressure_hpa,temperature_c,rain_mm
2025-06-01T00:00:00Z,986.4,18.2,0.0
2025-06-01T00:10:00Z,986.3,18.1,0.0
```

```powershell
poetry run python -m gnss_weather.analyze_troposphere `
  --meteo-csv data/meteo/MATE00ITA.csv
```

Pressione e temperatura vengono interpolate alle epoche GNSS. `rain_mm` è
opzionale e servirà come etichetta per addestrare e validare il modello locale;
non viene usato dall'indice dimostrativo.

### Che cosa significa «possibile pioggia»

GNSS non misura direttamente le gocce sulla testa del ricevitore: misura il
vapore integrato lungo molti cammini satellite-ricevitore. Un PWV elevato è una
condizione favorevole, non sufficiente, alla pioggia. L'indice incluso combina
il percentile locale di ZWD e la sua crescita nell'ultima ora. È trasparente e
utile per esplorare i dati, ma **non è ancora una probabilità calibrata né una
stima di mm/h**. Tre giorni non costituiscono una climatologia.

Per arrivare a una stima difendibile della pioggia sulla stazione occorre:

1. raccogliere almeno una stagione, meglio uno o più anni, di ZTD/PWV;
2. associare un pluviometro co-localizzato o radar meteo a 5–10 minuti;
3. definire un bersaglio, per esempio `pioggia > 0.1 mm nei prossimi 60 min`;
4. costruire feature con PWV/ZWD, variazioni a 1 e 3 ore, anomalia stagionale,
   gradienti troposferici, pressione, temperatura e loro tendenze;
5. separare training e test per blocchi temporali, mai con righe casuali;
6. calibrare la probabilità e valutarla con Brier score, reliability diagram e
   precision-recall, confrontandola con climatologia e persistenza.

Il pluviometro fornisce la verità puntuale; il radar aiuta a distinguere una
colonna umida senza precipitazione da una cella in arrivo. Per nowcasting
operativo conviene fondere entrambi con il segnale GNSS.

## ZTD direttamente dai RINEX con RTKLIB

PyTECGg va benissimo per download e parsing. Le sole effemeridi broadcast NAV,
però, normalmente non bastano per isolare uno ZTD con accuratezza millimetrica o
centimetrica: l'errore orbitale e di clock finirebbe in parte nei parametri
troposferici. Per questo il downloader prepara anche SP3, CLK, OSB, ANTEX, ERP e
attitude precise.

È disponibile un primo solver containerizzato basato su RTKLIB 2.4.3-b34,
compilato dal sorgente C ufficiale fissato al commit
`180043ee24b6d2b168f98b64be15f69d50046b1a`. Il profilo corrente è PPP-static
float, GPS+Galileo dual-frequency ionosphere-free, orbite e clock finali CODE
MGEX, ERP, IGS20 ANTEX, maree IERS, ZTD e gradienti troposferici stimati.
Hatanaka 2.8.1 converte il
RINEX `.crx.gz` all'interno del container.

Il binario C e le dipendenze restano fissati nell'immagine; il runner Python
viene montato dal progetto. Modificare costellazioni e maschera di elevazione
non richiede quindi di ricostruire il container.

La prima volta costruire l'immagine ed eseguire un giorno:

```powershell
poetry run python -m gnss_weather.run_rtklib_ppp `
  --station MATE00ITA --day 2025-06-01 --build `
  --systems gps,galileo --elevation-mask 10
```

Le volte successive omettere `--build`:

```powershell
poetry run python -m gnss_weather.run_rtklib_ppp `
  --station MATE00ITA --day 2025-06-02 `
  --systems gps,galileo --elevation-mask 10
```

Ogni elaborazione produce sotto `data/ppp/STATION/YYYY-MM-DD/`:

- `solution.pos`: soluzione PPP RTKLIB;
- `solution.pos.stat`: stati interni, incluse righe `$TROP`;
- `ztd.csv`: serie normalizzata UTC di ZTD e sigma;
- `rtklib.conf` e `rtklib.log`: configurazione e log riproducibili.

Per trasformare lo ZTD PPP in ZWD/PWV e generare il grafico, scartando le prime
quattro ore di convergenza del filtro forward:

```powershell
poetry run python -m gnss_weather.analyze_troposphere `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-01 `
  --ztd-csv data/ppp/MATE00ITA/2025-06-01/ztd.csv `
  --discard-first-hours 4 --surface-temperature-c 20 `
  --output data/derived/atmosphere_ppp_2025-06-01.csv

poetry run python -m gnss_weather.plot_atmosphere `
  --station MATE00ITA `
  --input data/derived/atmosphere_ppp_2025-06-01.csv `
  --output data/derived/atmosphere_ppp_2025-06-01.png `
  --title "MATE00ITA - atmosfera da ZTD RTKLIB PPP"
```

Sul 1 giugno 2025 si ottengono 2880 stime a 30 secondi. Dopo quattro ore di
convergenza, il confronto con il TRO IGS dà circa +11.1 mm di bias, 8.4 mm di
deviazione standard e 13.9 mm di RMSE. È un risultato promettente da prototipo,
non ancora una soluzione geodetica operativa: vanno verificati altri giorni,
coordinate a epoca, boundary giornalieri, loading oceanico e strategia
forward/backward o multi-day.

Per esportare e visualizzare separatamente più giorni di ZTD RTKLIB PPP e IGS
TRO, preservando i gap dei prodotti IGS mancanti:

```powershell
poetry run python -m gnss_weather.compare_ztd_sources `
  --station MATE00ITA --start 2026-02-03 --end 2026-02-06 `
  --output data/derived/ztd_2026-02-03_2026-02-06
```

Il comando produce due CSV, due grafici individuali, un grafico a pannelli e un
JSON di validazione. Le prime quattro ore di ciascun PPP giornaliero rimangono
nel CSV con `converged=False` e sono mostrate in grigio, ma vengono escluse dalle
statistiche contro IGS.

Il solver stima:

- clock del ricevitore a ogni epoca;
- ZWD come processo random walk (ZHD vincolato da pressione/VMF3);
- ambiguità per arco continuo e, se i dati lo consentono, gradienti N/E;
- correzioni antenna, relativistiche, solid Earth tide, phase wind-up,
  ocean loading e mapping VMF3.

La prima prova con coordinate BKG fissate ha mostrato che differivano di 17.8 cm
da quelle nel RINEX 2025, generando un forte bias troposferico. Per questo il
profilo corrente stima coordinate statiche. Il TRO IGS resta il riferimento per
bias, RMS, drift e validazione prima di usare lo ZTD per la meteorologia.

### TRO primario e PPP per i buchi

Quando il TRO IGS esiste, il PPP locale non viene usato nella serie finale. Il
modulo ibrido usa TRO con priorita', stima il bias RTKLIB sui giorni di
sovrapposizione e inserisce PPP corretto soltanto nei giorni senza TRO:

```powershell
poetry run python -m gnss_weather.build_hybrid_ztd `
  --station MATE00ITA --start 2026-03-01 --end 2026-03-31 `
  --output data/derived/march_2026
```

Il JSON prodotto include una validazione leave-one-day-out: ogni giorno PPP
viene trattato a turno come se il TRO mancasse, mentre il bias viene stimato
sugli altri giorni. Le prime quattro ore del PPP forward non vengono usate.

Il fallback puo' anche preparare ed eseguire RTKLIB automaticamente. Prima si
tenta il download dei TRO per tutto l'intervallo; poi il comando ibrido scarica
RINEX e prodotti CODE MGEX ed esegue il PPP GPS+Galileo **solo** per i giorni
che risultano privi del TRO:

```powershell
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2026-04-01 --end 2026-04-30 --tro-only

poetry run python -m gnss_weather.build_hybrid_ztd `
  --station MATE00ITA --start 2026-04-01 --end 2026-04-30 `
  --run-ppp-for-missing --elevation-mask 10 `
  --bias-report data/derived/march_2026/gapfill_validation_2026-03-01_2026-03-31.json `
  --output data/derived/april_2026
```

`--bias-report` rende esplicita e riproducibile la correzione
`RTKLIB_PPP - IGS_TRO`. In alternativa si puo' passare
`--bias-correction-mm`. Se nell'intervallo esistono gia' giorni elaborati sia
con PPP sia con TRO, la correzione viene stimata automaticamente da quelle
sovrapposizioni. Senza una correzione disponibile il programma conserva il gap:
non inserisce uno ZTD PPP non calibrato nella serie TRO.

Per ricavare PWV e grafico dalla serie ibrida:

```powershell
poetry run python -m gnss_weather.download_open_meteo `
  --station MATE00ITA --start 2026-04-01 --end 2026-04-30

poetry run python -m gnss_weather.analyze_troposphere `
  --station MATE00ITA --start 2026-04-01 --end 2026-04-30 `
  --ztd-csv data/derived/april_2026/ztd_hybrid_2026-04-01_2026-04-30.csv `
  --meteo-csv data/meteo/MATE00ITA_era5_2026-04-01_2026-04-30.csv `
  --output data/derived/april_2026/atmosphere_tro_era5_2026-04.csv

poetry run python -m gnss_weather.plot_atmosphere `
  --station MATE00ITA `
  --input data/derived/april_2026/atmosphere_tro_era5_2026-04.csv `
  --output data/derived/april_2026/atmosphere_tro_era5_2026-04.png
```

PyTECGg e' usato dal comando `download_rinex` per scaricare osservazioni EUREF
e navigazione broadcast BKG. Il parsing SINEX TRO, i prodotti ERA5 e il PPP non
passano invece da PyTECGg: il RINEX del PPP viene letto direttamente dal codice
C di RTKLIB.

Per scaricare soltanto i prodotti necessari, senza il dataset `--complete`:

```powershell
# Solo TRO IGS e metadati della stazione
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2026-03-01 --end 2026-03-31 --tro-only

# SP3, CLK ed ERP CODE MGEX per GPS+Galileo
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2026-03-02 --end 2026-03-02 --mgex-core
```

Una prova su MATE del 2 marzo 2026 ha mostrato che una maschera alta non e'
adatta alla stima ZTD con questo profilo:

| Maschera | Epoche PPP | RMSE contro TRO dopo 4 h |
| ---: | ---: | ---: |
| 10 gradi | 1999 | 20.0 mm |
| 15 gradi | 1707 | 26.6 mm |
| 20 gradi | 1501 | 35.9 mm |
| 25 gradi | 1245 | 53.9 mm |
| 30 gradi | 886 | 69.1 mm |

I link bassi sono piu' esposti al multipath, ma sono anche molto sensibili alla
troposfera e aiutano a separare ZTD e quota. Per questo il default resta 10
gradi; contro il multipath sono preferibili pesi dipendenti da elevazione/SNR e
controllo dei residui anziche' un cutoff a 25-30 gradi.

### Osservazioni meteorologiche RINEX

In questo intervallo EUREF non pubblica RINEX meteorologici per `MATE00ITA`.
VMF3 viene quindi usato come a priori idrostatico, ma non equivale a un sensore
di pressione in situ. Per stazioni che pubblicano il prodotto:

```powershell
poetry run python -m gnss_weather.download_products `
  --station STATION_ID --start 2025-06-01 --end 2025-06-03 --meteo
```

### Prodotti minimi solo GPS

Senza `--complete` vengono scaricati soltanto SP3/CLK IGS GPS, ERP e ANTEX:

```powershell
poetry run python -m gnss_weather.download_products `
  --station MATE00ITA --start 2025-06-01 --end 2025-06-03
```

Per eseguire i test:

```powershell
poetry run python -m unittest discover -v
```

## Dataset ML per pioggia nell'ora successiva

Il notebook [01_build_hourly_rain_dataset.ipynb](notebooks/01_build_hourly_rain_dataset.ipynb)
costruisce prima le feature sulla serie TRO nativa a cinque minuti e produce
poi un dataset orario senza leakage rispetto alla pioggia ERA5. Il target vale
uno quando la pioggia ERA5 nell'ora seguente e' strettamente maggiore di
0.0 mm. Le feature temporali includono seno/coseno di ora e giorno dell'anno,
oltre al solar zenith angle calcolato dalle coordinate della stazione. Per
avviarlo:

```powershell
poetry run jupyter lab notebooks/01_build_hourly_rain_dataset.ipynb
```

Lo stesso processo e' disponibile senza interfaccia grafica:

```powershell
poetry run python -m gnss_weather.ml_dataset `
  --station MATE00ITA --start 2024-07-01 --end 2026-08-20 `
  --era5 data/meteo/MATE00ITA_era5_2024-07-01_2026-08-20.csv `
  --rain-threshold-mm 0.0 `
  --output data/derived/ml/MATE00ITA_rain_hourly_2024-07-01_2026-08-20.csv
```

Il JSON accanto al CSV documenta colonne usate dal modello, copertura TRO,
soglia del target e politica anti-leakage.

Il notebook successivo riserva marzo-aprile 2026 al test ed esegue sei fold
walk-forward sul periodo precedente, escludendo tutti i dati successivi al
test. Due studi Optuna separati, senza class weighting per entrambi i modelli,
massimizzano F1 per il CatBoost decisionale e minimizzano Brier score per il
CatBoost probabilistico raw. Il test riporta F1, precision, recall, Brier e
Brier skill rispetto alla climatologia mensile storica, insieme al reliability
diagram raw. Il numero di trial e la geometria dei fold si configurano nelle
prime celle del notebook:

```powershell
poetry run jupyter lab notebooks/02_catboost_rain_forecasting.ipynb
```

I modelli nativi vengono salvati in `data/models/`; metriche per fold,
predizioni del test e tabelle complete dei trial Optuna in `data/derived/ml/`.
La calibrazione e l'export ONNX-ML sono lasciati ai passaggi successivi.

## Calibrazione Venn-Abers

Il notebook [03_venn_abers_calibration.ipynb](notebooks/03_venn_abers_calibration.ipynb)
applica Venn-Abers 1.5.4 come solo post-processing del CatBoost probabilistico.
Confronta calibrazione statica e rolling causale; sceglie tra finestre di 30,
60 e 90 giorni sui sei fold walk-forward storici, senza usare aprile 2026, che
resta il test. Il modello base, le feature e gli studi Optuna del notebook 02
non vengono modificati.

```powershell
poetry run jupyter lab notebooks/03_venn_abers_calibration.ipynb
```

Per rieseguirlo senza interfaccia grafica:

```powershell
poetry run jupyter nbconvert `
  --to notebook --execute --inplace `
  notebooks/03_venn_abers_calibration.ipynb
```

Il replay aggiorna il calibratore ogni giorno usando solo target già osservati;
se una finestra contiene meno di 20 esempi per classe, passa alla finestra più
ampia successiva. Il notebook confronta Brier, Brier skill, log-loss ed ECE per
CatBoost raw, Venn-Abers statico e rolling, mostra il reliability diagram e
salva anche `p0`/`p1`. Il warm-up è salvato in NPZ con score, target e timestamp.

## Scaricare ed elaborare altri N giorni

L'intervallo è inclusivo: per `N` giorni la data finale è `start + N - 1`.
Questo esempio scarica ed elabora sette giorni per MATE, mantenendoli in file
derivati separati:

```powershell
$STATION = "MATE00ITA"
$START_DATE = [datetime]"2025-06-04"
$N = 7
$START = $START_DATE.ToString("yyyy-MM-dd")
$END = $START_DATE.AddDays($N - 1).ToString("yyyy-MM-dd")

poetry run python -m gnss_weather.download_rinex `
  --station $STATION --start $START --end $END

poetry run python -m gnss_weather.download_products `
  --station $STATION --start $START --end $END --complete

poetry run python -m gnss_weather.analyze_troposphere `
  --station $STATION --start $START --end $END `
  --surface-temperature-c 20 `
  --output "data/derived/atmosphere_${START}_${END}.csv"

poetry run python -m gnss_weather.plot_atmosphere `
  --station $STATION `
  --input "data/derived/atmosphere_${START}_${END}.csv" `
  --output "data/derived/atmosphere_${START}_${END}.png"
```

Sostituire la temperatura costante con `--meteo-csv` quando sono disponibili
misure locali. I prodotti finali e i TRO non sono disponibili in tempo reale e
alcuni giorni/stazioni possono mancare; per questi giorni si puo' usare il
fallback PPP descritto sopra.
