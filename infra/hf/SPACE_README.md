---
title: maint-rag
emoji: 🛠️
colorFrom: orange
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
short_description: Cited answers from LOTO, FAA, and data center docs
---

# maint-rag

A maintenance and data center operations assistant: hybrid retrieval (vector + BM25, fused with RRF)
over OSHA lockout/tagout, the FAA maintenance handbook, DOE data center guidance, and synthetic work
orders. Every answer cites its source page.

Source code, evaluation results, and runbook: https://github.com/TTOD-SOL/maint-rag

This is a free Space: it sleeps after inactivity, so the first visit can take about 30 seconds to wake.
