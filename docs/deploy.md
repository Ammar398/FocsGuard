# Putting FocusGuard on the internet

FocusGuard has two halves, and only one of them goes on a server:

| Part | Where it runs | Why |
|---|---|---|
| **Server**: API, database, portal website | Render (this guide) | so managers, employees and students can sign in from anywhere |
| **Desktop agent + Helper**: camera, phone and eye analysis | each person's own Windows computer | video never leaves the computer; only numbers are uploaded |

So after deploying, every computer that is monitored still installs FocusGuard
once. A website alone cannot run the camera analysis.

The server is one Docker image (`Dockerfile`) that serves the API **and** the
portal from a single address, plus a Postgres database. `render.yaml` describes
both, so Render creates them in one step.

---

## 1. Put the code on GitHub

Render deploys from a Git repository.

1. Sign in at <https://github.com> and create a **private** repository, e.g. `focusguard`
   (no README, no .gitignore - the project has its own).
2. Give the repository address to Claude, or run:

   ```powershell
   git remote add origin https://github.com/<you>/focusguard.git
   git push -u origin main
   ```

   Windows asks you to sign in to GitHub in the browser the first time.

`.env`, the local databases, model weights and `dev_outbox/` are git-ignored and
are never uploaded.

## 2. Create the server on Render

1. Sign in at <https://render.com> (signing in with GitHub is easiest).
2. **New → Blueprint**, pick the `focusguard` repository, then **Apply**.
3. Render creates:
   * `focusguard-db`: a Postgres database;
   * `focusguard`: the website, built from `Dockerfile`. The first build takes about 5 minutes.
4. When it shows **Live**, open its address, e.g. `https://focusguard-xxxx.onrender.com`.
   `…/health` should answer `{"status":"ok", …, "database":"postgresql"}`.

Render generates `JWT_SECRET_KEY` itself; it is never in the repository.

## 3. Create the accounts again

The online server starts with an **empty** database. Accounts on this PC are not
copied. On the website:

* **Register your organisation** → you are the manager;
* **Employees → Add employee**, with a portal password;
* **Tasks → New task**, assigned to the employee;
* students use **Create a student account**.

## 4. Each monitored computer (employee or student)

Nobody needs a terminal. After signing in on the computer to monitor:

1. The **This computer** panel says *FocusGuard is not installed on this computer*
   and shows **Download FocusGuard for Windows**.
2. Open `FocusGuard-Setup.zip` → **Extract all** → double-click **Install FocusGuard**.
   It installs Python 3.11 if needed (via winget, for this user only), FocusGuard,
   the AI models, and starts the Helper now and at every Windows sign-in. The
   first run takes 5-10 minutes.
3. The portal notices the Helper by itself; the download disappears and
   **Connect this computer** takes its place. Approve the Windows dialog, then
   start a session or an assigned task.

The zip is built by the server (`api/v1/downloads.py`) with **its own address**
in `server.txt`, so the computer uploads to this server and its Helper trusts
this website and no other. Developers can do the same by hand:

```powershell
python -m focusguard_agent.helper --server https://focusguard-xxxx.onrender.com --install-startup
```

Chrome may ask once whether the site may **access devices on your local
network**. Allow it: that is the website talking to the Helper on this same
computer (`127.0.0.1`), nothing else on your network.

## 5. Free plan limits

| Limit | What you notice |
|---|---|
| The website sleeps after 15 minutes without visitors | the first page load takes up to a minute; uploads from agents also wake it, so they are never lost |
| The free Postgres database expires 30 days after creation | upgrade it (Basic, about $6/month) before then, or the data is deleted |

For a demonstration or FYP defence the free plan is enough. For real use,
upgrade both.

## 6. Email codes (sign-up verification and password reset)

Render's free plan blocks the SMTP mail ports, so Gmail/Outlook SMTP never
connects from there. FocusGuard sends through **Resend** over HTTPS instead.

1. In Resend, verify a domain you own (**Domains**), then create an API key
   (**API Keys** → *Sending access*).
2. In Render → `focusguard` → **Environment**:
   * `FOCUSGUARD_RESEND_API_KEY` = the key (`re_...`)
   * `FOCUSGUARD_MAIL_FROM` = `FocusGuard <no-reply@your-verified-domain>`
3. **Save, rebuild and deploy.** `https://<your-site>/health` then shows
   `"email": "resend", "email_verification": true`.

**No domain? Use Brevo instead** (free, 300 emails a day): create a Brevo
account, add your Gmail under **Senders** and click the link Brevo emails you,
turn off **Security → Authorised IPs** (Render's addresses change), create an
API key under **SMTP & API → API Keys**, then set `FOCUSGUARD_BREVO_API_KEY` and
`FOCUSGUARD_MAIL_FROM` = `FocusGuard <your.address@gmail.com>`. Mail sent "from
gmail.com" by another service can land in Spam; the sign-up page says so.

`FOCUSGUARD_REQUIRE_EMAIL_VERIFICATION` is `auto`: codes are asked for exactly
when email can be sent. Set it to `0` to switch verification off on purpose.
If an email is refused (wrong key, unverified domain), the reason is in the
service's **Logs**, and the person can press "send a new code" straight away.

## Trying the production setup on this PC

```powershell
cd frontend; npm run build; cd ..
$env:FOCUSGUARD_PORTAL_DIR = "frontend/dist"
.venv\Scripts\python.exe -m uvicorn focusguard_backend.main:app --port 8100 --proxy-headers
```

Open <http://127.0.0.1:8100>: the same single-address site Render runs.
