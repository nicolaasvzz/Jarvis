# Setting up your own Jarvis

Jarvis is a personal AI assistant that runs on **your own Windows PC** and
takes instructions typed, spoken, or sent from your phone. It plans tasks
before doing them, asks before anything dangerous, and tells you what it did.

Everything below sets up *your own* Jarvis — nothing is shared with whoever
sent you this. The AI thinking is done by Google's Gemini, and a **free**
API key covers it: no credit card, no powerful graphics card needed.

Takes about 10 minutes.

---

## The quick way

Open **PowerShell** and paste the block from the
[README's "Start here" section](../../README.md#start-here-windows-one-paste).
It installs everything, asks for your Gemini key (step 1 below explains how
to get one), and opens the dashboard. If that worked, skip to
[step 6](#6-try-it). The steps below are the same thing done by hand.

## 1. Get a free Gemini API key

1. Go to **https://aistudio.google.com/apikey** and sign in with any Google
   account.
2. Click **Create API key** and copy it somewhere for a minute.

Treat it like a password — anyone with it can use your free quota.

## 2. Install the prerequisites

- **Python 3.11 or newer** — https://python.org (during install, tick
  *"Add Python to PATH"*)
- **Git** — https://git-scm.com

Check they worked — open PowerShell and run:

```powershell
python --version
git --version
```

## 3. Get Jarvis

```powershell
git clone https://github.com/nicolaasvzz/Jarvis. jarvis
cd jarvis\backend
```

Jarvis has two folders: `backend` (the assistant) and `frontend` (the web
dashboard). Everything you run lives in `backend`.

## 4. Install it

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[api,dash,voice,phone]"
```

That gives you the assistant, its dashboard, a voice, and phone control.
Optional add-ons, install whichever you want:

```powershell
pip install -e ".[desktop]"    # control apps/windows, click, type
pip install -e ".[browser]"    # web automation
playwright install chromium    #   (run this after the browser one)
pip install -e ".[vision]"     # read the screen (also needs Tesseract OCR)
```

## 5. Configure

```powershell
copy .env.example .env
copy config\config.example.yaml config\config.yaml
jarvis token
notepad .env
```

In `.env`, paste your Gemini key and the token `jarvis token` just printed:

```
GEMINI_API_KEY=your-gemini-key
JARVIS_API_TOKEN=the-long-token-it-printed
```

`.env` is never uploaded or shared — it stays on your PC.

Then check the key works:

```powershell
jarvis brain
```

You want to see `connected: yes` and `Jarvis is ready.` If it says the key
was rejected, re-copy it from step 1.

Now open `config\config.yaml` and **point Jarvis at a test folder first** —
this is the only directory it's allowed to touch, and it cannot escape it:

```yaml
files:
  root: C:/Users/<your-username>/JarvisTest
```

Create that folder. Once you trust Jarvis, you can point this at a real
folder you want managed.

## 6. Try it

```powershell
jarvis dash
```

Your browser opens the dashboard. Type a request in the bar at the bottom —
for example *make a file called hello.txt that says hi* — and watch it plan
and work. If a step is dangerous (deleting a file, say), an **Allow / Deny**
card appears and nothing happens until you choose.

Or from the terminal:

```powershell
jarvis run "make a file called hello.txt that says hi"
```

Every time after this, start it with:

```powershell
cd ~\jarvis\backend
.venv\Scripts\activate
jarvis dash
```

## 7. Control it from your phone (Telegram)

This only makes *outbound* connections, so it needs **no wifi setup, no
port forwarding, and no exposed server** — any internet on the PC works.

1. In the Telegram app, message **@BotFather**, send `/newbot`, follow the
   prompts, and copy the bot token it gives you.
2. Add it to `backend\.env`:
   ```
   TELEGRAM_BOT_TOKEN=123456:ABC-your-token
   ```
3. In `backend\config\config.yaml`, turn the bridge on:
   ```yaml
   telegram:
     enabled: true
   ```
4. Restart `jarvis dash` (or run `jarvis phone` for just the bridge).
5. Message your new bot anything. It replies with your **chat id**. Put that
   in the config so only *you* can command your Jarvis, then restart:
   ```yaml
   telegram:
     enabled: true
     owner_chat_id: 123456789
   ```

Now, from Telegram you can send any task, tap **✅ Allow** / **⛔ Deny** on
dangerous steps, and use `/status`, `/task <id>`, `/cancel <id>`, `/tools`,
`/help`.

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
- **Keep your keys private** — the Gemini key, `JARVIS_API_TOKEN`, and the
  Telegram bot token. If one leaks, make a new one (AI Studio, `jarvis
  token`, or @BotFather) and replace it in `.env`.

## If something breaks

- `jarvis brain` is the first thing to run: it says whether Jarvis can reach
  Gemini at all, and what to do if it can't.
- *"Gemini rejected the API key"* — re-copy the key from
  https://aistudio.google.com/apikey into `GEMINI_API_KEY` in `backend\.env`.
- *"Gemini's rate limit was hit"* — the free tier allows a limited number of
  requests per minute and per day. Wait a minute; if it keeps happening, set
  `agent: {pool_size: 2}` in `config\config.yaml`.
- *"Missing required secret"* — you're running `jarvis` from the wrong
  folder. `cd` into `jarvis\backend` first; that's where `.env` lives.
- `jarvis tools` showing fewer tools than expected usually means an optional
  add-on isn't installed (step 4).
- Logs are JSON-lines at `%LOCALAPPDATA%\jarvis\Logs\jarvis.jsonl` — every
  command, decision, and error is in there.
