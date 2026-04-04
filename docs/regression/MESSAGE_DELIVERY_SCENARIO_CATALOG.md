# Message Delivery Scenario Catalog

This file is now a human-facing index.

The canonical scenario metadata for this capability lives in:

- `tests/scenarios/message_delivery/catalog.yaml`
- `tests/scenarios/message_delivery/observations.yaml`
- `tests/scenarios/message_delivery/test_message_delivery_scenarios.py`

## Capability

`scheduled result delivery`

## Covered Scenario Bands

- `MESSAGE-DELIVERY-001`
  Scheduled result finalizes its delivery anchor
- `MESSAGE-DELIVERY-002`
  Delivery override sends to the parent target but finalizes the source thread

## Next High-Priority Gaps

- `MESSAGE-DELIVERY-101` hidden message types should not leak a visible message
- `MESSAGE-DELIVERY-102` attachment upload failures preserve the text result path
- `MESSAGE-DELIVERY-201` Discord long-result splitting preserves the first chunk as scheduled anchor
- `MESSAGE-DELIVERY-202` quick-reply delivery keeps the primary message id stable
