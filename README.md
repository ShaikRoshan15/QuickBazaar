# QuickBazaar

QuickBazaar is an OLX-style classifieds web application built with **Python, Flask, SQLite, HTML, CSS and JavaScript**.

This repository is the **local Flask version only**. It does not contain Cloudflare Workers, Wrangler configuration, deployment caches, Worker dependencies, or deployment scripts.

## Features

- User registration and login
- Email OTP verification
- Forgot/reset password flow
- Create, edit and delete classified ads
- Browse and search listings
- User profiles
- Favorites
- Messaging/inbox
- Requests
- Admin dashboard
- Built-in chatbot interface
- SQLite database
- Responsive web UI

## Project Structure

```text
QuickBazaar/
├── app.py
├── requirements.txt
├── README.md
├── .env.example
├── .gitignore
├── schema.sql
├── run_local.bat
├── static/
│   ├── auth.js
│   ├── chatbot.js
│   ├── forms.js
│   ├── otp.js
│   └── style.css
└── templates/
    ├── base.html
    ├── index.html
    ├── auth.html
    ├── ad.html
    ├── post.html
    ├── profile.html
    ├── my_ads.html
    ├── inbox.html
    ├── requests.html
    ├── admin.html
    └── ...
```

## Run Locally

### Quick start on Windows

Double-click `run_local.bat`. It creates a virtual environment, installs the required packages, copies `.env.example` to `.env` if needed, and starts the application. Open **http://127.0.0.1:5000** in your browser.

To stop the application, press `Ctrl+C` in the command window.

### 1. Clone the repository

```bash
git clone YOUR_GITHUB_REPOSITORY_URL
cd QuickBazaar
```

### 2. Create a virtual environment

Windows:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Create `.env`

Copy `.env.example` to `.env`.

Windows:

```powershell
copy .env.example .env
```

macOS/Linux:

```bash
cp .env.example .env
```

### 5. Start the application

```bash
python app.py
```

Open:

**http://127.0.0.1:5000**

### Windows shortcut

You can also double-click:

```text
run_local.bat
```

It creates the virtual environment if necessary, installs dependencies and starts the application.

## OTP Testing Without Gmail

For local development, you can avoid configuring Gmail.

In `.env`:

```env
OTP_DEV_MODE=1
```

Start the application:

```bash
python app.py
```

The generated OTP will be printed in the terminal.

For real email delivery, set:

```env
OTP_DEV_MODE=0
SMTP_USER=quickbazaarofficials@gmail.com
SMTP_FROM=quickbazaarofficials@gmail.com
SMTP_PASS=YOUR_GMAIL_APP_PASSWORD
```

Use a **Gmail App Password**, not your normal Gmail account password.

## Database

QuickBazaar uses SQLite.

The database is created locally by the application. A generated database file should not be committed to GitHub.

If you want to start with a fresh database, stop the application and remove the generated SQLite database file, then start the app again.

## Environment Variables

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Flask session security |
| `SMTP_HOST` | SMTP server |
| `SMTP_PORT` | SMTP port |
| `SMTP_USER` | Email account |
| `SMTP_FROM` | Sender email |
| `SMTP_PASS` | Gmail App Password |
| `NOTIFY_EMAIL` | Notification recipient |
| `OTP_DEV_MODE` | Print OTP locally instead of sending email |
| `COOKIE_SECURE` | Secure cookie setting |
| `ANTHROPIC_API_KEY` | Optional AI fallback |
| `HOST` | Local server host |
| `PORT` | Local server port |

## GitHub Security

The following files are local/generated and must not be uploaded. `.gitignore` excludes them when you use Git:

```text
.env
.venv/
.secret_key
*.db
uploads/
__pycache__/
```

Use Git to publish this project, or manually leave these files out if uploading through GitHub's website; the website upload does not apply `.gitignore`.

Only `.env.example` belongs in the repository; it is a template. Do not put Gmail passwords, API keys, session secrets, personal database data, or uploaded user files in GitHub.

### Publish this project to GitHub

Create an empty repository on GitHub, then run these commands from the project folder. Replace `YOUR_USERNAME` and `YOUR_REPOSITORY` with your GitHub details:

```powershell
git init
git add .
git status
```

Review the `git status` output and make sure it does not list `.env`, `.secret_key`, `.venv`, `quickbazaar.db`, or anything inside `uploads/`. If the list is correct, commit and push:

```powershell
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

If you previously added a private file to Git's staging area, unstage it before committing with `git rm --cached <file>`. If a credential was ever pushed to GitHub, remove it from the repository and rotate/revoke that credential; deleting it in a later commit does not remove it from Git history.

## Tech Stack

- Python
- Flask
- SQLite
- HTML5
- CSS3
- JavaScript
- Werkzeug

## License

Add the license you prefer before publishing the repository.
