"""The human-facing landing page served at the Bridge root.

A browser hitting the bare host gets this self-contained page: a short
explanation of what Bridge is plus the same interactive URL mapper the docs
ship (ported from Vue to vanilla JS, since the Bridge serves no asset bundle).
The MCP base is derived client-side from ``window.location.origin`` so the same
page works unchanged on a self-hosted Bridge.
"""

from __future__ import annotations


def landing_html(docs_url: str, repo_url: str) -> str:
    """Render the landing page. ``docs_url``/``repo_url`` are trusted constants
    (not user input), so a plain placeholder swap is fine."""
    return _TEMPLATE.replace("__DOCS_URL__", docs_url).replace(
        "__REPO_URL__", repo_url)


_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>GraphQL MCP Bridge</title>
<meta name="description" content="Turn any public GraphQL API into MCP tools for your AI agent — no code, no servers. Paste a GraphQL endpoint, get an MCP URL.">
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh;
    font: 16px/1.6 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    background: #0d1117; color: #e6edf3;
    display: flex; justify-content: center;
  }
  main { width: 100%; max-width: 44rem; padding: 3rem 1.25rem 4rem; }
  .eyebrow {
    font-size: .8rem; letter-spacing: .08em; text-transform: uppercase;
    color: #7d8590; margin: 0 0 .5rem;
  }
  h1 { margin: 0 0 .5rem; font-size: 2rem; line-height: 1.2; }
  .lede { font-size: 1.1rem; color: #c9d1d9; margin: 0 0 .4rem; }
  p { color: #aab2bd; }
  strong { color: #e6edf3; }
  a { color: #58a6ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }

  .card {
    margin: 2rem 0; padding: 1.25rem 1.5rem;
    background: #161b22; border: 1px solid #30363d; border-radius: 12px;
  }
  .card h2 { margin: 0 0 .25rem; font-size: 1.1rem; }
  label { display: block; font-size: .82rem; color: #8b949e; margin: .9rem 0 .3rem; }
  input[type="url"], input[type="text"] {
    width: 100%; padding: .6rem .7rem;
    border: 1px solid #30363d; border-radius: 8px;
    background: #0d1117; color: #e6edf3;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: .85rem;
  }
  input[readonly] { background: #010409; color: #7ee787; }
  input:focus { outline: none; border-color: #58a6ff; }
  .arrow { display: flex; justify-content: center; color: #6e7681; margin: .5rem 0 .1rem; }
  .out-row { display: flex; gap: .5rem; align-items: stretch; }
  .out-row input { flex: 1 1 auto; min-width: 0; }
  button {
    padding: 0 1.1rem; border: 1px solid #238636; border-radius: 8px;
    background: #238636; color: #fff; font-weight: 600; font-size: .85rem;
    cursor: pointer; white-space: nowrap;
  }
  button:hover { background: #2ea043; border-color: #2ea043; }
  button.copied { background: #1f6feb; border-color: #1f6feb; }
  button:disabled { opacity: .5; cursor: not-allowed; }
  .hint { font-size: .8rem; color: #8b949e; margin: .55rem 0 0; }
  .hint.error { color: #f85149; }
  .hint code { color: #c9d1d9; }

  pre {
    margin: 1rem 0 0; padding: .9rem 1rem; overflow-x: auto;
    background: #010409; border: 1px solid #30363d; border-radius: 8px;
    font-size: .82rem; color: #c9d1d9;
  }
  .limits { font-size: .85rem; color: #8b949e; }
  .limits li { margin: .15rem 0; }
  footer {
    margin-top: 2.5rem; padding-top: 1.5rem; border-top: 1px solid #21262d;
    font-size: .85rem; color: #7d8590; display: flex; gap: 1.25rem; flex-wrap: wrap;
  }
</style>
</head>
<body>
<main>
  <p class="eyebrow">GraphQL → MCP</p>
  <h1>GraphQL MCP Bridge</h1>
  <p class="lede">Turn any public GraphQL API into tools your AI agent can call —
     no code, no servers, no sign-up.</p>
  <p>Paste a GraphQL endpoint below and Bridge gives you an <strong>MCP URL</strong>.
     Drop it into Claude, Cursor, or any MCP client and every query and mutation
     the API exposes becomes a callable tool. Bridge introspects the schema,
     forwards your auth headers, and proxies the calls.</p>

  <section class="card">
    <h2>Generate your MCP URL</h2>
    <label for="upstream">GraphQL endpoint</label>
    <input id="upstream" type="url" autocomplete="off" spellcheck="false"
           placeholder="https://countries.trevorblades.com/graphql">
    <div class="arrow" aria-hidden="true">
      <svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor"
           stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <line x1="12" y1="3" x2="12" y2="21"></line>
        <polyline points="6 15 12 21 18 15"></polyline></svg>
    </div>
    <label for="output">MCP URL <span style="color:#6e7681">(paste this into your MCP client)</span></label>
    <div class="out-row">
      <input id="output" type="text" readonly placeholder="Enter a GraphQL endpoint above…">
      <button id="copy" type="button" disabled>Copy</button>
    </div>
    <p class="hint" id="hint"></p>
    <pre id="config" aria-label="Example MCP client config"></pre>
  </section>

  <section>
    <h2>Good to know</h2>
    <ul class="limits">
      <li>Free and anonymous — <strong>60 requests/min per IP</strong>, 30s upstream timeout.</li>
      <li>Public endpoints only (no private/loopback hosts); schema introspection must be on.</li>
      <li>For private APIs, custom tool shaping, or higher volume, see the docs on
          self-hosting the open-source Bridge.</li>
    </ul>
  </section>

  <footer>
    <a href="__DOCS_URL__">Full documentation →</a>
    <a href="__REPO_URL__">GitHub</a>
  </footer>
</main>

<script>
(function () {
  var EXAMPLE = "https://countries.trevorblades.com/graphql";
  var BASE = window.location.origin + "/mcp/";
  var DEFAULT_HINT = "The upstream URL is base64url-encoded — no tricky " +
    "<code>%3A%2F</code> characters to escape in your JSON config.";

  var input = document.getElementById("upstream");
  var output = document.getElementById("output");
  var hint = document.getElementById("hint");
  var copyBtn = document.getElementById("copy");
  var config = document.getElementById("config");

  function toBase64Url(value) {
    var bytes = new TextEncoder().encode(value);
    var binary = "";
    bytes.forEach(function (b) { binary += String.fromCharCode(b); });
    return btoa(binary).replace(/\\+/g, "-").replace(/\\//g, "_").replace(/=+$/, "");
  }

  function esc(s) {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function serverName(raw) {
    try {
      var host = new URL(raw).hostname.split(".");
      // Skip a leading "api"/"www" label for a friendlier key.
      var label = host[0] === "api" || host[0] === "www" ? host[1] || host[0] : host[0];
      return label || "graphql";
    } catch (e) { return "graphql"; }
  }

  function update() {
    var typed = input.value.trim();
    var raw = typed || EXAMPLE;
    var url = "", html = "", isError = false;

    if (!/^https?:\\/\\//i.test(raw)) {
      html = "URL must start with <code>http://</code> or <code>https://</code>.";
      isError = true;
    } else {
      try {
        var u = new URL(raw);
        if (!(u.protocol === "http:" || u.protocol === "https:") || !u.hostname) {
          throw new Error("bad url");
        }
        url = BASE + toBase64Url(raw);
        html = typed ? DEFAULT_HINT
                     : DEFAULT_HINT + " <em>Showing example — paste your endpoint above.</em>";
      } catch (e) {
        html = "That doesn't look like a URL. Try " +
               "<code>https://api.example.com/graphql</code>.";
        isError = true;
      }
    }

    output.value = url;
    copyBtn.disabled = !url;
    hint.innerHTML = html;
    hint.classList.toggle("error", isError);

    if (url) {
      config.textContent =
        "{\\n" +
        '  "mcpServers": {\\n' +
        '    "' + serverName(raw) + '": {\\n' +
        '      "url": "' + url + '"\\n' +
        "    }\\n" +
        "  }\\n" +
        "}";
    } else {
      config.textContent = "";
    }
  }

  function copy() {
    if (!output.value) return;
    var done = function (ok) {
      if (!ok) return;
      copyBtn.textContent = "Copied!";
      copyBtn.classList.add("copied");
      setTimeout(function () {
        copyBtn.textContent = "Copy";
        copyBtn.classList.remove("copied");
      }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(output.value).then(function () { done(true); },
        function () { fallbackCopy(done); });
    } else {
      fallbackCopy(done);
    }
  }

  function fallbackCopy(done) {
    try {
      output.focus(); output.select();
      done(document.execCommand("copy"));
    } catch (e) { done(false); }
  }

  input.addEventListener("input", update);
  copyBtn.addEventListener("click", copy);
  update();
})();
</script>
</body>
</html>
"""
