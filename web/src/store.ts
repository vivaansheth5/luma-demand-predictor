/**
 * Which data is loaded (the sample store or the visitor's upload), kept for the browser tab so
 * a refresh does not lose an analysis. Nothing leaves the browser from here.
 */
import { loadDashboard } from "./data";
import type { Dashboard } from "./types";

export type Source = "sample" | "upload";

const KEY_SOURCE = "luma.source";
const KEY_UPLOAD = "luma.upload";
const KEY_PENDING = "luma.pending";

let sample: Promise<Dashboard> | null = null;
let upload: Dashboard | null = null;

function session(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

export function loadSample(): Promise<Dashboard> {
  sample ??= loadDashboard().catch((err) => {
    sample = null;
    throw err;
  });
  return sample;
}

export function savedSource(): Source | null {
  const value = session()?.getItem(KEY_SOURCE);
  return value === "sample" || value === "upload" ? value : null;
}

export function chooseSource(source: Source): void {
  try {
    session()?.setItem(KEY_SOURCE, source);
  } catch {
    /* storage can be full or blocked; the choice then lasts until reload */
  }
}

export function saveUpload(d: Dashboard): void {
  upload = d;
  chooseSource("upload");
  try {
    session()?.setItem(KEY_UPLOAD, JSON.stringify(d));
  } catch {
    /* too large to keep across a reload; it stays in memory for this visit */
  }
}

export function loadUpload(): Dashboard | null {
  if (upload) return upload;
  try {
    const raw = session()?.getItem(KEY_UPLOAD);
    upload = raw ? (JSON.parse(raw) as Dashboard) : null;
  } catch {
    upload = null;
  }
  return upload;
}

/** A question to ask as soon as the chat opens, set by the example questions on the home page. */
export function setPending(question: string): void {
  try {
    session()?.setItem(KEY_PENDING, question);
  } catch {
    /* ignore */
  }
}

export function takePending(): string | null {
  const s = session();
  const value = s?.getItem(KEY_PENDING) ?? null;
  s?.removeItem(KEY_PENDING);
  return value;
}
