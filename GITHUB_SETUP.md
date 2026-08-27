# BillBook — GitHub Setup Guide (first-time, start to finish)

Follow this top-to-bottom ONCE on your Windows PC. After the last section
you'll have: a private GitHub repo, your full v8.14.3 code pushed, secrets
configured for CI, and a daily one-command workflow.

Total time: ~30 minutes (plus ~2 min of GitHub account setup if you don't
have one yet).

---

## 0. What you're about to do (30-second version)

```
your PC                          GitHub.com
────────                         ────────────
D:\Coding\Personal\
  BILL_MANAGEMENT_SOFTWARE  ───►  private repo "billbook" (only you can see it)
  (git push from PowerShell)     • code versioned — nothing is ever lost
                                 • free CI runs your 576+ tests on every push
                                 • release tags build installers automatically
```

**Why PRIVATE?** BillBook is commercial software you sell. A public repo means
anyone can download your full source for free. Private = free for solo devs,
unlimited private repos on the free plan.

---

## 1. Install Git on Windows (5 min)

1. Download: https://git-scm.com/download/win
2. Run the installer. On the choice screens, these defaults are fine:
   - "Adjusting your PATH environment" → **Git from the command line and
     also from 3rd-party software** (this is the middle option — keep it)
   - "Choosing the SSH executable" → bundled OpenSSH
   - "Configuring the line ending conversions" → **Checkout Windows-style,
     commit Unix-style** (default)
   - Everything else → Next, Next, Install.
3. Close ALL open PowerShell windows, then open a NEW one and verify:

```powershell
git --version
# expected: git version 2.4x.x.windows.x  (any 2.4x is fine)
```

If you see "git is not recognized", reboot once (PATH refresh) and retry.

---

## 2. Tell Git who you are (1 min — one time ever)

```powershell
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
```

Use the SAME email as your GitHub account (step 3) or your commits won't
link to your profile picture. Check what you set:

```powershell
git config --global --list
```

---

## 3. Create the GitHub account + empty repo (5 min)

1. Go to https://github.com/signup if you don't have an account.
2. Once logged in, click **+** (top-right) → **New repository**.
3. Fill in:
   - Repository name: `billbook`
   - Visibility: **Private**  ← important, do not miss this
   - DO NOT tick "Add a README", ".gitignore", or "license" — we already
     have .gitignore and LICENSE.txt in the project; adding GitHub's would
     clash.
4. Click **Create repository**.
5. GitHub shows you a page with an URL like
   `https://github.com/YOUR_USERNAME/billbook.git` — copy it. We'll call
   this `<YOUR_REPO_URL>` below.

---

## 4. Put the latest code in place (5 min)

**Work from the fresh zip**, not your old folder — the zip (v8.14.3)
contains fixes your local copy doesn't have yet.

```powershell
# unzip the new archive somewhere clean
Expand-Archive C:\Users\you\Downloads\BILL_MANAGEMENT_SOFTWARE_v8.14.3.zip `
               -DestinationPath D:\Coding\Personal\v8.14.3 -Force
cd D:\Coding\Personal\v8.14.3\BILL_MANAGEMENT_SOFTWARE
```

> Your old `D:\Coding\Personal\BILL_MANAGEMENT_SOFTWARE` folder stays
> untouched. Once the repo is pushed (step 5), make the new folder your
> working copy and delete the old one when you're comfortable.

If you already have local data you care about (a `data\billbook.db` with
real sales), copy it across AFTER the push — remember `data/` is
git-ignored, so it never travels through GitHub, only through your own
file copies.

---

## 5. First push (5 min)

Still inside the project folder:

```powershell
# start version control in this folder
git init -b main

# the .gitignore already excludes secrets/data — trust but verify (step 6)
git add .

# first commit
git commit -m "BillBook v8.14.3 — initial import"

# connect it to GitHub (paste YOUR url from step 3)
git remote add origin <YOUR_REPO_URL>

