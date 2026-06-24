# JellyReg — Invite-Code Jellyfin Registration Portal

A self-hosted web app that provides invite-code-based user registration for Jellyfin, with built-in Quick Connect device authorization. Designed to be a one-page "Join [Your Server]" portal for friends and family.

## Features

- **Invite-code registration** — generate single-use or multi-use codes with optional expiration
- **User login** — existing users can log in to authorize new devices
- **Quick Connect authorization** — enter a 6-digit code from your TV or app to pair it, no existing client needed
- **Admin panel** — manage invites, view registered users, configure server settings
- **Configurable branding** — server name and Jellyfin URLs editable via the admin UI

## Quick Start

### 1. Create a `.env` file

```bash
cp .env.example .env
```

Edit `.env` with your values:

| Variable | Required | Description |
|---|---|---|
| `JELLYFIN_API_KEY` | Yes | Jellyfin API key (from Dashboard → API Keys) |
| `JELLYFIN_URL` | Yes | Internal URL to Jellyfin (e.g. `http://localhost:8096`) |
| `ADMIN_PASSWORD_HASH` | Yes | SHA-256 hash of your admin password |
| `SERVER_NAME` | No | Display name (default: `Jellyfin`) |
| `JELLYFIN_PUBLIC_URL` | No | Public URL shown to users after registration |
| `DEFAULT_LIBRARIES` | No | Comma-separated library names to grant access (empty = all) |

Generate the admin password hash:

```bash
python3 -c "import hashlib; print(hashlib.sha256(b'your_password').hexdigest())"
```

### 2. Run with Docker

```bash
docker compose up -d --build
```

The app will be available at `http://localhost:5050`.

### 3. Configure (optional)

Log in at `/admin` with your admin password. Use the **Settings** card to set:

- **Server Name** — what your Jellyfin instance is called (shown on the Join page)
- **Jellyfin Internal URL** — the URL the backend uses to reach Jellyfin
- **Jellyfin Public URL** — the link users see after registering (e.g. your Tailscale or public domain)

## How It Works

### Registration Flow

1. Admin generates an invite code from the admin panel
2. Share the JellyReg URL + invite code with the person
3. They enter a username, password, and the invite code
4. JellyReg creates the Jellyfin user, enables libraries, and authenticates as them
5. The success page shows a link to open Jellyfin and a **Connect a device** section

### Quick Connect Flow

After registration (or login), users can pair a TV or phone app:

1. TV/app shows a 6-digit Quick Connect code
2. User enters the code on the JellyReg success page
3. JellyReg authorizes the code via the Jellyfin API using the user's session
4. The device connects automatically — no existing logged-in client needed

## API Endpoints

| Endpoint | Method | Auth | Purpose |
|---|---|---|---|
| `/api/register` | POST | none | Create Jellyfin user (invite code required) |
| `/api/login` | POST | none | Authenticate existing Jellyfin user |
| `/api/qc/authorize` | POST | session | Authorize a Quick Connect code |
| `/api/admin/invite` | POST | admin | Generate invite code |
| `/api/admin/invite/\<code\>` | DELETE | admin | Delete invite code |
| `/api/admin/settings` | GET | admin | Read settings |
| `/api/admin/settings` | POST | admin | Update settings |
| `/api/admin/health` | GET | none | Jellyfin connectivity check |

## Requirements

- Jellyfin 10.8+
- Docker

## License

MIT
