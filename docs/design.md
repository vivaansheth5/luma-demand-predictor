# Dashboard design notes

The dashboard is for one reader at one moment: the person who decides, on Sunday evening, what each store should hold. Every screen answers a question that person has, in the order they have it.

| Screen | Question |
|---|---|
| Brief | What should I do this week, and how sure is the agent? |
| Forecast | What will this product sell in this store, why, and can the shelf meet it? |
| Plan | Exactly what is being ordered and moved, and what did it have to pass? |
| Signals | What is changing: styles, odd days, neighbourhoods? |
| Evidence | Why should I believe any of this? |
| Trace | How did the agent reach the plan? |

## Identity

The interface uses VYLN's own system: maroon as the brand colour, Instrument Serif for headlines, Geist for everything else, a lowercase wordmark, and sentence case throughout.

Type carries one extra rule. Serif is the agent's voice: its headline decision, its findings, and each thought in the trace. Sans is measurement: every number, label and table. A reader can tell judgement from data by the typeface alone.

The Brief opens with the decision as a sentence, set large. The first thing on screen is what to do, not a row of numbers to interpret.

## The size run

Every stock decision in fashion is made per size, so the smallest unit of the interface is a strip of four cells, S to XL. Each cell shows units on hand and is shaded by its chance of selling out. A badge shows what the agent is ordering for that size. The same strip appears in the risk list, the product picker and the product page, so it only has to be learned once.

## Colour

Each colour has one job.

| Colour | Hex | Job |
|---|---|---|
| Brand maroon | `#6E1423` | Navigation, buttons, the committed plan |
| Accent maroon | `#A3223C` | The agent and its forecast in charts |
| Denim blue | `#2E6FB7` | The opposite pole in diverging charts |
| Ochre | `#C98500` | A third series, where one is needed |
| Grey | `#A39A9D` | Context: the status quo, reference methods, observed sales |
| Maroon ramp | `#F7ECEE` to `#6E1423` | Magnitude: stockout risk from low to certain |

Charts follow an emphasis rule: the subject takes the accent and everything it is compared with stays grey. No chart uses more than three hues.

The accent, blue and ochre were checked as a set on the white chart surface with a palette validator (all pairs):

| Check | Result |
|---|---|
| Lightness band (OKLCH L 0.43 to 0.77) | Pass |
| Chroma floor (C at least 0.10) | Pass |
| Colour-vision separation, deuteranopia and protanopia (delta E at least 8) | Pass, worst pair 19.3 |
| Normal-vision separation (delta E at least 15) | Pass, worst pair 24.7 |
| Contrast against the surface (at least 3:1) | Pass |

Status is never colour alone: trend states and guardrail results always carry an icon and a word.

## Charts

* One axis per chart. Where two measures differ in scale (share of demand and buzz), they are drawn as two small charts side by side.
* Lines are 2px, bars are thin with a rounded data end and a square baseline, gridlines are solid hairlines.
* Every full-size chart has a hover readout, and the main ones have a "View as table" switch with the same numbers.
* Forecast intervals at network, store and category level are not sums of product intervals, because errors partly cancel. They use the measured spread of past forecast errors at that level.
* The store map is drawn to scale from coordinates with a 5 km bar. It has no base map, so it loads with no network access.

## Motion

Two things move. Transfer arrows draw themselves once when the map appears, and the trace can be replayed step by step. Both are skipped when the system asks for reduced motion.

## Structure

`web/src/charts` holds chart components built on `d3-scale` and `d3-shape` with hand-written SVG. `web/src/views` holds one file per screen. The app reads a single document, `data/dashboard.json`, written by `demand_agent.export`. `npm run build:single` produces one self-contained HTML file with the data embedded.
