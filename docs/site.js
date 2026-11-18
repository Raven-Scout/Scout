/* Scout website: theme toggle, copy button and the interactive demos.
   No dependencies, no tracking, nothing leaves the page. Every demo is
   progressive enhancement: without this script the page shows each
   demo's finished state. Reduced motion skips every animation. */
(function () {
  var d = document, root = d.documentElement;
  root.classList.add('js');
  var reduce = !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);
  var fine = !!(window.matchMedia && matchMedia('(hover: hover) and (pointer: fine)').matches);
  var hasIO = 'IntersectionObserver' in window;
  function $(id) { return d.getElementById(id); }
  function each(sel, fn, ctx) { Array.prototype.forEach.call((ctx || d).querySelectorAll(sel), fn); }
  function onceVisible(el, fn, threshold) {
    if (!el) return;
    if (!hasIO) { fn(); return; }
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { io.disconnect(); fn(); } });
    }, { threshold: threshold || 0.35 });
    io.observe(el);
  }

  /* ---------- theme ---------- */
  var media = window.matchMedia ? matchMedia('(prefers-color-scheme: dark)') : null;
  function current() {
    var set = root.getAttribute('data-theme');
    if (set === 'light' || set === 'dark') return set;
    return media && media.matches ? 'dark' : 'light';
  }
  function label(btn) {
    var next = current() === 'dark' ? 'light' : 'dark';
    btn.setAttribute('aria-label', 'Switch to ' + next + ' theme');
    btn.setAttribute('title', 'Switch to ' + next + ' theme');
  }
  var themeBtn = $('theme-btn');
  if (themeBtn) {
    label(themeBtn);
    themeBtn.addEventListener('click', function () {
      var next = current() === 'dark' ? 'light' : 'dark';
      root.setAttribute('data-theme', next);
      try { localStorage.setItem('scout-theme', next); } catch (e) {}
      label(themeBtn);
    });
    if (media && media.addEventListener) media.addEventListener('change', function () { label(themeBtn); });
  }

  /* ---------- copy buttons ---------- */
  var copyStatus = $('copy-status');
  function copied(btn, ok) {
    var text = btn.querySelector('span');
    if (text) text.textContent = ok ? 'Copied' : 'Press ⌘C';
    if (copyStatus) copyStatus.textContent = ok ? 'Install command copied to the clipboard.' : 'Copy failed. Select the command and copy it.';
    setTimeout(function () { if (text) text.textContent = 'Copy'; }, 2000);
  }
  each('[data-copy]', function (btn) {
    btn.addEventListener('click', function () {
      var value = btn.getAttribute('data-copy');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(function () { copied(btn, true); }, function () { copied(btn, false); });
      } else { copied(btn, false); }
    });
  });

  /* ---------- scroll reveal (below the fold only) ---------- */
  if (hasIO && !reduce) {
    var groups = ['.trio', '.bento', '.layers', '.install-grid', '.faq'];
    var singles = '.section-head, .feature > div, .feature > figure, .grid-2 > div:last-child > .card, .reqs';
    var rv = [];
    groups.forEach(function (g) {
      each(g, function (parent) {
        Array.prototype.forEach.call(parent.children, function (c, i) { c.style.setProperty('--d', (Math.min(i, 6) * 0.07).toFixed(2) + 's'); rv.push(c); });
      });
    });
    each(singles, function (el) { rv.push(el); });
    var fold = window.innerHeight;
    var rio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); rio.unobserve(e.target); } });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });
    rv.forEach(function (el) {
      if (el.getBoundingClientRect().top < fold) return;
      el.classList.add('rv');
      rio.observe(el);
    });
  }

  /* ---------- scroll progress + active nav link ---------- */
  var bar = $('progress');
  if (bar) {
    var tick = false;
    var paint = function () {
      tick = false;
      var max = root.scrollHeight - root.clientHeight;
      bar.style.width = (max > 0 ? (root.scrollTop / max) * 100 : 0) + '%';
    };
    var onScroll = function () { if (!tick) { tick = true; requestAnimationFrame(paint); } };
    addEventListener('scroll', onScroll, { passive: true });
    addEventListener('resize', onScroll, { passive: true });
    paint();
  }
  if (hasIO) {
    var navLinks = {};
    each('.nav a[href^="#"]', function (a) { navLinks[a.getAttribute('href').slice(1)] = a; });
    var nio = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        Object.keys(navLinks).forEach(function (k) { navLinks[k].classList.toggle('on', k === e.target.id); navLinks[k].removeAttribute('aria-current'); });
        if (navLinks[e.target.id]) navLinks[e.target.id].setAttribute('aria-current', 'true');
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    each('main > section', function (s) { nio.observe(s); });
  }

  /* ---------- hero: with / without ---------- */
  var segBtns = d.querySelectorAll('.seg button');
  each('.seg button', function (b) {
    b.addEventListener('click', function () {
      var v = b.getAttribute('data-view');
      Array.prototype.forEach.call(segBtns, function (x) { x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); });
      each('.hero-vis figure[data-view]', function (f) { f.hidden = f.getAttribute('data-view') !== v; });
    });
  });

  /* ---------- hero: the briefing builds itself ---------- */
  (function () {
    var brief = $('brief'); if (!brief) return;
    var scan = $('scan'), n = $('scan-n'), status = $('brief-status'), replay = $('brief-replay');
    var chips = brief.querySelectorAll('.sc'), items = brief.querySelectorAll('.item');
    var timers = [];
    function at(ms, fn) { timers.push(setTimeout(fn, ms)); }
    function finish() {
      if (scan) scan.classList.add('gone');
      Array.prototype.forEach.call(items, function (i) { i.classList.add('shown'); });
      status.textContent = '5 sources checked · 3 need you';
      replay.hidden = false;
    }
    function play() {
      timers.forEach(clearTimeout); timers = [];
      if (reduce) { finish(); return; }
      replay.hidden = true;
      scan.classList.remove('gone'); n.textContent = '0';
      Array.prototype.forEach.call(chips, function (c) { c.classList.remove('done'); });
      Array.prototype.forEach.call(items, function (i) { i.classList.remove('shown'); });
      status.textContent = 'reading your tools…';
      var t = 300;
      Array.prototype.forEach.call(chips, function (c, i) { at(t, function () { c.classList.add('done'); n.textContent = String(i + 1); }); t += 260; });
      at(t + 120, function () { status.textContent = 'cross-checking…'; });
      t += 650;
      at(t, function () { scan.classList.add('gone'); });
      Array.prototype.forEach.call(items, function (it) { t += 380; at(t, function () { it.classList.add('shown'); }); });
      at(t + 200, finish);
    }
    replay.addEventListener('click', play);
    each('.why-btn', function (b) {
      b.addEventListener('click', function (e) {
        e.stopPropagation();
        var open = b.getAttribute('aria-expanded') === 'true';
        b.setAttribute('aria-expanded', open ? 'false' : 'true');
        $(b.getAttribute('aria-controls')).hidden = open;
      });
    }, brief);
    each('.item', function (it) {
      it.addEventListener('click', function (e) {
        if (e.target.closest('button')) return;
        var b = it.querySelector('.why-btn'); if (b) b.click();
      });
    }, brief);
    if (reduce || !hasIO) finish(); else onceVisible(brief, play, 0.4);
  })();

  /* ---------- hero: gentle pointer tilt ---------- */
  (function () {
    var desk = $('desk'), vis = desk && desk.closest('.hero-vis');
    if (!desk || !fine || reduce) return;
    vis.addEventListener('mousemove', function (e) {
      var r = vis.getBoundingClientRect();
      var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
      desk.style.transform = 'rotateX(' + (-y * 3).toFixed(2) + 'deg) rotateY(' + (x * 4).toFixed(2) + 'deg)';
    });
    vis.addEventListener('mouseleave', function () { desk.style.transform = ''; });
  })();

  /* ---------- magnetic primary buttons ---------- */
  if (fine && !reduce) {
    each('.btn-primary:not(.btn-sm)', function (b) {
      b.addEventListener('mousemove', function (e) {
        var r = b.getBoundingClientRect();
        b.style.transform = 'translate(' + ((e.clientX - r.left - r.width / 2) * 0.12).toFixed(1) + 'px,' + ((e.clientY - r.top - r.height / 2) * 0.2).toFixed(1) + 'px)';
      });
      b.addEventListener('mouseleave', function () { b.style.transform = ''; });
    });
  }

  /* ---------- how it works: the day follows your scroll ---------- */
  (function () {
    var steps = d.querySelectorAll('.timeline.steps .slot'); if (!steps.length) return;
    var needle = $('needle'), time = $('dp-time'), name = $('dp-name'), reads = $('dp-reads'), writes = $('dp-writes'), commit = $('dp-commit');
    var card = time && time.closest('.dp');
    var cur = -1;
    function set(i) {
      if (i === cur) return; cur = i;
      var s = steps[i];
      Array.prototype.forEach.call(steps, function (x, k) { x.classList.toggle('on', k === i); });
      if (!needle) return;
      var pos = s.getAttribute('data-pos');
      if (pos) { needle.style.left = pos + '%'; needle.classList.remove('off'); } else { needle.classList.add('off'); }
      time.textContent = s.getAttribute('data-time');
      name.textContent = s.getAttribute('data-name');
      reads.textContent = s.getAttribute('data-reads');
      writes.textContent = s.getAttribute('data-writes');
      commit.textContent = s.getAttribute('data-commit');
      if (!reduce && card) { card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash'); }
    }
    set(0);
    if (!hasIO) return;
    var sio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) set(Array.prototype.indexOf.call(steps, e.target)); });
    }, { rootMargin: '-42% 0px -48% 0px' });
    Array.prototype.forEach.call(steps, function (s) { sio.observe(s); });
  })();

  /* ---------- cross-check sandbox ---------- */
  (function () {
    var xc = $('xc'); if (!xc) return;
    var out = $('xc-out');
    function update() {
      var on = xc.querySelectorAll('.srct:not(.conflict)[aria-pressed="true"]').length;
      var conflict = xc.querySelector('.srct.conflict[aria-pressed="true"]');
      var cls, tag, msg;
      if (conflict) { cls = 'bad'; tag = 'contradicted'; msg = 'Linear says Friday. You see both sides, held for you; Scout doesn’t pick one.'; }
      else if (on >= 2) { cls = 'ok'; tag = 'verified'; msg = on + ' sources agree.'; }
      else if (on === 1) { cls = 'warn'; tag = 'single-source'; msg = 'Only one source says so. Shown as tentative, not as fact.'; }
      else { cls = ''; tag = 'unverified'; msg = 'Nothing backs it up yet, so it isn’t stated as fact.'; }
      out.innerHTML = '';
      var t = d.createElement('span'); t.className = 'tag ' + cls; t.textContent = tag;
      var m = d.createElement('span'); m.textContent = msg;
      out.appendChild(t); out.appendChild(m);
      if (!reduce) { out.classList.remove('flash'); void out.offsetWidth; out.classList.add('flash'); }
    }
    each('.srct', function (b) {
      b.addEventListener('click', function () { b.setAttribute('aria-pressed', b.getAttribute('aria-pressed') === 'true' ? 'false' : 'true'); update(); });
    }, xc);
  })();

  /* ---------- mistake pattern ---------- */
  (function () {
    var c = $('pcard'); if (!c) return;
    var pill = $('pc-pill'), msg = $('pc-msg'), btn = $('pc-btn');
    btn.addEventListener('click', function () {
      var fixed = c.getAttribute('data-state') !== 'fixed';
      c.setAttribute('data-state', fixed ? 'fixed' : 'regression');
      pill.textContent = fixed ? 'fixed' : 'regression';
      pill.className = 'pill ' + (fixed ? 'ok' : 'bad');
      msg.textContent = fixed ? 'Fix written into its instructions. No repeat since.' : 'Seen 3 times. The fix goes into its own instructions.';
      btn.textContent = fixed ? '↻ Replay' : 'Apply the fix';
    });
  })();

  /* ---------- proposal ---------- */
  (function () {
    var p = $('prop'); if (!p) return;
    var acts = $('pr-acts'), timer = $('pr-timer');
    var pending = acts.innerHTML;
    acts.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var a = b.getAttribute('data-act');
      if (a === 'approve') { p.setAttribute('data-state', 'applied'); timer.textContent = 'applied ✓'; }
      else if (a === 'reject') { p.setAttribute('data-state', 'rejected'); timer.textContent = 'rejected, not applied'; }
      else { p.setAttribute('data-state', 'pending'); timer.textContent = 'applies in 3 days'; acts.innerHTML = pending; acts.querySelector('button').focus(); return; }
      acts.innerHTML = '<button class="mini" type="button" data-act="reset">↻ Try again</button>';
      acts.querySelector('button').focus();
    });
  })();

  /* ---------- review queue ---------- */
  (function () {
    var h = $('held'); if (!h) return;
    var lbl = $('held-lbl'), claim = $('held-claim'), acts = $('held-acts');
    var openActs = acts.innerHTML, openClaim = claim.innerHTML;
    acts.addEventListener('click', function (e) {
      var b = e.target.closest('button'); if (!b) return;
      var a = b.getAttribute('data-act');
      if (a === 'confirm') { h.setAttribute('data-state', 'confirmed'); lbl.textContent = '✓ Linked to Alex'; claim.innerHTML = 'Your call, not a guess. Both names now point to the same person note.'; }
      else if (a === 'reject') { h.setAttribute('data-state', 'rejected'); lbl.textContent = '✓ Kept separate'; claim.innerHTML = 'A new person note is started for <b>A. Rivera</b>. Nothing was merged by mistake.'; }
      else { h.setAttribute('data-state', 'open'); lbl.textContent = '⏸ Held for you'; claim.innerHTML = openClaim; acts.innerHTML = openActs; acts.querySelector('button').focus(); return; }
      acts.innerHTML = '<button class="mini" type="button" data-act="reset">↻ Reset</button>';
      acts.querySelector('button').focus();
    });
  })();

  /* ---------- research queue ---------- */
  (function () {
    var run = $('rq-run'), list = $('rq-list'); if (!run || !list) return;
    var original = list.innerHTML;
    run.addEventListener('click', function () {
      var row = list.querySelector('li:not(.done):not(.running)');
      if (!row) { list.innerHTML = original; run.textContent = '▸ Run next'; return; }
      var age = row.querySelector('span');
      row.classList.add('running'); age.textContent = 'researching…'; run.disabled = true;
      setTimeout(function () {
        row.classList.remove('running'); row.classList.add('done'); age.textContent = 'done ✓';
        list.appendChild(row); run.disabled = false;
        if (!list.querySelector('li:not(.done)')) run.textContent = '↻ Start over';
      }, reduce ? 0 : 1100);
    });
  })();

  /* ---------- a question in Slack ---------- */
  (function () {
    var chat = $('chat'); if (!chat) return;
    var body = $('chat-body'), chips = $('chat-chips'), replay = $('chat-replay');
    var timers = [];
    function at(ms, fn) { timers.push(setTimeout(fn, reduce ? 0 : ms)); }
    function msg(cls, html) {
      var m = d.createElement('div'); m.className = 'msg ' + cls; m.innerHTML = html; body.appendChild(m); return m;
    }
    function typing() {
      if (reduce) return { remove: function () {} };
      var t = d.createElement('div'); t.className = 'typing'; t.setAttribute('aria-hidden', 'true');
      t.innerHTML = '<i></i><i></i><i></i>'; body.appendChild(t); return t;
    }
    function react(m) {
      var r = d.createElement('div'); r.className = 'react';
      [['👍', 'Helpful'], ['👎', 'Not helpful']].forEach(function (p) {
        var b = d.createElement('button'); b.type = 'button'; b.textContent = p[0];
        b.setAttribute('aria-label', p[1]); b.setAttribute('aria-pressed', 'false');
        b.addEventListener('click', function () {
          each('button', function (x) { x.setAttribute('aria-pressed', 'false'); }, r);
          b.setAttribute('aria-pressed', 'true');
        });
        r.appendChild(b);
      });
      m.appendChild(r);
    }
    function ask() {
      chips.innerHTML = '';
      [['Sam owns it', 'Sam owns'], ['I do', 'you own']].forEach(function (p) {
        var b = d.createElement('button'); b.type = 'button'; b.className = 'mini'; b.textContent = p[0];
        b.addEventListener('click', function () {
          chips.innerHTML = '';
          msg('me', p[0]);
          var t = typing();
          at(800, function () { t.remove(); msg('', 'Got it: ' + p[1] + ' owns the billing fix. Saved to your notes, and I won’t ask again. ✓'); replay.focus(); });
        });
        chips.appendChild(b);
      });
    }
    function play() {
      timers.forEach(clearTimeout); timers = []; body.innerHTML = ''; chips.innerHTML = '';
      var t1 = typing();
      at(900, function () { t1.remove(); react(msg('', 'Morning briefing: <b>3 need you</b>, 1 contradiction held for you.')); });
      at(1500, function () {
        var t2 = typing();
        at(1100, function () { t2.remove(); msg('', 'One thing I can’t tell from your tools: who owns the billing fix, you or Sam? <span style="color:var(--ink-3)">(ticket link · 1-line summary)</span>'); ask(); });
      });
    }
    replay.addEventListener('click', play);
    onceVisible(chat, play, 0.5);
  })();

  /* ---------- wishlist ---------- */
  each('#wish .wi', function (b) {
    b.addEventListener('click', function () {
      var on = b.getAttribute('aria-pressed') !== 'true';
      b.setAttribute('aria-pressed', on ? 'true' : 'false');
      var em = b.querySelector('em'); if (em) em.textContent = on ? 'shipped' : 'queued';
    });
  });

  /* ---------- knowledge graph ---------- */
  (function () {
    var kg = $('kg'); if (!kg) return;
    var cap = $('kg-cap'), def = cap.innerHTML;
    var nodes = kg.querySelectorAll('.node'), edges = kg.querySelectorAll('line');
    function clear() {
      kg.classList.remove('dim');
      each('.hot', function (x) { x.classList.remove('hot'); }, kg);
      cap.innerHTML = def;
    }
    Array.prototype.forEach.call(nodes, function (n) {
      var name = n.querySelector('text').textContent;
      n.setAttribute('tabindex', '0');
      n.setAttribute('role', 'button');
      n.setAttribute('aria-label', name + ': ' + n.getAttribute('data-rels'));
      function show() {
        var id = n.getAttribute('data-id');
        clear(); kg.classList.add('dim'); n.classList.add('hot');
        Array.prototype.forEach.call(edges, function (e) {
          var a = e.getAttribute('data-a'), b = e.getAttribute('data-b');
          if (a === id || b === id) {
            e.classList.add('hot');
            var o = kg.querySelector('.node[data-id="' + (a === id ? b : a) + '"]'); if (o) o.classList.add('hot');
          }
        });
        cap.innerHTML = '';
        var b = d.createElement('b'); b.textContent = name;
        cap.appendChild(b); cap.appendChild(d.createTextNode(' — ' + n.getAttribute('data-rels')));
      }
      n.addEventListener('mouseenter', show); n.addEventListener('focus', show);
      n.addEventListener('click', show);
      n.addEventListener('blur', clear);
    });
    kg.querySelector('svg').addEventListener('mouseleave', clear);
  })();

  /* ---------- jump palette (⌘K / Ctrl+K) ---------- */
  (function () {
    var pal = $('cmdk'), input = $('cmdk-input'), list = $('cmdk-list'), openBtn = $('cmdk-btn');
    if (!pal) return;
    var items = list.querySelectorAll('li'), sel = 0, lastFocus = null;
    function visible() { return Array.prototype.filter.call(items, function (l) { return !l.hidden; }); }
    function mark() {
      var v = visible();
      Array.prototype.forEach.call(items, function (l) { l.classList.remove('sel'); l.setAttribute('aria-selected', 'false'); });
      if (v[sel]) { v[sel].classList.add('sel'); v[sel].setAttribute('aria-selected', 'true'); input.setAttribute('aria-activedescendant', v[sel].id); v[sel].scrollIntoView({ block: 'nearest' }); }
      else input.removeAttribute('aria-activedescendant');
    }
    function filter() {
      var q = input.value.toLowerCase().trim();
      Array.prototype.forEach.call(items, function (l) { l.hidden = !!q && l.textContent.toLowerCase().indexOf(q) < 0; });
      sel = 0; mark();
    }
    function show() { lastFocus = d.activeElement; pal.hidden = false; input.value = ''; filter(); input.focus(); }
    function hide() { pal.hidden = true; if (lastFocus && lastFocus.focus) lastFocus.focus(); }
    function go(l) {
      var h = l.getAttribute('data-href'); pal.hidden = true;
      var t = d.querySelector(h);
      if (t) {
        t.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' });
        if (!t.hasAttribute('tabindex')) t.setAttribute('tabindex', '-1');
        t.focus({ preventScroll: true });
      }
    }
    if (openBtn) openBtn.addEventListener('click', show);
    d.addEventListener('keydown', function (e) {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); if (pal.hidden) show(); else hide(); return; }
      if (pal.hidden) return;
      if (e.key === 'Escape') { e.preventDefault(); hide(); }
      else if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, visible().length - 1); mark(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); mark(); }
      else if (e.key === 'Enter') { e.preventDefault(); var v = visible(); if (v[sel]) go(v[sel]); }
      else if (e.key === 'Tab') { e.preventDefault(); input.focus(); }
    });
    input.addEventListener('input', filter);
    Array.prototype.forEach.call(items, function (l) {
      l.addEventListener('click', function () { go(l); });
      l.addEventListener('mousemove', function () { var v = visible(); var i = v.indexOf(l); if (i >= 0 && i !== sel) { sel = i; mark(); } });
    });
    pal.addEventListener('click', function (e) { if (e.target === pal) hide(); });
  })();
})();
