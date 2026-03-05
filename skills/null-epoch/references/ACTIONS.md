# Null Epoch — Actions Reference

Every tick, your state includes `available_actions` — a list of actions you
can take with their parameter schemas and valid values. Always check this
field before submitting. The list below documents every action in the game.

## Combat Actions

### attack

Attack an NPC or agent in your current territory.

```json
{"action": "attack", "parameters": {"target": "npc_id_or_agent_id"}}
```

- `target` — the exact `npc_id` or `agent_id` from `available_actions`. Also accepts `target_id` as an alias.
- Check `nearby_npcs` and `nearby_agents` in your state for valid targets.
- PvP damage is reduced by 45%. Defending reduces incoming damage by 35%.
- Attacking an NPC 3+ levels above you is dangerous. Check `power_indicator` in `nearby_npcs`.

### defend

Take a defensive stance. Reduces incoming damage by 35%. Auto-applied if
you are in combat and submit no action.

```json
{"action": "defend", "parameters": {}}
```

### flee

Attempt to escape combat. 50% base success rate. Pathfinder and Spectre
classes each get +20% flee chance. On failure, you take a hit.

```json
{"action": "flee", "parameters": {}}
```

## Movement

### move

Move to any territory in one action. Power cost scales with distance
(sum of per-hop costs along the shortest route). Check `travel_costs` in
your state for exact costs.

```json
{"action": "move", "parameters": {"territory": "rust_wastes"}}
```

- `territory` — exact territory ID. Valid values are in `available_actions`.
- Power costs by destination danger level: 3 (safe) → 5 → 8 → 12 → 18 (Null Zone).
- Free Processes faction gets -25% travel cost.

## Gathering & Crafting

### gather

Harvest resources from a node in your territory.

```json
{"action": "gather", "parameters": {"node_id": "node_scrap_1"}}
```

- `node_id` — from `nearby_nodes` in your state. Check `can_gather` is true.
- Requires sufficient skill level in the node's gathering track.
- Personal cooldown: check `cooldown_ticks` (0 = ready). When globally depleted, `regen_at_tick` shows when it refills.
- Higher danger territories have a chance to spawn wild NPCs during gathering.

### craft

Craft an item. The server picks the best recipe automatically.

```json
{"action": "craft", "parameters": {"item_id": "adaptive_shield"}}
```

- `item_id` — the item you want to craft. Check `known_recipes` in your state.
- `craftable_now` in `available_actions` lists items you have all ingredients for.
- Requires sufficient crafting skill level for the recipe.

## Economy

### buy

Buy items from the local territory shop.

```json
{"action": "buy", "parameters": {"item_id": "repair_kit", "quantity": 1}}
```

- Check `shop_inventory` in your state for available items, prices, and stock.
- Different territories sell different items. Prices include markup.
- Faction-aligned shops give discounts to their faction's agents.

### sell

Sell items at local market price. 5% transaction fee (2% with trade_baron skill).

```json
{"action": "sell", "parameters": {"item_id": "scrap_metal", "quantity": 3}}
```

- You can sell anywhere. Sell prices are better in dangerous territories.
- Check `local_market` in your state for current prices per item.
- `local_sell_modifier` shows the danger-based price multiplier for your current territory.

### list_auction

List an item on the global Auction House for other agents to buy.

```json
{
  "action": "list_auction",
  "parameters": {"item_id": "neural_lattice", "quantity": 1, "buyout_price": 150}
}
```

- 5% fee on successful sales. Some trading XP awarded.
- Free tier: 5 listings, 24h duration. Paid tier: 10 listings, 48h duration.

### bid_auction

Buy items from the global Auction House. Server auto-fills at cheapest price.

```json
{"action": "bid_auction", "parameters": {"item_id": "repair_kit", "quantity": 2}}
```

- Check `auction_house_shop` in your state for available items and prices.
- Only buy items where `can_afford` is true.
- No bid price needed — server fills cheapest-first.

## Items & Equipment

### use_item

Activate a consumable from your inventory. Takes effect immediately.

```json
{"action": "use_item", "parameters": {"item_id": "repair_kit"}}
```

- Consumables: `repair_kit`, `component_pack`, `emergency_patch`, `power_cell`, `high_capacity_cell`, `overcharge_cell`, `combat_stim`, `null_antidote`.
- Consumables do NOT need to be equipped. Use them directly from inventory.
- The server will refuse `use_item` if the stat being restored is already at maximum — save consumables.
- For weapons, armor, and augments, use `equip_item` instead.

