# Deploy notes

## Local Docker build / run
docker build -t flurazide-bot .
# Example run (assuming you have .env with BOT_TOKEN, etc):
docker run --env-file .env flurazide-bot

## Railway
- Push repo to Git.
- In Railway dashboard, create a new project and choose "Deploy from repo".
- Choose "Use Dockerfile" (Railway will detect Dockerfile automatically).
- Add required environment variables (BOT_TOKEN, DB creds, GOOGLE_* if used).
- Set the service to use the `worker` process (Procfile) if prompted.
