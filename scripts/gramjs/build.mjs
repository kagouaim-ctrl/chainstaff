import * as esbuild from "esbuild";
import path from "path";
const T = path.resolve("node_modules/telegram");
const stub = path.resolve("stub.js");
const plugin = { name: "gramjs-browser", setup(b) {
  b.onResolve({ filter: /^\.\/CryptoFile$/ }, () => ({ path: path.resolve("cryptofile.js") }));
  b.onResolve({ filter: /^crypto$/ }, () => ({ path: path.join(T, "crypto/crypto.js") }));
  b.onResolve({ filter: /^(net|tls|fs|socks|node-localstorage|constants)$/ }, () => ({ path: stub }));
}};
await esbuild.build({
  entryPoints: ["entry.js"], bundle: true, minify: true, format: "iife", platform: "browser",
  outfile: "../../web/vendor/gramjs.min.js", target: "es2020", legalComments: "eof", plugins: [plugin],
  define: { "process.env.NODE_ENV": '"production"', global: "globalThis" },
  inject: ["shim.js"],
  alias: { os: "os-browserify/browser", path: "path-browserify", util: "util", events: "events" },
  logLevel: "warning",
});
