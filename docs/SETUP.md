Setup Guide
This guide walks you through setting up Project Free Kick from scratch.
---
1. Twilio (WhatsApp messaging)
Sign up at twilio.com — free trial gives ~$15 credit
From the dashboard, copy your Account SID and Auth Token
Join the WhatsApp sandbox:
Go to Messaging → Try it out → Send a WhatsApp message
Note the sandbox number (e.g. `+14155238886`)
On your phone, send the join code (e.g. `join water-reader`) to that number via WhatsApp
Each pilot user must do this from their own phone
2. football-data.org
Sign up at football-data.org
Copy your API key from the dashboard
Free tier supports Liverpool fixture lookups
3. GitHub repository
Create a new repo called `project-free-kick`
Upload all the files from this folder
Go to Settings → Secrets and variables → Actions
Add these secrets:
Name	Value
`TWILIO_ACCOUNT_SID`	Your full 34-char SID starting with `AC`
`TWILIO_AUTH_TOKEN`	Your 32-char auth token
`TWILIO_FROM_NUMBER`	`whatsapp:+14155238886`
`USER_PHONE_NUMBERS`	Comma-separated list, e.g. `whatsapp:+12039394042,whatsapp:+12035551234`
`FOOTBALL_API_KEY`	Your football-data.org key
4. Test it
Go to Actions → Free Kick — Nudge Scheduler → Run workflow
Within 30 seconds, every user in `USER_PHONE_NUMBERS` should get a test WhatsApp message
If anyone doesn't get the message, check they've joined the Twilio sandbox
5. You're live
Mon/Wed/Fri at 9am UTC: scheduled nudges fire
Every 15 mins 2-10pm UTC: checks for upcoming Liverpool matches and sends pre-match predictions
7pm UTC daily: checks finished matches and sends results
---
Troubleshooting
No texts arriving
Check Twilio → Monitor → Logs → Messages for the actual status
Confirm all users have joined the sandbox (`join water-reader` to `+14155238886`)
Verify GitHub secrets have no leading/trailing spaces
HTTP 401 errors
Account SID or Auth Token is wrong/truncated — copy them fresh from Twilio
SID is exactly 34 characters starting with `AC`
Auth Token is exactly 32 characters
Match not detected
Check Liverpool's actual fixture list on football-data.org
Free tier has rate limits — wait 60s and retry
