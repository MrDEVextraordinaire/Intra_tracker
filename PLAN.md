# 42 Campus Tracker - Project Plan

## Goal
Track student performance across 13 metrics from location data + API data.

## Status (2026-04-21)

### Working ✅
- Live location polling (`--poll`, snapshots in `data/snapshots.jsonl`)
- Exam rank scores via HTML scraping (`exam_scrape.py`, extracts actual scores from project pages)
- Location tracking (snapshots saved every poll)

### Data Collected
| Data | Location | Records |
|------|----------|---------|
| Students | `data/students.json` | 398 |
| Locations | `data/snapshots.jsonl` | Multiple snapshots |
| Exam ranks | `data/exam_ranks.json` | 381 students |

## 13 Performance Metrics

### Tier 1 (Must Have)
1. **Weighted exam score** - From exam ranks and regular exams API
2. **Outstanding flags** - From projects API (`validated?` + score ≥ 100?)
3. **Correction distribution** - From scale_teams API (future)
4. **First-pass rate** - From exam ranks (first attempt pass/fail)
5. **Logtime efficiency** - From coalitions API (future)

### Tier 2 (Should Have)
6. **Mutual correction pairs** - From scale_teams API (who corrects whom)
7. **Hibernation/cramming cycles** - From location history (gaps/bursts)
8. **Night owl patterns** - From location history (late-night sessions)

### Tier 3 (Nice to Have)
9. **Seat proximity clusters** - From location history (who sits near whom)
10. **Proximity↔correlation** - Correlate proximity with correction rates
11. **Favorite host/cluster** - Most frequent cluster/seat per student

### Tier 4 (Advanced)
12. **Retry count** - From exam ranks (occurrence count)
13. **Negative flag rate** - From correction points (negative flags)

## Next Steps

1. **Complete location history saving** - Fix per-student location files (currently not saving)
2. **Add scale_teams API** - Peer evaluations (metrics 3,6,10,11,13)
3. **Build analytics dashboard** - Compute all 13 metrics from collected data
4. **Add regular exams API** - For weighted exam scores (metric 1)
5. **Optimize scrapers** - Add caching, better error handling

## Usage

```bash
# Poll locations continuously (15 min interval)
python3 cli.py --poll

# Scrape exam ranks (requires fresh cookie from browser)
python3 exam_scrape.py --limit 100  # Test with 100 students

# View collected data
ls -la data/exam_ranks.json
head -5 data/exam_ranks.json
```

## Files
- `exam_scrape.py` - HTML scraper for exam scores (works with React/Bootstrap layouts)
- `api_client.py` - OAuth + pagination (fixed)
- `tracker.py` - Location polling (saves snapshots)
- `models.py` - Data classes
- `storage.py` - JSON helpers
- `cli.py` - Command interface
- `PLAN.md` - This file