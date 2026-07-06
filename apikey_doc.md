# API Reference

Welcome to the API reference. Authenticate all requests by including your `X-API-Key` in the request headers.

# Customers

---

## Create a new customer

Registers a new customer for the tenant. Customers represent the entities that hold subscriptions and payment methods.

### Endpoint
`POST /v1/customers/create`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `email` | `string` | **Yes** |  |
| `name` | `string` | **Yes** |  |
| `external_id` | `string` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/customers/create" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
      "email": "string",
      "name": "string",
      "external_id": "c56a4180-65aa-42ec-a945-5fd21dec0538"
    }'
```

---

## List all customers

Returns a paginated list of all customers for the current tenant.

### Endpoint
`GET /v1/customers/all`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/customers/all" \
  -H "X-API-Key: sk_test_your_secret_key_here"
```

---

## Get a customer

Fetches a specific customer by ID.

### Endpoint
`GET /v1/customers/{customer_id}`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/customers/{customer_id}" \
  -H "X-API-Key: sk_test_your_secret_key_here"
```

---

## Update a customer

Partially updates a customer's information.

### Endpoint
`PATCH /v1/customers/{customer_id}/update`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `email` | `string` | No |  |
| `name` | `string` | No |  |
| `external_id` | `string` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/customers/{customer_id}/update" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
      "email": "string",
      "name": "string",
      "external_id": "c56a4180-65aa-42ec-a945-5fd21dec0538"
    }'
```

---

## Delete a customer

Deletes a customer permanently.

### Endpoint
`DELETE /v1/customers/{customer_id}/del`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/customers/{customer_id}/del" \
  -H "X-API-Key: sk_test_your_secret_key_here" \

```

---

# Plans

---

## Create a new plan

Creates a new billing plan defining pricing, interval, and features.

### Endpoint
`POST /v1/plans/create`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |
| `X-Project-ID` | `string` | The UUID of the project. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | **Yes** |  |
| `amount` | `number` | **Yes** |  |
| `currency` | `string` | **Yes** |  |
| `interval` | `string` | **Yes** |  |
| `interval_count` | `int` | No |  |
| `trial_period_days` | `int` | No |  |
| `installments_count` | `int` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/plans/create" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: your_project_id_here" \
  -d '{
      "name": "string",
      "amount": 0.0,
      "currency": "string",
      "interval": "string",
      "interval_count": 0,
      "trial_period_days": 0,
      "installments_count": 0
    }'
```

---

## List all plans

Returns a paginated list of all plans for the current tenant, including subscription count and total collected revenue.

### Endpoint
`GET /v1/plans/list`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/plans/list" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

## Get a plan

Fetches a specific plan by ID, including subscription count and total collected revenue.

### Endpoint
`GET /v1/plans/{plan_id}`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/plans/{plan_id}" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

## Update a plan

Partially updates a plan's information.

### Endpoint
`PATCH /v1/plans/{plan_id}/update`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |
| `X-Project-ID` | `string` | The UUID of the project. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | No |  |
| `status` | `string` | No |  |
| `trial_period_days` | `int` | No |  |
| `installments_count` | `int` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/plans/{plan_id}/update" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: your_project_id_here" \
  -d '{
      "name": "string",
      "status": "string",
      "trial_period_days": 0,
      "installments_count": 0
    }'
```

---

## Archive a plan

Archives a plan so it can no longer be subscribed to.

### Endpoint
`POST /v1/plans/{plan_id}/archive`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/plans/{plan_id}/archive" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: your_project_id_here" \

```

---

# Subscriptions

---

## Create a new subscription

Creates a new subscription for a customer to a plan. If the plan has a trial period, it starts in `trialing` state. Otherwise, it initiates a checkout session to collect the first payment, returning `checkout_link` and `order_reference`.

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
| `customer_id` | `string` | **Yes** |  |
| `plan_id` | `string` | **Yes** |  |
| `payment_method_id` | `string` | No |  |
| `trial` | `boolean` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/create" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
      "customer_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "plan_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "payment_method_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "trial": false
    }'
```

---

## Cancel a subscription

Transitions a subscription to `canceled` status immediately. Stops all future billing.

### Endpoint
`POST /v1/subscriptions/{subscription_id}/cancel`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/{subscription_id}/cancel" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \

```

---

