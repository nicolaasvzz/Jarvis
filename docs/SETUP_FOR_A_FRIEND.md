# Setting up your own Jarvis

Jarvis is a personal AI assistant that runs on **your own Windows PC** and
takes instructions from your phone. It plans tasks before doing them, asks
before anything dangerous, and tells you what it did.

Everything below runs on *your* machine, including the AI model itself —
nothing is shared with anyone else, and there is no API key to buy.

Takes about 10 minutes, plus a download.

---

## 1. Install the prerequisites

- **Python 3.11 or newer** — https://python.org (during install, tick
  *"Add Python to PATH"*)
- **Git** — https://git-scm.com
- **Ollama** — https://ollama.com — this is what actually runs the AI model
  on your PC. Install it and leave it running.

Check they worked — open PowerShell and run:

```powershell
python --version
git --version
ollama --version
```

All three should print a version number. Now download the model Jarvis
uses (about 5 GB, one time):

```powershell
ollama pull qwen3:8b
```

> **How much computer do I need?** `qwen3:8b` wants roughly 8 GB of free
> RAM, and is much faster with a dedicated graphics card. If your PC
> struggles, `ollama pull qwen3:4b` and set `LLM_MODEL=qwen3:4b` in step 5.

## 2. Get Jarvis

```powershell
git clone <REPO-URL> jarvis
cd jarvis
```

*(Ask whoever sent you this for the repo URL and the branch name to use.)*

## 3. Install it

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[phone]"
```

That gives you the assistant plus phone control. Optional add-ons, install
whichever you want:

```powershell
pip install -e ".[desktop]"    # control apps/windows, click, type
pip install -e ".[browser]"    # web automation
playwright install chromium    #   (run this after the browser one)
pip install -e ".[vision]"     # read the screen (also needs Tesseract OCR)
```

## 4. Check the AI model is working

```powershell
jarvis brain
```

You want to see:

```
provider: ollama
model:    qwen3:8b
endpoint: http://localhost:11434
connected: yes — 1 model(s) downloaded
model 'qwen3:8b' is available — Jarvis is ready.
```

If it says it can't reach Ollama, start it (open the Ollama app, or run
`ollama serve`) and try again. If it says the model isn't downloaded, run
the `ollama pull` from step 1.

Nothing you type ever leaves your PC: Jarvis talks to Ollama on
`localhost`, and Ollama runs the model on your own hardware.

## 5. Configure

```powershell
copy .env.example .env
copy config\config.example.yaml config\config.yaml
```

`.env` already selects the local model, so there is nothing you must
change:

```
LLM_PROVIDER=ollama
LLM_MODEL=qwen3:8b
```

*(Only if you'd rather use Anthropic's Claude than a model on your own PC:
run `pip install -e ".[llm]"`, set `LLM_PROVIDER=anthropic`, and put your
own key from https://console.anthropic.com in `ANTHROPIC_API_KEY`. That
one is paid per use and sends your requests to Anthropic — the local
setup above does neither.)*

`.env` is gitignored, so nothing in it ever gets committed.

Now open `config\config.yaml` and **point Jarvis at a test folder first** —
this is the only directory it's allowed to touch, and it cannot escape it:

```yaml
files:
  root: C:/Users/<your-username>/JarvisTest
```

Create that folder. Once you trust Jarvis, you can repoint this at a real
folder you want managed.

## 6. Try it on the PC first

```powershell
jarvis tools
```

Lists what Jarvis can do on your machine (depends which add-ons you
installed). Then run one task:

```powershell
jarvis run "make a file called hello.txt that says hi"
```

It will show you its plan and progress. If a step is dangerous (deleting a
file, for example) it stops and asks `y/N` first.

If that worked, you're up and running.

## 7. Control it from your phone (Telegram)

This only makes *outbound* connections, so it needs **no wifi, no LAN, no
port forwarding, and no exposed server** — any internet on the PC works,
even tethering to your phone.

1. In the Telegram app, message **@BotFather**, send `/newbot`, follow the
   prompts, and copy the bot token it gives you.
2. Add it to `.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   ```
3. In `config\config.yaml`, turn the bridge on:
   ```yaml
   telegram:
     enabled: true
   ```
4. Start it:
   ```powershell
   jarvis phone
   ```
5. Message your new bot anything. It replies with your **chat id**. Put that
   in the config so only *you* can command your Jarvis, then restart
   `jarvis phone`:
   ```yaml
   telegram:
     enabled: true
     owner_chat_id: 123456789
   ```

Now, from Telegram you can:

- **Send any task** — just type it normally.
- **Approve or deny** dangerous actions by tapping **✅ Allow** / **⛔ Deny**.
- `/status` — recent tasks · `/task <id>` — details · `/cancel <id>` — stop one
- `/tools` — what it can do · `/help` — all commands

Leave `jarvis phone` running on the PC and you can drive it from anywhere.

---

## Safety notes worth reading

- **Jarvis can control your computer.** The confirmation prompts (delete
  files, send email, install software, change system settings, spend money,
  run elevated commands) are your safety net — leave them switched on.
- **Start in a test folder** (step 5) until you trust it with real files.
- **Keep `owner_chat_id` set.** Without it, anyone who finds your bot could
  send it commands. With it, strangers are refused.
- **Don't expose the HTTP API to the internet.** Leave `api.host` at
  `127.0.0.1` (the default) and use the Telegram bridge for remote access.
- **Keep your Telegram bot token private** — it's the key to the bridge.
  If it leaks, revoke it with @BotFather and set a new one.

## If something breaks

- Logs are JSON-lines at `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` — every
  command, decision, and error is in there.
- `jarvis brain` is the first thing to run: it says whether Jarvis can
  reach the model at all, and what to do if it can't.
- `jarvis tools` showing fewer tools than expected usually means an optional
  add-on isn't installed (step 3).
- *"Could not reach Ollama"* means Ollama isn't running — start the Ollama
  app, or run `ollama serve`.
- *"Ollama does not have the model"* means the download in step 1 didn't
  finish — run `ollama pull qwen3:8b` again.
- Tasks that take a long time are normal on a slower PC the first time a
  model is used (the weights load into memory). If they time out, raise
  `llm.timeout` in `config\config.yaml`.
