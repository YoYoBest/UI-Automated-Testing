import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const projectRoot = path.resolve("..");
const inputPath = path.join(projectRoot, "tests/Common_Test_Cases/公共用例_UI自动化.xlsx");
const outputDir = path.join(projectRoot, "outputs/019fc2e9-a3da-7de3-adaf-0e18b712e218");
const outputPath = inputPath;
const workDir = path.join(projectRoot, ".artifact-work/edit-sheet");
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(workDir, { recursive: true });

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.getItem("编辑");
const originalTail = sheet.getRange("A6:Z131").values;

sheet.getRange("D2:E5").values = [
  ["修改已有输入框值", "用户已登录且拥有编辑权限；已进入编辑页面；目标输入框已有合法值"],
  ["空值输入框新增内容", "用户已登录且拥有编辑权限；已进入编辑页面；目标非必填输入框当前为空"],
  ["修改选择框码值", "用户已登录且拥有编辑权限；已进入编辑页面；目标选择框至少有两个可用码值且当前选中第一个"],
  ["重复上传同一附件", "用户已登录且拥有编辑权限；已进入编辑页面；目标附件字段已有一个上传成功的附件"],
];

sheet.getRange("G2:K5").values = [
  ["记录原值，将最后一个字符替换为“9”，保存后重新打开编辑页面", "保存成功；重新打开后字段值为修改后的值，其他字段保持不变", "P0", "是", "未执行"],
  ["在目标空输入框中输入“9”，保存后重新打开编辑页面", "保存成功；重新打开后字段显示“9”，其他字段保持不变", "P0", "是", "未执行"],
  ["将目标选择框由第一个码值改为第二个码值，保存后重新打开编辑页面", "保存成功；重新打开后选中第二个码值，显示文本与内部值对应正确", "P0", "是", "未执行"],
  ["再次选择并上传同一个文件", "系统不重复新增同一附件；已有附件保持一份，页面无重复记录", "P1", "是", "未执行"],
];

sheet.getRange("I2:I5").dataValidation = { rule: { type: "list", values: ["P0", "P1", "P2"] } };
sheet.getRange("J2:J5").dataValidation = { rule: { type: "list", values: ["是", "否"] } };
sheet.getRange("K2:K5").dataValidation = { rule: { type: "list", values: ["未执行", "通过", "失败", "阻塞"] } };

const preview = await workbook.render({ sheetName: "编辑", range: "A1:N15", scale: 1.5, format: "png" });
await fs.writeFile(path.join(workDir, "编辑-after.png"), new Uint8Array(await preview.arrayBuffer()));

const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 }, summary: "formula errors",
});
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);

const exported = await SpreadsheetFile.importXlsx(await FileBlob.load(outputPath));
const exportedSheet = exported.worksheets.getItem("编辑");
const firstRows = exportedSheet.getRange("A1:N5").values;
const exportedTail = exportedSheet.getRange("A6:Z131").values;
const problems = [];
for (let row = 1; row <= 4; row += 1) {
  for (let col = 0; col <= 10; col += 1) {
    if (firstRows[row][col] == null || String(firstRows[row][col]).trim() === "") {
      problems.push(`${String.fromCharCode(65 + col)}${row + 1}为空`);
    }
  }
}
if (JSON.stringify(originalTail) !== JSON.stringify(exportedTail)) problems.push("EDIT-005之后内容发生变化");
if ((errors.ndjson || "").includes('"kind":"match"')) problems.push("检测到公式错误");

await fs.writeFile(path.join(workDir, "编辑-verification.json"), JSON.stringify({
  outputPath, problems, firstRows, formulaErrors: errors.ndjson || "",
}, null, 2), "utf8");
console.log(JSON.stringify({ outputPath, problems, preview: path.join(workDir, "编辑-after.png") }, null, 2));
if (problems.length) process.exitCode = 2;
