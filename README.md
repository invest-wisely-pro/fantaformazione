# Fantaformazione

Offline-first web app that suggests the best fantasy football (fantacalcio) line-up for each Serie A matchday.

- **App**: `index.html` (single file), `sw.js` (offline cache), `manifest.webmanifest`, `icons/`. Your squad and settings live in IndexedDB on the device.
- **Data**: `dati.json`, rebuilt every morning by `.github/workflows/aggiorna-dati.yml` running `tools/aggiorna_dati.py` (Python standard library only, no API keys).
- **Source**: [understat.com](https://understat.com) public Serie A data (fixtures, results, player season and per-match stats). Standings, matchdays, estimated fantasy scores and probable suspensions are derived from it.

A **Mercato** tab applies a Moneyball approach: value over replacement per role, fair auction prices, under/overvalued players against market quotations, trade finder across league rosters, free agents and repair-auction bidding plan. Backtested on a full real Understat season.

See `LEGGIMI.txt` (Italian) for setup.
