// Embed the dashboard data into the single-file build so it opens with no server.
import { readFileSync, writeFileSync } from "node:fs";

const html = readFileSync("dist-single/index.html", "utf8");
const data = readFileSync("public/data/dashboard.json", "utf8").replace(/</g, "\\u003c");
const tag = `<script>window.__DASHBOARD__=${data}</script>`;
const out = html.replace("<div id=\"root\"></div>", () => `<div id="root"></div>${tag}`);
if (out === html) throw new Error("root element not found in dist-single/index.html");
writeFileSync("dist-single/index.html", out);
console.log(`dist-single/index.html: ${(out.length / 1024).toFixed(0)} KB`);
