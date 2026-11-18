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
    var groups = ['.stages', '.trio', '.bento', '.readers-grid', '.layers', '.install-grid', '.faq'];
    var singles = '.section-head, .hub-fig, .vault-fig, .readers-head, .feature > div, .feature > figure, .grid-2 > div:last-child > .card, .reqs';
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

  /* ---------- vault: the statement's ending cycles while it's on screen ----------
     The word is aria-hidden; screen readers get a static "yours" beside it.
     Under reduced motion it stays on "yours". */
  (function () {
    var rot = $('rot'); if (!rot || reduce) return;
    var words = ['yours', 'on your Mac', 'plain Markdown', 'readable without us', 'one commit per run'];
    var i = 0, timer = null;
    function next() {
      rot.classList.add('out');
      setTimeout(function () { i = (i + 1) % words.length; rot.textContent = words[i]; rot.classList.remove('out'); }, 250);
    }
    function start() { if (!timer) timer = setInterval(next, 2600); }
    function stop() { clearInterval(timer); timer = null; }
    if (!hasIO) { start(); return; }
    new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) start(); else stop(); }); }).observe(rot);
  })();

  /* ---------- chapter strip: shown while a numbered chapter is on screen ---------- */
  (function () {
    var strip = $('chapters'); if (!strip || !hasIO) return;
    var links = {};
    each('a', function (a) { links[a.getAttribute('href').slice(1)] = a; }, strip);
    var current = null;
    function paint() {
      strip.classList.toggle('on', !!(current && links[current]));
      Object.keys(links).forEach(function (k) {
        var on = k === current;
        links[k].classList.toggle('on', on);
        if (on) { links[k].setAttribute('aria-current', 'true'); var w = links[k].parentNode; if (w.scrollWidth > w.clientWidth) w.scrollLeft = links[k].offsetLeft - w.offsetLeft - 16; }
        else links[k].removeAttribute('aria-current');
      });
    }
    var cio = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) current = e.target.id; else if (current === e.target.id) current = null; });
      paint();
    }, { rootMargin: '-45% 0px -50% 0px' });
    each('main > section', function (s) { if (s.id) cio.observe(s); });
  })();

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

  /* ---------- problem: the same morning, without and with Scout ---------- */
  (function () {
    var fig = $('morning'); if (!fig) return;
    var list = $('stages'), tally = $('tally'), lbl = $('stages-lbl');
    var stages = list.querySelectorAll('.stage');
    var views = {
      without: {
        lbl: 'One morning, without Scout',
        tally: '→ And nothing you worked out this morning is kept for tomorrow.',
        s: [['8', 'tools with something new', 'Slack · Gmail · Linear · Calendar · GitHub · Granola · Drive · WhatsApp'],
            ['113', 'unread messages, threads and notifications'],
            ['3', 'tools that disagree about the same thing: the ticket, the pull request and the thread'],
            ['4×', 'more before evening, as new replies arrive']]
      },
      'with': {
        lbl: 'The same morning, with Scout',
        tally: '→ And what it worked out stays in ~/Scout, one commit per run.',
        s: [['1', 'list, with its sources', 'in the app, as Markdown, or by Slack DM or Telegram'],
            ['6', 'items that need you, each tagged verified, single-source or contradicted'],
            ['1', 'contradiction held for you, with both sides shown'],
            ['0×', 'checks by hand: catch-up scans run at 11:00, 13:00, 17:00 and 19:00']]
      }
    };
    function show(v) {
      var data = views[v];
      fig.setAttribute('data-view', v);
      each('.seg button', function (b) { b.setAttribute('aria-pressed', b.getAttribute('data-view') === v ? 'true' : 'false'); }, fig);
      function write() {
        lbl.textContent = data.lbl; tally.textContent = data.tally;
        Array.prototype.forEach.call(stages, function (st, i) {
          st.querySelector('b').textContent = data.s[i][0];
          st.querySelector('.d').textContent = data.s[i][1];
          var apps = st.querySelector('.apps-list'); if (apps) apps.textContent = data.s[i][2];
        });
        list.classList.remove('swap');
      }
      if (reduce) { write(); return; }
      list.classList.add('swap'); setTimeout(write, 200);
    }
    /* Scrolling drives it. Where the figure fits on screen it sticks under the header
       inside a taller track and flips halfway through; where it doesn't (short phones),
       it flips once you've scrolled most of it into view. Scrolling back flips it back.
       The buttons scroll to the matching point, so scroll and buttons never disagree. */
    var track = $('morning-track'), prog = $('stages-prog'), head = d.querySelector('.site-head');
    var current = 'without', figH = 0, stick = 76, scrolly = false, manual = false, ticking = false;
    function setView(v) { if (v !== current) { current = v; show(v); } }
    function progress() {
      var extra = track.offsetHeight - figH;
      return extra > 0 ? Math.min(1, Math.max(0, (stick - track.getBoundingClientRect().top) / extra)) : 0;
    }
    function update() {
      ticking = false;
      if (scrolly) { var p = progress(); prog.style.width = (p * 100).toFixed(1) + '%'; setView(p >= 0.5 ? 'with' : 'without'); }
      else if (!manual) setView(fig.getBoundingClientRect().bottom < innerHeight * 0.8 ? 'with' : 'without');
    }
    function measure() {
      stick = (head ? head.offsetHeight : 60) + 16;
      figH = fig.offsetHeight;
      scrolly = !!track && figH < innerHeight - stick - 24;
      if (track) {
        track.classList.toggle('scrolly', scrolly);
        figH = fig.offsetHeight; /* the progress bar only shows in scrolly mode */
        track.style.setProperty('--fig-h', figH + 'px'); track.style.setProperty('--stick', stick + 'px');
      }
      update();
    }
    function onScroll() { if (!ticking) { ticking = true; requestAnimationFrame(update); } }
    each('.seg button', function (b) {
      b.addEventListener('click', function () {
        var v = b.getAttribute('data-view');
        if (scrolly) {
          var y = scrollY + track.getBoundingClientRect().top - stick + (v === 'with' ? 0.75 : 0.15) * (track.offsetHeight - figH);
          window.scrollTo({ top: y, behavior: reduce ? 'auto' : 'smooth' });
        } else { manual = true; setView(v); }
      });
    }, fig);
    if (hasIO) new IntersectionObserver(function (es) { es.forEach(function (e) { if (!e.isIntersecting) manual = false; }); }).observe(fig);
    addEventListener('scroll', onScroll, { passive: true });
    addEventListener('resize', measure, { passive: true });
    if (d.fonts && d.fonts.ready) d.fonts.ready.then(measure);
    measure();
  })();

  /* ---------- everything is a file: follow a [[link]] into the vault ---------- */
  (function () {
    var bar = $('vault-bar'), trace = $('vb-trace'); if (!bar || !trace) return;
    var def = trace.innerHTML, active = null;
    var map = {
      priya: [['priya', 'proj'], '<b>[[priya]]</b> → people/priya.md. The owner field points at a person note, not a name typed into a meeting.'],
      export: [['export', 'pr412'], '<b>[[export]]</b> → topics/export.md, cited to the pull request it came from in sources/.'],
      permissions: [['permissions', 'sync'], '<b>[[permissions]]</b> → topics/permissions.md, cited to the meeting in sources/ where it was agreed.'],
      alex: [['alex', 'proj'], '<b>[[alex]]</b> → people/alex-rivera.md. One short name, one person note, so Alex never splits in two.']
    };
    function clear() {
      active = null; bar.classList.remove('tracing'); trace.innerHTML = def;
      each('.fc.hot', function (f) { f.classList.remove('hot'); }, bar);
      each('.spec-code button.l', function (b) { b.classList.remove('on'); b.setAttribute('aria-pressed', 'false'); });
    }
    function show(b) {
      var k = b.getAttribute('data-link'), m = map[k]; if (!m) return;
      clear(); active = k; bar.classList.add('tracing');
      m[0].forEach(function (f) { var el = bar.querySelector('[data-f="' + f + '"]'); if (el) el.classList.add('hot'); });
      each('.spec-code button.l[data-link="' + k + '"]', function (x) { x.classList.add('on'); x.setAttribute('aria-pressed', 'true'); });
      trace.innerHTML = m[1];
    }
    each('.spec-code button.l', function (b) {
      b.setAttribute('aria-pressed', 'false');
      b.addEventListener('click', function () { show(b); });
      if (fine) { b.addEventListener('mouseenter', function () { show(b); }); b.addEventListener('mouseleave', clear); }
      b.addEventListener('focus', function () { show(b); });
      b.addEventListener('blur', clear);
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
          at(800, function () { t.remove(); msg('', 'Got it: ' + p[1] + ' the billing fix. Saved to your notes, and I won’t ask again. ✓'); replay.focus(); });
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
