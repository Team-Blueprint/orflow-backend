# Subscriptions

The Subscriptions API allows you to create and manage recurring payments for your integration.

---

## Create Subscription

Create a subscription on your integration.

### Endpoint
`POST /v1/subscriptions/create`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `customer_id` | `string` | **Yes** | The UUID of the customer to subscribe. |
| `plan_id` | `string` | **Yes** | The UUID of the plan to subscribe the customer to. |
| `payment_method_id` | `string` | No | The UUID of the payment method to charge. If not provided, a checkout link will be generated. |
| `trial` | `boolean` | No | Set to `true` to force a trial period. Defaults to `false`. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/create" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
    "plan_id": "b03f8a02-5e46-4dc4-a1a6-2b4a1f6a1e3e",
    "trial": false
  }'
```

### Sample Response

```json
{
  "subscription": {
    "id": "e98e0e7a-3db9-4f7f-8d2a-8c4b12345678",
    "tenant_id": "1b9d6bcd-bbfd-4b2d-9b5d-ab8dfbbd4bed",
    "customer_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
    "plan_id": "b03f8a02-5e46-4dc4-a1a6-2b4a1f6a1e3e",
    "payment_method_id": null,
    "status": "incomplete",
    "type": "recurring",
    "current_period_start": null,
    "current_period_end": null,
    "trial_end": null,
    "canceled_at": null,
    "cancel_at_period_end": false,
    "created_at": "2026-07-06T15:30:00Z",
    "updated_at": "2026-07-06T15:30:00Z"
  },
  "checkout_link": "https://api-sandbox.nomba.com/checkout/ord_123456789",
  "order_reference": "ord_123456789"
}
```
