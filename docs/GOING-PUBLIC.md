# Going public

The one-time moves that open this repository: the course videos go to a
private repository and the engine's history starts fresh. Written down here
because neither is a code change; delete the file once it is done.

## Why the split

`topics/` holds the NUS course videos (today: the HBM topic under
`computer-architecture/`). The engine is meant to be public; the course
material is not. The engine now reads the topics tree from
`BLENDERKC_TOPICS` (the environment, then a `BLENDERKC_TOPICS=` line in the
checkout's `.env`, then `<checkout>/topics`), so the two no longer have to
share a repository.

## Step 1: the private topics repository

To run by hand; no repositories are created here.

`topics/` is gitignored, so the simplest arrangement is no repository at
all: the tree stays put, untracked, inside the checkout — how remotionkc
and revideokc keep their course videos today. A private repository adds
backup and history, nothing more.

1. Make a new **private** repository, e.g. `c-wenlong/blenderkc-topics`.
   Two ways to seed it:
   - *Keep the history:* push a copy of this repository, then delete
     everything but `topics/` in one commit. The HBM topic's storyboard and
     shot history stays browsable.
   - *Simple:* `git init`, copy `topics/` in, one commit. The shot history
     is short and remains in this repository's private history either way.
2. In every blenderkc checkout that builds videos, point at it in the
   checkout's `.env` (it is per-clone configuration, like `BLENDERKC_HOME`):

   ```
   BLENDERKC_TOPICS=~/src/blenderkc-topics/topics
   ```

   `blenderkc home` reports where the tree resolves; `blenderkc topics`
   should list the same topics as before.
3. Nothing downstream changes: `topics --json` reports the same shape for
   self-learn's catalog, and `build --host` pushes the configured tree to
   `<snapshot>/topics/` on the render host, which needs no configuration.

Keep `topics/demo/kit-tour` in **this** repository: it is the documented
example (docs/KIT.md links to it) and gives a public clone something that
renders. Only `topics/computer-architecture/` is private.

## Step 2: fresh history for the public repository

`topics/` is in this repository's history (added in PR #4, touched through
PR #14), so flipping visibility on this repository publishes the course
videos even after they are deleted from the worktree. The public repository
starts with fresh history:

1. Finish step 1, then delete `topics/computer-architecture/` from the
   worktree and commit (the demo stays).
2. Fresh root commit on an orphan branch, then rename over main:

   ```
   git checkout --orphan fresh
   git add -A
   git commit -m "blenderkc: narrated photoreal 3D explainers in Blender"
   git branch -f main fresh && git checkout main
   ```

3. Push to a **new public** repository — never to this remote: the old
   objects would still be reachable by SHA. The clean way to keep the name:
   rename this repository (e.g. `blenderkc-private`, stays private as the
   archive with full history), create `c-wenlong/blenderkc` public, push the
   orphan `main` there.
4. Point self-learn's `.gitmodules` at the public URL and update the
   submodule pin; set the repo description and topics on GitHub.
5. Local clones flip remote with
   `git remote set-url origin https://github.com/c-wenlong/blenderkc`.

## Checks already run

- `gitleaks git` over the full history: no secrets found (the `.env` was
  never committed).
- Licence: MIT, in `LICENSE`. The Inter fonts under `blenderkc/fonts/` are
  SIL OFL (`OFL.txt` ships beside them), fine to redistribute.
- `requirements-narration.txt` pins `c-wenlong/gnosia-audio`, a private
  repository: `make narration-deps` works only for its collaborators until
  that repo is public too. Without it there is no narration, so `narrate`
  and any full `build` are out (`--no-narrate` only reuses narration a
  `narrate` already staged). What still works voice-free: `still --timing
  estimate`, `topics`, `new`, and the test suite. A documented limitation
  for the public repo, not a blocker.
