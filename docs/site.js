/* Scout website: theme toggle and copy buttons. No dependencies, no tracking.
   The page works fully without this script. */
(function () {
  var root = document.documentElement;
  var media = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

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

  var themeBtn = document.getElementById('theme-btn');
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

  var status = document.getElementById('copy-status');
  function done(btn, ok) {
    var text = btn.querySelector('span');
    if (text) text.textContent = ok ? 'Copied' : 'Press ⌘C';
    if (status) status.textContent = ok ? 'Install command copied to the clipboard.' : 'Copy failed. Select the command and copy it.';
    setTimeout(function () { if (text) text.textContent = 'Copy'; }, 2000);
  }

  Array.prototype.forEach.call(document.querySelectorAll('[data-copy]'), function (btn) {
    btn.addEventListener('click', function () {
      var value = btn.getAttribute('data-copy');
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(value).then(function () { done(btn, true); }, function () { done(btn, false); });
      } else {
        done(btn, false);
      }
    });
  });
})();
