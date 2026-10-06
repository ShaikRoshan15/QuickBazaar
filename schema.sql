-- QuickBazaar D1 schema. Safe to run repeatedly (all statements are IF NOT EXISTS).
-- The Worker also creates any missing table automatically on first request, so this step is optional but recommended.
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, name TEXT, email TEXT UNIQUE, phone TEXT, pw TEXT, verified INTEGER DEFAULT 0, profile_pic TEXT);
CREATE TABLE IF NOT EXISTS ads(id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, description TEXT, price INTEGER, category TEXT, city TEXT, image TEXT, status TEXT DEFAULT 'active', views INTEGER DEFAULT 0, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS favorites(user_id INTEGER, ad_id INTEGER, PRIMARY KEY(user_id, ad_id));
CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, ad_id INTEGER, sender_id INTEGER, receiver_id INTEGER, body TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS ad_images(id INTEGER PRIMARY KEY, ad_id INTEGER, filename TEXT);
CREATE TABLE IF NOT EXISTS stored_images(id TEXT PRIMARY KEY, data TEXT NOT NULL, content_type TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS interests(id INTEGER PRIMARY KEY, ad_id INTEGER, buyer_id INTEGER, status TEXT DEFAULT 'pending', UNIQUE(ad_id, buyer_id));
CREATE TABLE IF NOT EXISTS reviews(id INTEGER PRIMARY KEY, seller_id INTEGER, reviewer_id INTEGER, ad_id INTEGER, rating INTEGER, comment TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, UNIQUE(seller_id, reviewer_id));
CREATE TABLE IF NOT EXISTS reports(id INTEGER PRIMARY KEY, ad_id INTEGER, user_id INTEGER, reason TEXT);
CREATE TABLE IF NOT EXISTS otp_codes(id INTEGER PRIMARY KEY, email TEXT, purpose TEXT, code TEXT, expires_at REAL, attempts INTEGER DEFAULT 0, sent_at REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS notifications(id INTEGER PRIMARY KEY, user_id INTEGER, kind TEXT, body TEXT, created TIMESTAMP DEFAULT CURRENT_TIMESTAMP, read INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS idx_otp_lookup ON otp_codes(email, purpose);
CREATE INDEX IF NOT EXISTS idx_ads_status_created ON ads(status, created);
CREATE INDEX IF NOT EXISTS idx_ads_user ON ads(user_id);
CREATE INDEX IF NOT EXISTS idx_ad_images_ad ON ad_images(ad_id);
CREATE INDEX IF NOT EXISTS idx_interests_seller ON interests(ad_id, status);
CREATE INDEX IF NOT EXISTS idx_interests_buyer ON interests(buyer_id);
CREATE INDEX IF NOT EXISTS idx_messages_ad ON messages(ad_id, created);
