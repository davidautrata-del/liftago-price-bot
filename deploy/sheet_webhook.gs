// Apps Script pro Google Sheet "Liftago – monitoring cen (Odvoz vozu)".
// Přijímá řádky od bota (HTTP POST, JSON {"rows": [[...], ...]}) a připisuje je na konec listu "Data".
const SHEET_NAME = 'Data';
const COLUMNS = 8;

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
    const sh = SpreadsheetApp.getActive().getSheetByName(SHEET_NAME);
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
