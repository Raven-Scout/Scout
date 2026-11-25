/* Scout website: one anonymous page-view count per page load, sent to PostHog.
   This is the site's only request to another server, and privacy.html#website
   describes it. Keep the two in step: change what this sends, change that page.

   What it sends: the page's address without its query string or #fragment,
   any utm_* campaign tags, the linking site's domain (never its full URL), the
   page title and the screen width. The ID is random and new on every page
   load, so two page views can't be tied to one person. No cookies, no
   localStorage, no fingerprinting, and no PostHog script: this file is the
   whole client.

   It sends nothing when: the browser asks not to be tracked (Do Not Track or
   Global Privacy Control), the page isn't served from the live site (a local
   preview or file://), or POSTHOG_KEY is empty. */
(function () {
  var POSTHOG_KEY = 'phc_w32gYfKcEpFCFsKvtYG48CQA7kauyZUaSafK38rbGHKj'; /* public project key (safe to publish) */
  var POSTHOG_HOST = 'https://us.i.posthog.com'; /* the project's region: us. (an EU project would be eu.) */
  var LIVE_HOST = 'raven-scout.github.io';

  var nav = window.navigator || {};
  var loc = window.location;
  if (!POSTHOG_KEY || loc.hostname !== LIVE_HOST) return;
  if (nav.doNotTrack === '1' || window.doNotTrack === '1' || nav.globalPrivacyControl === true) return;
  if (!window.fetch) return;

  function randomId() {
    var c = window.crypto;
    if (c && c.randomUUID) return c.randomUUID();
    var b = new Uint8Array(16);
    if (c && c.getRandomValues) c.getRandomValues(b);
    else for (var i = 0; i < 16; i++) b[i] = Math.floor(Math.random() * 256);
    return Array.prototype.map.call(b, function (x) { return (x + 256).toString(16).slice(1); }).join('');
  }

  var props = {
    $current_url: loc.origin + loc.pathname,
    $host: loc.host,
    $pathname: loc.pathname,
    $title: document.title,
    $screen_width: window.screen ? window.screen.width : undefined,
    $lib: 'scout-site',
    $process_person_profile: false
  };

  var ref = '';
  try { ref = document.referrer ? new URL(document.referrer).hostname : ''; } catch (e) {}
  if (ref === loc.hostname) ref = '';
  props.$referrer = ref ? 'https://' + ref : '$direct';
  props.$referring_domain = ref || '$direct';

  try {
    new URLSearchParams(loc.search).forEach(function (v, k) {
      if (/^utm_(source|medium|campaign|term|content)$/.test(k)) props[k] = v.slice(0, 200);
    });
  } catch (e) {}

  var id = randomId();

  try {
    window.fetch(POSTHOG_HOST + '/i/v0/e/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ api_key: POSTHOG_KEY, event: '$pageview', distinct_id: id, properties: props }),
      keepalive: true,
      credentials: 'omit',
      referrerPolicy: 'no-referrer'
    }).catch(function () {});
  } catch (e) {}
})();
