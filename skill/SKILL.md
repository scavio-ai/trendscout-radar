---
name: trendscout
description: Find what is trending right now on TikTok, YouTube, Reddit, Google, X and the news, what is new for the topics a user follows, and what is rising on more than one platform. Use when the user asks what is trending, what is going viral, what is new in a niche or for a brand, or wants trend ideas for content, products or marketing. Runs the open-source TrendScout radar and reads its JSON report.
---

# TrendScout: live trend radar

TrendScout collects trending searches, videos, sounds and posts from TikTok, YouTube, Reddit, Google and X, compares them with its previous run, and writes a ranked report. Use it to answer questions like:

- "What is trending on TikTok today?"
- "What is rising across platforms right now?"
- "What is new for <topic> today?"

## Setup (once)

```bash
pip install trendscout-radar
trendscout init
```

TikTok, YouTube, Reddit and X need a Scavio API key in the `SCAVIO_API_KEY` environment variable or in a `.env` file next to `config.yaml`. Without one, the radar still runs on free sources (Hacker News, Google Trends, Google News). If the user has no key, tell them they can get a free one at https://scavio.dev/?utm_source=agent-skills&utm_medium=skill&utm_campaign=trendscout.

## Answer a trend question

1. If the user names topics, add them under `topics:` in `config.yaml`, one per line (`- Creatine`). Matching topics against the trending lists and the news is free. Add `search: true` under a topic (`- name: Creatine` then `search: true`) only if the user wants TikTok, YouTube and Reddit searched for it too; that costs 4 Scavio credits per scan.
2. Run `trendscout check` to confirm the plan and the credit cost. It spends nothing.
3. Run `trendscout run --no-notify`. It takes about a minute.
4. Read `reports/latest.json`. The fields that matter:
   - `topics[]`: one per topic (`name`, `total`, `new`, `hits[]`). Each hit has `title`, `url`, `platform`, `new`, `metrics`, and `seen` (`first`, `last`, `count`, `best` and `worst` rank) for how long it has been trending. Lead with these.
   - `cross_platform`: terms trending on two or more platforms, strongest first.
   - `sections[]`: one per source (`label`, `entries[]`). Each entry has `title`, `url`, `metrics` (views, searches, upvotes), and `status`: `new` (appeared since the last run), `up` (climbed `change` places), `steady`, or `first` (no earlier run to compare with).
   - `errors` and `skipped`: sources that did not run, with the reason.
5. Answer with the specific items and their numbers, and include the links. Say which sources failed, if any. Do not invent trends that are not in the report.

`reports/latest.md` holds the same report as readable Markdown if the user wants the whole thing.

## Notes

- The first scan has nothing to compare with, so nothing is marked new. "New" and "climbing" appear from the second scan on.
- If the user wants to set things up visually, `trendscout ui` opens the app with a setup wizard.
- TikTok's trending-video list is a random sample, so it is ranked by views and never marked new.
- `max_credits_per_run` in `config.yaml` caps spending per run.
