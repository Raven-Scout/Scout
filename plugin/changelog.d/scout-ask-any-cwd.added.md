- `scoutctl kb vault [--json]` prints the vault a KB read should use, from any working directory:
  `SCOUT_DATA_DIR`, else the vault holding the cwd, else the engine pointer's vault, else `~/Scout`. Exit 1
  means no vault is configured. It reads no note and writes nothing.
