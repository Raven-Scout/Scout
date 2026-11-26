# Third-party code in the knowledge-base web pages

The knowledge map (`kb-map.html`) and the note reader (`kb-reader.html`) run in a
`WKWebView` from files bundled with the app. Nothing is loaded from the network.
These files are unmodified upstream release builds:

| File | Project | Version | Licence |
| --- | --- | --- | --- |
| `d3-dispatch.min.js` | [d3-dispatch](https://github.com/d3/d3-dispatch) | 3.0.1 | ISC |
| `d3-quadtree.min.js` | [d3-quadtree](https://github.com/d3/d3-quadtree) | 3.0.1 | ISC |
| `d3-timer.min.js` | [d3-timer](https://github.com/d3/d3-timer) | 3.0.1 | ISC |
| `d3-force.min.js` | [d3-force](https://github.com/d3/d3-force) | 3.0.0 | ISC |
| `markdown-it.min.js` | [markdown-it](https://github.com/markdown-it/markdown-it) | 14.1.1 | MIT |
| `purify.min.js` | [DOMPurify](https://github.com/cure53/DOMPurify) | 3.4.14 | Apache-2.0 or MPL-2.0 |
| `highlight.min.js` | [highlight.js](https://github.com/highlightjs/highlight.js) | 11.10.0 ("common" build) | BSD-3-Clause |

To update one, replace the file with the same project's `dist/` (or CDN "common")
minified build and change the version here. The d3 files must load in the order
`kb-map.html` lists them: d3-force reads the other three from the `d3` global.

The pages also load the app's bundled brand fonts (Newsreader 16 pt and JetBrains Mono
v2.304, SIL Open Font License 1.1) from `Scout/Resources/Fonts/`, which the app build copies
next to these pages; their `OFL.txt` texts are in that folder.

Everything else in this folder (`kb-*.html`, `kb-*.css`, `kb-*.js`) is Scout's own
code, under the app's licence.