## Pause a subscription

Transitions an active subscription to `paused` status. It will not be billed until resumed.

### Endpoint
`POST /v1/subscriptions/{subscription_id}/pause`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/{subscription_id}/pause" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \

```

---

## Resume a paused subscription

Transitions a paused subscription back to `active` status.

### Endpoint
`POST /v1/subscriptions/{subscription_id}/resume`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/{subscription_id}/resume" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \

```

---

## Change a subscription's plan with proration

Switches an active subscription to a new plan, crediting unused time on the old plan and charging the remaining time on the new plan as explicit invoice line items. The net amount is charged immediately; a failed charge falls into the dunning flow.

### Endpoint
`POST /v1/subscriptions/{subscription_id}/change-plan`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `new_plan_id` | `string` | **Yes** |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/{subscription_id}/change-plan" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
      "new_plan_id": "c56a4180-65aa-42ec-a945-5fd21dec0538"
    }'
```

---

## Get subscription audit log

Returns the full history of state transitions for a specific subscription, newest first.

### Endpoint
`GET /v1/subscriptions/{subscription_id}/audit-log`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/{subscription_id}/audit-log" \
  -H "X-API-Key: sk_test_your_secret_key_here"
```

---

## List subscriptions

Returns all subscriptions for the current tenant and project. Optionally filter by plan_id.

### Endpoint
`GET /v1/subscriptions/list`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/list" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

## List subscribers

Returns all customers with subscriptions in the current project. Optionally filter by plan_id.

### Endpoint
`GET /v1/subscriptions/subscribers/list`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscriptions/subscribers/list" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

# Subscription-pages

---

## Create a subscription page

Creates a shareable subscription page linked to a plan. A unique code is auto-generated for the URL.

### Endpoint
`POST /v1/subscription-pages/create`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |
| `X-Project-ID` | `string` | The UUID of the project. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `plan_id` | `string` | **Yes** |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/create" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: your_project_id_here" \
  -d '{
      "plan_id": "c56a4180-65aa-42ec-a945-5fd21dec0538"
    }'
```

---

## List subscription pages

Returns all subscription pages for the current tenant and project.

### Endpoint
`GET /v1/subscription-pages/list`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/list" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

## Get a subscription page

Fetches a specific subscription page by ID.

### Endpoint
`GET /v1/subscription-pages/{page_id}`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/{page_id}" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here"
```

---

## Delete a subscription page

Permanently deletes a subscription page.

### Endpoint
`DELETE /v1/subscription-pages/{page_id}`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `X-Project-ID` | `string` | The UUID of the project. |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/{page_id}" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "X-Project-ID: your_project_id_here" \

```

---

## Update a subscription page

Partially updates a subscription page (e.g. change plan or deactivate).

### Endpoint
`PATCH /v1/subscription-pages/{page_id}/update`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |
| `X-Project-ID` | `string` | The UUID of the project. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `plan_id` | `string` | No |  |
| `is_active` | `boolean` | No |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/{page_id}/update" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -H "X-Project-ID: your_project_id_here" \
  -d '{
      "plan_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
      "is_active": false
    }'
```

---

## Get plan info by page code (public)

Public endpoint that returns plan details for a given subscription page code. Used by the checkout frontend to render the page.

### Endpoint
`GET /v1/subscription-pages/code/{code}`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/code/{code}" \
  -H "X-API-Key: sk_test_your_secret_key_here"
```

---

## Initiate checkout for a subscription page (public)

Public endpoint that creates a customer and subscription, then returns a Nomba checkout link for card payment.

### Endpoint
`POST /v1/subscription-pages/code/{code}/checkout`

### Headers

| Header | Type | Description |
| :--- | :--- | :--- |
| `X-API-Key` | `string` | Set value to your secret key (e.g. `sk_test_...` or `sk_live_...`). |
| `Content-Type` | `string` | Set value to `application/json`. |

### Body Parameters

| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | **Yes** |  |
| `email` | `string` | **Yes** |  |

### Example Request (cURL)

```sh
curl "https://api.yourdomain.com/v1/subscription-pages/code/{code}/checkout" \
  -H "X-API-Key: sk_test_your_secret_key_here" \
  -H "Content-Type: application/json" \
  -d '{
      "name": "string",
      "email": "string"
    }'
```

---

