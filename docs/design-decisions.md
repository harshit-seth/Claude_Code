# Design decisions

## Why exact-match edits?

**Q: Why does `edit_file` replace an exact string that must occur only once?**
**A:** A unique match makes the intended change explicit and prevents silently editing the wrong occurrence. If the text is missing or ambiguous, the operation fails instead of guessing. The permission prompt can preview the exact resulting diff.

## Why this permission model?

**Q: Why allow read-only tools immediately but prompt for mutations?**
**A:** Inspection is the normal low-risk path for an agent, while writes and shell commands can change the workspace or environment. Showing a diff before file writes makes the proposed change reviewable. One-time approval and per-tool session approval reduce repetitive prompts while keeping the default conservative. `--yolo` is an explicit opt-out; selected dangerous shell patterns remain blocked.

## How does context compaction work?

**Q: What happens when conversation history grows too large?**
**A:** Before each model request, the context manager counts tokens (or estimates them), truncates oversized tool output, and checks whether usage exceeds 70% of the configured context window. When it does, it summarizes earlier messages and keeps the latest six. If the optional Anthropic token-count endpoint is unavailable or not configured, counting falls back to a character-based estimate. Compaction reduces context pressure, but can lose details.

## Why have a max-iteration limit?

**Q: Why stop an agent that keeps requesting tools?**
**A:** A bounded loop prevents an accidental or model-driven tool-call cycle from running indefinitely and gives the user a clear failure to investigate. The default is 25 model iterations per turn.

## What would I improve next?

**Q: Which improvements would add the most value?**
**A:** First, strengthen isolation and permission controls for shell and MCP execution. Next, persist resumable conversation sessions with explicit retention and redaction controls. Then expand evaluation coverage, record reproducible model/configuration metadata, and measure task quality across repeated runs. Finally, make context token counting provider-aware and preserve more structured state during compaction.
