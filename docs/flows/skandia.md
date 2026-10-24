# Skandia México — call flow

Getting the credentials: [`docs/credentials.md`](../credentials.md#skandia).

Provider id: `skandia`  
API: `mobile.skandia.com.mx` (SKANDIA NET MX mobile app backend)

## Auth

`username` (CURP, 18 characters) + `password` (6-digit numeric PIN) — same credentials as the web portal and mobile app.

No captcha or OTP: the app API takes the CURP and PIN directly.

## Flow

```mermaid
sequenceDiagram
    participant C as Caller
    participant P as Skandia provider
    participant S as mobile.skandia.com.mx

    C->>P: fetch_balances()
    P->>S: POST /api/CoporateApp/Login/login?user=CURP&password=PIN
    S-->>P: {Token JWT Plan PartId}
    P->>S: POST /api/CoporateApp/Employees/Employees  Authorization: Basic JWT  corpplan: Plan
    S-->>P: {Products [{ProductName TotalAmmount Detail}]}
    P->>P: map Products to list[Balance(INVESTMENT)]
    P-->>C: list[Balance]
```

## Response structure (Employees)

```json
{
  "Products": [
    {
      "ProductName": "FONDO DE AHORRO",
      "ProductType": "FondoAhorro",
      "TotalAmmount": 50000.00,
      "Detail": [
        {"Id": "S", "Key": "APORTACION EMPLEADO", "Value": 25000.00},
        {"Id": "T", "Key": "APORTACION EMPRESA",  "Value": 25000.00}
      ]
    }
  ]
}
```

One `Balance(AccountKind.INVESTMENT)` is emitted per `Products` entry using `TotalAmmount`.

## Token

The JWT returned by login expires in **20 minutes**. The provider re-logs in on every
`fetch_balances()` call so the caller never has to manage session state.

## Error handling

| Condition | Exception |
|---|---|
| Missing `username` or `password` | `AuthRequired` |
| HTTP 401 from login | `AuthRequired` |
| HTTP 5xx from login | `SessionExpired` |
| `Token` null / error message in response | `AuthRequired` |
| HTTP 401/403 from Employees | `SessionExpired` |
| `Products` missing or empty | `LayoutChanged` |
