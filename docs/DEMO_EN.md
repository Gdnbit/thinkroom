# Try ThinkRoom

[Home](../README_EN.md) · [简体中文](DEMO.md)

Compare different ways a small shop could pilot AI customer support.

## Add the sources

Follow the [quick start](../README_EN.md#quick-start) and open the [chat workspace](http://127.0.0.1:5173/chat). Choose **上传资料** (Upload sources) and add these three sample files.

| Source | Perspective |
| --- | --- |
| [Start small](../examples/demo-perspectives/01-small-pilot.txt) | Start with common, low-risk questions. |
| [Set boundaries first](../examples/demo-perspectives/02-data-risks.txt) | Evaluate privacy, answer accuracy, and maintenance costs. |
| [Shop owner's notes](../examples/demo-perspectives/03-own-notes.txt) | Separate public store policies from private order information. |

## Ask a question

| Mode | Example prompt |
| --- | --- |
| **普通问答** (Chat) | What does each source emphasize? Cite the evidence for each point. |
| **简报生成** (Briefing) | If we start next week, which questions should we cover first? What should we verify? |
| **委员会讨论** (Committee) | Compare the plan from the perspectives of pilot speed, privacy, and maintenance cost. |

Open **参考资料** (Sources) in the answer to inspect its citations. Use **保存判断** (Save judgment) on eligible answers and record the outcome under **复盘** (Review).

## Model setup

Mock mode uses sample responses so you can explore the workspace. To use a live model, supply `OPENROUTER_API_KEY` to the backend process through your credential manager and start it without `--mock`. Live requests can incur charges and send relevant sources to the configured provider.

The README workspace preview uses a sample conversation and answer.
