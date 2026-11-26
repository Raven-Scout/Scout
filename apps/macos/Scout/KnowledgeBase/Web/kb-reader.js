// Scout note reader: renders a knowledge-base note with markdown-it, keeps
// every block tied to its source lines, and lets you edit a block in place or
// select any text and leave a comment for Scout.
//
// Swift → page:  render {source, display, missing, reset}, endEdit, setEditable {editable}
// Page → Swift:  ready, openWiki {target}, openTag {tag}, openURL {url},
//                edit {kind, start, end, line, col, original, buffer}, editDone, editDiscard,
//                comment {quote, text, afterLine}, retract {marker, from, to},
//                toggleTask {line}, jsError
(function () {
  'use strict';

  const doc = document.getElementById('doc');
  const bubble = document.getElementById('comment-bubble');
  const composer = document.getElementById('composer');
  const composerText = document.getElementById('composer-text');
  const composerSend = document.getElementById('composer-send');

  let source = '';          // the note as written (edits are cut from this)
  let sourceLines = [];
  let missing = new Set();  // wikilink targets with no note
  let markers = [];         // Scout comment markers found in the note, by index
  let editing = null;       // {el, kind, ...} while an inline edit is open
  let pendingRange = null;  // the selection a comment is being written about
  let version = 0;          // the render this page shows; echoed on every change
  // Marks the elements the renderer itself numbered. Note HTML can carry its
  // own data-ls / data-line attributes; only elements with this render's
  // nonce are trusted to say which source lines they are.
  let nonce = '';
  const supportsHighlights = typeof CSS !== 'undefined' && CSS.highlights && typeof Highlight !== 'undefined';

  // Every change the page asks for names the render it was made against, so
  // Swift can drop one that arrives after the note moved on.
  function send(type, payload) {
    scout.post(type, Object.assign({ v: version }, payload || {}));
  }

  // ---------- markdown ----------
  const md = window.markdownit({
    html: true,
    linkify: true,
    typographer: false,
    breaks: false,
  });
  md.linkify.set({ fuzzyLink: false, fuzzyEmail: false });

  // Every block-level opening token carries its source line range.
  md.core.ruler.push('scout_lines', (state) => {
    for (const t of state.tokens) {
      if (!t.map) continue;
      if (t.nesting === 1 || t.type === 'fence' || t.type === 'code_block' || t.type === 'hr' || t.type === 'html_block') {
        t.attrSet('data-ls', String(t.map[0]));
        t.attrSet('data-le', String(t.map[1] - 1));
        t.attrSet('data-k', nonce);
      }
    }
  });

  // Number table cells, so an edit knows which column it is in.
  md.core.ruler.push('scout_cells', (state) => {
    let col = 0;
    for (const t of state.tokens) {
      if (t.type === 'tr_open') col = 0;
      else if (t.type === 'th_open' || t.type === 'td_open') { t.attrSet('data-col', String(col++)); t.attrSet('data-k', nonce); }
    }
  });

  // - [ ] / - [x] task items.
  md.core.ruler.push('scout_tasks', (state) => {
    const tokens = state.tokens;
    for (let i = 2; i < tokens.length; i++) {
      const inline = tokens[i];
      if (inline.type !== 'inline' || tokens[i - 1].type !== 'paragraph_open' || tokens[i - 2].type !== 'list_item_open') continue;
      const first = inline.children && inline.children[0];
      if (!first || first.type !== 'text') continue;
      const m = /^\[([ xX])\]\s+/.exec(first.content);
      if (!m) continue;
      first.content = first.content.slice(m[0].length);
      const done = m[1] !== ' ';
      const li = tokens[i - 2];
      li.attrJoin('class', 'task' + (done ? ' done' : ''));
      const box = new state.Token('html_inline', '', 0);
      box.content = `<input type="checkbox" class="task-box" data-k="${nonce}" data-line="${li.map ? li.map[0] : ''}"${done ? ' checked' : ''} aria-label="${done ? 'Done' : 'To do'}">`;
      inline.children.unshift(box);
    }
  });

  // > [!note] Title — Obsidian-style callouts.
  md.core.ruler.push('scout_callouts', (state) => {
    const tokens = state.tokens;
    for (let i = 0; i + 2 < tokens.length; i++) {
      if (tokens[i].type !== 'blockquote_open' || tokens[i + 1].type !== 'paragraph_open') continue;
      const inline = tokens[i + 2];
      const first = inline.children && inline.children[0];
      if (!first || first.type !== 'text') continue;
      const m = /^\[!(\w+)\][+-]?\s*(.*)$/.exec(first.content);
      if (!m) continue;
      const kind = m[1].toLowerCase();
      tokens[i].attrJoin('class', 'callout callout-' + kind);
      tokens[i].attrSet('data-title', m[2] || kind);
      first.content = '';
      // Drop the line break that followed the [!type] line.
      if (inline.children[1] && inline.children[1].type === 'softbreak') inline.children.splice(1, 1);
    }
  });

  // Fenced code: our own <pre>, highlighted, with its line range and language.
  md.renderer.rules.fence = (tokens, idx) => {
    const t = tokens[idx];
    const lang = (t.info || '').trim().split(/\s+/)[0];
    let body;
    if (lang && window.hljs && hljs.getLanguage(lang)) {
      try { body = hljs.highlight(t.content, { language: lang, ignoreIllegals: true }).value; } catch (e) { body = null; }
    }
    if (body == null) body = md.utils.escapeHtml(t.content);
    return `<pre class="code" data-k="${nonce}" data-ls="${t.map[0]}" data-le="${t.map[1] - 1}"${lang ? ` data-lang="${md.utils.escapeHtml(lang)}"` : ''}><code class="hljs">${body}</code></pre>\n`;
  };
  md.renderer.rules.code_block = md.renderer.rules.fence;
  md.renderer.rules.html_block = (tokens, idx) => {
    const t = tokens[idx];
    return `<div class="html-block" data-k="${nonce}" data-ls="${t.map[0]}" data-le="${t.map[1] - 1}">${t.content}</div>\n`;
  };
  const tableOpen = md.renderer.rules.table_open || ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options));
  md.renderer.rules.table_open = (tokens, idx, options, env, self) => `<div class="table-wrap">${tableOpen(tokens, idx, options, env, self)}`;
  md.renderer.rules.table_close = () => '</table></div>\n';

  // ---------- rendering ----------
  // A marker never spans lines (ScoutMarker's contract is line by line). A
  // prose mention of the opener with no closer on its line must not pair
  // with a real comment further down: that would swallow the lines between
  // into one and shift every line number after it.
  const MARKER_RE = /\/\/==<<([^\n]*?)>>==\/\//g;
  const FRONTMATTER_KEYS = /^([A-Za-z_][\w\- ]*):\s?(.*)$/;

  function render(message) {
    if (editing) closeEditor(false);
    hideBubble();
    // A comment being written was anchored in the old render: start over.
    closeComposer();
    version = message.v || 0;
    nonce = Math.random().toString(36).slice(2) + Date.now().toString(36);
    const keepScroll = !message.reset;
    const scrollY = window.scrollY;
    source = message.source || '';
    sourceLines = source.split('\n');
    missing = new Set((message.missing || []).map((t) => t.toLowerCase()));
    let display = message.display != null ? message.display : source;

    // Frontmatter: blanked in place (so line numbers hold), shown as properties.
    const lines = display.split('\n');
    let front = null;
    if (lines[0] && lines[0].trim() === '---') {
      const end = lines.findIndex((l, i) => i > 0 && l.trim() === '---');
      if (end > 0) {
        front = lines.slice(1, end);
        for (let i = 0; i <= end; i++) lines[i] = '';
      }
    }

    // Scout markers become placeholders that survive markdown untouched,
    // then chips once the HTML is built.
    // Only outside fenced code and inline code spans.
    markers = [];
    let fence = null;
    for (let i = 0; i < lines.length; i++) {
      const t = lines[i].trim();
      if (fence) { if (t.startsWith(fence)) fence = null; continue; }
      if (t.startsWith('```') || t.startsWith('~~~')) { fence = t.slice(0, 3); continue; }
      lines[i] = lines[i].replace(MARKER_RE, (whole, _inner, at, line) => {
        const ticks = (line.slice(0, at).match(/`/g) || []).length;
        if (ticks % 2 === 1) return whole;
        markers.push(whole);
        return `SCOUTMARKER${markers.length - 1}X`;
      });
    }
    display = lines.join('\n');

    const html = md.render(display);
    const clean = DOMPurify.sanitize(html, {
      ADD_ATTR: ['data-ls', 'data-le', 'data-col', 'data-line', 'data-lang', 'data-title', 'data-k', 'checked'],
      // Note HTML must not restyle the page (a transparent full-page link over
      // everything, say): inline styles are dropped.
      FORBID_ATTR: ['style'],
      // Note ids and names get a `user-content-` prefix, so note HTML can't
      // stand in for the reader's own elements (the composer's quote, say).
      SANITIZE_NAMED_PROPS: true,
      ADD_TAGS: ['details', 'summary', 'input'],
      ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|scout-wiki|scout-tag|obsidian|linear):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
      FORBID_TAGS: ['style', 'script', 'iframe', 'object', 'embed', 'form'],
    });
    doc.innerHTML = clean;
    distrustNoteHTML();

    decorateLinks();
    insertMarkers();
    buildProperties(front);
    highlightCommentedPassages();
    window.scrollTo(0, keepScroll ? scrollY : 0);
  }

  // Anything claiming source lines without this render's nonce came from the
  // note's own HTML: strip the claim, and drop look-alike checkboxes.
  function distrustNoteHTML() {
    // Classes the reader gives its own chrome; on note HTML they would only
    // dress text up as a comment chip, an editor or the properties box.
    for (const el of doc.querySelectorAll('.scout-comment, .block-editor, .props, .comment-only, .edited-flash')) {
      el.classList.remove('scout-comment', 'block-editor', 'props', 'comment-only', 'edited-flash');
    }
    for (const el of doc.querySelectorAll('[data-ls], [data-le], [data-line], [data-col], [data-k], input')) {
      if (el.getAttribute('data-k') === nonce) continue;
      if (el.tagName === 'INPUT') { el.remove(); continue; }
      for (const a of ['data-ls', 'data-le', 'data-line', 'data-col', 'data-k']) el.removeAttribute(a);
    }
  }

  function decorateLinks() {
    for (const a of doc.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href');
      if (href.startsWith('scout-wiki:')) {
        a.classList.add('wikilink');
        const target = decodeTarget(href);
        if (missing.has(target.toLowerCase())) {
          a.classList.add('missing');
          a.title = `No note named “${target}” yet`;
        } else {
          a.title = target;
        }
      } else if (href.startsWith('scout-tag:')) {
        a.classList.add('tag');
        a.title = 'Find notes tagged #' + decodeTarget(href);
      } else {
        a.classList.add('external');
        a.title = href;
      }
    }
  }

  function decodeTarget(href) {
    const raw = href.replace(/^[a-z-]+:\/\//i, '').replace(/^[a-z-]+:/i, '');
    try { return decodeURIComponent(raw); } catch (e) { return raw; }
  }

  function parseMarker(text) {
    const inner = text.replace(/^\/\/==<<\s*/, '').replace(/\s*>>==\/\/$/, '');
    const m = /^on "([^"]*)":\s*([\s\S]*)$/.exec(inner);
    return m ? { quote: m[1], comment: m[2] } : { quote: null, comment: inner };
  }

  function insertMarkers() {
    if (!markers.length) return;
    const walker = document.createTreeWalker(doc, NodeFilter.SHOW_TEXT);
    const hits = [];
    while (walker.nextNode()) {
      if (/SCOUTMARKER\d+X/.test(walker.currentNode.nodeValue)) hits.push(walker.currentNode);
    }
    for (const node of hits) {
      const parts = node.nodeValue.split(/(SCOUTMARKER\d+X)/);
      const frag = document.createDocumentFragment();
      for (const part of parts) {
        const m = /^SCOUTMARKER(\d+)X$/.exec(part);
        if (!m) { if (part) frag.appendChild(document.createTextNode(part)); continue; }
        frag.appendChild(markerChip(Number(m[1])));
      }
      node.parentNode.replaceChild(frag, node);
    }
    // A paragraph that is only a comment reads as a block, not inline prose.
    for (const p of doc.querySelectorAll('p')) {
      const chips = p.querySelectorAll(':scope > .scout-comment');
      if (chips.length && [...p.childNodes].every((n) => n.nodeType === 1 ? n.classList.contains('scout-comment') || n.tagName === 'BR' : !n.nodeValue.trim())) {
        p.classList.add('comment-only');
      }
    }
  }

  function markerChip(index) {
    const raw = markers[index] || '';
    const { quote, comment } = parseMarker(raw);
    const chip = document.createElement('span');
    chip.className = 'scout-comment';
    chip.dataset.marker = String(index);
    chip.setAttribute('role', 'note');
    chip.setAttribute('aria-label', 'Comment for Scout: ' + comment + (quote ? ', about “' + quote + '”' : ''));
    chip.innerHTML =
      `<svg class="icon" width="12" height="12" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3.5h10a1 1 0 0 1 1 1v6a1 1 0 0 1-1 1H7l-3 2.5v-2.5H3a1 1 0 0 1-1-1v-6a1 1 0 0 1 1-1Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>` +
      `<span class="text">${quote ? `<span class="quote">“${scout.escape(quote)}”</span> — ` : ''}${scout.escape(comment)}</span>` +
      `<span class="who">for Scout · pending</span>` +
      `<button class="retract" type="button" title="Retract this comment" aria-label="Retract comment">×</button>`;
    chip.querySelector('.retract').addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (editing) return;
      const block = chip.closest('[data-ls]');
      send('retract', {
        marker: raw,
        from: block ? Number(block.dataset.ls) : 0,
        to: block ? Number(block.dataset.le) : sourceLines.length - 1,
      });
    });
    chip.addEventListener('mouseenter', () => setActiveHighlight(chip));
    chip.addEventListener('mouseleave', () => setActiveHighlight(null));
    return chip;
  }

  // Frontmatter and the note's "**Last updated:**"/"**Prev:**"/"**Parent:**"
  // header fold into one quiet box under the title.
  function buildProperties(front) {
    const meta = [];
    const kids = [...doc.children];
    let i = 0;
    const title = kids[0] && kids[0].tagName === 'H1' ? kids[0] : null;
    if (title) i = 1;
    for (; i < kids.length; i++) {
      const el = kids[i];
      if (el.tagName !== 'P') break;
      const text = el.textContent.trim();
      if (/^(Last updated|Prev|Parent):/.test(text)) meta.push(el); else break;
    }
    const fields = [];
    if (front) {
      let current = null;
      for (const line of front) {
        const m = FRONTMATTER_KEYS.exec(line);
        if (m && !/^\s/.test(line)) { current = { key: m[1], value: m[2] }; fields.push(current); }
        else if (current && line.trim()) current.value += (current.value ? '\n' : '') + line.trim();
      }
    }
    if (!fields.length && !meta.length) return;
    const box = document.createElement('details');
    box.className = 'props';
    const typeField = fields.find((f) => f.key === 'type');
    const summaryBits = [];
    if (typeField && typeField.value) summaryBits.push(typeField.value);
    if (fields.length) summaryBits.push(`${fields.length} propert${fields.length === 1 ? 'y' : 'ies'}`);
    if (meta.length) summaryBits.push('history');
    box.innerHTML = `<summary>Properties &amp; history <span class="props-meta">· ${scout.escape(summaryBits.join(' · '))}</span></summary><div class="props-body"></div>`;
    const body = box.querySelector('.props-body');
    if (fields.length) {
      const dl = document.createElement('dl');
      for (const f of fields) {
        const dt = document.createElement('dt');
        dt.textContent = f.key;
        const dd = document.createElement('dd');
        dd.textContent = f.value.replace(/^["']|["']$/g, '');
        dl.append(dt, dd);
      }
      body.appendChild(dl);
    }
    for (const p of meta) body.appendChild(p);
    if (title) title.after(box); else doc.prepend(box);
  }

  // ---------- highlighting the passages comments are about ----------
  let commentRanges = new Map(); // chip index -> Range

  function textIndex(root) {
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
      acceptNode: (n) => (n.parentElement.closest('.scout-comment') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
    });
    const nodes = [];
    let text = '';
    while (walker.nextNode()) {
      nodes.push({ node: walker.currentNode, start: text.length });
      text += walker.currentNode.nodeValue;
    }
    return { nodes, text };
  }

  function rangeFor(index, start, end) {
    const locate = (pos) => {
      for (let i = index.nodes.length - 1; i >= 0; i--) {
        if (index.nodes[i].start <= pos) return [index.nodes[i].node, pos - index.nodes[i].start];
      }
      return null;
    };
    const a = locate(start), b = locate(end);
    if (!a || !b) return null;
    const r = document.createRange();
    r.setStart(a[0], Math.min(a[1], a[0].nodeValue.length));
    r.setEnd(b[0], Math.min(b[1], b[0].nodeValue.length));
    return r;
  }

  function highlightCommentedPassages() {
    commentRanges = new Map();
    if (!supportsHighlights) return;
    CSS.highlights.delete('scout-commented');
    CSS.highlights.delete('scout-active');
    const ranges = [];
    for (const chip of doc.querySelectorAll('.scout-comment')) {
      const { quote } = parseMarker(markers[Number(chip.dataset.marker)] || '');
      if (!quote) continue;
      const needle = quote.replace(/…$/, '').replace(/\s+/g, ' ').trim();
      if (needle.length < 2) continue;
      // Look in the chip's own block, then in the blocks before it.
      let block = chip.closest('[data-ls]') || doc;
      for (let hops = 0; block && hops < 4; hops++) {
        const index = textIndex(block);
        const flat = index.text.replace(/\s/g, ' ');
        const at = flat.indexOf(needle);
        if (at >= 0) {
          const r = rangeFor(index, at, at + needle.length);
          if (r) { ranges.push(r); commentRanges.set(chip.dataset.marker, r); }
          break;
        }
        block = previousBlock(block);
      }
    }
    if (ranges.length) CSS.highlights.set('scout-commented', new Highlight(...ranges));
  }

  function previousBlock(el) {
    let cur = el;
    while (cur && cur !== doc) {
      let prev = cur.previousElementSibling;
      while (prev && !prev.matches('[data-ls], .table-wrap, .props')) prev = prev.previousElementSibling;
      if (prev) return prev;
      cur = cur.parentElement;
    }
    return null;
  }

  function setActiveHighlight(chip) {
    if (!supportsHighlights) return;
    const r = chip && commentRanges.get(chip.dataset.marker);
    if (r) CSS.highlights.set('scout-active', new Highlight(r));
    else CSS.highlights.delete('scout-active');
  }

  // ---------- links & tasks ----------
  doc.addEventListener('click', (e) => {
    const a = e.target.closest('a[href]');
    if (a && doc.contains(a)) {
      e.preventDefault();
      if (editing) return;
      // Read the attribute, not `a.href`: on an SVG <a> that is an
      // SVGAnimatedString, not a URL.
      const href = (a.getAttribute('href') || '').trim();
      if (href.startsWith('scout-wiki:')) send('openWiki', { target: decodeTarget(href) });
      else if (href.startsWith('scout-tag:')) send('openTag', { tag: decodeTarget(href) });
      else if (/^[a-z][a-z0-9+.-]*:/i.test(href)) send('openURL', { url: href });
      return;
    }
    const box = e.target.closest('input.task-box');
    if (box) {
      e.preventDefault();
      if (editing || box.dataset.line === '') return;
      send('toggleTask', { line: Number(box.dataset.line) });
    }
  });

  // ---------- in-place editing ----------
  function blockRange(el) {
    let start = Number(el.dataset.ls), end = Number(el.dataset.le);
    if (el.tagName === 'LI') {
      // A list item's own lines stop where its nested list starts.
      const nested = el.querySelector(':scope > ul[data-ls], :scope > ol[data-ls]');
      if (nested) end = Math.max(start, Number(nested.dataset.ls) - 1);
    }
    // markdown-it counts the blank line after a list's last item as part of
    // it; editing or commenting there would eat the paragraph break.
    while (end > start && !(sourceLines[end] || '').trim()) end--;
    return [start, end];
  }

  doc.addEventListener('dblclick', (e) => {
    if (editing || !scout.editable) return;
    if (e.target.closest('.scout-comment, a, input, .props > summary, .block-editor')) return;
    const cell = e.target.closest('td, th');
    const row = cell && cell.closest('tr[data-ls]');
    if (row) {
      e.preventDefault();
      // A row that isn't a plain `| … |` line (a table in a quote or indented
      // under a list) is edited as its whole line, prefix and all.
      if ((sourceLines[Number(row.dataset.ls)] || '').startsWith('|')) openCellEditor(cell);
      else openBlockEditor(row);
      return;
    }
    let el = e.target.closest('[data-ls]');
    if (!el || !doc.contains(el)) return;
    // Edit a whole table / list from its cells and items, never the wrapper.
    if (el.tagName === 'TABLE' || el.tagName === 'UL' || el.tagName === 'OL') return;
    e.preventDefault();
    window.getSelection().removeAllRanges();
    openBlockEditor(el);
  });

  function openBlockEditor(el) {
    const [start, end] = blockRange(el);
    if (!(start >= 0) || end >= sourceLines.length) return;
    hideBubble();
    const original = sourceLines.slice(start, end + 1).join('\n');
    const wrap = document.createElement('div');
    wrap.className = 'block-editor';
    wrap.innerHTML =
      `<textarea spellcheck="true" aria-label="Edit this block (markdown)"></textarea>` +
      `<div class="bar"><span class="note">Markdown · <kbd>Esc</kbd> or <kbd>⌘↩</kbd> when done · <kbd>⌘S</kbd> saves the note</span>` +
      `<button type="button" class="btn quiet" data-act="discard">Discard</button>` +
      `<button type="button" class="btn primary" data-act="done">Done</button></div>`;
    const ta = wrap.querySelector('textarea');
    ta.value = original;
    // Edit a list item in place without swallowing its nested list.
    const host = el.tagName === 'LI' ? el : el;
    if (el.tagName === 'TR') {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = el.children.length || 1;
      td.appendChild(wrap);
      tr.appendChild(td);
      el.hidden = true;
      el.after(tr);
      editing = { kind: 'block', el, wrap: tr, start, end, original, hidden: [el] };
    } else if (el.tagName === 'LI') {
      const hidden = [];
      for (const child of [...el.childNodes]) {
        if (child.nodeType === 1 && (child.tagName === 'UL' || child.tagName === 'OL')) continue;
        if (child.nodeType === 1) { child.dataset.wasHidden = child.hidden ? '1' : ''; child.hidden = true; hidden.push(child); }
        else if (child.nodeType === 3) { const span = document.createElement('span'); span.hidden = true; el.replaceChild(span, child); span.appendChild(child); hidden.push(span); }
      }
      el.insertBefore(wrap, el.firstChild);
      editing = { kind: 'block', el, wrap, start, end, original, hidden };
    } else {
      host.hidden = true;
      host.after(wrap);
      editing = { kind: 'block', el, wrap, start, end, original, hidden: [host] };
    }
    const autosize = () => { ta.style.height = 'auto'; ta.style.height = ta.scrollHeight + 2 + 'px'; };
    ta.addEventListener('input', () => {
      autosize();
      send('edit', { kind: 'block', start, end, original, buffer: ta.value });
    });
    ta.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' || (e.key === 'Enter' && e.metaKey)) { e.preventDefault(); finishEdit(); }
    });
    wrap.querySelector('[data-act="done"]').addEventListener('click', finishEdit);
    wrap.querySelector('[data-act="discard"]').addEventListener('click', discardEdit);
    autosize();
    ta.focus();
    ta.setSelectionRange(ta.value.length, ta.value.length);
  }

  function openCellEditor(cell) {
    const tr = cell.closest('tr[data-ls]');
    const line = Number(tr.dataset.ls);
    const col = Number(cell.dataset.col);
    if (!(line >= 0) || !(col >= 0)) return;
    hideBubble();
    const original = cellSource(sourceLines[line] || '', col);
    const before = cell.innerHTML;
    cell.classList.add('editing');
    cell.innerHTML = '<input type="text" aria-label="Edit cell">';
    const input = cell.querySelector('input');
    input.value = original;
    editing = { kind: 'cell', el: cell, before, line, col, original };
    input.addEventListener('input', () => send('edit', { kind: 'cell', line, col, original, buffer: input.value }));
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === 'Escape') { e.preventDefault(); finishEdit(); }
    });
    input.addEventListener('blur', () => { if (editing && editing.el === cell) finishEdit(); });
    input.focus();
    input.select();
  }

  // The source text of one cell, `\|` kept as written (matches KBMarkdownLexer.splitRow).
  function cellSource(line, col) {
    let t = line.trim();
    if (t.startsWith('|')) t = t.slice(1);
    if (t.endsWith('|') && !t.endsWith('\\|')) t = t.slice(0, -1);
    const cells = [];
    let cur = '';
    for (let i = 0; i < t.length; i++) {
      if (t[i] === '\\' && t[i + 1] === '|') { cur += '|'; i++; continue; }
      if (t[i] === '|') { cells.push(cur.trim()); cur = ''; continue; }
      cur += t[i];
    }
    cells.push(cur.trim());
    return cells[col] != null ? cells[col] : '';
  }

  function finishEdit() {
    if (!editing) return;
    closeEditor(true);
    send('editDone');
  }
  function discardEdit() {
    if (!editing) return;
    closeEditor(true);
    send('editDiscard');
  }
  function closeEditor(restoreView) {
    const e = editing;
    editing = null;
    if (!e || !restoreView) return;
    if (e.kind === 'cell') {
      e.el.classList.remove('editing');
      e.el.innerHTML = e.before;
    } else {
      e.wrap.remove();
      for (const h of e.hidden) h.hidden = h.dataset && h.dataset.wasHidden === '1';
    }
  }

  // ---------- comments on a selection ----------
  function selectionInDoc() {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
    const range = sel.getRangeAt(0);
    if (!doc.contains(range.commonAncestorContainer)) return null;
    const node = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentElement;
    if (node.closest('.block-editor, .scout-comment, .props, td.editing')) return null;
    const text = sel.toString().trim();
    if (!text) return null;
    return { range: range.cloneRange(), text };
  }

  function placeAt(el, rect, below) {
    el.hidden = false;
    const w = el.offsetWidth, h = el.offsetHeight;
    const x = Math.max(10 + w / 2, Math.min(window.innerWidth - 10 - w / 2, rect.left + rect.width / 2));
    return { x, w, h, top: below ? rect.bottom + 8 : rect.top - 8 };
  }

  function showBubble() {
    if (editing || !composer.hidden) return;
    const s = selectionInDoc();
    if (!s) { hideBubble(); return; }
    const rects = s.range.getClientRects();
    const rect = rects.length ? rects[0] : s.range.getBoundingClientRect();
    bubble.hidden = false;
    const below = rect.top < 48;
    const x = Math.max(60, Math.min(window.innerWidth - 60, rect.left + Math.min(rect.width, 400) / 2));
    bubble.style.left = x + 'px';
    bubble.style.top = (below ? s.range.getBoundingClientRect().bottom + 8 + bubble.offsetHeight : rect.top - 6) + 'px';
  }
  function hideBubble() { bubble.hidden = true; }

  let selTimer = null;
  document.addEventListener('selectionchange', () => {
    clearTimeout(selTimer);
    selTimer = setTimeout(() => {
      if (!composer.hidden) return;
      if (!selectionInDoc()) hideBubble();
    }, 60);
  });
  doc.addEventListener('mouseup', () => setTimeout(showBubble, 10));
  document.addEventListener('keyup', (e) => { if (e.shiftKey && composer.hidden) showBubble(); });
  window.addEventListener('scroll', () => { if (!bubble.hidden) showBubble(); }, { passive: true });
  bubble.addEventListener('mousedown', (e) => e.preventDefault()); // keep the selection
  bubble.addEventListener('click', openComposer);

  document.addEventListener('keydown', (e) => {
    if (e.key.toLowerCase() === 'm' && e.metaKey && e.shiftKey) {
      if (selectionInDoc()) { e.preventDefault(); openComposer(); }
    }
  });

  function openComposer() {
    const s = selectionInDoc();
    if (!s) return;
    pendingRange = s;
    hideBubble();
    if (supportsHighlights) CSS.highlights.set('scout-pending', new Highlight(s.range));
    document.getElementById('composer-quote').textContent = s.text.length > 220 ? s.text.slice(0, 220) + '…' : s.text;
    composerText.value = '';
    updateSend();
    composer.hidden = false;
    const rect = s.range.getBoundingClientRect();
    const w = composer.offsetWidth, h = composer.offsetHeight;
    const left = Math.max(12, Math.min(window.innerWidth - w - 12, rect.left + rect.width / 2 - w / 2));
    let top = rect.bottom + 10;
    if (top + h > window.innerHeight - 12) top = Math.max(12, rect.top - h - 10);
    composer.style.left = left + 'px';
    composer.style.top = top + 'px';
    window.getSelection().removeAllRanges();
    composerText.focus();
  }

  function closeComposer() {
    composer.hidden = true;
    pendingRange = null;
    if (supportsHighlights) CSS.highlights.delete('scout-pending');
  }

  function updateSend() {
    const blocked = !!editing;
    document.getElementById('composer-wait').hidden = !blocked;
    composerSend.disabled = blocked || !composerText.value.trim();
  }

  // The line a comment goes after: the end of the smallest block holding the
  // end of the selection. Tables, code and raw HTML take it after the whole
  // block, so the marker never lands inside them.
  function anchorLine(range) {
    let node = range.endContainer.nodeType === 1 ? range.endContainer : range.endContainer.parentElement;
    if (!doc.contains(node)) return null;
    const whole = node.closest('.table-wrap, pre.code, .html-block');
    if (whole) {
      const holder = whole.matches('[data-le]') ? whole : whole.querySelector('[data-le]');
      if (holder) {
        // A code block also says where it starts, so the marker can take the
        // block's container indent rather than its code indent.
        const code = whole.matches('pre.code') ? Number(holder.dataset.ls) : null;
        return { line: Number(holder.dataset.le), codeStart: code };
      }
    }
    const el = node.closest('[data-ls]');
    if (!el || !doc.contains(el)) return { line: sourceLines.length - 1, codeStart: null };
    return { line: blockRange(el)[1], codeStart: null };
  }

  composerText.addEventListener('input', updateSend);
  composerText.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); closeComposer(); }
    if (e.key === 'Enter' && e.metaKey) { e.preventDefault(); composer.requestSubmit(); }
  });
  document.getElementById('composer-cancel').addEventListener('click', closeComposer);
  composer.addEventListener('submit', (e) => {
    e.preventDefault();
    if (composerSend.disabled || !pendingRange) return;
    const anchor = anchorLine(pendingRange.range);
    if (anchor == null) { closeComposer(); return; }
    send('comment', {
      quote: pendingRange.text,
      text: composerText.value.trim(),
      afterLine: anchor.line,
      codeStart: anchor.codeStart,
    });
    closeComposer();
  });
  document.addEventListener('mousedown', (e) => {
    if (!composer.hidden && !composer.contains(e.target) && !composerText.value.trim()) closeComposer();
  });

  // ---------- messages ----------
  scout.editable = true;
  // Read-only: what the page shows, so a test hosting the real page can tell
  // a blank reader from a rendered one (UI audit T1).
  window.kbReaderState = () => ({ version, text: doc.textContent });

  scout.on('render', render);
  scout.on('endEdit', () => closeEditor(true));
  scout.on('setEditable', (m) => { scout.editable = !!m.editable; });
})();
