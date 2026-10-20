# Going public

What it takes to flip this repository — or one shaped like it — from
private to public. Public is a one-way door: once flipped, the repo is
indexable, cloneable by anyone, and every ref anyone has ever fetched is
out of your control. Everything below is a precondition, not a nicety.

## The requirements

1. **Licence.** `LICENSE` (MIT) at the root. Without one, publishing the
   code grants no right to use, modify or redistribute it.
2. **Redistributable assets.** Everything tracked must be yours or
   permissively licensed. Here: the Inter fonts under `blenderkc/fonts/`
   are SIL OFL (`OFL.txt` ships beside them). Vendored code needs the
   same check per vendored file — anything that cannot be redistributed
   must be dropped or replaced before the flip.
3. **No secrets in the worktree or the history.** `gitleaks git` over the
   full history, not just HEAD — `.env` was never committed here, so the
   scan is clean. A secret that ever entered a commit is public the
   moment the repo is; rotating it out of HEAD does not help.
4. **No private content, in the worktree or the history.** This is the
   check that decides whether a visibility flip is even possible (below).
   `topics/` here is content, not engine: the public tree ships only the
   `demo/kit-tour` example and gitignores the rest; course videos live
   untracked in the owner's checkout or in a private repo pointed to by
   `BLENDERKC_TOPICS`.
5. **Docs.** README with install and use, CHANGELOG, the design doc. A
   public repo is read by people who were not in the room.
6. **CI.** A workflow on every push/PR. Public repositories get free
   GitHub-hosted runners; private ones on this account did not get any,
   so CI only became real after the flip.
7. **Known limitations written down.** `requirements-narration.txt` pins
   `c-wenlong/gnosia-audio`, a private repository: a public clone cannot
   `make narration-deps` until it opens too. Everything voice-free —
   `still --timing estimate`, `topics`, `new`, the tests — works.
8. **Metadata.** Description, license detected, no leftover junk files.
   Existing PRs, issues, stars and watchers come along unchanged when
   visibility flips on the same repo.

## Two ways over the line

**A. Flip visibility** — `gh repo edit --visibility public
--accept-visibility-change-consequences`. Works only when the whole
history satisfies every requirement above. If private content ever sat
in a commit, deleting it from the worktree is not enough, and rewriting
history is not a fix either: GitHub keeps `refs/pull/*` for every PR ever
opened and serves objects by SHA, so the old commits stay fetchable.
A force-push leaves a public leak.

**B. Fresh history** — what this repository did, because `topics/` was in
its history since PR #4:

1. Rename the private repo (`gh repo rename blenderkc-private`): keeps
   all history, PRs and issues, stays private as the archive.
2. Build a clean tree (`git archive HEAD | tar -x`, drop the private
   content, add the gitignore rules that keep it out), one root commit.
3. Create the public repo under the original name and push. The URL
   never changes, so `.gitmodules`, clones and links all keep working.
4. Repoint checkouts (`git fetch origin && git reset --hard origin/main`),
   restore the untracked `topics/`, bump the submodule pin in the parent.
5. Set the description; CI runs for real on the first public push.

## Where this repo landed

- `c-wenlong/blenderkc` — public, fresh history, CI green.
- `c-wenlong/blenderkc-private` — private archive of the pre-public
  history.
- Course topics: untracked in the checkout (the remotionkc and revideokc
  pattern); a private `blenderkc-topics` mirror is optional backup, not
  required, since the engine resolves `topics/` by default.
- Open item: gnosia-audio is private, so narration is owner-only until
  that repository follows the same checklist.
