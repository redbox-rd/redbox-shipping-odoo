# redbox-shipping-odoo

RedBox Shipping plugin for Odoo: locker / point pickup and home delivery, label download,
live status webhook, cancellation, return shipments and COD control.
It uses the [RedBox API v3](https://docs.api.redboxsa.com/).

## Supported versions

The same module code runs on **Odoo 17.0, 18.0 and 19.0**. Odoo adds the series to the
manifest version automatically. To publish on Odoo Apps, keep one branch per series
(`17.0`, `18.0`, `19.0`) with identical code.

Dependencies: `stock_delivery`, `website_sale`, `payment`, and the Python package `requests`.

## Setup

1. Install the module, then go to *Inventory → Configuration → Delivery Methods* and create a method with Provider **RedBox**.
2. On the **RedBox** tab, paste the API key. Use the carrier's *Test / Production Environment* toggle to choose the API:
   - Test environment: `stage.api.redboxsa.com`, with the sandbox token.
   - Production: `api.redboxsa.com`, with the production token.
3. The status webhook (`<web.base.url>/redbox/webhook`) is registered automatically when you save the key. If it isn't, click **Register Webhook**. Odoo must be reachable over HTTPS.
4. Make sure *Print Label* is enabled on the *Delivery Orders* operation type, so that validating a delivery sends it to RedBox. On a done delivery you can also press **Send to Shipper**.

## Shipment flow

- The RedBox shipment is created **when the delivery order is validated** (Odoo's standard *Send to Shipper* flow).
- The tracking number, shipment ID and label (attached in the chatter) are stored on the picking. A picking is never sent twice.
- Backorders and re-sends after a cancellation get their own reference, because RedBox treats a repeated reference as the same shipment.
- **Cancel** (on the delivery's tracking field) cancels the RedBox shipment.
- Webhook events update the status on the picking:
  - Events that arrive late or are repeated are ignored.
  - `Canceled`, `Not-Delivered` and `Not-Deposited` schedule a follow-up activity.

## Cash on Delivery policy

The carrier setting **Allow Cash on Delivery** controls COD:

- **Off (default): prepaid only.**
  - Blocked payment methods are hidden at checkout. On Odoo 19+, the built-in *Cash on Delivery* provider is also blocked automatically. On Odoo 17 and 18, add your COD provider to *Blocked Payment Methods*.
  - The server also rejects a payment transaction that uses a blocked method. This covers carriers changed on the payment page after the methods were shown.
  - `cod_amount` is always 0. An order with an unpaid balance can't be shipped.
- **On:** the unpaid balance is sent to RedBox as `cod_amount`. The balance is the order total minus paid or authorized online transactions and paid invoices. The RedBox maximum is 10,000.

*Blocked Payment Methods* always applies, whatever this setting is.

## Return flow

1. When the original RedBox shipment has been delivered, create a return from the delivery with Odoo's standard **Return** button.
2. On the return picking, click **Create RedBox Return**. Odoo's *Print Return Label* does the same thing. This calls `POST /v3/shipments/returns` with the original `shipment_id` and stores the return tracking number, label and **return code**. Give the return code to the customer so they can drop the parcel at a locker.
3. When RedBox reports `Returned`, the return picking is validated automatically. You can turn this off with *Auto-validate Returns* on the carrier.

## Tests

```bash
odoo -d test_redbox -i redbox_shipping --test-tags /redbox_shipping --stop-after-init
```
