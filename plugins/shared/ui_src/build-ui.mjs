import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const require = createRequire("/tmp/po-ui-build/package.json");
const esbuild = require("esbuild");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT_DIR = path.resolve(HERE, "../ui_dist");
fs.mkdirSync(OUT_DIR, { recursive: true });

const cssText = fs.readFileSync(path.join(HERE, "styles.css"), "utf8");

function buildSingleHtml(entryFile, outFile, title) {
  const result = esbuild.buildSync({
    entryPoints: [path.join(HERE, entryFile)],
    bundle: true,
    write: false,
    format: "iife",
    platform: "browser",
    target: ["es2020"],
    minify: true,
    nodePaths: ["/tmp/po-ui-build/node_modules"],
  });
  const jsText = result.outputFiles[0].text;
  const safeJs = jsText.replace(/<\/script>/gi, "<\\/script>");
  const safeCss = cssText.replace(/<\/style>/gi, "<\\/style>");

  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>${title}</title>
  <style>${safeCss}</style>
</head>
<body>
  <div id="root"><div style="padding:24px;font-family:system-ui,sans-serif;color:#58635c">正在初始化 PaperOrchestra Studio 工作台…</div></div>
  <script>${safeJs}</script>
</body>
</html>
`;

  const outPath = path.join(OUT_DIR, outFile);
  fs.writeFileSync(outPath, html, "utf8");
  const bytes = Buffer.byteLength(html, "utf8");
  if (bytes > 900 * 1024) {
    throw new Error(`${outFile} exceeded 900KB limit: ${bytes} bytes`);
  }
  if (/https?:\/\/(fonts|cdn|unpkg|jsdelivr)/i.test(html)) {
    throw new Error(`${outFile} contains external CDN reference which CSP would block`);
  }
  console.log(`Built ${outFile}: ${(bytes / 1024).toFixed(1)} KB -> ${outPath}`);
}

buildSingleHtml("workbench.ts", "workbench.html", "PaperOrchestra Studio · 实时论文工作台");
buildSingleHtml("tool-tree.ts", "tool-tree.html", "PaperOrchestra Studio · 工具树地图");
