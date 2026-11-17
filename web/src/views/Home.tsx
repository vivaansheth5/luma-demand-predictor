import { SizeRun } from "../components/parts";
import { downloadTemplate } from "../upload";

export const GITHUB = "https://github.com/vivaansheth5/vyln-demand-agent";

const EXAMPLES = [
  "What should I restock this week?",
  "Which styles are catching on?",
  "What will sell best next month?",
  "Which sizes run out first?",
  "What isn't selling?",
  "How accurate are these forecasts?",
];

/** A real exchange from the sample store, so the first thing a visitor sees is the product working. */
function Preview() {
  return (
    <figure className="preview" aria-label="An example question and answer from the sample store">
      <div className="preview-q">What should I restock this week?</div>
      <div className="preview-steps">Checked what could sell out · Read the restock plan</div>
      <div className="preview-a">
        <p>
          Start with the <strong>black KERB zip hoodie at Indiranagar</strong>. Sizes L and XL will probably sell out
          before a new order could arrive.
        </p>
        <ul>
          <li>Move 7 units in from three nearby stores today. They get there first.</li>
          <li>Then order 28 more. It's a large order, so it waits for your approval.</li>
        </ul>
      </div>
      <div className="preview-card">
        <div>
          <div className="preview-card-name">KERB Regular Zip Hoodie, Black</div>
          <div className="preview-card-sub">Indiranagar · on hand now, plus the order</div>
        </div>
        <SizeRun sizes={["S", "M", "L", "XL"]} stock={[5, 6, 4, 1]} risk={[0.04, 0.3, 0.52, 0.64]} order={[5, 9, 9, 5]} />
      </div>
      <figcaption>From the sample store</figcaption>
    </figure>
  );
}

export function Home({ onSample, onUpload }: { onSample: (question?: string) => void; onUpload: () => void }) {
  return (
    <div className="home">
      <section className="home-hero">
        <div className="hero-copy">
          <h1>Know what will sell before it sells.</h1>
          <p className="hero-lede">
            Luma is a free AI agent for fashion brands. Upload your sales, then ask it what to restock, which styles are
            catching on and which sizes will run out.
          </p>
          <div className="hero-cta">
            <button className="btn big" onClick={onUpload}>
              Upload your sales
            </button>
            <button className="btn ghost big" onClick={() => onSample()}>
              Try a sample store
            </button>
          </div>
          <p className="hero-small">Free and open source. No account needed.</p>
        </div>
        <Preview />
      </section>

      <section className="how" aria-labelledby="how-title">
        <h2 id="how-title">How to use it</h2>
        <ol className="howto">
          <li>
            <h3>Export your sales</h3>
            <p>
              On Shopify, go to <b>Orders</b> and choose <b>Export</b>. Add the <b>Products</b> export too if you can. From
              any other system, export a spreadsheet of sales with a date, product, size and quantity on each row.
            </p>
          </li>
          <li>
            <h3>Upload the files</h3>
            <p>
              Drop them in. Luma learns how each product sells, in each size, and checks every style, colour and fit for a
              trend. It takes a minute or two.
            </p>
          </li>
          <li>
            <h3>Ask Luma</h3>
            <p>
              Ask in plain words. Luma looks up the answer in your own numbers and tells you what to do, product by product
              and size by size.
            </p>
          </li>
        </ol>
      </section>

      <section className="ask" aria-labelledby="ask-title">
        <h2 id="ask-title">Things you can ask</h2>
        <p>Try one on the sample store.</p>
        <div className="ask-list">
          {EXAMPLES.map((q) => (
            <button key={q} className="ask-chip" onClick={() => onSample(q)}>
              {q}
            </button>
          ))}
        </div>
      </section>

      <section className="files" aria-labelledby="files-title">
        <div className="files-intro">
          <h2 id="files-title">The files Luma reads</h2>
          <p>
            Column names don't have to match exactly: <i>qty</i>, <i>quantity</i> and <i>units</i> all work. Luma needs
            at least 16 weeks of sales, and gets better with a year or more.
          </p>
        </div>
        <div className="files-table" role="table" aria-label="Files and what they add">
          <div className="files-row head" role="row">
            <span role="columnheader">File</span>
            <span role="columnheader">What's in it</span>
            <span role="columnheader">What it adds</span>
          </div>
          <div className="files-row" role="row">
            <span role="cell">
              <b>Sales</b> <em>needed</em>
            </span>
            <span role="cell">One row per day, product and size: date, product ID, size, units sold. Add a store column if you have several.</span>
            <span role="cell">
              Forecasts, trends and unusual days.{" "}
              <button className="linklike" onClick={() => downloadTemplate("sales.csv")}>
                Template
              </button>
            </span>
          </div>
          <div className="files-row" role="row">
            <span role="cell">
              <b>Products</b> <em>needed</em>
            </span>
            <span role="cell">One row per product: ID, name, price. Add any columns that describe it, such as category, style, colour or fit.</span>
            <span role="cell">
              Trends on every column you add.{" "}
              <button className="linklike" onClick={() => downloadTemplate("products.csv")}>
                Template
              </button>
            </span>
          </div>
          <div className="files-row" role="row">
            <span role="cell">
              <b>Stock</b> <em>optional</em>
            </span>
            <span role="cell">Units on hand now, by product and size (and store).</span>
            <span role="cell">
              What will sell out, and what to reorder.{" "}
              <button className="linklike" onClick={() => downloadTemplate("stock.csv")}>
                Template
              </button>
            </span>
          </div>
          <div className="files-row" role="row">
            <span role="cell">
              <b>Shopify</b> <em>either</em>
            </span>
            <span role="cell">The orders export, and the products export if you have it, exactly as Shopify gives them.</span>
            <span role="cell">Everything above. Stock too, if the products export includes inventory.</span>
          </div>
        </div>
      </section>

      <section className="privacy" aria-labelledby="privacy-title">
        <h2 id="privacy-title">Your data</h2>
        <p>
          Your files are analysed and then thrown away: nothing is saved on a server, and the results stay in your browser
          tab. When you ask a question, the question and the numbers Luma looks up to answer it are sent to Anthropic's
          Claude to write the reply. Anthropic does not train on it.
        </p>
      </section>

      <footer className="home-foot">
        <span>
          <span className="brand-mark small">luma</span> is open source.
        </span>
        <span>
          <a href={GITHUB} target="_blank" rel="noreferrer">
            Code on GitHub
          </a>
          <a href={`${GITHUB}/blob/main/docs/methods.md`} target="_blank" rel="noreferrer">
            How the forecasting works
          </a>
        </span>
      </footer>
    </div>
  );
}
