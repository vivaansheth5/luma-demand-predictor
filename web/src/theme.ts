// Chart colours. Each one has a single job (see docs/design.md):
//   accent  - the agent and its forecast
//   counter - the opposite pole in diverging charts, and a second series when one is needed
//   third   - a third series, kept in reserve
//   quiet   - context: the status quo, reference methods, observed sales
// The accent/counter/third trio passes the colour-vision checks on a white surface
// (all-pairs deuteranopia and protanopia separation, 3:1 contrast).
export const color = {
  accent: "#4338CA",
  counter: "#C2500C",
  third: "#00917A",
  quiet: "#A09FB0",
  ink: "#17162B",
  secondary: "#57566B",
  muted: "#8A899C",
  grid: "#EAEAF0",
  axis: "#CBCAD8",
  surface: "#FFFFFF",
  brand: "#2A2473",
  good: "#0C8A0C",
  warning: "#B97A00",
  critical: "#C7362F",
};

type RGB = [number, number, number];

function hexToRgb(hex: string): RGB {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const toLinear = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
const toSrgb = (c: number) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);

function rgbToOklab([r, g, b]: RGB): RGB {
  const [lr, lg, lb] = [toLinear(r / 255), toLinear(g / 255), toLinear(b / 255)];
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

function oklabToHex([L, a, b]: RGB): string {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const rgb = [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ].map((c) => Math.round(255 * Math.min(1, Math.max(0, toSrgb(c)))));
  return `#${rgb.map((c) => c.toString(16).padStart(2, "0")).join("")}`;
}

function mix(from: string, to: string, t: number): string {
  const a = rgbToOklab(hexToRgb(from));
  const b = rgbToOklab(hexToRgb(to));
  return oklabToHex([a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]);
}

const LIGHT = "#EEEDFB";
export const NEUTRAL = "#ECEBF0";

/** Sequential ramp for magnitude (stockout risk): one hue, pale to brand indigo. t in 0..1. */
export const riskColor = (t: number): string => mix(LIGHT, color.brand, Math.min(1, Math.max(0, t)));

/** Diverging ramp around a neutral midpoint. t in -1..1: ember below, indigo above. */
export function divergingColor(t: number): string {
  const c = Math.min(1, Math.max(-1, t));
  return c >= 0 ? mix(NEUTRAL, color.accent, c) : mix(NEUTRAL, color.counter, -c);
}

/** White or near-black text for a label placed inside a filled mark. */
export function inkOn(fill: string): string {
  const [r, g, b] = hexToRgb(fill).map((c) => toLinear(c / 255));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.32 ? color.ink : "#FFFFFF";
}
