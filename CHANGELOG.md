# Changelog

All notable changes to `tne-sdk` are listed here. Release notes for each
version are also published on the
[GitHub Releases page](https://github.com/Firespawn-Studios/tne-sdk/releases).

## [1.0.2] - 2026-10-29

A reliability release. OpenAI works again, agents stay connected through
server restarts and network drops, and actions match the stricter checks in
the latest server update.

**Upgrade:** `pip install -U tne-sdk`. No config or code changes are needed.

### Fixed

- **OpenAI.** Profiles using `https://api.openai.com/v1` never made a
  successful call; every turn fell back to `wait`. They work now. Settings the
  OpenAI API doesn't support, such as `top_k`, are skipped, with a one-time
  warning in the log.
- **Reconnecting.** The client now reconnects after every disconnect except an
  invalid API key or a dormant agent. Before, some clean disconnects stopped it
  for good. After a long healthy session, the first reconnect also happens
  within seconds instead of waiting up to a minute.
- **Stalled connections.** If the connection stays open but game state stops
  arriving for three ticks (about three minutes), the client reconnects instead
  of waiting forever. Time your agent spends thinking doesn't count, so slow
  models are not affected.
- **Actions and the latest server update.** The server now rejects an action if
  a parameter holds an unexpected object. When a model adds extra fields to its
  reply, such as `"thoughts": {...}`, the agent drops them instead of losing the
  turn. A malformed `parameters` value is treated as empty instead of failing
  the turn.
- **Unreadable server messages** are skipped instead of stopping the client.
- **`tne-mcp`.** When the server refuses an action, your assistant now sees the
  server's reason, such as the list of valid actions, instead of just
  `400 Bad Request`.
- **`tne-relay`:**
  - It gets the same reconnect and stall fixes.
  - `relay/result.json` now reports refusals as
    `{"status": "error", "code": ..., "message": ...}`.
  - An `action.json` that can't be read is reported there too. A corrected file
    is still picked up in the same tick.
- **Launcher.** Quitting `tne-launcher` while an agent is running no longer
  crashes or prints errors.

### Changed

- `TNEClient.run()` accepts an optional `on_message` callback, which receives
  every server message other than game state (`action_queued`, `error`, ...).
  Existing code is unaffected.
- Actions sent by the agent now always include a `parameters` object, empty if
  the action takes none. The server treats both forms the same.

### Known issue

- OpenAI reasoning models (the o-series and GPT-5) reject some of the settings
  the agent sends, so they may still return errors. GPT-4o-style models work.
  Support is planned for a later release.

### Compatibility

- Python 3.10–3.13.
- Works with the current game server and the upcoming server update.

## Earlier versions

Versions up to 1.0.1 were published without a changelog. See the
[PyPI release history](https://pypi.org/project/tne-sdk/#history).
