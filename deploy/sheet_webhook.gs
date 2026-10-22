// Apps Script pro Google Sheet "Liftago – monitoring cen (Odvoz vozu)".
// Přijímá řádky od bota (HTTP POST, JSON {"rows": [[...], ...], "gid": 1586305980})
// a připisuje je na list s tím gid. Když gid v sešitu není, použije list "Data".
const SHEET_GID = 1586305980;
const SHEET_NAME = 'Data';
const COLUMNS = 8;

function targetSheet(requestedGid) {
  const ss = SpreadsheetApp.getActive();
  const wanted = String(requestedGid || SHEET_GID);
  const sheets = ss.getSheets();
  for (let i = 0; i < sheets.length; i++) {
    if (String(sheets[i].getSheetId()) === wanted) return sheets[i];
  }
  const byName = ss.getSheetByName(SHEET_NAME);
  if (!byName) throw new Error('List gid ' + wanted + ' ani „' + SHEET_NAME + '“ nenalezen');
  return byName;
}

function doPost(e) {
  const lock = LockService.getScriptLock();
  try {
    lock.waitLock(20000);
    const body = JSON.parse(e.postData.contents);
    const rows = body.rows;
    if (!Array.isArray(rows) || rows.length === 0 || rows.length > 100) {
      throw new Error('Neplatná data');
    }
    const clean = rows.map(function (r) {
      const out = [];
      for (let i = 0; i < COLUMNS; i++) {
        let v = (r[i] === null || r[i] === undefined) ? '' : r[i];
        // text začínající = + - @ by se vyhodnotil jako vzorec
        if (typeof v === 'string' && /^[=+\-@]/.test(v)) v = "'" + v;
        out.push(v);
      }
      return out;
    });
    const sh = targetSheet(body.gid);
    sh.getRange(sh.getLastRow() + 1, 1, clean.length, COLUMNS).setValues(clean);
    return reply({ ok: true, added: clean.length });
  } catch (err) {
    return reply({ ok: false, error: String(err) });
  } finally {
    try { lock.releaseLock(); } catch (x) {}
  }
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}
