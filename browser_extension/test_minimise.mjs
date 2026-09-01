/**
 * Cross-language check: the extension's minimiseUrl() must agree with Python's
 * normalise_domain() / coarse_path_prefix().
 *
 * The two implementations exist because URL minimisation has to happen inside
 * the browser - so the query string never crosses a process boundary - while the
 * agent needs the same logic to validate what arrives. Two implementations of one
 * rule is exactly the kind of thing that silently drifts apart, and a drift here
 * would leak search terms.
 *
 * The cases below mirror tests/test_urls.py one for one.
 *
 *     node browser_extension/test_minimise.mjs
 *
 * tests/test_extension_parity.py runs this automatically when node is available.
 */

import { readFileSync } from "fs";
import { fileURLToPath } from "url";
import { dirname, join } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const source = readFileSync(join(here, "src", "background.js"), "utf8");

const IGNORED_SCHEMES = [
  "chrome:", "chrome-extension:", "edge:", "extension:",
  "about:", "file:", "devtools:", "view-source:", "moz-extension:",
];
const STRIPPED_PREFIXES = ["www.", "m.", "mobile."];

const body = source.slice(
  source.indexOf("function minimiseUrl"),
  source.indexOf("async function getSettings"),
);
const minimiseUrl = new Function(
  "IGNORED_SCHEMES", "STRIPPED_PREFIXES", `${body}; return minimiseUrl;`,
)(IGNORED_SCHEMES, STRIPPED_PREFIXES);

const cases = [
  // [url, expected domain, expected path prefix]
  ["https://www.youtube.com/watch?v=ABC123", "youtube.com", "/watch"],
  ["https://github.com/user/private-repo", "github.com", "/user"],
  ["http://m.facebook.com/", "facebook.com", null],
  ["https://DOCS.python.org/3/library/", "docs.python.org", "/3"],
  ["https://example.com:8443/app", "example.com", "/app"],
  ["https://www.google.com/search?q=how+to+treat+depression&hl=en", "google.com", "/search"],
  ["https://example.com/docs#section-private", "example.com", "/docs"],
  ["https://drive.google.com/file/d/SECRET_ID/view", "drive.google.com", "/file"],
  ["https://github.com", "github.com", null],
  // Browser-internal pages and local files are never tracked.
  ["chrome://settings", null, null],
  ["file:///C:/Users/IC/private/thesis.docx", null, null],
  ["about:blank", null, null],
  ["edge://history", null, null],
  ["http://localhost:8000", null, null],
];

let failures = 0;
for (const [url, expectedDomain, expectedPath] of cases) {
  const result = minimiseUrl(url);
  const domain = result ? result.domain : null;
  const path = result ? result.path_prefix : null;

  if (domain !== expectedDomain || path !== expectedPath) {
    failures++;
    console.error(`FAIL ${url}`);
    console.error(`  expected ${expectedDomain} ${expectedPath}`);
    console.error(`  got      ${domain} ${path}`);
  }
}

// The guarantee that matters most: no query string survives, ever.
const sensitive = JSON.stringify(
  minimiseUrl("https://google.com/search?q=private+medical+question&user=me@example.com"),
);
for (const secret of ["private", "medical", "example.com", "q="]) {
  if (sensitive.includes(secret)) {
    failures++;
    console.error(`FAIL query string leaked: ${secret} found in ${sensitive}`);
  }
}

console.log(`minimiseUrl: ${cases.length} cases, ${failures} failure(s)`);
process.exit(failures ? 1 : 0);
