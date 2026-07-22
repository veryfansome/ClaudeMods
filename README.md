# ClaudeMods

Claude Code plugins, served from this repo as a local marketplace.

## Plugins

- **[evolve](plugins/evolve/)** — a ShinkaEvolve-inspired evolutionary search that works with any project: inventor agents propose candidates over a marked code region or a chunk registry, and a deterministic engine owns selection (archive, weighted parent sampling, novelty dedup, pristine-clone scoring, noise-floored promotion, holdout firewall). See its [README](plugins/evolve/README.md).
- **[shared-memory](plugins/shared-memory/)** — distills generalizable lessons out of per-project auto-memory into a common, git-shareable store, so a correction Claude learns in one project changes how it works in every project. See its [README](plugins/shared-memory/README.md) and [roadmap](plugins/shared-memory/ROADMAP.md).
