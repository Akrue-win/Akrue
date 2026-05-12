# Project Free Kick — Webhook Server

A Flask app that catches WhatsApp replies via Twilio and logs them to Google Sheets.

---

## What this does

When a user replies "1", "2", or "3" to a Free Kick WhatsApp prompt:
1. Twilio forwards the message here via POST
2. This server finds their active match in the Google Sheet
3. Logs their prediction
4. Sends back a confirm message

---

## Deploy to Railway

### 1. Sign in to Railway
- Go to railway.app and sign in with GitHub

### 2. Create a new project
- Click **New Project** → **Deploy from GitHub repo**
- Select your `project-free-kick` repo
- Important: set the **Root Directory** to `webhook/` in the service settings

### 3. Add environment variables
In the Railway project, go to **Variables** and add:

| Name | Value |
|------|-------|
| `SHEET_ID` | Your Google Sheet ID (the long string from the URL) |
| `GOOGLE_CREDS_JSON` | Paste the *entire contents* of your service account JSON file |

> ⚠️ For `GOOGLE_CREDS_JSON`, open the JSON file in a text editor, select all, copy, and paste the whole thing as the value. Railway accepts multi-line values.

### 4. Get your public URL
- Railway gives you a URL like `https://project-free-kick-production.up.railway.app`
- Find it under **Settings → Domains** in your service
- If there isn't one, click **Generate Domain**

### 5. Configure Twilio
- Go to Twilio Console → **Messaging → Settings → WhatsApp sandbox settings**
- Set the **"When a message comes in"** webhook URL to:
  ```
  https://YOUR-RAILWAY-URL.up.railway.app/whatsapp
  ```
- Method: **HTTP POST**
- Click **Save**

---

## Test

1. Send any message to the Twilio sandbox WhatsApp number from your phone
2. You should get an automatic reply explaining how to use the system
3. Check the Railway logs to see the incoming message logged

---

## Files

- `app.py` — Flask app handling Twilio webhooks
- `requirements.txt` — Python dependencies
- `Procfile` — tells Railway how to start the app
- `railway.json` — Railway build configuration
