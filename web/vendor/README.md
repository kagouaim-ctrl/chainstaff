# Vendored browser libraries

`gramjs.min.js`: [GramJS](https://github.com/gram-js/gramjs) `telegram@2.26.22` (MIT) plus
[qrcode-generator](https://github.com/kazuhikoarase/qrcode-generator) 1.4.4 (MIT), bundled for the browser
with esbuild. It exposes `window.GramJS = {TelegramClient, Api, StringSession, NewMessage, qrcode}`.
The console loads it only when the chat page connects to the owner's own Telegram account.

Rebuild: see `scripts/build_gramjs.sh`.
