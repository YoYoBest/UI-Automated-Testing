import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workbookPath = "D:/Auto_Testing/UI-Smoke-Testing/tests/Common_Test_Cases/公共用例_UI自动化.xlsx";
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(workbookPath));
const sheet = workbook.worksheets.getItem("新增");

const overview = await workbook.inspect({
  kind: "sheet",
  include: "id,name",
  maxChars: 3000,
});
const rows = await workbook.inspect({
  kind: "table",
  sheetId: "新增",
  range: "A1:J22",
  include: "values,formulas",
  tableMaxRows: 22,
  tableMaxCols: 10,
  tableMaxCellChars: 200,
  maxChars: 16000,
});

console.log(overview.ndjson);
console.log(rows.ndjson);