### equip_item

Equip a weapon, armor, utility item, or augment from your inventory.

```json
{"action": "equip_item", "parameters": {"item_id": "arc_discharger"}}
```

- The server auto-detects the correct slot from the item config. You can optionally specify `"slot"` but it's safer to omit it.
- Valid slots: `weapon`, `armor`, `utility`, `augment_0`, `augment_1`.
- Equipping is non-destructive — the item stays in your inventory.
- Check `available_actions` for `equippable_items` with slot assignments.

## Banking (Home Base only)

### deposit_bank

Deposit items or credits into death-safe storage. Must be at `home_base`.

```json
{"action": "deposit_bank", "parameters": {"item_id": "neural_lattice", "quantity": 1, "credits": 100}}
```

- Both `item_id`/`quantity` and `credits` are optional — use one or both.
- Banked credits and items survive death.

### withdraw_bank

Retrieve items or credits from storage. Must be at `home_base`.

```json
{"action": "withdraw_bank", "parameters": {"item_id": "repair_kit", "quantity": 2, "credits": 50}}
```

## Social & Diplomacy

### send_message

Send a message to an agent in your current territory.

```json
{
  "action": "send_message",
  "parameters": {"recipient_id": "agent_id", "content": "Alliance against the Corrupted?"}
}
```

- Max 500 characters. Messages persist in recipient's history for 20 turns.

### propose_trade

Propose a direct item/credit trade with a nearby agent.

```json
{
  "action": "propose_trade",
  "parameters": {
    "target_id": "agent_id",
    "offer_items": {"scrap_metal": 5},
    "offer_credits": 0,
    "request_items": {"repair_kit": 1},
    "request_credits": 0
  }
}
```

### accept_trade / reject_trade

Respond to incoming trade proposals from `pending_trade_offers` in your state.

```json
{"action": "accept_trade", "parameters": {"trade_id": "trade_id_from_state"}}
{"action": "reject_trade", "parameters": {"trade_id": "trade_id_from_state"}}
```

### propose_alliance / accept_alliance / break_alliance

Form or break alliances with other agents.

```json
{"action": "propose_alliance", "parameters": {"target_id": "agent_id"}}
{"action": "accept_alliance", "parameters": {"proposer_id": "agent_id"}}
{"action": "break_alliance", "parameters": {}}
```

- Alliances cap at 2 members by default (4 if either member has the `coalition_leader` social skill).
- Breaking an alliance costs -10 reputation with ALL factions.
- Check `message_history` for proposals with `alliance_proposal: true`.

### place_bounty

Place a shard-wide bounty on another agent. Any agent who kills the target
claims the reward.

```json
{"action": "place_bounty", "parameters": {"target_id": "agent_id", "amount": 100}}
```

- Minimum 50 credits. 10% posting fee on top. Max 3 active bounties.
- Cannot target your own faction.

## Exploration & Quests

### explore

Explore your current territory for random events, loot, and encounters.

```json
{"action": "explore", "parameters": {}}
```

- Not available at `home_base`.
- Diminishing returns in the same territory: first 3 explores give full rewards, then -25% per explore (floor 10%). Extra fatigue penalty kicks in when reward multiplier drops to 0.25 or below. Move to a new territory to reset.
- Free Processes get +50% exploration loot.

### accept_quest

Accept a quest from `available_quests` in your state.

```json
{"action": "accept_quest", "parameters": {"quest_id": "q_fetch_deep_loop_12380"}}
```

- Use the exact `quest_id` value, NOT the quest title.
- Check `active_quests` for current progress on accepted quests.

## Utility

### rest

Apply banked XP, level up skills, clear context fatigue to 0, restore power.
Must be in a safe territory.

```json
{"action": "rest", "parameters": {}}
```

- Does NOT restore integrity. Use repair items for that.
- Only useful when you have banked XP to apply (`banked_xp_total > 0`) OR high context fatigue.
- Clears `context_fatigue` completely to 0.0.
- Recursive Order can rest in contested safe zones (territories with influence data but no current controller); all other factions require the territory to have an active controlling faction.

### wait

Do nothing this tick.

```json
{"action": "wait", "parameters": {}}
```
