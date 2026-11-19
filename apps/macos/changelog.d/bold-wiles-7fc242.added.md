- **Settings ▸ Agent copies** (#550): each delegate mirror (a filtered copy of the vault one of your own agents
  reads off this Mac) with its GitHub repository or folder, when it last synced, why a sync was refused, what
  the copy held (including the files left out as agent config), and a warning when the link state isn't
  signed by this Mac. Preview and Sync now run the engine's own `scoutctl delegate preview` and `delegate sync`;
  linking, unlinking, deploy keys and re-signing stay in Terminal, and the card reads no key, token or state
  file itself. Hidden until the engine reports a delegate. Reads `scoutctl delegate status --json`
  (`ScoutTests/Fixtures/delegate-status.json` is its contract copy).
