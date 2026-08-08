# Vendored assets

## htmx 1.9.10 (app/web/static/htmx.min.js)

- Source: https://unpkg.com/htmx.org@1.9.10/dist/htmx.min.js
- License: BSD-2-Clause
- SHA-256: b3bdcf5c741897a53648b1207fff0469a0d61901429ba1f6e88f98ebd84e669e

Vendored so the Content-Security-Policy (`script-src 'self'`) stays strict —
loading htmx from unpkg.com would be blocked by the CSP, making `hx-post`
inert and the ask/chat spinners never show. Upgrade deliberately: replace
the file and update the hash in the same commit.
