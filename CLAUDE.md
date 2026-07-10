# CLAUDE.md

## UI Quality Rules

This app should feel like a professional keirin data analysis console. Keep it simple, premium, trustworthy, and comfortable for repeated work use. Avoid old gambling-site styling.

### Current UI Issues To Avoid

- Do not let race cards, metrics, tickets, and explanations all share the same visual weight.
- Do not create nested card-heavy layouts inside race cards. Use dividers, badges, pills, and spacing for hierarchy.
- Do not rely on a single green palette. Use neutral surfaces with restrained accent colors.
- Do not allow sidebar controls to dominate the main forecast workflow.
- Do not add a visible data-source screen or source tab unless the user explicitly asks for it.
- Do not leave Japanese UI labels garbled or overly technical.
- Do not let mobile layouts depend on wide two-column grids.

### Shared Components

- Use `.button`, `.tab`, `.metric`, `.race-card`, `.surface-panel`, `.data-table`, `.badge`, `.rank-pill`, `.ticket-chip`, `.car`, `.race-ribbon`, and `.plan-card` as the main reusable UI primitives.
- Add new UI by composing those primitives before creating new visual patterns.
- Race cards should follow this order: header, confidence, main pick/top 3/tickets, scenario, lines, details.
- Race cards should include diagram-style visualizations when explaining line relationships or race flow.
- Tables should always use `.data-table` inside `.table-wrap` so mobile overflow remains controlled.

### Visual Tokens

- Design language: Linear-style dark navy (质感) + Polymarket-style market cards (情報カード) + Stripe-style finance dashboard (結果と収支).
- Palette lives in `app/styles.css` `:root`: bg `#080B10`, card `#10151D`, border `#202733`, text `#F4F7FA`, muted `#8D97A6`, accent `#5EE6A8`, profit `#42D392`, loss `#FF6678`, caution `#F4C95D`.
- Keirin car colors are used ONLY as small bibs/badges (`.car-1`..`.car-9`), never as page-level theming.
- Border radius: cards 12px, buttons/inputs 8px, small chips 4px. No pill buttons.
- Subtle borders (1px `--color-border`), generous card padding, restrained gradients. Numbers use tabular-nums; KPI values are the largest text on screen.
- Profit/loss always colored via `--color-profit` / `--color-loss`.

### Layout

- Desktop layout: fixed-width sidebar plus flexible main workspace.
- Main pages should start with `.page-header`, then KPI metrics, then the primary work surface.
- Sidebar groups should be quiet and secondary. The forecast list is the main workflow.
- Cards should be scan-friendly: strong title, concise metadata, clear primary action/result.
- For WINTICKET-inspired race UI, prefer a compact product nav, current-venue board, start-time chips, confidence badges, AI forecast blocks, and comment/detail affordances.
- Use official keirin car-number colors for `.car-1` through `.car-9` when displaying car numbers.
- Today's forecast list should be grouped by venue first, then sorted by start time within each venue.
- Dense forecast lists should default to collapsed `.race-ribbon` rows. The ribbon summary must show start time, race number, top pick, confidence, and primary tickets; the expanded body can show scenarios, line diagrams, comments, and entry details.
- Capital planning must distinguish live odds from estimated odds. Show the race, ticket, stake, projected return, confidence, risk, expected value guide, and whether the odds source is `LIVE` or estimated.
- When WINTICKET EX data is available, surface it as short decision signals beside the ticket; do not bury it in raw JSON.
- Expected value and return views should feel like an investment dashboard: stake, projected return, hit probability, risk, and live/estimated odds must be visually separated.
- Race details should make the line diagram, top pick, tickets, comments, and player table easy to scan without opening unrelated data-source panels.

### Mobile

- At tablet/mobile widths, stack the sidebar above the workspace.
- Reduce grids to one column where content is explanatory or long.
- Keep KPI cards two columns only when labels still fit; otherwise stack.
- All tables must remain horizontally scrollable instead of shrinking text.
- Race ribbons should switch to a compact two-line summary on narrow screens instead of shrinking the forecast text.
- Buttons, tabs, and race ribbons must keep at least 40px tap targets.

### Copy

- UI copy should be short, plain Japanese.
- Use domain terms such as 本命, 対抗, 展開, ライン, 買い目, 期待値, 収支, 的中目安, and 学習状態 consistently.
- Do not add explanatory marketing copy to the main app screen.
