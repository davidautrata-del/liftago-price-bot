# Liftago price bot: kontext pro Cursor

Otevři ve Cursoru složku `~/Documents/liftago-price-bot` a řekni mu: "Přečti CURSOR_CONTEXT.md a pokračuj podle sekce Co zbývá."

## Cíl
Bot 10x denně (09:30–17:00, rovnoměrně) zadá v appce **Liftago** (iPhone appka z App Store, běží na Macu s Apple Silicon) kategorii **"Odvoz vozu"** a náhodnou pražskou trasu 1–20 km. Přečte cenu, ETA a ceny nabídek řidičů a zapíše řádek do Google Sheetu.

## Pravidla (nesmí se porušit)
- Bot **nikdy neklikne na "Objednat"**. Poptávku po přečtení nabídek zruší (Zavřít → Zrušit jízdu).
- **Žádná tajemství v kódu, příkazech ani v chatu.** `SHEET_WEBHOOK_URL` (tajná adresa Apps Script webhooku) je jen v `.env`. Soubor `.env` nevypisuj ani nevkládej do promptů. Když se někde objeví tajná URL, vyměň ji (nové nasazení, staré archivovat).
- Změny lokátorů dělej v `selectors.mac.json`, ne natvrdo v kódu.

## Prostředí
- macOS, Appium 3 + driver `mac2`, Xcode (kvůli WebDriverAgentMac), Python 3.13 ve `.venv`.
- `.env` (nevkládat do gitu): `PLATFORM=mac`, `LIFTAGO_BUNDLE_ID=com.adleritech.app.itaxipassenger`, `SHEET_ID=1B4KfTAtr59zif3DZ6WGJ2j-pNOw4MvohOABXnoZryC8`, `SHEET_GID=1586305980`, `SHEET_TAB=Data`, `SHEET_WEBHOOK_URL=<tajné>`, `DAY_START=09:30`, `DAY_END=17:00`, `RUNS_PER_DAY=10`, `MOCK=0`.
- Terminálu / procesům jsou povolena Zpřístupnění a Nahrávání obrazovky.

## Soubory
- `scheduler.py`: denní plán 10 časů (střed okna ± 25 % šířky okna), zmeškaný slot starší než 15 min se přeskočí, sám startuje Appium (`appium` v PATH). Plán v `state/plan-YYYY-MM-DD.json`.
- `run_once.py`: jedno měření, volá driver a `sink.write_row`. Při chybě zapíše řádek s poznámkou `CHYBA: ...`.
- `liftago_driver.py`: ovládání appky (mac2 i android). Důležité metody: `query`, `_type_and_pick`, `_tap_optional`, `_find_category_row`, `_category_texts`, `_collect_offers`, `_close_offers`, `_stop_wda`, `_dump_debug`. Při chybě ukládá screenshot a XML do `debug/`.
- `selectors.mac.json`: všechny lokátory pro Mac (predicate / xpath / id). **V NSPredicate nelze použít atribut `type`**, jen `label`, `title`, `value`, `identifier`, `elementType`.
- `selectors.json`: Android (neověřeno).
- `parsing.py`: `parse_prices`, `parse_eta`, `parse_km`, `parse_offers`.
- `routes.py` + `addresses.py`: pool 37 pražských adres, výběr tras podle pásem 1–3, 3–6, 6–10, 10–15, 15–20 km (OSRM, záložně haversine × 1.3).
- `sink.py`: zápis vždy do `results.csv`, do sheetu přes Apps Script webhook (`SHEET_WEBHOOK_URL`) nebo service account. Při výpadku fronta v `state/pending_rows.jsonl`.
- `deploy/sheet_webhook.gs`: Apps Script (doPost, přidá řádky na list s gid 1586305980, jinak Data). `deploy/install_launchd.sh install|uninstall`: běh na pozadí po přihlášení (LaunchAgent `cz.bolt.liftago-bot`, log `state/bot.log`).
- `inspect_ui.py`: vypíše prvky aktuální obrazovky appky (bez restartu appky). Na to se kalibrují lokátory.

## Průběh v appce (ověřeno ručně přes inspect_ui.py)
1. Úvodní obrazovka: tlačítko `Kam pojedeme?`.
2. Obrazovka "Zadejte trasu": dvě `XCUIElementTypeTextField` (`Nástupní adresa`, `Cílová adresa`). Pickup se musí změnit, výchozí je aktuální poloha. Po napsání adresy jsou návrhy tlačítka za `Vybrat na mapě` (bere se první). To první tlačítko často nemá popisek; v pozadí se mačká jako první `AXButton` za `Vybrat na mapě`, ne podle popisku.
3. **Mezikroky, které se objeví jen u některých adres (oblasti s pevnými body):** tlačítko `Potvrdit adresu vyzvednutí` po výběru pickupu a `Potvrdit cíl jízdy` po výběru cíle (selektor `confirm_dropoff` je zatím odhad, neověřený).
4. Popup `Pro koho je tato jízda?` → tlačítko `Potvrdit` (volitelný).
5. Seznam kategorií. Řádek "Odvoz vozu" je tlačítko s popiskem `Odvoz vozu Řidič vás odveze ve vašem voze Odhad ceny 550 Kč za 6 min`. Pokud už je vybraný, je to `Other` s popiskem začínajícím `vybráno`. Klepnutí na vybranou kategorii otevře info o službě, proto se neklikne. Kategorie může být schovaná pod tlačítkem `X další`. Může mít `Pouze později` (nedostupná) nebo v seznamu chybět → do sloupců se zapíše `žádný výsledek`. Cena v seznamu = **"Cena bez slevy"**.
6. Klik na `Přejít k výběru řidiče` až když je vybraná Odvoz vozu (starý popisek měl navíc `· Odvoz vozu`; jiná kategorie za oddělovačem se nepoužije). Bot čeká 20 s, sbírá všechny ceny nabídek (`Příjezd`, `Fixní cena`, `545 Kč`, tlačítko `Objednat` se nekliká). Ceny jdou do sloupce "Driver offers (CZK)" oddělené čárkou.
7. Zavření: `button.close` (Zavřít) → karta s tlačítkem `Zrušit jízdu` (xpath na Button). Po zrušení se nic dalšího nezobrazí.

