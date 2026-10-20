# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
keyed by date rather than by release, because nothing here is versioned.

## [Unreleased]

### Added
- **`BLENDERKC_TOPICS`: the topics tree can live outside the checkout.**
  Resolved like the data root (the environment, then a line in the
  checkout's `.env`, then `<checkout>/topics`), absolute only. Every verb
  reads topics from it and `new` writes into it, so the course videos can
  live in a private repository while the engine is public. `build --host`
  pushes it to `<snapshot>/topics/` by its own rsync — the host needs no
  configuration — and the snapshot's digest covers it, so two divergent
  topics trees never share a snapshot. `home` and `doctor` report where it
  resolves.
- MIT licence, CI and this changelog, ahead of the repository going public.
