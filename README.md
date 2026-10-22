# Liftago price bot (Odvoz vozu, Praha)

Bot 10x denně v rozptýlených časech (09:30–17:00) zadá v appce Liftago kategorii "Odvoz vozu" náhodnou pražskou trasu 1–20 km (pásma 1–3, 3–6, 6–10, 10–15, 15–20 km se střídají), přečte cenu bez slevy, cenu se slevou a ETA a zapíše řádek do sheetu. Objednávku nikdy nepotvrzuje.

Výchozí varianta: iPhone appka z App Store běžící na Macu s Apple Silicon (`PLATFORM=mac`, Appium driver mac2). Android (`PLATFORM=android`) je také podporovaný.

## Instalace (Mac, appka z App Store)
```bash
xcode-select --install            # Xcode (celý z App Store) je potřeba pro mac2 driver
brew install node
npm i -g appium && appium driver install mac2
cd liftago-price-bot
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env
osascript -e 'id of app "Liftago"'   # výstup vlož do LIFTAGO_BUNDLE_ID v .env
```
Oprávnění (Nastavení systému → Soukromí a zabezpečení): povol **Zpřístupnění** a **Nahrávání obrazovky** pro Terminál (nebo aplikaci, ze které bot běží). Při prvním spuštění Appium nabídne povolení i pro WebDriverAgentMac Runner. V Liftagu buď přihlášený.

## Průběh v appce, který bot dělá
1. klik na "Kam pojedeme?", 2. změna adresy vyzvednutí, 3. zadání cíle ("Zadejte cíl jízdy") a výběr prvního návrhu, 4. popup "Pro koho je tato jízda" → Potvrdit, 5. v seznamu kategorií čte cenu (= cena bez slevy) a "za X min" jen z řádku "Odvoz vozu",
6. označí "Odvoz vozu", klikne na "Přejít k výběru řidiče", 20 s sbírá ceny nabídek řidičů (do sloupce "Nabídky řidičů", oddělené čárkou) a obrazovku zavře tlačítkem "Zavřít". Bot nikdy neklikne na "Objednat".

## Kalibrace (jednorázově, nutná)
`inspect_ui.py` spusť na čtyřech obrazovkách (hlavní, po kliku na "Kam pojedeme?", popup, seznam kategorií) a podle výpisu uprav lokátory.

Texty a prvky v appce nejsou předem známé. 1) Spusť `appium` v jiném okně a ručně otevři v appce obrazovku, 2) `.venv/bin/python inspect_ui.py`, 3) uprav `selectors.mac.json` (kategorie, pole Odkud/Kam, první návrh adresy). Zkoušej `.venv/bin/python run_once.py`. Při chybě se do `debug/` uloží screenshot a XML obrazovky a řádek se zapíše s poznámkou CHYBA.

Ceny se čtou heuristicky ze všech textů s "Kč": vyšší = bez slevy, nižší = se slevou, jedna cena = obě stejné. Pokud appka ukazuje i jiné částky, uprav `parsing.py`.

## Zápis do Google Sheetu (Apps Script webhook)
Vždy se píše do `results.csv`. Do sheetu to jde přes webhook:
1. Otevři sheet → Rozšíření → Apps Script, smaž obsah a vlož `deploy/sheet_webhook.gs`, ulož.
2. Nasadit → Nové nasazení → typ Webová aplikace, Spustit jako: já, Kdo má přístup: Kdokoli → Nasadit (povol oprávnění).
3. Zkopíruj URL webové aplikace a vlož ji do `.env` jako `SHEET_WEBHOOK_URL=...`. URL drž v tajnosti, kdo ji zná, může do sheetu psát.
Při výpadku se řádky odešlou při dalším běhu. Alternativa je service account (`GOOGLE_APPLICATION_CREDENTIALS`).

## Provoz
```bash
.venv/bin/python scheduler.py --plan        # dnešní plán
MOCK=1 .venv/bin/python run_once.py         # test bez appky
.venv/bin/python run_once.py                # jedno ostré měření (Appium musí běžet)
```
Trvalý běh, varianta A (jednodušší): v Terminálu `caffeinate -i .venv/bin/python scheduler.py`; okno nech otevřené. Scheduler si sám spustí Appium.
Varianta B: `./deploy/install_launchd.sh install` (spustí se po přihlášení). Pozor: oprávnění Zpřístupnění a Nahrávání obrazovky se u procesů spouštěných launchd mohou přidělovat zvlášť (node, WebDriverAgentMac Runner). Když měření v logu `state/bot.log` selhávají, použij variantu A.
Mac musí být zapnutý, odemčený a přihlášený. Slot zmeškaný o víc než 15 min se přeskočí.

## Poznámky
- Ovladač pro mac2 je napsaný podle dokumentace a nebyl zkoušen na reálné appce. Čekej ladění lokátorů a případně zadávání textu.
- `addresses.py` obsahuje ~37 adres; souřadnice jsou přibližné. Adresy, které našeptávač nenajde, vyřaď.
- Automatizace cizí appky může porušovat podmínky služby Liftago; zvaž interní schválení.
