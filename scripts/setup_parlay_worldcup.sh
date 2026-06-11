#!/bin/bash
# Quick setup script for World Cup parlay feature
# Run this to initialize everything at once (after Supabase tables are created)

set -e

echo "🌍 Setting up World Cup Parlay..."
echo ""

# 1. Check environment
echo "1️⃣  Checking environment variables..."
if [ -z "$FOOTBALL_DATA_API_KEY" ]; then
    echo "⚠️  FOOTBALL_DATA_API_KEY not set. Export it first:"
    echo "   export FOOTBALL_DATA_API_KEY='your-api-key'"
    echo ""
    read -p "Continue anyway? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 2. Verify dependencies
echo "2️⃣  Checking Python dependencies..."
python -c "import requests; import supabase" && echo "✓ Dependencies OK" || {
    echo "✗ Missing dependencies. Run: pip install -r requirements.txt"
    exit 1
}

# 3. Sync matches
echo ""
echo "3️⃣  Syncing World Cup matches..."
python src/sync_worldcup_matches.py

# 4. Test API
echo ""
echo "4️⃣  Testing API endpoints (requires running webhook server)..."
echo "⏸️  Make sure the webhook server is running in another terminal:"
echo "   gunicorn webhook.app:app --bind 0.0.0.0:5000"
echo ""
read -p "Continue? (y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "Testing GET /parlay/worldcup/matches..."
    curl -s "http://localhost:5000/parlay/worldcup/matches?phone=%2B1234567890" | python -m json.tool | head -20
fi

echo ""
echo "✅ Setup complete!"
echo ""
echo "📋 Next steps:"
echo "  1. Test the web interface: python -m http.server 8000 (then visit http://localhost:8000/web/parlay.html)"
echo "  2. Set up cron jobs in railway.yaml (see PARLAY_SETUP.md)"
echo "  3. Add link to parlay in web/app.html"
echo "  4. Deploy to Railway"
echo ""
echo "📖 Full docs: see PARLAY_FEATURE.md and PARLAY_SETUP.md"
