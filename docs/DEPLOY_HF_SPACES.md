# Deploying SpyMonk on Hugging Face Spaces (free)

This puts the whole app (UI and API) in one free Hugging Face Space. Your archive is kept in a private Hugging Face dataset, so it survives restarts.

## What you get

| | Free Space (CPU basic) |
| --- | --- |
| Hardware | 2 vCPU, 16 GB RAM, enough for the local embedding model |
| Address | `https://<your-name>-<space-name>.hf.space` |
| Sleep | After 48 hours without visits; the next visit wakes it |
| Disk | Wiped on every restart, rebuild, or wake-up |
| Your data | Restored from your private backup dataset at every start; changes are copied there every 15 seconds and at shutdown |

The first visit after a restart or sleep takes about 1–2 minutes while the app restores your archive and rebuilds its search index. The first build of the Space takes about 10 minutes.

## One-time setup

### 1. Create a Hugging Face account

Sign up at [huggingface.co/join](https://huggingface.co/join). Below, `YOUR-NAME` is your username.

### 2. Create the private backup dataset

Go to [huggingface.co/new-dataset](https://huggingface.co/new-dataset), name it `spymonk-data`, choose **Private**, and create it. The app refuses to use a public dataset, because the archive holds your questions and sources.

### 3. Create two access tokens

At [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens):

- **Deploy token** (stays on your computer): type **Write**. It lets the deploy script create and update the Space.
- **Backup token** (goes into the Space): type **Fine-grained**, with write access to the contents of `YOUR-NAME/spymonk-data` only. If it ever leaked, it could touch nothing else.

### 4. Choose an app password

The app asks for this token before it does anything, so only you can use your Space. Generate one and save it somewhere safe:

```powershell
.\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 5. Log in on your computer

```powershell
.\.venv\Scripts\hf.exe auth login
```

Paste the **deploy token** when asked.

## Deploy

In PowerShell, from the project folder:

```powershell
$env:APP_API_TOKEN = "<your app password from step 4>"
$env:BACKUP_HF_TOKEN = "<your backup token from step 3>"

.\.venv\Scripts\python.exe scripts\deploy_hf_space.py --space YOUR-NAME/spymonk `
  --secret LLM_API_KEY --secret APP_API_TOKEN --secret BACKUP_HF_TOKEN `
  --variable LLM_BASE_URL --variable LLM_MODEL `
  --variable BACKUP_TARGET=hf-dataset --variable BACKUP_HF_REPO=YOUR-NAME/spymonk-data
```

- Settings without `=value` are read from your environment, then from your `.env` file. If your `.env` names the model key differently (for example `NVIDIA_API_KEY`), use that name instead of `LLM_API_KEY`.
- Add `--dry-run` first to see exactly which files and settings would be sent. Only code is uploaded: never `.env`, your data, your logs, or the index.
- The Space is created **private**. Opening it requires being logged in to Hugging Face, and then the app password.

Then open the **Logs** tab on the Space page and wait for `Application startup complete`.

## Bring your current archive along (optional)

To start the Space with the topics and PDFs you already have on this computer, copy them into the backup once, before the first start or between restarts:

```powershell
$env:BACKUP_TARGET = "hf-dataset"
$env:BACKUP_HF_REPO = "YOUR-NAME/spymonk-data"
$env:BACKUP_HF_TOKEN = "<your backup token>"
.\.venv\Scripts\python.exe -m backend.services.backup push
```

Restart the Space (Settings → Restart) to load it. To copy the hosted archive back to this computer later, run the same command with `pull`. `pull` never overwrites a local file.

## Use it

1. Open `https://YOUR-NAME-spymonk.hf.space` (or the Space page).
2. Enter your app password when asked.
3. Research a topic. About 15 seconds after it saves, the files appear in your dataset's **Files** tab: that's the backup working.

## Update the app

Run the same deploy command again. The Space rebuilds, restores your archive from the dataset, and rebuilds the index. Nothing is lost.

## Troubleshooting

| What you see | Why, and what to do |
| --- | --- |
| Runtime error: "Set APP_API_TOKEN" | The app won't run publicly without a password. Add the `APP_API_TOKEN` secret (Settings → Variables and secrets). |
| Runtime error: "Could not restore the archive" | The backup token can't read the dataset. Check `BACKUP_HF_REPO` and that `BACKUP_HF_TOKEN` has write access to that dataset. |
| Runtime error: "The backup dataset … is public" | Make the dataset private (dataset Settings → Change visibility). |
| Research fails: "Public web search is temporarily unavailable" | DuckDuckGo often blocks cloud servers. Try again later; a licensed search API is the lasting fix (see `docs/review-gap-analysis.md`, GAP-10). |
| "Invalid host header" | You're using a custom domain. Add it to the `APP_ALLOWED_HOSTS` variable and its `https://` origin to `APP_ALLOWED_ORIGINS`. |
| The app asks for the password again | It's kept for the browser tab only; paste it again. |

Logs are on the Space's **Logs** tab. Research run logs are also saved in your dataset under `logs/research/`.

## Settings reference

| Setting | Where | Value |
| --- | --- | --- |
| `LLM_API_KEY` (or your provider's key name) | Secret | Your model provider key |
| `APP_API_TOKEN` | Secret | Your app password |
| `BACKUP_HF_TOKEN` | Secret | Fine-grained token with write access to the dataset |
| `LLM_BASE_URL`, `LLM_MODEL` | Variable | As in your `.env` |
| `BACKUP_TARGET` | Variable | `hf-dataset` |
| `BACKUP_HF_REPO` | Variable | `YOUR-NAME/spymonk-data` |
| `BACKUP_INTERVAL_SECONDS` | Variable (optional) | Seconds between backups, default 15 |

The image already sets the data directory, hosted mode, and index rebuild on start. On a Space, the allowed host and origin come from the Space automatically.
