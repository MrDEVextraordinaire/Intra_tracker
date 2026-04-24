#!/usr/bin/env python3
import json
import os
os.environ['MPLCONFIGDIR'] = '/tmp/matplotlib'

from student_ranker import score_single_exam, build_df

# Get raw data
with open('data/exam_ranks.json') as f:
    raw = json.load(f)

gomar = raw.get('gomar', {})
rank_02 = gomar.get('exam_ranks', {}).get('rank_02', [])

print(f'gomar rank_02 attempts:')
for a in rank_02:
    print(f'  occ {a["occurrence"]}: date={a["date"]}, score={a["score"]}')

print('\n--- Scoring rank_02 ---')
result = score_single_exam(rank_02)
print(f'score_single_exam: {result}')

# Build full df
print('\n--- Building DataFrame ---')
df = build_df()
print(f'Total users: {len(df)}')

if 'gomar' in df.index:
    g = df.loc['gomar']
    print(f'\ngomar scores:')
    print(f'  piscine_avg: {g["piscine_avg"]}')
    print(f'  rank_score_raw: {g["rank_score_raw"]}')
    print(f'  streak_mult: {g["streak_mult"]}')
    print(f'  rank_score: {g["rank_score"]}')
    print(f'  final_score: {g["final_score"]}')
    print(f'\n  per-exam:')
    print(f'    rank_02_score: {g["rank_02_score"]}')
    print(f'    rank_02_tier: {g["rank_02_tier"]}')
    print(f'    rank_02_attempts: {g["rank_02_attempts"]}')
    print(f'    rank_02_weeks_late: {g["rank_02_weeks_late"]}')
else:
    print('gomar not in df!')