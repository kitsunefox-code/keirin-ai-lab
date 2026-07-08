# Internet Learning Policy

This project can learn from public web pages, but it should not copy a decade of copyrighted material into the local database.

## Safe Storage

- Results and numeric race data: store structured fields.
- Race comments: store short excerpts, emotion tags, and feature values.
- Columns and articles: store URL, title, fingerprint, short excerpt, and tags.
- Paid, login-only, or app-only data: do not fetch automatically.

## Source Priority

1. KEIRIN.JP official schedule/results/basic data.
2. WINTICKET public racecards, only low-frequency and URL-driven.
3. netkeirin news/columns/database, only as tags and short excerpts unless explicit rights are available.
4. Other voting sites only after their terms are reviewed.

## Backfill Shape

For 5-10 years, run in small batches:

1. Create the month plan.
2. Discover official race/result URLs for one month.
3. Save race entries/results.
4. Save comments as features.
5. Save columns as emotion/background tags.
6. Retrain and measure top-1/top-3/calibration by holdout month.

Never run an unrestricted crawler.
