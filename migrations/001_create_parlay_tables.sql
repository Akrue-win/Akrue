-- Create parlay tables for multi-match betting
-- This migration creates tables for parlay picks and results

CREATE TABLE IF NOT EXISTS parlay_picks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_phone TEXT NOT NULL,
    sport TEXT NOT NULL,
    parlay_locked BOOLEAN DEFAULT FALSE,
    picks_locked INT DEFAULT 0,
    locked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_parlay_user FOREIGN KEY (user_phone) REFERENCES users(phone_number) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_parlay_picks_user_sport ON parlay_picks(user_phone, sport);
CREATE INDEX IF NOT EXISTS idx_parlay_picks_locked ON parlay_picks(parlay_locked);

CREATE TABLE IF NOT EXISTS parlay_results (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_phone TEXT NOT NULL,
    sport TEXT NOT NULL,
    week_id TEXT,
    parlay_id UUID,
    picks_locked INT DEFAULT 0,
    picks_correct INT DEFAULT 0,
    bonus_earned DECIMAL(10, 2) DEFAULT 0,
    settled BOOLEAN DEFAULT FALSE,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT fk_parlay_result_user FOREIGN KEY (user_phone) REFERENCES users(phone_number) ON DELETE CASCADE,
    CONSTRAINT fk_parlay_result_picks FOREIGN KEY (parlay_id) REFERENCES parlay_picks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_parlay_results_user_sport ON parlay_results(user_phone, sport);
CREATE INDEX IF NOT EXISTS idx_parlay_results_week ON parlay_results(week_id);
CREATE INDEX IF NOT EXISTS idx_parlay_results_settled ON parlay_results(settled);
