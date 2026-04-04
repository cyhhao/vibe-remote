# Message Delivery Scenario Catalog

## Capability

`scheduled result delivery`

## Scenarios

### `MESSAGE-DELIVERY-001 Scheduled result finalizes its delivery anchor`

- Type: happy path
- Layer: scenario
- Given:
  A scheduled turn emits a normal result into its target channel.
- When:
  The dispatcher sends the result.
- Then:
  The sent message becomes the finalized scheduled anchor.
- Current test:
  `tests/test_message_delivery_scenarios.py::test_scheduled_result_delivery_scenario_finalizes_anchor`

### `MESSAGE-DELIVERY-002 Delivery override sends to the parent target but finalizes the source thread`

- Type: happy path
- Layer: scenario
- Given:
  A scheduled result is configured to deliver into the parent channel from a thread-scoped source context.
- When:
  The dispatcher sends the result.
- Then:
  The message is delivered to the override target while finalizing the original source-thread anchor metadata.
- Current test:
  `tests/test_message_delivery_scenarios.py::test_scheduled_result_delivery_override_scenario_uses_parent_channel_target`

## Next High-Priority Gaps

- `MESSAGE-DELIVERY-101` hidden message types should not leak a visible message
- `MESSAGE-DELIVERY-102` attachment upload failures preserve the text result path
- `MESSAGE-DELIVERY-201` Discord long-result splitting preserves the first chunk as scheduled anchor
- `MESSAGE-DELIVERY-202` quick-reply delivery keeps the primary message id stable
