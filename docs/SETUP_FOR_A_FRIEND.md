# Setting up your own Jarvis

Jarvis is a personal AI assistant that runs on **your own Windows PC** and
takes instructions from your phone. It plans tasks before doing them, asks
before anything dangerous, and tells you what it did.

Everything below runs on *your* machine with *your* own API key — nothing is
shared with anyone else.

Takes about 10 minutes.

---

## 1. Install the prerequisites

- **Python 3.11 or newer** — https://python.org (during install, tick
  *"Add Python to PATH"*)
- **Git** — https://git-scm.com

Check they worked — open PowerShell and run:

```powershell
python --version
git --version
```

Both should print a version number.

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
pip install -e ".[llm,phone]"
```

That gives you the assistant plus phone control. Optional add-ons, install
whichever you want:

```powershell
pip install -e ".[desktop]"    # control apps/windows, click, type
pip install -e ".[browser]"    # web automation
playwright install chromium    #   (run this after the browser one)
pip install -e ".[vision]"     # read the screen (also needs Tesseract OCR)
```

## 4. Get your own Anthropic API key

1. Sign up at **https://console.anthropic.com**
2. Create an API key and copy it.
3. Add a small amount of credit. Each task costs roughly a few cents —
   **you are paying for your own usage**, so start small and watch the
   dashboard until you know your pattern.

**Keep this key secret — it's tied to your billing.** Never paste it into a
chat, screenshot, or a public repo. If it leaks, revoke it in the console and
make a new one.

## 5. Configure

```powershell
copy .env.example .env
copy config\config.example.yaml config\config.yaml
```

Open `.env` in Notepad and fill in your key:

```
ANTHROPIC_API_KEY=sk-ant-...your-key...
```

`.env` is gitignored, so your key never gets committed.

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
- **Your API key is money.** Keep it private; revoke and rotate if unsure.

## If something breaks

- Logs are JSON-lines at `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` — every
  command, decision, and error is in there.
- `jarvis tools` showing fewer tools than expected usually means an optional
  add-on isn't installed (step 3).
- `Missing required secret 'anthropic_api_key'` means `.env` isn't filled in
  or you're running from a different folder.