# push
git push -u origin main
```

Windows will pop up a "Git Credential Manager" window the first time —
sign in with your GitHub account in the browser and click Authorize.
Git remembers it afterwards.

Refresh the GitHub page in your browser — you should now see the whole
project tree.

---

## 6. VERIFY THE SAFETY NET (2 min — do not skip)

The `.gitignore` is pre-configured to keep dangerous things OUT of GitHub.
After `git add .` and the push, run this — the output must be EMPTY:

```powershell
git ls-files | Select-String -Pattern "\.env$|\.key$|\.pem$|^data/|pos_backup_example|\.tauri|\.db$"
```

If it prints ANY line, STOP — something sensitive is tracked. Untrack it
with `git rm --cached <path>` and fix the `.gitignore` rule before pushing
again.

What should never be in the repo, and why:

| Path | Why excluded |
|---|---|
| `.env` | contains your real APP_PASSWORD / Google client secret |
| `data/` | real customer PII — sales, customers, phone numbers (PECA!) |
| `desktop/.tauri/updater-private.key` | updater signing key = anyone with it can ship "updates" to your clients |
| `*.key`, `*.pem`, `*.crt` | any other key material |
| `build/dist/`, `installer/*.exe` | build outputs, not source (huge + regenerable) |
| `desktop/src-tauri/target/` | Rust build cache (GBs) |
| `pos_backup_example/` | sample data with real business PII |

Also double-check on github.com itself: browse the repo in the browser and
confirm there is NO `data` folder and NO `.env` file listed.

---

## 7. Secrets for CI + auto-updater (5 min)

### 7a. Generate the updater keypair (on YOUR machine)

```powershell
cd D:\Coding\Personal\v8.14.3\BILL_MANAGEMENT_SOFTWARE
powershell -ExecutionPolicy Bypass -File build\windows\generate_updater_keys.ps1
```

This creates `desktop\.tauri\updater-private.key` (SECRET — git-ignored)
and prints the **public key** in green.

### 7b. Paste the public key into the app config

Open `desktop\tauri.conf.json`, find:

```json
"pubkey": "REPLACE_WITH_REAL_ED25519_PUBKEY_BEFORE_ENABLING"
```

Replace the placeholder with the printed public key (one long line). Also
replace `BILLBOOK_OWNER/BILLBOOK_REPO` in the `endpoints` URL with your
real `username/repo` from step 3. Leave `"active": false` for now — you
only flip it to true when you actually start shipping auto-updates.

Commit the change:

```powershell
git add desktop\tauri.conf.json
git commit -m "config: real updater pubkey + repo URL"
git push
```

### 7c. Back up the private key OUTSIDE this machine

The keygen script already warned you — do it now, not "later":
copy `desktop\.tauri\updater-private.key` to a USB stick AND a personal
cloud drive AND print the base64 on paper if you're paranoid (you should
be). **If you lose it, no future update can ever be accepted by any
installed client.** It is intentionally git-ignored — GitHub is NOT a
backup of this file.

### 7d. Add repo secrets (so CI can sign builds)

On github.com → your repo → **Settings** → **Secrets and variables** →
**Actions** → **New repository secret**, add:

| Secret name | Value |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | full CONTENTS of updater-private.key (paste the text, not the path) |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | the password you typed during keygen — or skip this secret entirely if you left it empty |

(The variable names matter — Tauri v2 reads exactly these; older
`TAURI_PRIVATE_KEY` names are silently ignored, which produced unsigned
bundles before v8.14.2.)

---

## 8. Your daily workflow from now on (2 min to learn)

That's genuinely all there is day-to-day:

```powershell
git add .
git commit -m "short message: what you changed and why"
git push
```

Do this at least at the end of every work session — a commit is a
checkpoint you can always roll back to. `git log --oneline` shows your
history; `git diff` shows uncommitted changes; `git checkout -- <file>`
discards accidental edits in one file.

### Shipping a release (a tagged version)

When v8.15.0 is ready:

```powershell
git tag v8.15.0
git push origin v8.15.0
```

Pushing a `v*` tag triggers the **Desktop Build** workflow (below) which
builds the Tauri shell and attaches installers to a GitHub Release.

---

## 9. What the two GitHub Actions workflows do

Both are already in `.github/workflows/` — they run themselves, you
never install anything:

1. **Tests** (`test.yml`) — on every push to `main`: spins up a clean
   Ubuntu runner, installs requirements, runs the full pytest suite.
   You get an email only when something FAILS. Green check on the repo
   front page = all tests passing.
2. **Desktop Build** (`desktop.yml`) — only when you push a `v*` tag:
   builds the Tauri desktop shell for Windows (+ macOS) with the signing
   key from your repo secrets, and creates a GitHub Release with the
   installers attached.

**Cost note:** GitHub Free gives 2,000 CI minutes/month. The Windows
build uses regular minutes; the macOS job burns **10x** minutes (a
12-min macOS job eats 120 of your 2,000). If you never sell the macOS
build, open `.github/workflows/desktop.yml` and delete the whole
`build-macos:` job (and remove it from the `release: needs:` line) —
that keeps you comfortably inside the free tier.

---

## 10. Troubleshooting quick list

| Symptom | Fix |
|---|---|
| `git: command not found` | Reboot after Git install (PATH refresh), new PowerShell window |
| Push asks for password and rejects it | GitHub removed account passwords — use the Credential Manager browser popup, or a Personal Access Token (Settings → Developer settings) |
| `git ls-files` check shows a `.db` file | `git rm --cached <file>`, commit, push — the file stays on disk, just leaves the repo |
| CI Tests workflow red on first push | Click the failing run → most likely a pre-existing flaky test, not your push. Compare against the run before yours |
| Want to undo the last commit (not yet pushed) | `git reset --soft HEAD~1` |
| Committed something secret by accident (already pushed) | Change the secret immediately (rotate password/key), then consider `git filter-repo` — prevention via step 6 is far easier |

---

## Checklist (tick as you go)

- [ ] Git installed, `git --version` works
- [ ] `user.name` / `user.email` configured
- [ ] GitHub account + **private** repo created
- [ ] v8.14.3 zip unzipped, `git init`, committed, pushed
- [ ] `git ls-files` safety check is EMPTY
- [ ] Updater keypair generated, pubkey pasted into tauri.conf.json
- [ ] Private key backed up to USB + cloud (outside this PC)
- [ ] Repo secrets `TAURI_SIGNING_PRIVATE_KEY` (+ password) added
- [ ] First push shows green check on the Tests workflow
