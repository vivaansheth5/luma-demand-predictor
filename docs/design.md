# Dashboard design notes

The dashboard is for one reader at one moment: the person at a fashion brand who decides, once a week, what to buy and where to hold it. Every screen answers a question that person has, in the order they have it.

| Screen | Question |
|---|---|
| Brief | What should I do this week, and how sure is the agent? |
| Forecast | What will this product sell in this store, why, and can the shelf meet it? |
| Plan | Exactly what is being ordered and moved, and what did it have to pass? |
| Signals | What is changing: styles, colours, odd days, stores? |
| Evidence | Why should I believe any of this? |
| Trace | How did the agent reach the plan? |

## Identity

Luma means light, and indigo is the dye of denim, so the brand colour is a deep indigo with a warm ember as its opposite. Headlines are set in Fraunces, everything else in Geist, with a lowercase wordmark and sentence case throughout. The name of the brand whose data is loaded sits beside the wordmark.

Type carries one extra rule. Serif is the agent's voice: its headline decision, its findings, and each thought in the trace. Sans is measurement: every number, label and table. A reader can tell judgement from data by the typeface alone.

The Brief opens with the decision as a sentence, set large. The first thing on screen is what to do, not a row of numbers to interpret.

## The size run

Every stock decision in fashion is made per size, so the smallest unit of the interface is a strip with one cell per size, in the order a brand's sizes are worn. Each cell shows units on hand and is shaded by its chance of selling out. A badge shows what the agent is ordering for that size. The same strip appears in the risk list, the product picker and the product page, so it only has to be learned once.

## Colour

Each colour has one job.

| Colour | Hex | Job |
|---|---|---|
| Brand indigo | `#2A2473` | Navigation, buttons, the committed plan |
| Accent indigo | `#4338CA` | Luma and its forecast in charts |
| Ember | `#C2500C` | The opposite pole in diverging charts: what lowers, what fades |
| Green | `#00917A` | A third series, kept in reserve |
| Grey | `#A09FB0` | Context: the status quo, reference methods, observed sales |
| Indigo ramp | `#EEEDFB` to `#2A2473` | Magnitude: stockout risk from low to certain |

Charts follow an emphasis rule: the subject takes the accent and everything it is compared with stays grey. No chart uses more than three hues.

The accent, ember and green were checked as a set on the white chart surface with a palette validator (all pairs):

| Check | Result |
|---|---|
| Lightness band (OKLCH L 0.43 to 0.77) | Pass |
| Chroma floor (C at least 0.10) | Pass |
| Colour-vision separation, deuteranopia and protanopia (delta E at least 8) | Pass, worst pair 12.2 |
| Normal-vision separation (delta E at least 15) | Pass, worst pair 24.9 |
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

`web/src/charts` holds chart components built on `d3-scale` and `d3-shape` with hand-written SVG. `web/src/views` holds one file per screen. The app reads a single document, `data/dashboard.json`, written by `luma.export`. `npm run build:single` produces one self-contained HTML file with the data embedded.
