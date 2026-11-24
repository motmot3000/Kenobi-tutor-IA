// Which version of Kenobi runs this page.
// - Served as a plain file (GitHub Pages, any static hosting): "browser". Everything runs in the browser
//   (browser/backend.js), with the learner's own API key.
// - Served by server.py: the server answers this file itself with "server", and the page uses its API.
window.KENOBI_BACKEND = "browser";
