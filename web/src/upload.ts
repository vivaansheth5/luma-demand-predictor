/**
 * Reading the files a brand drops in: recognising what each one is from its header row, and
 * sending them to /api/analyze, compressed.
 */
import type { Dashboard } from "./types";

export type Role =
  | "sales"
  | "catalog"
  | "stock"
  | "shopify_orders"
  | "shopify_products"
  | "stores"
  | "events"
  | "discounts"
  | "ignore";

export const ROLE_LABELS: Record<Role, string> = {
  sales: "Sales",
  catalog: "Products",
  stock: "Stock levels",
  shopify_orders: "Shopify orders export",
  shopify_products: "Shopify products export",
  stores: "Stores",
  events: "Events and sales calendar",
  discounts: "Planned discounts",
  ignore: "Don't use this file",
};

export interface Picked {
  file: File;
  text: string;
  role: Role;
  rows: number;
  columns: string[];
}

const norm = (c: string) => c.trim().toLowerCase().replace(/^"|"$/g, "").replace(/[\s-]+/g, "_");
const any = (cols: string[], names: string[]) => names.some((n) => cols.includes(n));

/** Split one CSV line into cells, allowing quoted cells that contain commas. */
function cells(line: string): string[] {
  const out: string[] = [];
  let cur = "";
  let quoted = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (quoted && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else quoted = !quoted;
    } else if (ch === "," && !quoted) {
      out.push(cur);
      cur = "";
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

export function detect(columns: string[]): Role {
  const cols = columns.map(norm);
  const date = any(cols, ["date", "day", "order_date", "sale_date", "created_at"]);
  const qty = any(cols, ["units_sold", "units", "quantity", "qty", "sold"]);
  const sku = any(cols, ["sku_id", "sku", "product_id", "item_id", "style_id", "article"]);
  const price = any(cols, ["price", "retail_price", "mrp", "list_price"]);
  if (cols.includes("lineitem_quantity")) return "shopify_orders";
  if (cols.includes("handle") && any(cols, ["variant_sku", "variant_price"])) return "shopify_products";
  if (cols.includes("event") && date) return "events";
  if (cols.includes("discount") && sku) return "discounts";
  if (date && qty && sku) return "sales";
  if (sku && any(cols, ["on_hand", "stock", "inventory", "available"]) && !date) return "stock";
  if (sku && price && !date) return "catalog";
  if (any(cols, ["lat", "latitude"]) || (any(cols, ["store_id", "store"]) && !sku)) return "stores";
  return "ignore";
}

export async function pick(file: File): Promise<Picked> {
  const text = await file.text();
  const firstBreak = text.indexOf("\n");
  const header = (firstBreak < 0 ? text : text.slice(0, firstBreak)).replace(/^﻿/, "").replace(/\r$/, "");
  const columns = cells(header).map((c) => c.trim());
  let rows = 0;
  for (let i = firstBreak; i >= 0 && i < text.length; i = text.indexOf("\n", i + 1)) {
    if (i + 1 < text.length && text[i + 1] !== "\n" && text[i + 1] !== "\r") rows++;
  }
  return { file, text, role: detect(columns), rows, columns };
}

/** What is still missing before Luma can run, in plain words; empty when ready. */
export function missing(files: Picked[]): string[] {
  const roles = new Set(files.map((f) => f.role));
  if (roles.has("shopify_orders")) return [];
  const out: string[] = [];
  if (!roles.has("sales")) out.push("a sales file");
  if (!roles.has("catalog")) out.push("a products file");
  return out;
}

async function gzip(text: string): Promise<Blob | null> {
  if (typeof CompressionStream === "undefined") return null;
  const stream = new Blob([text]).stream().pipeThrough(new CompressionStream("gzip"));
  return new Response(stream).blob();
}

export class UploadError extends Error {}

export const MAX_UPLOAD_BYTES = 4_300_000;

export async function analyse(files: Picked[], options: { brand?: string; currency?: string }): Promise<Dashboard> {
  const tables: Record<string, string> = {};
  for (const f of files) {
    if (f.role === "ignore") continue;
    if (tables[f.role]) throw new UploadError(`Two files are marked as ${ROLE_LABELS[f.role].toLowerCase()}. Keep one.`);
    tables[f.role] = f.text;
  }
  const json = JSON.stringify({ files: tables, options });
  const packed = await gzip(json);
  const body: Blob = packed ?? new Blob([json]);
  if (body.size > MAX_UPLOAD_BYTES) {
    throw new UploadError(
      "These files are too big to upload here, even compressed. Keep the last year of sales, or your best-selling products, and try again.",
    );
  }
  let response: Response;
  try {
    response = await fetch("/api/analyze", {
      method: "POST",
      headers: { "content-type": "application/octet-stream", ...(packed ? { "x-luma-encoding": "gzip" } : {}) },
      body,
    });
  } catch {
    throw new UploadError("Luma could not be reached. Check your connection and try again.");
  }
  if (response.status === 504) {
    throw new UploadError("The analysis took too long. Try with fewer products or a shorter history.");
  }
  const payload = (await response.json().catch(() => null)) as (Dashboard & { error?: string }) | null;
  if (!response.ok || !payload || payload.error) {
    throw new UploadError(payload?.error ?? "Something went wrong while analysing the data. Try again.");
  }
  return payload;
}

const TEMPLATES: Record<string, string> = {
  "sales.csv":
    "date,store_id,sku_id,size,units_sold\n2026-01-05,downtown,TEE-001,M,3\n2026-01-05,downtown,TEE-001,L,1\n2026-01-05,riverside,DRS-014,S,2\n",
  "products.csv":
    "sku_id,name,category,price,unit_cost,style,color,fit\nTEE-001,Boxy pocket tee,Tops,35,14,minimal,black,boxy\nDRS-014,Tiered midi dress,Dresses,89,34,romantic,sage,relaxed\n",
  "stock.csv": "store_id,sku_id,size,on_hand\ndowntown,TEE-001,M,9\ndowntown,TEE-001,L,4\nriverside,DRS-014,S,0\n",
};

export function downloadTemplate(name: keyof typeof TEMPLATES | string): void {
  const url = URL.createObjectURL(new Blob([TEMPLATES[name] ?? ""], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