## Sheet
Sloupce: Date & time, From, To, Distance (km), ETA (min), Price without discount (CZK), Driver offers (CZK), Note. Texty v datech jsou česky ("žádný výsledek", "N nabídek").

## Známé problémy a co zbývá
1. **9:49 22. 10.: tlačítko "Přejít k výběru řidiče · Odvoz vozu" nenalezeno.** V `debug/20261022_104003_go_button_missing.xml` (stejná chyba v 10:38) má tlačítko popisek jen `Přejít k výběru řidiče`, bez koncovky `Odvoz vozu`. Vybraná byla kategorie Liftago a snímek obrazovky ukazuje Slack v popředí, takže klik myší na řádek Odvoz vozu do appky nedolehl. Selektor v `selectors.mac.json` je `label BEGINSWITH 'Přejít k výběru řidiče'`. Když je to tlačítko na obrazovce, stiskne se a sbírají se nabídky; řádek Odvoz vozu se kvůli tomu znovu nemačká. Starý popisek s `· Odvoz vozu` pořád platí, jiná kategorie za oddělovačem ne.
2. Ověřit selektor `confirm_dropoff` (`Potvrdit cíl jízdy`) na adresách v polygonech (Václavské náměstí, Hlavní nádraží).
3. První návrh v našeptávači bývá obecná ulice bez čísla. Zvážit výběr návrhu, který nejlépe odpovídá napsané adrese.
4. ETA občas None (kategorie "Pouze později" nebo jiný formát). Ověřit.
5. Po relaci Appium zůstává běžet `xcodebuild` / WebDriverAgentMac a macOS hlásí "Je spuštěná automatizace". `_stop_wda()` v `close()` ho zabíjí přes `pkill -f`. Ověřit, že hláška po měření mizí a následující měření fungují.
6. Webhook musí mít v Apps Scriptu přístup **Anyone**. "Anyone within Bolt" nefunguje, bot se nepřihlašuje. Po změně `deploy/sheet_webhook.gs` publikuj novou verzi stejného nasazení (URL v `.env` se nemění). Ověřit, že se řádky objevují na listu gid 1586305980 (dřívější chyba `JSONDecodeError`; `sink.py` teď loguje HTTP kód a začátek odpovědi bez URL).
7. Běh na hlavním Macu. Výchozí je `BACKGROUND=1`: po startu relace se okno schová, akce jdou přes AXPress / nastavení hodnoty (ne myš, ne klávesy do aktivního okna, kurzor se nevrací zpět) a fokus se vrací aplikaci, kterou měl uživatel otevřenou. Skrytý běh Liftago neukončuje (ani při zavření relace), jinak další hledání míří do Finderu a vrátí 404. Atributy zpřístupnění se předávají jako text AXWindows, ne jako neexportovaný symbol. Když úvod není vidět, okno se ukáže za ostatními aplikacemi a nenechá se v popředí. Po měření se okno vrátí na původní místo. Plánovač i Appium jsou oddělené procesy (LaunchAgent). `BACKGROUND=0` je staré chování v popředí. Po změně kódu znovu `./deploy/install_launchd.sh install`.
8. Volitelně přeložit texty v datech do angličtiny ("no result", "N offers").
9. Neznámé: provozní/ToS dopad automatizace cizí appky (konzultovat interně), vytváření poptávek, které řidiči vidí, dokud se nezruší.

## Užitečné příkazy (ve složce projektu)
```
.venv/bin/python scheduler.py --plan          # dnešní plán
MOCK=1 .venv/bin/python run_once.py           # test bez appky (zápis jde do sheetu!)
appium                                        # v samostatném okně (ostrý test)
.venv/bin/python run_once.py                  # jedno ostré měření
.venv/bin/python inspect_ui.py                # výpis prvků aktuální obrazovky (Appium musí běžet)
./deploy/install_launchd.sh install           # (re)start bota na pozadí
tail -f state/bot.log
pkill -f WebDriverAgentMac; pkill -f WebDriverAgentRunner   # uklidit zbylé procesy
```
Po změně kódu bot na pozadí restartuj (`install` znovu), jinak běží stará verze. Plán dne se při smazání `state/plan-*.json` vylosuje znovu.
