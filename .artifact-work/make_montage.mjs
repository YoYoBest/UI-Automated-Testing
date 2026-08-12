import fs from "node:fs/promises";
import path from "node:path";
import sharp from "sharp";

const root = path.resolve("..");
const previewDir = path.join(root, ".artifact-work/previews-after");
const names = (await fs.readdir(previewDir)).filter((name) => name.endsWith(".png")).sort();
const tileWidth = 720;
const tileHeight = 430;
const columns = 3;
const rows = Math.ceil(names.length / columns);
const composites = [];

for (let index = 0; index < names.length; index += 1) {
  const file = path.join(previewDir, names[index]);
  const image = await sharp(file).resize(tileWidth - 20, tileHeight - 48, {
    fit: "inside",
    withoutEnlargement: true,
    background: "#FFFFFF",
  }).flatten({ background: "#FFFFFF" }).png().toBuffer();
  const label = Buffer.from(
    `<svg width="${tileWidth}" height="36"><rect width="100%" height="100%" fill="#1F4E78"/><text x="12" y="24" font-family="Microsoft YaHei" font-size="18" fill="white">${names[index].replace(".png", "")}</text></svg>`,
  );
  const left = (index % columns) * tileWidth;
  const top = Math.floor(index / columns) * tileHeight;
  composites.push({ input: label, left, top });
  composites.push({ input: image, left: left + 10, top: top + 42 });
}

await sharp({
  create: { width: columns * tileWidth, height: rows * tileHeight, channels: 3, background: "#F3F4F6" },
}).composite(composites).png().toFile(path.join(root, ".artifact-work/all-sheets-montage.png"));
