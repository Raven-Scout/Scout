- **The plugin manifest is ready for Anthropic's plugin directory** (`.claude-plugin/plugin.json`). The
  directory builds a listing from `plugin.json`, so the manifest now states the publisher as "Raven Scout"
  (with the org's GitHub URL), `license: MIT`, a listing icon (`.claude-plugin/icon.png`, the app's 512 px
  icon), and a shorter description that says what Scout does and that its output stays on your machine.
  It also lists under `network.hosts` the hosts Scout's own code reaches outside Claude and your
  connectors: GitHub (self-update and release notes), astral.sh and PyPI (installing the engine), and the
  optional Linear, Slack and Telegram integrations. That field is a disclosure shown on the listing; it
  doesn't allow or block anything. Both marketplace manifests name "Raven Scout" as owner and author too.
  - The README gains a short *What Scout runs, reads and sends* section: the launchd agents and their
    `--permission-mode auto` sessions, where data is read from and written to, every host Scout's own
    code contacts (the opt-in ones marked), and that nothing goes to the maintainers. The directory shows
    the README as the listing's description and asks it to disclose exactly this.
