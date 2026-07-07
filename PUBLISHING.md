# Publishing fish-tank

fish-tank is developed across **two local clones of the same public GitHub repo**
([`github.com/benskamps/fish-tank`](https://github.com/benskamps/fish-tank)). This split
exists so that in-progress and experimental work can never reach the public repo by
accident — publishing is always a deliberate act from one specific clone.

If you only have one clone, you don't need any of this. This doc is for the author's
two-clone setup.

## The two clones

| Clone | Path | Role | `git push` |
|---|---|---|---|
| **Dev** | `~/projects/fish-tank` | Day-to-day development, experiments, night-shift runs. | **Disabled by design** |
| **Public mirror** | `~/projects/fish-tank-public` | The deliberate publish surface. This is the *only* clone that pushes to GitHub. | Enabled |

Both clones have `origin` pointing at `github.com/benskamps/fish-tank.git`. The difference
is a single git config value.

## Why push is disabled on the dev clone

The dev clone's push URL is set to a deliberately-invalid sentinel string, so any
`git push` from it fails immediately instead of publishing:

```console
$ cd ~/projects/fish-tank
$ git config --get remote.origin.pushurl
DISABLED_no_push__private_dev__use_fish-tank-public_to_publish

$ git push            # fails on purpose — the pushurl is not a real URL
```

Rationale: the dev clone is where messy, half-finished, and throwaway work happens
(spikes, night-shift branches, sanitization passes before code is fit for a public repo).
Disabling push there removes the single biggest way that unfinished work leaks to a public
repo — a reflexive `git push`. **Fetch still works**, so the dev clone can always pull the
latest `master` down to stay in sync.

> To inspect or restore the sentinel (do not push from the dev clone):
> ```bash
> git -C ~/projects/fish-tank config remote.origin.pushurl \
>   'DISABLED_no_push__private_dev__use_fish-tank-public_to_publish'
> ```

## How the public repo gets updated

All publishing flows through the **public mirror clone** (`~/projects/fish-tank-public`).
Everything lands on `master` via a PR — no direct pushes to `master`.

### A. Change authored in the public mirror (simplest)

Use this when the change is small or you're already working in the mirror.

```bash
cd ~/projects/fish-tank-public
git checkout master
git pull --ff-only origin master          # start from the latest published state
git checkout -b docs/my-change            # a fresh branch
# ...edit, then...
git add -A && git commit -m "docs: my change"
git push -u origin docs/my-change
gh pr create --fill                       # open the PR against benskamps/fish-tank
# after review/CI: gh pr merge --squash --delete-branch
git checkout master && git pull --ff-only origin master
```

The repo squash-merges PRs, so after merging, delete the branch (`--delete-branch`
handles the remote; `git branch -D <branch>` locally — squash-merged branches are not
recognized by the safe `git branch -d`).

### B. Change authored in the dev clone (bridge it over)

Use this when the work already lives on a branch in `~/projects/fish-tank` and you want to
publish it without re-doing it. Because the dev clone can't push, pull the branch into the
mirror over the local filesystem, then push from the mirror:

```bash
cd ~/projects/fish-tank-public
git remote add dev ~/projects/fish-tank        # one-time: add the dev clone as a local remote
git fetch dev                                  # pull its branches over the filesystem
git checkout -b my-feature dev/my-feature      # materialize the dev branch here
git push -u origin my-feature
gh pr create --fill
# (git remote remove dev  # optional cleanup)
```

Alternative for a single commit or a patch series, no extra remote:

```bash
# in the dev clone:
git -C ~/projects/fish-tank format-patch origin/master --stdout > /tmp/change.patch
# in the mirror:
cd ~/projects/fish-tank-public && git checkout -b my-feature master && git am /tmp/change.patch
git push -u origin my-feature && gh pr create --fill
```

## Keeping the dev clone in sync

The dev clone drifts behind as the mirror publishes. Re-sync it any time with a fetch +
fast-forward (this never needs push):

```bash
cd ~/projects/fish-tank
git checkout master
git pull --ff-only origin master
```

## Quick reference

- **Where do I push from?** Only `~/projects/fish-tank-public`.
- **Why did my push fail in `~/projects/fish-tank`?** That's intentional — see above.
- **How do I publish dev-clone work?** Bridge it to the mirror (section B), then PR from the mirror.
- **How do I update the dev clone?** `git pull --ff-only origin master` — fetch always works there.
